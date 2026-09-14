"""
etl/sync_forms.py
Syncs:
  - form_types
  - forms  (metadata only — content parsing is handled by etl_time_tickets.py
            and future per-form-type parsers)
"""

import logging
from .utils import paginate, upsert, get_last_sync, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_form_types(conn):
    log.info("Syncing form_types...")
    rows = paginate("/formtypes")

    mapped = [{
        "id":                    r["Id"],
        "name":                  r.get("Name"),
        "type":                  r.get("Type"),
        "document_template_id":  r.get("DocumentTemplateId"),
        "hidden":                r.get("Hidden", False),
        "can_duplicate":         r.get("CanDuplicate", False),
        "is_private":            r.get("IsPrivate", False),
        "is_resource":           r.get("IsResource", False),
        "is_followup":           r.get("IsFollowup", False),
        "is_deleted":            r.get("IsDeleted", False),
        "created_by":            r.get("CreatedBy"),
        "last_modified_by":      r.get("LastModifiedBy"),
        "created_on":            r.get("CreatedOn"),
        "last_modified_on":      r.get("LastModifiedOn"),
        "etl_synced_on":         now_iso(),
    } for r in rows]

    upsert(conn, "form_types", mapped, conflict_col="id")
    set_last_sync(conn, "form_types", len(mapped))
    log.info(f"  form_types: {len(mapped)} rows")


def sync_forms(conn, incremental: bool = True):
    log.info("Syncing forms...")

    params = {}
    if incremental:
        since = get_last_sync(conn, "forms")
        if since:
            params["submittedSince"] = since
            log.info(f"  incremental: submittedSince={since}")

    rows = paginate("/forms", extra_params=params)

    # Get valid IDs for all FK targets to avoid constraint violations
    with conn.cursor() as cur:
        # forms.document_template_id FKs to form_types.id, but the SiteDocs API
        # returns the template GUID (= form_types.document_template_id). Build a
        # GUID -> form_types.id map so we can translate before storing.
        cur.execute("SELECT id::text, document_template_id::text FROM form_types")
        ft_rows = cur.fetchall()
        valid_template_ids = {r[0] for r in ft_rows}
        template_guid_to_id = {r[1]: r[0] for r in ft_rows if r[1]}
        cur.execute("SELECT id::text FROM locations")
        valid_location_ids = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT company_id::text FROM companies")
        valid_company_ids  = {r[0] for r in cur.fetchall()}

    nulled = 0
    mapped = []
    for r in rows:
        tmpl_id    = r.get("DocumentTemplateId")
        loc_id     = r.get("LocationId")
        company_id = r.get("CreatingCompanyId")

        # API gives the template GUID; translate to the form_types.id the FK needs.
        if tmpl_id and tmpl_id not in valid_template_ids:
            tmpl_id = template_guid_to_id.get(tmpl_id)
            if tmpl_id is None: nulled += 1
        if loc_id and loc_id not in valid_location_ids:
            loc_id = None
            nulled += 1
        if company_id and company_id not in valid_company_ids:
            company_id = None
            nulled += 1

        mapped.append({
            "id":                           r["Id"],
            "label":                        r.get("Label"),
            "document_template_id":         tmpl_id,
            "document_template_version_id": r.get("DocumentTemplateVersionId"),
            "document_id":                  r.get("DocumentId"),
            "location_id":                  loc_id,
            "creating_company_id":          company_id,
            "preceding_version_id":         r.get("PrecedingVersionId"),
            "has_good_data":                r.get("HasGoodData", False),
            "is_deleted":                   r.get("IsDeleted", False),
            "is_private":                   r.get("IsPrivate", False),
            "due":                          r.get("Due"),
            "created_by":                   r.get("CreatedBy"),
            "last_modified_by":             r.get("LastModifiedBy"),
            "created_on":                   r.get("CreatedOn"),
            "last_modified_on":             r.get("LastModifiedOn"),
            "etl_synced_on":                now_iso(),
        })

    if nulled:
        log.warning(f"  {nulled} FK references set to NULL (unknown template/location/company IDs)")

    upsert(conn, "forms", mapped, conflict_col="id")
    set_last_sync(conn, "forms", len(mapped))
    log.info(f"  forms: {len(mapped)} rows")


def run(conn):
    # Form types are a small, near-static lookup (99 rows, unchanged since June
    # 2026); forms arrive hourly. Letting a form_types failure abort the stage
    # meant a broken /formtypes endpoint silently stopped all form ingestion --
    # and with it time_tickets, which is derived from forms -- for three months.
    # The GUID -> id map below falls back to whatever is already in the table, so
    # forms still load correctly against the last known set of types.
    try:
        sync_form_types(conn)
    except Exception as e:
        conn.rollback()
        log.error(f"  form_types sync failed ({e}); continuing with forms using "
                  f"the form types already on file")
    sync_forms(conn)
