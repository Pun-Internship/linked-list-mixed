import os
import requests
import pandas as pd
import logging
import json
from urllib.parse import quote
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Airtable config
API_TOKEN = os.getenv("AIRTABLE_API_TOKEN_PBN")
BASE_ID = os.getenv("AIRTABLE_BASE_ID_PBN")
MAIN_TABLE = os.getenv("AIRTABLE_TABLE_NAME_PBN")
WEBSITE_TABLE = "Website"
CATEGORY_TABLE = "Categories"
LAST_SYNC_FILE = "last_updated_time_pbn.json"
CSV_PATH = "dataset_pbn.csv"

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("fetch_airtable_pbn.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
    force=True
)
log = logging.info

# 🔒 Helper to mask sensitive IDs
def mask_id(value, visible=6):
    if not value or len(value) <= visible:
        return value
    return value[:visible] + "***"

def load_last_sync_time():
    if os.path.exists(LAST_SYNC_FILE):
        with open(LAST_SYNC_FILE, "r") as f:
            return json.load(f).get("last_sync")
    return "1970-01-01T00:00:00.000Z"

def save_last_sync_time(timestamp):
    with open(LAST_SYNC_FILE, "w") as f:
        json.dump({"last_sync": timestamp}, f)
    log(f"📌 Sync time updated to: {timestamp}")

def fetch_all_records(table_name, modified_after=None):
    log(f"📥 Fetching records from table: {mask_id(table_name)}...")
    url = f"https://api.airtable.com/v0/{BASE_ID}/{quote(table_name)}"
    records = []
    offset = None

    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        if modified_after and table_name == MAIN_TABLE:
            params["filterByFormula"] = f"IS_AFTER({{Last Modified}}, '{modified_after}')"

        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code != 200:
            log(f"❌ Error fetching from {table_name}: {response.text}")
            return []

        data = response.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")

        if not offset:
            break

    log(f"✅ Fetched {len(records)} records from {mask_id(table_name)}")
    return records

def detect_field_name(records, table_name):
    if not records:
        raise ValueError(f"❌ No records found in {table_name} table.")
    first_fields = list(records[0]["fields"].keys())
    if not first_fields:
        raise ValueError(f"❌ No fields found in the first record of {table_name} table.")
    return first_fields[0]  # Use the only or first field

def build_id_to_name_map(table_name):
    records = fetch_all_records(table_name)
    field_name = detect_field_name(records, table_name)
    log(f"✅ Detected field for {table_name}: {field_name}")
    return {
        rec["id"]: rec["fields"].get(field_name, f"Unknown ({rec['id']})")
        for rec in records
    }

def map_main_records(records, website_map, category_map):
    mapped = []

    for r in records:
        fields = r.get("fields", {})

        # Map linked Website records
        raw_websites = fields.get("Website", [])
        website_names = [website_map.get(wid, f"Unknown ({wid})") for wid in raw_websites] if isinstance(raw_websites, list) else []

        # Map linked Category records
        raw_categories = fields.get("Categories", [])
        category_names = [category_map.get(cid, f"Unknown ({cid})") for cid in raw_categories] if isinstance(raw_categories, list) else []

        mapped.append({
            "record_id": r["id"],
            "Main Keyword": fields.get("Main Keyword", ""),
            "🔗 Keyword Link": fields.get("🔗 Keyword Link", ""),
            "Categories": ", ".join(category_names),
            "Website": ", ".join(website_names),
            "Last Modified": fields.get("Last Modified", "")
        })

    return mapped

def append_to_csv(updated_records):
    if not updated_records:
        log("ℹ️ No records to update.")
        if not os.path.exists(CSV_PATH):
            empty_df = pd.DataFrame(columns=["record_id", "Main Keyword", "🔗 Keyword Link", "Categories", "Website", "Last Modified"])
            empty_df.to_csv(CSV_PATH, index=False)
            log("📄 Created empty dataset.csv with headers.")
        return

    new_df = pd.DataFrame(updated_records)
    record_id_col = "record_id"

    # Load existing dataset
    if os.path.exists(CSV_PATH):
        existing_df = pd.read_csv(CSV_PATH, dtype=str)
        before_len = len(existing_df)

        if record_id_col not in existing_df.columns:
            raise ValueError(f"❌ '{record_id_col}' column missing in existing CSV.")

        existing_df = existing_df[~existing_df[record_id_col].isin(new_df[record_id_col])]
        dropped = before_len - len(existing_df)
        log(f"🗑️ Removed {dropped} duplicate records based on '{record_id_col}'")
    else:
        existing_df = pd.DataFrame()

    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    combined_df.to_csv(CSV_PATH, index=False)
    log(f"✅ Updated dataset.csv with {len(new_df)} new/updated records (total rows now: {len(combined_df)})")

if __name__ == "__main__":
    try:
        log("📦 Loading environment variables from .env")
        if not API_TOKEN or not BASE_ID or not MAIN_TABLE:
            log("❌ Missing one or more environment variables:")
            log(f"  - AIRTABLE_API_TOKEN: {mask_id(API_TOKEN)}")
            log(f"  - AIRTABLE_BASE_ID: {mask_id(BASE_ID)}")
            log(f"  - AIRTABLE_TABLE_NAME: {mask_id(MAIN_TABLE)}")
            exit(1)

        log("🚀 Starting Airtable sync process")
        full_fetch = not os.path.exists(CSV_PATH)

        website_map = build_id_to_name_map(WEBSITE_TABLE)
        category_map = build_id_to_name_map(CATEGORY_TABLE)

        if full_fetch:
            log("🆕 No dataset.csv found. Doing full sync...")
            all_records = fetch_all_records(MAIN_TABLE)
        else:
            last_sync = load_last_sync_time()
            log(f"🕒 Last sync time: {last_sync}")
            all_records = fetch_all_records(MAIN_TABLE, modified_after=last_sync)

        mapped_records = map_main_records(all_records, website_map, category_map)
        append_to_csv(mapped_records)

        if mapped_records:
            latest_modified = max(r.get("Last Modified", load_last_sync_time()) for r in mapped_records)
            save_last_sync_time(latest_modified)
        else:
            log("📭 No updates to sync time.")

    except Exception as e:
        log(f"❌ Exception during sync: {e}")
        exit(1)