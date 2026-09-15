"""
etl/sync_workers.py
Syncs:
  - workers
  - worker_locations (junction)
"""

import logging
import psycopg2.extras
from .utils import as_utc, paginate, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_workers(conn):
    log.info("Syncing workers...")
    rows = paginate("/workers")

    mapped = [{
        "id":                r["Id"],
        "active":            r.get("Active", True),
        "first_name":        r.get("FirstName"),
        "last_name":         r.get("LastName"),
        "job_title":         r.get("JobTitle"),
        "employer_id":       r.get("EmployerId"),
        "contractor_id":     r.get("ContractorId"),
        "contractor_name":   r.get("ContractorName"),
        "is_external":       r.get("IsExternal", False),
        "street_address":    r.get("StreetAddress"),
        "city":              r.get("City"),
        "postal_code":       r.get("PostalCode"),
        "mobile_number":     r.get("MobileNumber"),
        "phone_number":      r.get("PhoneNumber"),
        "email":             r.get("Email"),
        "date_hired":        r.get("DateHired"),
        "employee_number":   r.get("EmployeeNumber"),
        "emergency_contact1": r.get("EmergencyContact1"),
        "emergency_contact2": r.get("EmergencyContact2"),
        "emergency_notes":   r.get("EmergencyNotes"),
        "created_on":        as_utc(r.get("CreatedOn")),
        "last_modified_on":  as_utc(r.get("LastModifiedOn")),
        "etl_synced_on":     now_iso(),
    } for r in rows]

    upsert(conn, "workers", mapped, conflict_col="id")
    set_last_sync(conn, "workers", len(mapped))
    log.info(f"  workers: {len(mapped)} rows")
    return rows


def sync_worker_locations(conn):
    """Fetch locations assigned to each worker via /workers/{id}/locations, in parallel."""
    log.info("Syncing worker_locations...")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .utils import api_get

    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM locations")
        valid_location_ids = {row[0] for row in cur.fetchall()}

        cur.execute("SELECT id::text FROM workers")
        worker_ids = [row[0] for row in cur.fetchall()]

    def _fetch(wid):
        try:
            result = api_get(f"/workers/{wid}/locations")
            return wid, result or []
        except Exception as e:
            log.debug(f"  worker_locations skip for worker {wid}: {e}")
            return wid, []

    mapped, seen, skipped = [], set(), 0
    futures = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(_fetch, wid) for wid in worker_ids]
        for fut in as_completed(futures):
            wid, locs = fut.result()
            for loc in locs:
                lid = str(loc.get("Id") or loc.get("LocationId") or "")
                if not lid or lid not in valid_location_ids:
                    skipped += 1
                    continue
                key = (wid, lid)
                if key not in seen:
                    mapped.append({"worker_id": wid, "location_id": lid})
                    seen.add(key)

    if mapped:
        sql = """
            INSERT INTO worker_locations (worker_id, location_id)
            VALUES (%(worker_id)s, %(location_id)s)
            ON CONFLICT (worker_id, location_id) DO NOTHING
        """
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, mapped, page_size=500)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"worker_locations batch insert failed: {e}")
            from .utils import log_etl_error
            log_etl_error(conn, "worker_locations", None, e)

    set_last_sync(conn, "worker_locations", len(mapped))
    log.info(f"  worker_locations: {len(mapped)} rows ({skipped} location refs skipped)")


def run(conn):
    sync_workers(conn)
    sync_worker_locations(conn)
