"""
sync_forms_local.py
Syncs all forms + metadata from SiteDocs into the forms table in Postgres.

Usage:
    python sync_forms_local.py
"""

import os, json, logging, time
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

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def api_get(path: str, params: dict = None) -> dict:
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


def fetch_all_forms() -> list:
    forms, page = [], 0
    while True:
        batch = api_get("/forms", {"count": PAGE_SIZE, "page": page})
        if not batch:
            break
        forms.extend(batch)
        log.info(f"  Page {page}: {len(batch)} forms ({len(forms)} total)")
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return forms


# ---------------------------------------------------------------------------
# Upsert forms
# ---------------------------------------------------------------------------
UPSERT_FORM = """
INSERT INTO forms (
    id, label, is_deleted, is_private,
    document_template_id, location_id,
    creating_company_id, due, has_good_data,
    created_by, created_on, last_modified_by, last_modified_on
)
VALUES (
    %(id)s, %(label)s, %(is_deleted)s, %(is_private)s,
    %(document_template_id)s, %(location_id)s,
    %(creating_company_id)s, %(due)s, %(has_good_data)s,
    %(created_by)s, %(created_on)s, %(last_modified_by)s, %(last_modified_on)s
)
ON CONFLICT (id) DO UPDATE SET
    label               = EXCLUDED.label,
    is_deleted          = EXCLUDED.is_deleted,
    document_template_id = EXCLUDED.document_template_id,
    location_id         = EXCLUDED.location_id,
    has_good_data       = EXCLUDED.has_good_data,
    last_modified_by    = EXCLUDED.last_modified_by,
    last_modified_on    = EXCLUDED.last_modified_on;
"""


def map_form(f: dict) -> dict:
    return {
        "id":                    f.get("Id"),
        "label":                 f.get("Label"),
        "is_deleted":            f.get("IsDeleted", False),
        "is_private":            f.get("IsPrivate", False),
        "document_template_id":  f.get("DocumentTemplateId"),
        "location_id":           f.get("LocationId"),
        "creating_company_id":   f.get("CreatingCompanyId"),
        "due":                   f.get("Due"),
        "has_good_data":         f.get("HasGoodData", False),
        "created_by":            f.get("CreatedBy"),
        "created_on":            f.get("CreatedOn"),
        "last_modified_by":      f.get("LastModifiedBy"),
        "last_modified_on":      f.get("LastModifiedOn"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("Fetching all forms from SiteDocs...")
    forms = fetch_all_forms()
    log.info(f"Found {len(forms)} forms. Upserting into DB...")

    conn = psycopg2.connect(**DB_CONN)
    batch, ok_count, err_count = [], 0, 0

    for i, form in enumerate(forms):
        try:
            batch.append(map_form(form))
            if len(batch) >= 100:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, UPSERT_FORM, batch, page_size=100)
                conn.commit()
                ok_count += len(batch)
                log.info(f"  Upserted {ok_count}/{len(forms)}")
                batch = []
        except Exception as e:
            log.error(f"  Failed {form.get('Id')}: {e}")
            err_count += 1
            try:
                conn.rollback()
            except Exception:
                pass
            batch = []

    if batch:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT_FORM, batch, page_size=100)
        conn.commit()
        ok_count += len(batch)

    conn.close()
    log.info(f"Done. {ok_count} upserted, {err_count} errors.")

if __name__ == "__main__":
    main()
