"""
etl/sync_signatures.py
Syncs form_signatures from the /signatures endpoint.
"""

import logging
from .utils import paginate, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_signatures(conn):
    log.info("Syncing form_signatures...")
    rows = paginate("/signatures")

    mapped = [{
        "id":                        r["Id"],
        "form_id":                   r.get("DocumentVersionId"),
        "employee_id":               r.get("EmployeeId"),
        "image_id":                  r.get("ImageId"),
        "created_on":                r.get("CreatedOn"),
        "last_modified_on":          r.get("LastModifiedOn"),
        "is_deleted":                r.get("IsDeleted", False),
        "latitude":                  r.get("Latitude"),
        "longitude":                 r.get("Longitude"),
        "signatory_first_name":      r.get("SignatoryFirstName"),
        "signatory_last_name":       r.get("SignatoryLastName"),
        "signatory_title":           r.get("SignatoryTitle"),
        "signatory_type":            r.get("SignatoryType"),
        "signatory_contractor_id":   r.get("SignatoryContractorId"),
        "signatory_contractor_name": r.get("SignatoryContractorName"),
        "approval_status":           r.get("ApprovalStatus"),
        "etl_synced_on":             now_iso(),
    } for r in rows]

    upsert(conn, "form_signatures", mapped, conflict_col="id")
    set_last_sync(conn, "form_signatures", len(mapped))
    log.info(f"  form_signatures: {len(mapped)} rows")


def run(conn):
    sync_signatures(conn)
