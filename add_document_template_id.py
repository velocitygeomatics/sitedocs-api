"""
add_document_template_id.py
Adds document_template_id to time_tickets and populates it from the forms table.
"""
import os, logging
import psycopg2
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

DB_CONN = dict(
    host     = os.environ["POSTGRES_HOST"],
    dbname   = os.environ["POSTGRES_DB"],
    user     = os.environ["POSTGRES_USER"],
    password = os.environ["POSTGRES_PASSWORD"],
    port     = int(os.environ.get("POSTGRES_PORT", 5432)),
    sslmode  = "require",
)

SQL = """
-- 1. Add column
ALTER TABLE time_tickets
    ADD COLUMN IF NOT EXISTS document_template_id UUID;

-- 2. Populate from forms table (forms.document_template_id -> time_tickets)
UPDATE time_tickets tt
SET document_template_id = f.document_template_id
FROM forms f
WHERE f.id = tt.form_id
  AND tt.document_template_id IS NULL;

-- 3. Index for join performance
CREATE INDEX IF NOT EXISTS idx_tt_document_template_id
    ON time_tickets(document_template_id);
"""

VERIFY_SQL = """
SELECT
    COUNT(*)                                        AS total,
    COUNT(document_template_id)                     AS populated,
    COUNT(*) - COUNT(document_template_id)          AS still_null,
    COUNT(ft.id)                                    AS resolves_to_form_type
FROM time_tickets tt
LEFT JOIN form_types ft
    ON ft.document_template_id = tt.document_template_id;
"""

def run():
    conn = psycopg2.connect(**DB_CONN)

    log.info("Adding document_template_id to time_tickets...")
    with conn.cursor() as cur:
        cur.execute(SQL)
    conn.commit()
    log.info("Done.")

    log.info("Verifying...")
    with conn.cursor() as cur:
        cur.execute(VERIFY_SQL)
        total, populated, still_null, resolves = cur.fetchone()
    log.info(f"  Total tickets:          {total}")
    log.info(f"  Populated:              {populated}")
    log.info(f"  Still null:             {still_null}")
    log.info(f"  Resolves to form_type:  {resolves}")

    conn.close()

if __name__ == "__main__":
    run()
