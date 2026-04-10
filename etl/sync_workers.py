"""
etl/sync_workers.py
Syncs:
  - workers
  - worker_locations (junction)
"""

import logging
import psycopg2.extras
from .utils import paginate, upsert, set_last_sync, now_iso

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
        "created_on":        r.get("CreatedOn"),
        "last_modified_on":  r.get("LastModifiedOn"),
        "etl_synced_on":     now_iso(),
    } for r in rows]

    upsert(conn, "workers", mapped, conflict_col="id")
    set_last_sync(conn, "workers", len(mapped))
    log.info(f"  workers: {len(mapped)} rows")
    return rows


def sync_worker_locations(conn):
    """
    Fetch workers assigned to each location.
    Skips any location_id not present in the locations table.
    """
    log.info("Syncing worker_locations...")

    from .utils import api_get, paginate

    # Get valid location IDs already in DB
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM locations")
        valid_location_ids = {row[0] for row in cur.fetchall()}

    locations = paginate("/locations")
    mapped = []
    seen = set()
    skipped = 0

    for loc in locations:
        lid = loc["Id"]
        if lid not in valid_location_ids:
            skipped += 1
            continue
        try:
            workers = paginate("/workers", extra_params={"locationId": lid})
            for w in workers:
                key = (w["Id"], lid)
                if key not in seen:
                    mapped.append({"worker_id": w["Id"], "location_id": lid})
                    seen.add(key)
        except Exception as e:
            log.debug(f"  worker_locations skip for location {lid}: {e}")

    if mapped:
        sql = """
            INSERT INTO worker_locations (worker_id, location_id)
            VALUES (%(worker_id)s, %(location_id)s)
            ON CONFLICT (worker_id, location_id) DO NOTHING
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, mapped, page_size=100)
        conn.commit()

    set_last_sync(conn, "worker_locations", len(mapped))
    log.info(f"  worker_locations: {len(mapped)} rows ({skipped} locations skipped)")


def run(conn):
    sync_workers(conn)
    sync_worker_locations(conn)
