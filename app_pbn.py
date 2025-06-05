from flask import Flask, request, jsonify, render_template
import pandas as pd
from sentence_transformers import SentenceTransformer, util
import numpy as np
import re, string
import logging
import os
import time
from fetch_airtable_pbn import append_to_csv

app = Flask(__name__)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("webhook_pbn.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
    force=True
)

log = logging.info  # ✅ THIS LINE IS REQUIRED


MODEL_NAME  = "all-mpnet-base-v2"
TOP_LIMIT   = 20
HARD_THRES  = 0.75
SOFT_THRES  = 0.50
DATASET_PATH = "dataset_pbn.csv"

import re
import string

def clean(text: str) -> str:
    # Remove scheme like "http://" or "https://"
    if "://" in text:
        text = text.split("://", 1)[1]

    # Keep dots and hyphens only
    allowed = string.punctuation.replace('.', '').replace('-', '')
    return re.sub(r"\s+", " ",
                  text.translate(str.maketrans("", "", allowed))
                  ).strip().lower()


def load_dataset():
    df = pd.read_csv(DATASET_PATH).dropna(subset=["Main Keyword", "🔗 Keyword Link", "Categories", "Website"])
    df["Main Keyword"] = df["Main Keyword"].astype(str).apply(clean)
    df["🔗 Keyword Link"] = df["🔗 Keyword Link"].astype(str)
    df["Categories"] = df["Categories"].astype(str).apply(clean)
    df["Website"] = df["Website"].astype(str).apply(clean)
    
    df["WebsiteName"] = df["Website"]
    df["CategoryName"] = df["Categories"]
    
    return df

df = load_dataset()
embedder = SentenceTransformer(MODEL_NAME)
keywords = df["Main Keyword"].tolist()
keyword_vecs = embedder.encode(keywords, normalize_embeddings=True)

last_mtime = os.path.getmtime(DATASET_PATH)
def reload_if_needed():
    global df, keywords, keyword_vecs, last_mtime
    current_mtime = os.path.getmtime(DATASET_PATH)
    if current_mtime > last_mtime:
        df = load_dataset()
        keywords = df["Main Keyword"].tolist()
        keyword_vecs = embedder.encode(keywords, normalize_embeddings=True)
        last_mtime = current_mtime
        logging.info("🔄 Dataset reloaded due to file update")

@app.route("/linklist-pbn", methods=["GET", "POST"])
def home():
    reload_if_needed()
    websites = sorted(df["WebsiteName"].unique())
    return render_template("index_pbn.html", websites=websites)

@app.route("/linklist-pbn/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        record = data.get("record")

        if not record or "id" not in record:
            return jsonify({"status": "error", "reason": "Missing 'record' or 'id'"}), 400

        flat_record = {"id": record["id"], **record.get("fields", {})}
        log(f"📩 Webhook received. Record ID: {record['id']}")

        # Ensure CSV path set
        os.environ["CSV_PATH"] = "dataset_pbn.csv"

        # ✅ Call deduplication appender
        append_to_csv([flat_record])
        return jsonify({"status": "success", "updated": record["id"]}), 200

    except Exception as e:
        import traceback
        log(f"❌ Exception during webhook: {e}")
        log(traceback.format_exc())
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/linklist-pbn/search", methods=["POST"])
def search():
    try:
        # โหลดข้อมูลใหม่ถ้าจำเป็น
        reload_if_needed()

        # รับค่า input จาก client
        data = request.get_json()
        input_kw = data.get("keywords", [""])[0]
        selected_website = data.get("website", "").strip().lower()
        cleaned_input = clean(input_kw)


        # กรอง DataFrame ตามเว็บไซต์ (ถ้ามี)
        filtered_df = df[df["WebsiteName"].str.lower() == selected_website] if selected_website else df
        if filtered_df.empty:
            return jsonify({"error": "No keywords for this website"}), 404

        # เตรียมรายการ Main Keyword และ embedding
        kw_list = filtered_df["Main Keyword"].tolist()
        kw_vecs = embedder.encode(kw_list, normalize_embeddings=True)
        input_vec = embedder.encode(cleaned_input, normalize_embeddings=True)

        # คำนวณ similarity
        sims = [(kw, float(util.cos_sim(input_vec, vec))) for kw, vec in zip(kw_list, kw_vecs)]
        sims.sort(key=lambda x: x[1], reverse=True)

        # แยก matched vs suggested ตาม threshold
        matched = [(kw, sim) for kw, sim in sims if sim >= HARD_THRES]
        suggested = [(kw, sim) for kw, sim in sims if HARD_THRES > sim >= SOFT_THRES]

        # เอา top N ทั้ง matched + suggested
        total_items = matched[:TOP_LIMIT]
        if len(total_items) < TOP_LIMIT:
            need = TOP_LIMIT - len(total_items)
            total_items += suggested[:need]

        # สร้าง lookup สำหรับลิงก์และหมวดหมู่
        html_map   = dict(zip(filtered_df["Main Keyword"], filtered_df["🔗 Keyword Link"]))
        cat_lookup = dict(zip(filtered_df["Main Keyword"], filtered_df["CategoryName"]))

        # เตรียมผลลัพธ์ลิงก์และคำ
        match_words = [kw for kw, sim in matched[:TOP_LIMIT]]
        suggest_words = [kw for kw, sim in suggested[:TOP_LIMIT]]

        # กรองผลลัพธ์ที่ซ้ำกับ input
        match_words    = [kw for kw in match_words    if kw.lower() != cleaned_input.lower()]
        suggest_words  = [kw for kw in suggest_words  if kw.lower() != cleaned_input.lower()]

        # ถ้าไม่มีผลลัพธ์ทั้งคู่ ให้เคลียร์ output
        if not match_words and not suggest_words:
            return jsonify({
                "input": input_kw,
                "category": None,
                "matched_keywords": [],
                "matched_links": [],
                "suggested_keywords": [],
                "suggested_links": [],
                "message": "No similar keywords found."
            })

        # สร้างลิงก์จาก lookup
        match_links   = [html_map.get(kw, f"<a href='#'>{kw}</a>") for kw in match_words]
        suggest_links = [html_map.get(kw, f"<a href='#'>{kw}</a>") for kw in suggest_words]

        # หมวดหมู่ใช้ของผลลัพธ์แรก (ถ้ามี)
        first_kw = match_words[0] if match_words else suggest_words[0]
        category = cat_lookup.get(first_kw, "ไม่ทราบหมวดหมู่")

        # ตอบกลับ JSON
        return jsonify({
            "input": input_kw,
            "category": category,
            "matched_keywords": match_words,
            "matched_links": match_links,
            "suggested_keywords": suggest_words,
            "suggested_links": suggest_links
        })

    except Exception as e:
        logging.exception("❌ Error during prediction")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003)
