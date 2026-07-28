"""
fix_form_types.py
Two-step fix:
  1. Migrate form_types table to use DocumentTemplateId as PK
  2. Re-sync all form types from SiteDocs with correct keying

Run once from C:\deploy\VG-SiteDocs-ETL:
    python fix_form_types.py
"""

import os, json, time, logging
import requests, psycopg2, psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

SITEDOCS_TOKEN = os.environ["SITEDOCS_API_TOKEN"]
API_BASE       = "https://api-1.sitedocs.com/api/v1"
HEADERS        = {"Authorization": SITEDOCS_TOKEN, "Accept": "application/json"}

DB_CONN = dict(
    host     = os.environ["POSTGRES_HOST"],
    dbname   = os.environ["POSTGRES_DB"],
    user     = os.environ["POSTGRES_USER"],
    password = os.environ["POSTGRES_PASSWORD"],
    port     = int(os.environ.get("POSTGRES_PORT", 5432)),
    sslmode  = "require",
)

# ---------------------------------------------------------------------------
# Step 1 — migrate schema
# ---------------------------------------------------------------------------
MIGRATION_SQL = """
-- Add document_template_id column if it doesn't exist
ALTER TABLE form_types
    ADD COLUMN IF NOT EXISTS document_template_id UUID;

-- Populate it from the existing id for any rows already present
-- (we'll overwrite everything in step 2 anyway)
UPDATE form_types SET document_template_id = id WHERE document_template_id IS NULL;

-- Drop old FK constraint from time_tickets if it exists
ALTER TABLE time_tickets
    DROP CONSTRAINT IF EXISTS time_tickets_location_id_fkey;

-- We'll rely on the ETL upsert to fix the join — no hard FK for now
-- since SiteDocs data can have orphaned references

-- Add index on document_template_id for join performance
CREATE INDEX IF NOT EXISTS idx_form_types_doc_template_id
    ON form_types(document_template_id);
"""

# ---------------------------------------------------------------------------
# Step 2 — full re-sync using DocumentTemplateId as the canonical key
# ---------------------------------------------------------------------------
UPSERT_SQL = """
INSERT INTO form_types (
    id, document_template_id, name, type, hidden, can_duplicate,
    is_private, is_resource, is_followup, is_deleted,
    created_on, last_modified_on
) VALUES (
    %(id)s, %(document_template_id)s, %(name)s, %(type)s, %(hidden)s, %(can_duplicate)s,
    %(is_private)s, %(is_resource)s, %(is_followup)s, %(is_deleted)s,
    %(created_on)s, %(last_modified_on)s
)
ON CONFLICT (id) DO UPDATE SET
    document_template_id = EXCLUDED.document_template_id,
    name                 = EXCLUDED.name,
    type                 = EXCLUDED.type,
    hidden               = EXCLUDED.hidden,
    can_duplicate        = EXCLUDED.can_duplicate,
    is_private           = EXCLUDED.is_private,
    is_resource          = EXCLUDED.is_resource,
    is_followup          = EXCLUDED.is_followup,
    is_deleted           = EXCLUDED.is_deleted,
    last_modified_on     = EXCLUDED.last_modified_on;
"""

def fetch_form_types():
    r = requests.get(f"{API_BASE}/formtypes?includeDeleted=true",
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(0.3)
    return r.json()

def parse_row(ft):
    return {
        "id":                   ft["Id"],
        "document_template_id": ft.get("DocumentTemplateId") or ft["Id"],
        "name":                 ft.get("Name", ""),
        "type":                 ft.get("Type", ""),
        "hidden":               ft.get("Hidden", False),
        "can_duplicate":        ft.get("CanDuplicate", False),
        "is_private":           ft.get("IsPrivate", False),
        "is_resource":          ft.get("IsResource", False),
        "is_followup":          ft.get("IsFollowup", False),
        "is_deleted":           ft.get("IsDeleted", False),
        "created_on":           ft.get("CreatedOn"),
        "last_modified_on":     ft.get("LastModifiedOn"),
    }

def run():
    conn = psycopg2.connect(**DB_CONN)

    # Step 1 — migrate
    log.info("Step 1: Running schema migration...")
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    conn.commit()
    log.info("Migration done.")

    # Step 2 — fetch and upsert
    log.info("Step 2: Fetching form types from SiteDocs...")
    form_types = fetch_form_types()
    log.info(f"  Got {len(form_types)} form types")

    rows = [parse_row(ft) for ft in form_types]

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=50)
    conn.commit()
    log.info(f"  Upserted {len(rows)} rows.")

    # Step 3 — verify Time Ticket template is now findable
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, document_template_id, name
            FROM form_types
            WHERE document_template_id = '6c3f93b6-1326-478b-a6d6-59aba925a1c1'
        """)
        row = cur.fetchone()
        if row:
            log.info(f"  ✓ Time Ticket found: id={row[0]}, doc_template_id={row[1]}, name={row[2]}")
        else:
            log.warning("  ✗ Time Ticket NOT found — check sync")

    # Step 4 — how many time tickets can now resolve their form type?
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total_tickets,
                COUNT(ft.id) AS resolved,
                COUNT(*) - COUNT(ft.id) AS unresolved
            FROM time_tickets tt
            LEFT JOIN form_types ft
                ON ft.document_template_id = tt.document_template_id
        """)
        total, resolved, unresolved = cur.fetchone()
        log.info(f"  Join check: {resolved}/{total} tickets resolve ({unresolved} unresolved)")

    conn.close()
    log.info("Done.")

if __name__ == "__main__":
    run()
