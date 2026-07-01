"""
etl/sync_lookups.py
Syncs all lookup/type tables:
  - company_types
  - equipment_types
  - attachment_types
  - certification_types
"""

import logging
from .utils import paginate, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)


def _safe_paginate(path: str) -> list:
    """Paginate with graceful handling of 500 errors from SiteDocs."""
    try:
        return paginate(path)
    except Exception as e:
        log.warning(f"  Skipping {path} — API error: {e}")
        return []


def sync_company_types(conn):
    log.info("Syncing company_types...")
    rows = _safe_paginate("/companytypes")
    if not rows:
        log.warning("  company_types: 0 rows (API unavailable — skipped)")
        return

    mapped = [{
        "company_type_id": r["CompanyTypeId"],
        "company_id":      r.get("CompanyId"),
        "name":            r.get("Name"),
        "is_deleted":      r.get("IsDeleted", False),
        "created_by":      r.get("CreatedBy"),
        "created_on":      r.get("CreatedOn"),
        "modified_on":     r.get("ModifiedOn"),
        "etl_synced_on":   now_iso(),
    } for r in rows]

    upsert(conn, "company_types", mapped, conflict_col="company_type_id")
    set_last_sync(conn, "company_types", len(mapped))
    log.info(f"  company_types: {len(mapped)} rows")


def sync_equipment_types(conn):
    log.info("Syncing equipment_types...")
    rows = _safe_paginate("/equipmentTypes")
    if not rows:
        log.warning("  equipment_types: 0 rows (API unavailable — skipped)")
        return

    mapped = [{
        "equipment_type_id": r["EquipmentTypeId"],
        "name":              r.get("Name"),
        "account_id":        r.get("AccountId"),
        "is_deleted":        r.get("IsDeleted", False),
        "created_by":        r.get("CreatedBy"),
        "created_on":        r.get("CreatedOn"),
        "modified_on":       r.get("ModifiedOn"),
        "etl_synced_on":     now_iso(),
    } for r in rows]

    upsert(conn, "equipment_types", mapped, conflict_col="equipment_type_id")
    set_last_sync(conn, "equipment_types", len(mapped))
    log.info(f"  equipment_types: {len(mapped)} rows")


def sync_attachment_types(conn):
    log.info("Syncing attachment_types...")
    rows = _safe_paginate("/attachmentTypes")
    if not rows:
        log.warning("  attachment_types: 0 rows (API unavailable — skipped)")
        return

    mapped = [{
        "attachment_type_id": r["AttachmentTypeId"],
        "name":               r.get("Name"),
        "account_id":         r.get("AccountId"),
        "is_deleted":         r.get("IsDeleted", False),
        "created_by":         r.get("CreatedBy"),
        "created_on":         r.get("CreatedOn"),
        "modified_on":        r.get("ModifiedOn"),
        "etl_synced_on":      now_iso(),
    } for r in rows]

    upsert(conn, "attachment_types", mapped, conflict_col="attachment_type_id")
    set_last_sync(conn, "attachment_types", len(mapped))
    log.info(f"  attachment_types: {len(mapped)} rows")


def sync_certification_types(conn):
    log.info("Syncing certification_types...")
    rows = _safe_paginate("/certificationtypes")
    if not rows:
        log.warning("  certification_types: 0 rows (API unavailable — skipped)")
        return

    mapped = [{
        "id":               r["Id"],
        "name":             r.get("Name"),
        "company_id":       r.get("CompanyId"),
        "is_deleted":       r.get("IsDeleted", False),
        "created_on":       r.get("CreatedOn"),
        "last_modified_on": r.get("LastModifiedOn"),
        "etl_synced_on":    now_iso(),
    } for r in rows]

    upsert(conn, "certification_types", mapped, conflict_col="id")
    set_last_sync(conn, "certification_types", len(mapped))
    log.info(f"  certification_types: {len(mapped)} rows")


def run(conn):
    sync_company_types(conn)
    sync_equipment_types(conn)
    sync_attachment_types(conn)
    sync_certification_types(conn)
