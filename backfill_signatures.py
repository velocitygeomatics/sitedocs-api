"""
One-time backfill: re-populate signed_by / signed_on / signature_lat /
signature_lng on the time_tickets table.

History behind this script
--------------------------
The original etl_time_tickets._extract_signature() walked
Content.Groups[].Items[] looking for a group titled "Signatures" with
items carrying SignerName / SignedOn fields. SiteDocs' real data model
puts signatures on a separate endpoint (GET /api/v1/signatures) — so
every row the ETL produced had signed_by / signed_on NULL, even when
the ticket clearly showed a signature on the SiteDocs PDF.

After the ETL fix lands (see parse_time_ticket + fetch_signatures_batch
in etl_time_tickets.py), this script re-fetches signatures for every
time_tickets row in Postgres and updates the four columns in place.

Run:
    python -m backfill_signatures              # all rows with NULL signed_by
    python -m backfill_signatures --all        # overwrite all rows, not just NULLs
    python -m backfill_signatures --limit 50   # dry-run the first N
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

import psycopg2
import psycopg2.extras

# Reuse the ETL helpers so the API auth, pagination and signature picking
# logic stays in one place.
from etl_time_tickets import (
    fetch_signatures_batch,
    fetch_signatures_for_form,
    _pick_crew_chief_signature,
    _get_db_conn_kwargs,
)

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger(__name__)


def run(all_rows: bool = False, limit: Optional[int] = None) -> None:
    where = "" if all_rows else "WHERE signed_by IS NULL OR signed_on IS NULL"
    sql = f"""
        SELECT form_id
        FROM time_tickets
        {where}
        ORDER BY submitted_on DESC NULLS LAST
        {'LIMIT %(limit)s' if limit else ''}
    """

    conn = psycopg2.connect(**_get_db_conn_kwargs())
    cur  = conn.cursor()
    cur.execute(sql, {"limit": limit} if limit else None)
    form_ids = [r[0] for r in cur.fetchall()]
    log.info(f"Backfill target: {len(form_ids)} time_tickets rows")

    if not form_ids:
        log.info("Nothing to do.")
        conn.close()
        return

    # One paged batch covers every signature in the company history. For a
    # small target set the per-form fallback is cheaper, so pick the
    # strategy by size.
    use_batch = len(form_ids) > 50
    sigs_by_form: dict[str, list[dict]] = {}
    if use_batch:
        log.info("Fetching signatures batch (no addedSince window)…")
        sigs_by_form = fetch_signatures_batch(added_since=None)
        log.info(f"Batch covers {len(sigs_by_form)} forms")

    updated = 0
    skipped = 0
    for fid in form_ids:
        sigs = sigs_by_form.get(fid) if use_batch else fetch_signatures_for_form(fid)
        if sigs is None:
            sigs = fetch_signatures_for_form(fid)
        name, ts, lat, lng = _pick_crew_chief_signature(sigs or [])
        if not name and not ts:
            skipped += 1
            continue
        cur.execute(
            """
            UPDATE time_tickets
               SET signed_by     = %s,
                   signed_on     = %s,
                   signature_lat = %s,
                   signature_lng = %s
             WHERE form_id = %s
            """,
            (name, ts, lat, lng, fid),
        )
        updated += 1
        if updated % 50 == 0:
            conn.commit()
            log.info(f"Committed {updated} rows so far")

    conn.commit()
    conn.close()
    log.info(f"Backfill done. updated={updated}, skipped_no_signature={skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all",   action="store_true", help="Overwrite every row, not just NULLs")
    ap.add_argument("--limit", type=int, help="Limit to first N rows (for dry-running)")
    args = ap.parse_args()
    run(all_rows=args.all, limit=args.limit)
