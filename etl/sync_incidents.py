"""
etl/sync_incidents.py
Syncs:
  - incident_folder_types
  - incident_folders
  - incident_folder_statuses
"""

import logging
from .utils import paginate, api_get, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_incident_folder_types(conn):
    log.info("Syncing incident_folder_types...")
    rows = paginate("/incidentfoldertypes")

    mapped = [{
        "folder_type_id":      r["FolderTypeId"],
        "name":                r.get("Name"),
        "summary":             r.get("Summary"),
        "kind":                r.get("Kind"),
        "is_user_generated":   r.get("IsUserGenerated", False),
        "is_hidden":           r.get("IsHidden", False),
        "has_private_reports": r.get("HasPrivateReports", False),
        "created_on":          r.get("CreatedOn"),
        "etl_synced_on":       now_iso(),
    } for r in rows]

    upsert(conn, "incident_folder_types", mapped, conflict_col="folder_type_id")
    set_last_sync(conn, "incident_folder_types", len(mapped))
    log.info(f"  incident_folder_types: {len(mapped)} rows")


def sync_incident_folders(conn):
    log.info("Syncing incident_folders...")
    rows = paginate("/incidentfolders")

    # API returns ProcessFolderViewModelV2
    # Handle both single object and array response
    if isinstance(rows, dict):
        rows = [rows]

    mapped = [{
        "id":           r["Id"],
        "folder_type_id": r.get("TypeId"),
        "name":         r.get("Name"),
        "type_name":    r.get("TypeName"),
        "latest_status": r.get("LatestStatus"),
        "is_active":    r.get("IsActive", True),
        "created_by":   r.get("CreatedBy"),
        "created_on":   r.get("CreatedOn"),
        "modified_on":  r.get("ModifiedOn"),
        "etl_synced_on": now_iso(),
    } for r in rows if isinstance(r, dict) and r.get("Id")]

    upsert(conn, "incident_folders", mapped, conflict_col="id")
    set_last_sync(conn, "incident_folders", len(mapped))
    log.info(f"  incident_folders: {len(mapped)} rows")
    return mapped


def sync_incident_folder_statuses(conn, folders: list):
    log.info("Syncing incident_folder_statuses...")
    mapped = []
    errors = 0

    for folder in folders:
        fid = folder["id"]
        try:
            statuses = api_get(f"/incidentfolders/{fid}/statuses")
            if not statuses:
                continue

            for s in statuses:
                sid = s.get("statusId")
                if not sid:
                    continue
                mapped.append({
                    "status_id":             sid,
                    "folder_id":             fid,
                    "company_id":            s.get("CompanyId"),
                    "status":                s.get("status"),
                    "comment":               s.get("Comment"),
                    "occurred_on_utc":       s.get("OccurredOnUtc"),
                    "is_final_checkpoint":   s.get("IsFinalCheckpoint", False),
                    "triggered_by_user_id":  s.get("TriggeredByUserId"),
                    "requires_intervention": s.get("RequiresIntervention", False),
                    "report_count":          s.get("ReportCount", 0),
                    "etl_synced_on":         now_iso(),
                })

        except Exception as e:
            log.warning(f"  statuses failed for folder {fid}: {e}")
            errors += 1

    if mapped:
        upsert(conn, "incident_folder_statuses", mapped, conflict_col="status_id")

    set_last_sync(conn, "incident_folder_statuses", len(mapped))
    log.info(f"  incident_folder_statuses: {len(mapped)} rows ({errors} errors)")


def run(conn):
    sync_incident_folder_types(conn)
    folders = sync_incident_folders(conn)
    sync_incident_folder_statuses(conn, folders)
