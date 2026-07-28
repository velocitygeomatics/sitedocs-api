"""
insert_missing_locations.py
Fetches 2 missing locations from SiteDocs and inserts them into the DB.
Safe — uses ON CONFLICT DO NOTHING, existing data is untouched.
"""

import os, requests, psycopg2
from dotenv import load_dotenv

load_dotenv()

SITEDOCS_TOKEN = os.environ["SITEDOCS_API_TOKEN"]
HEADERS = {"Authorization": SITEDOCS_TOKEN, "Accept": "application/json"}

MISSING_IDS = [
    "78303e0c-1f3a-4894-9920-b56e96e30d70",  # 160183 (Cenovus energy)
    "9ce76f95-6d6d-47ab-b68c-4c582e6bef84",  # 260156 (MVA Group)
]

DB_CONN = dict(
    host     = os.environ["POSTGRES_HOST"],
    dbname   = os.environ["POSTGRES_DB"],
    user     = os.environ["POSTGRES_USER"],
    password = os.environ["POSTGRES_PASSWORD"],
    port     = int(os.environ.get("POSTGRES_PORT", 5432)),
    sslmode  = "require",
)

conn = psycopg2.connect(**DB_CONN)

for loc_id in MISSING_IDS:
    r = requests.get(f"https://api-1.sitedocs.com/api/v1/locations/{loc_id}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    d = r.json()
    name = d.get("Name") or d.get("name") or ""
    print(f"Fetched: {loc_id} | {name}")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO locations (id, name, is_archived, created_on, last_modified_on)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """, (loc_id, name, d.get("IsArchived", False)))
    conn.commit()
    print(f"  Inserted OK")

conn.close()
print("Done — 2 locations inserted.")
