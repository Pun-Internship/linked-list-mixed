import os
import requests
import pandas as pd
import logging
import json
from urllib.parse import quote
from datetime import datetime
from dotenv import load_dotenv
from datetime import timezone
from pytz import timezone as pytz_timezone

load_dotenv()

API_TOKEN = os.getenv("AIRTABLE_API_TOKEN_CLIENT")
BASE_ID = os.getenv("AIRTABLE_BASE_ID_CLIENT")
TABLE_IDS = {
    "seo_team": "tbl8IsAhz0Iz0trrF",
    "project_list": "tbl47jwAiaLqdD7H6",
    "main_content": "tblEZLLELhP9q5QLH"
}
CSV_PATH = os.getenv("CSV_PATH", "dataset_client.csv") # change path here

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("fetch_airtable_client.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
    force=True
)
log = logging.info

def fetch_all_records(table_id, label=""):
    log(f"🔄 Fetching records from {label} (ID: {table_id[:6]}***) ...")
    url = f"https://api.airtable.com/v0/{BASE_ID}/{quote(table_id)}"
    all_records = []
    offset = None
    while True:
        params = {"offset": offset} if offset else {}
        res = requests.get(url, headers=HEADERS, params=params)
        if res.status_code != 200:
            log(f"❌ Failed to fetch {label}: {res.text}")
            return []
        data = res.json()
        all_records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    log(f"✅ Fetched {len(all_records)} records from {label}")
    return all_records

def append_to_csv(updated_records):
    if not updated_records:
        log("ℹ️ No records to update.")
        return

    seo_records = fetch_all_records(TABLE_IDS["seo_team"], "SEO Team")
    project_records = fetch_all_records(TABLE_IDS["project_list"], "Project List")
    main_records = updated_records if updated_records else fetch_all_records(TABLE_IDS["main_content"], "Main Content")

    log(f"🔍 DEBUG: Processing {len(main_records)} main records")
    
    seo_map = {r["id"]: r["fields"].get("Name", f"Unknown ({r['id']})") for r in seo_records}
    project_map = {r["id"]: r["fields"].get("Project", f"Unknown ({r['id']})") for r in project_records}

    log(f"🔍 DEBUG: SEO map has {len(seo_map)} entries")
    log(f"🔍 DEBUG: Project map has {len(project_map)} entries")

    enriched_rows = []
    for i, r in enumerate(main_records):
        f = r.get("fields", {})

        needed_fields = [
            "Keyword", "Keyword Name", "url", "Month", "SEO", "Project",
            "Content Type", "Internal Link / External Link"
        ]
        f = {k: v for k, v in f.items() if k in needed_fields}

        
        # Debug specific record processing
        if i < 5:  # Log first 5 records for debugging
            log(f"🔍 DEBUG Record {i}: Raw fields keys: {list(f.keys())}")
            log(f"🔍 DEBUG Record {i}: Keyword field: '{f.get('Keyword', 'NOT_FOUND')}'")
        
        seo_ids = f.get("SEO", [])
        project_ids = f.get("Project", [])
        seo_names = [seo_map.get(sid, f"Unknown ({sid})") for sid in seo_ids]
        project_names = [project_map.get(pid, f"Unknown ({pid})") for pid in project_ids]
        f["SEO Name"] = ", ".join(seo_names)
        f["Project Name"] = ", ".join(project_names)
        enriched_rows.append(f)

    log(f"🔍 DEBUG: Created {len(enriched_rows)} enriched rows")

    df = pd.DataFrame(enriched_rows)
    log(f"🔍 DEBUG: DataFrame shape: {df.shape}")
    log(f"🔍 DEBUG: DataFrame columns: {list(df.columns)}")
    
    if 'Keyword' in df.columns:
        log(f"🔍 DEBUG: Keyword column sample values: {df['Keyword'].head(10).tolist()}")
        log(f"🔍 DEBUG: Total non-null Keywords: {df['Keyword'].notna().sum()}")
        
        # Check for the specific keyword we're looking for
        test_keywords = ['แจ้งความออนไลน์', 'โอซาก้า ที่เที่ยว']
        # for test_kw in test_keywords:
        #     matches = df[df['Keyword'].astype(str).str.contains(test_kw, na=False)]
        #     log(f"🔍 DEBUG: Found {len(matches)} records containing '{test_kw}'")
        #     if len(matches) > 0:
        #         log(f"🔍 DEBUG: Sample match for '{test_kw}': {matches.iloc[0]['Keyword']}")
    else:
        log("❌ DEBUG: No 'Keyword' column found in DataFrame!")
        
    # manual adjustment
    if "Content Type" in df.columns:
        df = df[df["Content Type"] == "On Page"]

    keyword_counts = df['Keyword'].dropna().astype(str).value_counts()
    non_unique_keywords = keyword_counts[keyword_counts > 1].index
    drop_mask = df['Keyword'].isin(non_unique_keywords) & ~df['Keyword'].astype(str).str.contains('-')
    df_cleaned = df[~drop_mask].copy()

    log(f"🔍 DEBUG: After deduplication: {len(df_cleaned)} rows (removed {len(df) - len(df_cleaned)})")

    def split_keyword(val):
        if pd.isna(val):
            return pd.Series([None, None])
        val_str = str(val)
        
        # Debug the splitting process
        if 'แจ้งความออนไลน์' in val_str or 'โอซาก้า ที่เที่ยว' in val_str:
            log(f"🔍 DEBUG: Processing keyword '{val_str}'")
        
        parts = val_str.split('-', 1)
        if len(parts) == 2:
            blog_name = parts[0].strip()
            keyword_name = parts[1].strip()
            if 'แจ้งความออนไลน์' in val_str or 'โอซาก้า ที่เที่ยว' in val_str:
                log(f"🔍 DEBUG: Split result - Blog: '{blog_name}', Keyword: '{keyword_name}'")
            return pd.Series([blog_name, keyword_name])
        else:
            if 'แจ้งความออนไลน์' in val_str or 'โอซาก้า ที่เที่ยว' in val_str:
                log(f"🔍 DEBUG: No split needed - using as keyword: '{val_str.strip()}'")
            return pd.Series([None, val_str.strip()])

    df_cleaned[['Blog Name', 'Keyword Name']] = df_cleaned['Keyword'].apply(split_keyword)
    
    log(f"🔍 DEBUG: After keyword splitting:")
    log(f"🔍 DEBUG: Blog Name sample: {df_cleaned['Blog Name'].head(3).tolist()}")
    log(f"🔍 DEBUG: Keyword Name sample: {df_cleaned['Keyword Name'].head(3).tolist()}")
    
    # Check if our test keywords made it through
    # for test_kw in ['แจ้งความออนไลน์', 'โอซาก้า ที่เที่ยว']:
    #     matches = df_cleaned[df_cleaned['Keyword Name'].astype(str).str.contains(test_kw, na=False)]
    #     log(f"🔍 DEBUG: Final dataset contains {len(matches)} records with keyword name '{test_kw}'")
    #     if len(matches) > 0:
    #         sample_row = matches.iloc[0]
    #         log(f"🔍 DEBUG: Sample row for '{test_kw}': SEO='{sample_row.get('SEO Name')}', Project='{sample_row.get('Project Name')}'")

    cols_to_front = ['Blog Name', 'Keyword Name', 'SEO Name', 'Project Name']
    all_cols = cols_to_front + [c for c in df_cleaned.columns if c not in cols_to_front]
    df_cleaned = df_cleaned[all_cols]

    if os.path.exists(CSV_PATH):
        existing_df = pd.read_csv(CSV_PATH, dtype=str)
        log(f"🔍 DEBUG: Existing CSV has {len(existing_df)} rows")
        combined_df = pd.concat([existing_df, df_cleaned], ignore_index=True)
        combined_df.drop_duplicates(subset=["Keyword Name"], keep="last", inplace=True)
        log(f"🔍 DEBUG: After combining and deduplicating: {len(combined_df)} rows")
    else:
        combined_df = df_cleaned
        log(f"🔍 DEBUG: Creating new CSV with {len(combined_df)} rows")

    # Final check before saving
    # for test_kw in ['แจ้งความออนไลน์', 'โอซาก้า ที่เที่ยว']:
    #     final_matches = combined_df[combined_df['Keyword Name'].astype(str).str.contains(test_kw, na=False)]
    #     log(f"🔍 DEBUG: FINAL CSV will contain {len(final_matches)} records with '{test_kw}'")

    combined_df.to_csv(CSV_PATH, index=False)
    log(f"✅ Updated {CSV_PATH} with {len(df_cleaned)} records (total: {len(combined_df)})")

if __name__ == "__main__":
    try:
        if not API_TOKEN or not BASE_ID:
            log("❌ Missing environment variables")
            exit(1)
        log("🚀 Starting Airtable sync") 

        # Check Thailand time (UTC+7)
        thailand = pytz_timezone('Asia/Bangkok')
        now_th = datetime.now(thailand)

        need_to_download = False

        if os.path.exists(CSV_PATH) and False:
            log("🗑️ Dataset exists. Deleting and re-downloading.")
            os.remove(CSV_PATH)
            need_to_download = True
        else:
            log("📂 Dataset not found. Will download it now.")
            need_to_download = True

        if need_to_download and False:
            main_records = fetch_all_records(TABLE_IDS["main_content"], "Main Content")
            append_to_csv(main_records)

    except Exception as e:
        log(f"❌ Exception during sync: {e}")
        exit(1)
