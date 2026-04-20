"""
etl/sync_form_contents.py
Fetches raw form content from /forms/content/{id} and caches it in
form_contents. Only fetches forms that are new or modified since last fetch.
"""

import logging
import psycopg2.extras
from .utils import api_get, set_last_sync, now_iso

log = logging.getLogger(__name__)

FORM_TYPE_ID = "6c3f93b6-1326-478b-a6d6-59aba925a1c1"  # Time Ticket


def sync_form_contents(conn):
    log.info("Syncing form_contents...")

    # Find time ticket forms that need content fetched:
    # - not yet in form_contents, OR
    # - form was modified after we last fetched its content
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT f.id, f.last_modified_on
            FROM forms f
            LEFT JOIN form_contents fc ON fc.form_id = f.id
            WHERE f.document_template_id = %s
              AND f.is_deleted = false
              AND (
                fc.form_id IS NULL
                OR f.last_modified_on > fc.fetched_on
              )
            ORDER BY f.created_on
        """, (FORM_TYPE_ID,))
        pending = cur.fetchall()

    log.info(f"  form_contents: {len(pending)} forms need content fetch")

    ok = skipped = errors = 0
    batch = []

    for row in pending:
        form_id = str(row["id"])
        try:
            content = api_get(f"/forms/content/{form_id}")
            if content is None:
                skipped += 1
                continue

            import json
            if isinstance(content, str):
                content = json.loads(content)

            batch.append({
                "form_id":     form_id,
                "raw_content": json.dumps(content),
                "fetched_on":  now_iso(),
            })

            if len(batch) >= 50:
                _flush(conn, batch)
                ok += len(batch)
                log.info(f"  form_contents: fetched {ok}/{len(pending)}")
                batch = []

        except Exception as e:
            log.warning(f"  form_contents: failed {form_id}: {e}")
            errors += 1

    if batch:
        _flush(conn, batch)
        ok += len(batch)

    set_last_sync(conn, "form_contents", ok)
    log.info(f"  form_contents: {ok} fetched, {skipped} skipped, {errors} errors")


def _flush(conn, batch):
    sql = """
        INSERT INTO form_contents (form_id, raw_content, fetched_on)
        VALUES (%(form_id)s, %(raw_content)s::jsonb, %(fetched_on)s)
        ON CONFLICT (form_id) DO UPDATE
            SET raw_content = EXCLUDED.raw_content,
                fetched_on  = EXCLUDED.fetched_on
    """
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, batch, page_size=50)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"  form_contents flush failed: {e}")


def run(conn):
    sync_form_contents(conn)
