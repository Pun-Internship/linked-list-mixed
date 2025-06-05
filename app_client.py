from flask import Flask, request, jsonify, render_template
import pandas as pd
from sentence_transformers import SentenceTransformer, util
import numpy as np
import re, string
import logging
import os
import time
from fetch_airtable_client import append_to_csv

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app_client.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
    force=True
)

log = logging.info  # ✅ THIS LINE IS REQUIRED

MODEL_NAME  = "all-mpnet-base-v2"
MAX_OUTPUT = 10
SOFT_THRES  = 0.1
DATASET_PATH = "dataset_client.csv" # change path here

def clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    
    # Minimal cleaning - just normalize whitespace and convert to lowercase
    text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
    return text.strip().lower()

def load_dataset():
    df = pd.read_csv(DATASET_PATH).dropna(subset=["Keyword Name", "SEO Name", "Project Name", "Keyword"])
    
    if "Content Type" in df.columns:
        df = df[df["Content Type"] == "On Page"]
    
    df["Keyword"] = df["Keyword"].astype(str)
    df["Keyword Name"] = df["Keyword Name"].astype(str).apply(clean)
    df["url"] = df["url"].astype(str)
    df["SEO Name"] = df["SEO Name"].astype(str)
    df["Project Name"] = df["Project Name"].astype(str)
    df["Month"] = df["Month"].astype(str) if "Month" in df.columns else ""
    df["Internal Link / External Link"] = df["Internal Link / External Link"].astype(str) if "Internal Link / External Link" in df.columns else ""
    
    return df

df = load_dataset()
embedder = SentenceTransformer(MODEL_NAME)
keywords = df["Keyword Name"].tolist()
keyword_vecs = embedder.encode(keywords, normalize_embeddings=True)

last_mtime = os.path.getmtime(DATASET_PATH)

def reload_if_needed():
    global df, keywords, keyword_vecs, last_mtime
    current_mtime = os.path.getmtime(DATASET_PATH)
    if current_mtime > last_mtime:
        df = load_dataset()
        keywords = df["Keyword Name"].tolist()
        keyword_vecs = embedder.encode(keywords, normalize_embeddings=True)
        last_mtime = current_mtime
        logging.info("🔄 Dataset reloaded due to file update")

def get_projects_by_seo():
    reload_if_needed()
    projects_map = {}
    try:
        for seo_name in df["SEO Name"].unique():
            projects = df[df["SEO Name"] == seo_name]["Project Name"].unique().tolist()
            projects_map[seo_name] = sorted(projects)
    except Exception as e:
        logging.error(f"Error creating projects map: {e}")
        projects_map = {}
    return projects_map

@app.route("/linked-list-matcher", methods=["GET", "POST"]) # change path here
def home():
    try:
        reload_if_needed()
        seo_names = sorted(df["SEO Name"].unique()) if len(df) > 0 else []
        projects_map = get_projects_by_seo()
        initial_projects = projects_map.get(seo_names[0], []) if seo_names else []
        return render_template("index_client.html", 
                             seoNames=seo_names,
                             projects_map=projects_map,
                             initialProjects=initial_projects)
    except Exception as e:
        logging.error(f"Error in home route: {e}")
        return render_template("index_client.html", 
                             seoNames=[],
                             projects_map={},
                             initialProjects=[])

@app.route("/linked-list-matcher/webhook", methods=["POST"]) # change path here
def webhook():
    try:
        data = request.json
        record = data.get("record")

        if not record or "id" not in record:
            return jsonify({"status": "error", "reason": "Missing 'record' or 'id'"}), 400

        flat_record = {"id": record["id"], **record.get("fields", {})}
        log(f"📩 Webhook received. Record ID: {record['id']}")

        os.environ["CSV_PATH"] = "dataset.csv"
        append_to_csv([flat_record])
        return jsonify({"status": "success", "updated": record["id"]}), 200

    except Exception as e:
        import traceback
        log(f"❌ Exception during webhook: {e}")
        log(traceback.format_exc())
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/linked-list-matcher/get-projects", methods=["POST"]) # change path here
def get_projects():
    try:
        data = request.get_json()
        seo_name = data.get("seoName", "")

        reload_if_needed()
        projects = df[df["SEO Name"] == seo_name]["Project Name"].dropna().unique().tolist()
        projects = sorted(projects)

        return jsonify({"projects": projects})
    except Exception as e:
        logging.error(f"Error in get-projects route: {e}")
        return jsonify({"projects": []}), 500
    
@app.route("/linked-list-matcher/search", methods=["POST"]) # change path here
def search():
    try:
        reload_if_needed()
        data = request.json
        input_kw = data.get("keywords", [""])[0]
        selected_seo_name = data.get("seoName", "").strip()
        selected_project_name = data.get("projectName", "").strip()
        cleaned_input = clean(input_kw)

        log(f"🔍 Search input: '{input_kw}' -> cleaned: '{cleaned_input}'")
        log(f"📊 SEO Name: '{selected_seo_name}', Project: '{selected_project_name}'")

        filtered_df = df[df["SEO Name"] == selected_seo_name]
        filtered_df['Keyword Name'] = filtered_df['Keyword Name'].astype(str).apply(clean)
        if selected_project_name:
            filtered_df = filtered_df[filtered_df["Project Name"] == selected_project_name]

        log(f"📋 Filtered dataset size: {len(filtered_df)}")
        
        # Debug: Check if the keyword exists in filtered dataset
        filtered_df["Keyword Name"] = filtered_df["Keyword Name"].astype(str).apply(clean)
        matching_keywords = filtered_df[filtered_df["Keyword Name"] == cleaned_input]
        log(f"🎯 Found {len(matching_keywords)} matching keywords for '{cleaned_input}'")
        
        if matching_keywords.empty:
            # Additional debug info
            all_keywords_in_filter = filtered_df["Keyword Name"].tolist()
            log(f"❌ Exact match not found. Sample keywords in dataset: {all_keywords_in_filter[:10]}")
            log(f"🔍 ALL keywords for SEO '{selected_seo_name}' and Project '{selected_project_name}': {all_keywords_in_filter}")
            
            # Check for partial matches (for debugging)
            partial_matches = [kw for kw in all_keywords_in_filter if cleaned_input in kw or kw in cleaned_input]
            log(f"🔄 Partial matches found for '{cleaned_input}': {len(partial_matches)}")
            
            return jsonify({"error": "Keyword not found"}), 404

        input_data = matching_keywords.iloc[0]
        month = input_data.get("Month", "")
        keyword_display_text = input_data["Keyword"]  # ✅ Use display version
        internal_external = input_data.get("Internal Link / External Link", "")
        url = input_data["url"]

        def extract_links(link_str):
            if not isinstance(link_str, str):
                return []
            pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
            return [{"title": m.group(1), "url": m.group(2)} for m in pattern.finditer(link_str)]

        links = extract_links(internal_external)

        kw_list = filtered_df["Keyword Name"].tolist()
        kw_vecs = embedder.encode(kw_list, normalize_embeddings=True)
        input_vec = embedder.encode(cleaned_input, normalize_embeddings=True)
        sims = [(kw, float(util.cos_sim(input_vec, vec))) for kw, vec in zip(kw_list, kw_vecs)]
        sims.sort(key=lambda x: x[1], reverse=True)
        model_output = [kw for kw, sim in sims if kw != cleaned_input and sim >= SOFT_THRES][:MAX_OUTPUT]

        model_output_links = []
        for kw in model_output:
            row = filtered_df[filtered_df["Keyword Name"] == kw]
            if not row.empty:
                model_output_links.append({
                    "text": kw,  # ✅ Show readable keyword
                    "url": row.iloc[0]["url"]
                })
            else:
                model_output_links.append({
                    "text": kw,
                    "url": "#"
                })

        return jsonify({
            "month": month,
            "keyword": {
                "name": keyword_display_text,
                "url": url
            },
            "links": links if links else [],
            "model_output": model_output_links if model_output_links else [],
            "raw_internal_link_text": internal_external if internal_external.strip() else ""
        }), 200

    except Exception as e:
        logging.error(f"❌ Error in search route: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004)