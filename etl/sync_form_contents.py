"""
etl/sync_form_contents.py
Fetches raw form content from /forms/content/{id} and caches it in
form_contents. Only fetches forms that are new or modified since last fetch.
"""

import logging
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import psycopg2.extras
from .utils import api_get, paginate, set_last_sync, now_iso

log = logging.getLogger(__name__)

MAX_WORKERS = 10
FLUSH_EVERY = 50


def sync_form_contents(conn):
    log.info("Syncing form_contents...")

    # Pull every form across all form types — form_contents caches the raw
    # JSON for any downstream parser, not just time tickets.
    api_forms = paginate("/forms")
    api_form_map = {f["Id"]: f for f in api_forms}
    log.info(f"  API returned {len(api_form_map)} forms")

    if not api_form_map:
        log.warning("  No forms returned from API — skipping")
        return

    # Check which ones we already have cached
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT form_id::text, fetched_on
            FROM form_contents
            WHERE form_id = ANY(%s::uuid[])
        """, (list(api_form_map.keys()),))
        cached = {str(r["form_id"]): r["fetched_on"] for r in cur.fetchall()}

    # Need fetch if: not cached, OR form modified after last fetch
    pending = []
    for fid, form in api_form_map.items():
        last_modified = form.get("LastModifiedOn")
        if fid not in cached:
            pending.append(fid)
        elif last_modified and cached[fid]:
            try:
                mod = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
                fetched = cached[fid]
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                if mod > fetched:
                    pending.append(fid)
            except Exception:
                pending.append(fid)

    log.info(f"  form_contents: {len(pending)} forms need content fetch")

    def _fetch(form_id):
        try:
            content = api_get(f"/forms/content/{form_id}")
            if content is None:
                return form_id, "skip", None
            if isinstance(content, str):
                content = json.loads(content)
            return form_id, "ok", {
                "form_id":     form_id,
                "raw_content": json.dumps(content),
                "fetched_on":  now_iso(),
            }
        except Exception as e:
            log.warning(f"  form_contents: failed {form_id}: {e}")
            return form_id, "error", None

    ok = skipped = errors = 0
    batch = []
    total = len(pending)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_fetch, fid) for fid in pending]
        for fut in as_completed(futures):
            _, status, row = fut.result()
            if status == "ok":
                batch.append(row)
                if len(batch) >= FLUSH_EVERY:
                    _flush(conn, batch)
                    ok += len(batch)
                    log.info(f"  form_contents: fetched {ok}/{total}")
                    batch = []
            elif status == "skip":
                skipped += 1
            else:
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
