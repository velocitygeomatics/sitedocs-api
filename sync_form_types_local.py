"""
sync_form_types_local.py
Syncs all form types from SiteDocs into the form_types table in Postgres.

Usage:
    python sync_form_types_local.py
"""

import os, logging, time
from dotenv import load_dotenv
import requests
import psycopg2
import psycopg2.extras

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

SITEDOCS_TOKEN = os.environ["SITEDOCS_API_TOKEN"]
API_BASE       = "https://api-1.sitedocs.com/api/v1"
PAGE_SIZE      = 100
RATE_LIMIT     = 0.3

DB_CONN = dict(
    host     = os.environ["POSTGRES_HOST"],
    dbname   = os.environ["POSTGRES_DB"],
    user     = os.environ["POSTGRES_USER"],
    password = os.environ["POSTGRES_PASSWORD"],
    port     = int(os.environ.get("POSTGRES_PORT", 5432)),
    sslmode  = "require",
)

HEADERS = {"Authorization": SITEDOCS_TOKEN, "Accept": "application/json"}

def api_get(path, params=None):
    url = f"{API_BASE}{path}"
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            wait = 2 ** attempt * 5
            log.warning(f"Rate limited — waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(RATE_LIMIT)
        return r.json()
    raise RuntimeError(f"Failed after retries: {path}")

def fetch_all_form_types():
    types, page = [], 0
    while True:
        batch = api_get("/documenttemplates", {"count": PAGE_SIZE, "page": page})
        if not batch:
            break
        types.extend(batch)
        log.info(f"  Page {page}: {len(batch)} types ({len(types)} total)")
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return types

UPSERT = """
INSERT INTO form_types (id, name, type, hidden, can_duplicate, is_private, is_resource, is_followup, is_deleted, created_on, last_modified_on)
VALUES (%(id)s, %(name)s, %(type)s, %(hidden)s, %(can_duplicate)s, %(is_private)s, %(is_resource)s, %(is_followup)s, %(is_deleted)s, %(created_on)s, %(last_modified_on)s)
ON CONFLICT (id) DO UPDATE SET
    name            = EXCLUDED.name,
    type            = EXCLUDED.type,
    hidden          = EXCLUDED.hidden,
    is_deleted      = EXCLUDED.is_deleted,
    last_modified_on = EXCLUDED.last_modified_on;
"""

def map_type(t):
    return {
        "id":               t.get("Id"),
        "name":             t.get("Name") or t.get("DocumentTemplateName") or "",
        "type":             t.get("Type") or "form",
        "hidden":           t.get("Hidden", False),
        "can_duplicate":    t.get("CanDuplicate", False),
        "is_private":       t.get("IsPrivate", False),
        "is_resource":      t.get("IsResource", False),
        "is_followup":      t.get("IsFollowup", False),
        "is_deleted":       t.get("IsDeleted", False),
        "created_on":       t.get("CreatedOn"),
        "last_modified_on": t.get("LastModifiedOn"),
    }

def main():
    log.info("Fetching form types from SiteDocs...")
    try:
        types = fetch_all_form_types()
    except Exception as e:
        log.warning(f"documenttemplates endpoint failed ({e}), trying formtypes...")
        types = []
        page = 0
        while True:
            batch = api_get("/formtypes", {"count": PAGE_SIZE, "page": page})
            if not batch:
                break
            types.extend(batch)
            log.info(f"  Page {page}: {len(batch)} types ({len(types)} total)")
            if len(batch) < PAGE_SIZE:
                break
            page += 1

    log.info(f"Found {len(types)} form types. Upserting...")

    conn = psycopg2.connect(**DB_CONN)
    batch, ok_count, err_count = [], 0, 0

    for t in types:
        try:
            batch.append(map_type(t))
            if len(batch) >= 100:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, UPSERT, batch, page_size=100)
                conn.commit()
                ok_count += len(batch)
                log.info(f"  Upserted {ok_count}/{len(types)}")
                batch = []
        except Exception as e:
            log.error(f"  Failed {t.get('Id')}: {e}")
            err_count += 1
            try:
                conn.rollback()
            except Exception:
                pass
            batch = []

    if batch:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT, batch, page_size=100)
        conn.commit()
        ok_count += len(batch)

    conn.close()
    log.info(f"Done. {ok_count} upserted, {err_count} errors.")

if __name__ == "__main__":
    main()
