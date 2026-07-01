"""
etl/sync_equipment.py
Syncs:
  - equipments
  - equipment_details
  - equipment_locations (junction)
  - equipment_workers (junction)
"""

import logging
import psycopg2.extras
from .utils import paginate, api_get, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_equipments(conn):
    log.info("Syncing equipments...")
    rows = paginate("/equipments")

    mapped = [{
        "equipment_id":        r["EquipmentId"],
        "name":                r.get("Name"),
        "equipment_type_id":   r.get("EquipmentTypeId"),
        "equipment_type_name": r.get("EquipmentTypeName"),
        "account_id":          r.get("AccountId"),
        "is_deleted":          r.get("IsDeleted", False),
        "created_by":          r.get("CreatedBy"),
        "created_on":          r.get("CreatedOn"),
        "modified_on":         r.get("ModifiedOn"),
        "etl_synced_on":       now_iso(),
    } for r in rows]

    upsert(conn, "equipments", mapped, conflict_col="equipment_id")
    set_last_sync(conn, "equipments", len(mapped))
    log.info(f"  equipments: {len(mapped)} rows")
    return rows


def sync_equipment_details(conn, equipments: list):
    log.info("Syncing equipment_details...")
    mapped = []
    errors = 0

    for eq in equipments:
        eid = eq["EquipmentId"]
        try:
            r = api_get(f"/equipments/details/{eid}")
            if not r:
                continue

            mapped.append({
                "equipment_id":          r.get("EquipmentId") or eid,
                "description":           r.get("Description"),
                "equipment_status":      r.get("EquipmentStatus"),
                "manufacturer":          r.get("Manufacturer"),
                "model_number":          r.get("ModelNumber"),
                "scan_id":               r.get("ScanID"),
                "serial_number":         r.get("SerialNumber"),
                "fuel_type_consumption": r.get("FuelType_Consumtion"),
                "weight_and_dimensions": r.get("WeightAndDimentions"),
                "noise_level":           r.get("NoiseLevel"),
                "power_requirements":    r.get("PowerRequirements"),
                "owner":                 r.get("Owner"),
                "operator":              r.get("Operator"),
                "responsible_person":    r.get("ResponsiblePerson"),
                "odometer":              r.get("Odometer"),
                "hours":                 r.get("Hours"),
                "cost_value":            r.get("Cost_Value"),
                "purchase_date":         r.get("PurchaseDate"),
                "warranty_coverage":     r.get("WarrantyCoverage"),
                "manufacture_date":      r.get("ManufactureDate"),
                "last_calibration_date": r.get("LastCalibrationDate"),
                "decommissioning_date":  r.get("DecommissioningDate"),
                "etl_synced_on":         now_iso(),
            })

        except Exception as e:
            log.warning(f"  equipment_details failed for {eid}: {e}")
            errors += 1

    upsert(conn, "equipment_details", mapped, conflict_col="equipment_id")
    set_last_sync(conn, "equipment_details", len(mapped))
    log.info(f"  equipment_details: {len(mapped)} rows ({errors} errors)")


def _fetch_equipment_views(equipments: list) -> dict:
    """Fetch /equipments/{id} once per equipment, returning {eid: detail}."""
    views = {}
    for eq in equipments:
        eid = eq["EquipmentId"]
        try:
            detail = api_get(f"/equipments/{eid}")
            if detail:
                views[eid] = detail
        except Exception as e:
            log.debug(f"  equipment view fetch skip {eid}: {e}")
    return views


def sync_equipment_locations(conn, views: dict):
    """Extract locations from pre-fetched equipment views."""
    log.info("Syncing equipment_locations...")
    mapped = []
    seen = set()

    for eid, detail in views.items():
        for loc in detail.get("Locations") or []:
            lid = loc.get("Id") or loc.get("LocationId")
            if lid:
                key = (eid, lid)
                if key not in seen:
                    mapped.append({"equipment_id": eid, "location_id": lid})
                    seen.add(key)

    if mapped:
        sql = """
            INSERT INTO equipment_locations (equipment_id, location_id)
            VALUES (%(equipment_id)s, %(location_id)s)
            ON CONFLICT (equipment_id, location_id) DO NOTHING
        """
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, mapped, page_size=100)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"equipment_locations batch insert failed: {e}")
            from .utils import log_etl_error
            log_etl_error(conn, "equipment_locations", None, e)

    set_last_sync(conn, "equipment_locations", len(mapped))
    log.info(f"  equipment_locations: {len(mapped)} rows")


def sync_equipment_workers(conn, views: dict):
    """Extract workers from pre-fetched equipment views."""
    log.info("Syncing equipment_workers...")
    mapped = []
    seen = set()

    for eid, detail in views.items():
        for w in detail.get("Workers") or []:
            wid = w.get("Id") or w.get("WorkerId")
            if wid:
                key = (eid, wid)
                if key not in seen:
                    mapped.append({"equipment_id": eid, "worker_id": wid})
                    seen.add(key)

    if mapped:
        sql = """
            INSERT INTO equipment_workers (equipment_id, worker_id)
            VALUES (%(equipment_id)s, %(worker_id)s)
            ON CONFLICT (equipment_id, worker_id) DO NOTHING
        """
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, mapped, page_size=100)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"equipment_workers batch insert failed: {e}")
            from .utils import log_etl_error
            log_etl_error(conn, "equipment_workers", None, e)

    set_last_sync(conn, "equipment_workers", len(mapped))
    log.info(f"  equipment_workers: {len(mapped)} rows")


def run(conn):
    equipments = sync_equipments(conn)
    sync_equipment_details(conn, equipments)
    views = _fetch_equipment_views(equipments)
    sync_equipment_locations(conn, views)
    sync_equipment_workers(conn, views)
