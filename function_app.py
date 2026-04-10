"""
function_app.py
SiteDocs REST API — Azure Functions v2

Endpoints:
  Locations
    GET /api/locations
    GET /api/locations/{id}
    GET /api/locations/{id}/forms
    GET /api/locations/{id}/attachments

  Forms
    GET /api/forms
    GET /api/forms/{id}
    GET /api/forms/{id}/attachments

  Attachments
    GET /api/attachments/{domainObjectId}
    GET /api/attachments/{domainObjectId}/{kind}

  Lookups
    GET /api/lookup/form-types
    GET /api/lookup/attachment-types
    GET /api/lookup/company-types
    GET /api/lookup/companies
"""

import os
import logging
import azure.functions as func
from shared.db import (
    query, get_connection, require_api_key, ok, error, not_found,
    parse_pagination, paginated_response
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ===========================================================================
# LOCATIONS
# ===========================================================================

@app.route(route="locations", methods=["GET"])
def get_locations(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/locations
    Query params: isArchived (bool), name (str), page, count
    """
    auth = _check_api_key(req)
    if auth: return auth

    count, offset = parse_pagination(req)
    page = offset // count if count else 0

    conditions = []
    params = []

    is_archived = req.params.get("isArchived")
    if is_archived is not None:
        conditions.append("is_archived = %s")
        params.append(is_archived.lower() == "true")

    name = req.params.get("name")
    if name:
        conditions.append("name ILIKE %s")
        params.append(f"%{name}%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    total_rows = query(f"SELECT COUNT(*) as n FROM locations {where}", tuple(params))
    total = total_rows[0]["n"] if total_rows else 0

    sql = f"""
        SELECT
            l.id, l.name, l.description, l.address,
            l.start_date, l.end_date, l.is_archived,
            l.creating_company_id,
            c.name AS creating_company_name,
            l.created_on, l.last_modified_on
        FROM locations l
        LEFT JOIN companies c ON c.company_id = l.creating_company_id
        {where}
        ORDER BY l.name
        LIMIT %s OFFSET %s
    """
    params.extend([count, offset])
    rows = query(sql, tuple(params))
    return ok(paginated_response(rows, total, page, count))


@app.route(route="locations/{id}", methods=["GET"])
def get_location(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/locations/{id}"""
    auth = _check_api_key(req)
    if auth: return auth

    location_id = req.route_params.get("id")
    rows = query("""
        SELECT
            l.id, l.name, l.description, l.address,
            l.start_date, l.end_date, l.is_archived,
            l.creating_company_id,
            c.name AS creating_company_name,
            l.created_on, l.last_modified_on
        FROM locations l
        LEFT JOIN companies c ON c.company_id = l.creating_company_id
        WHERE l.id = %s
    """, (location_id,))

    if not rows:
        return not_found("Location")
    return ok(rows[0])


@app.route(route="locations/{id}/forms", methods=["GET"])
def get_location_forms(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/locations/{id}/forms
    Query params: formTypeId, submittedSince, isDeleted, page, count
    """
    auth = _check_api_key(req)
    if auth: return auth

    location_id = req.route_params.get("id")
    count, offset = parse_pagination(req)
    page = offset // count if count else 0

    conditions = ["f.location_id = %s"]
    params = [location_id]

    form_type_id = req.params.get("formTypeId")
    if form_type_id:
        conditions.append("f.document_template_id = %s")
        params.append(form_type_id)

    submitted_since = req.params.get("submittedSince")
    if submitted_since:
        conditions.append("f.created_on >= %s")
        params.append(submitted_since)

    is_deleted = req.params.get("isDeleted", "false")
    conditions.append("f.is_deleted = %s")
    params.append(is_deleted.lower() == "true")

    where = "WHERE " + " AND ".join(conditions)

    total_rows = query(f"SELECT COUNT(*) as n FROM forms f {where}", tuple(params))
    total = total_rows[0]["n"] if total_rows else 0

    sql = f"""
        SELECT
            f.id, f.label, f.is_deleted, f.is_private,
            f.document_template_id,
            ft.name AS form_type_name, ft.type AS form_type,
            f.location_id,
            f.creating_company_id,
            c.name AS creating_company_name,
            f.due, f.has_good_data,
            f.created_by, f.created_on,
            f.last_modified_by, f.last_modified_on
        FROM forms f
        LEFT JOIN form_types ft ON ft.id = f.document_template_id
        LEFT JOIN companies c ON c.company_id = f.creating_company_id
        {where}
        ORDER BY f.created_on DESC
        LIMIT %s OFFSET %s
    """
    params.extend([count, offset])
    rows = query(sql, tuple(params))
    return ok(paginated_response(rows, total, page, count))


@app.route(route="locations/{id}/attachments", methods=["GET"])
def get_location_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/locations/{id}/attachments — kind=4 (Project)"""
    auth = _check_api_key(req)
    if auth: return auth

    location_id = req.route_params.get("id")
    rows = query("""
        SELECT
            a.attachment_id, a.domain_object_id, a.kind,
            a.original_file_name, a.file_size_in_kb, a.content_type,
            a.name, a.company_id,
            at.name AS attachment_type_name,
            a.effective_on, a.expires_on,
            a.created_by, a.created_at, a.updated_at
        FROM attachments a
        LEFT JOIN attachment_types at ON at.attachment_type_id = a.general_type_id
        WHERE a.domain_object_id = %s AND a.kind = 4
        ORDER BY a.created_at DESC
    """, (location_id,))
    return ok(rows)


# ===========================================================================
# FORMS
# ===========================================================================

@app.route(route="forms", methods=["GET"])
def get_forms(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms
    Query params: locationId, formTypeId, submittedSince, isDeleted, page, count
    """
    auth = _check_api_key(req)
    if auth: return auth

    count, offset = parse_pagination(req)
    page = offset // count if count else 0

    conditions = []
    params = []

    location_id = req.params.get("locationId")
    if location_id:
        conditions.append("f.location_id = %s")
        params.append(location_id)

    form_type_id = req.params.get("formTypeId")
    if form_type_id:
        conditions.append("f.document_template_id = %s")
        params.append(form_type_id)

    submitted_since = req.params.get("submittedSince")
    if submitted_since:
        conditions.append("f.created_on >= %s")
        params.append(submitted_since)

    is_deleted = req.params.get("isDeleted", "false")
    conditions.append("f.is_deleted = %s")
    params.append(is_deleted.lower() == "true")

    where = "WHERE " + " AND ".join(conditions) if conditions else "WHERE f.is_deleted = false"

    total_rows = query(f"SELECT COUNT(*) as n FROM forms f {where}", tuple(params))
    total = total_rows[0]["n"] if total_rows else 0

    sql = f"""
        SELECT
            f.id, f.label, f.is_deleted, f.is_private,
            f.document_template_id,
            ft.name AS form_type_name, ft.type AS form_type,
            f.location_id,
            l.name AS location_name,
            f.creating_company_id,
            c.name AS creating_company_name,
            f.due, f.has_good_data,
            f.created_by, f.created_on,
            f.last_modified_by, f.last_modified_on
        FROM forms f
        LEFT JOIN form_types ft ON ft.id = f.document_template_id
        LEFT JOIN locations l ON l.id = f.location_id
        LEFT JOIN companies c ON c.company_id = f.creating_company_id
        {where}
        ORDER BY f.created_on DESC
        LIMIT %s OFFSET %s
    """
    params.extend([count, offset])
    rows = query(sql, tuple(params))
    return ok(paginated_response(rows, total, page, count))


@app.route(route="forms/{id}", methods=["GET"])
def get_form(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/forms/{id}"""
    auth = _check_api_key(req)
    if auth: return auth

    form_id = req.route_params.get("id")
    rows = query("""
        SELECT
            f.id, f.label, f.is_deleted, f.is_private,
            f.document_template_id, f.document_template_version_id,
            f.document_id, f.preceding_version_id,
            ft.name AS form_type_name, ft.type AS form_type,
            f.location_id,
            l.name AS location_name,
            f.creating_company_id,
            c.name AS creating_company_name,
            f.due, f.has_good_data,
            f.created_by, f.created_on,
            f.last_modified_by, f.last_modified_on
        FROM forms f
        LEFT JOIN form_types ft ON ft.id = f.document_template_id
        LEFT JOIN locations l ON l.id = f.location_id
        LEFT JOIN companies c ON c.company_id = f.creating_company_id
        WHERE f.id = %s
    """, (form_id,))

    if not rows:
        return not_found("Form")
    return ok(rows[0])


@app.route(route="forms/{id}/attachments", methods=["GET"])
def get_form_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/forms/{id}/attachments — kind=5 (FormType)"""
    auth = _check_api_key(req)
    if auth: return auth

    form_id = req.route_params.get("id")
    rows = query("""
        SELECT
            a.attachment_id, a.domain_object_id, a.kind,
            a.original_file_name, a.file_size_in_kb, a.content_type,
            a.name, a.company_id,
            at.name AS attachment_type_name,
            a.effective_on, a.expires_on,
            a.created_by, a.created_at, a.updated_at
        FROM attachments a
        LEFT JOIN attachment_types at ON at.attachment_type_id = a.general_type_id
        WHERE a.domain_object_id = %s AND a.kind = 5
        ORDER BY a.created_at DESC
    """, (form_id,))
    return ok(rows)


# ===========================================================================
# ATTACHMENTS
# ===========================================================================

@app.route(route="attachments/{domainObjectId}", methods=["GET"])
def get_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/attachments/{domainObjectId} — all kinds"""
    auth = _check_api_key(req)
    if auth: return auth

    domain_object_id = req.route_params.get("domainObjectId")
    rows = query("""
        SELECT
            a.attachment_id, a.domain_object_id, a.kind,
            a.original_file_name, a.file_size_in_kb, a.content_type,
            a.name, a.company_id,
            at.name AS attachment_type_name,
            a.effective_on, a.expires_on,
            a.created_by, a.created_at, a.updated_at
        FROM attachments a
        LEFT JOIN attachment_types at ON at.attachment_type_id = a.general_type_id
        WHERE a.domain_object_id = %s
        ORDER BY a.kind, a.created_at DESC
    """, (domain_object_id,))
    return ok(rows)


@app.route(route="attachments/{domainObjectId}/{kind}", methods=["GET"])
def get_attachments_by_kind(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/attachments/{domainObjectId}/{kind}
    kind: 0=Contractor, 1=Equipment, 2=Incident, 3=Worker,
          4=Project, 5=FormType, 6=Resource, 7=ProcessRun
    """
    auth = _check_api_key(req)
    if auth: return auth

    domain_object_id = req.route_params.get("domainObjectId")
    kind_str = req.route_params.get("kind")

    # Accept both int and string kind values
    kind_map = {
        "contractor": 0, "equipment": 1, "incident": 2,
        "worker": 3, "project": 4, "formtype": 5,
        "resource": 6, "processrun": 7
    }
    try:
        kind = int(kind_str)
    except (ValueError, TypeError):
        kind = kind_map.get(kind_str.lower() if kind_str else "", None)
        if kind is None:
            return error(400, f"Invalid kind '{kind_str}'. Use 0-7 or name (e.g. 'worker', 'project')")

    rows = query("""
        SELECT
            a.attachment_id, a.domain_object_id, a.kind,
            a.original_file_name, a.file_size_in_kb, a.content_type,
            a.name, a.company_id,
            at.name AS attachment_type_name,
            a.effective_on, a.expires_on,
            a.created_by, a.created_at, a.updated_at
        FROM attachments a
        LEFT JOIN attachment_types at ON at.attachment_type_id = a.general_type_id
        WHERE a.domain_object_id = %s AND a.kind = %s
        ORDER BY a.created_at DESC
    """, (domain_object_id, kind))
    return ok(rows)


# ===========================================================================
# LOOKUPS
# ===========================================================================

@app.route(route="lookup/form-types", methods=["GET"])
def get_form_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/form-types"""
    auth = _check_api_key(req)
    if auth: return auth

    name = req.params.get("name")
    type_filter = req.params.get("type")  # form, followup, resource

    conditions = ["is_deleted = false"]
    params = []

    if name:
        conditions.append("name ILIKE %s")
        params.append(f"%{name}%")
    if type_filter:
        conditions.append("type = %s")
        params.append(type_filter)

    where = "WHERE " + " AND ".join(conditions)
    rows = query(f"""
        SELECT id, name, type, hidden, can_duplicate,
               is_private, is_resource, is_followup,
               created_on, last_modified_on
        FROM form_types
        {where}
        ORDER BY name
    """, tuple(params))
    return ok(rows)


@app.route(route="lookup/attachment-types", methods=["GET"])
def get_attachment_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/attachment-types"""
    auth = _check_api_key(req)
    if auth: return auth

    rows = query("""
        SELECT attachment_type_id, name, created_on, modified_on
        FROM attachment_types
        WHERE is_deleted = false
        ORDER BY name
    """)
    return ok(rows)


@app.route(route="lookup/company-types", methods=["GET"])
def get_company_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/company-types"""
    auth = _check_api_key(req)
    if auth: return auth

    rows = query("""
        SELECT company_type_id, name, created_on, modified_on
        FROM company_types
        WHERE is_deleted = false
        ORDER BY name
    """)
    return ok(rows)


@app.route(route="lookup/companies", methods=["GET"])
def get_companies(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/lookup/companies
    Query params: isActive, name, companyTypeId
    """
    auth = _check_api_key(req)
    if auth: return auth

    conditions = []
    params = []

    is_active = req.params.get("isActive")
    if is_active is not None:
        conditions.append("c.is_active = %s")
        params.append(is_active.lower() == "true")

    name = req.params.get("name")
    if name:
        conditions.append("c.name ILIKE %s")
        params.append(f"%{name}%")

    company_type_id = req.params.get("companyTypeId")
    if company_type_id:
        conditions.append("c.company_type_id = %s")
        params.append(company_type_id)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = query(f"""
        SELECT
            c.company_id, c.name, c.is_active,
            c.company_type_id,
            ct.name AS company_type_name,
            c.created_on
        FROM companies c
        LEFT JOIN company_types ct ON ct.company_type_id = c.company_type_id
        {where}
        ORDER BY c.name
    """, tuple(params))
    return ok(rows)


# ===========================================================================
# TIME TICKETS
# ===========================================================================

@app.route(route="time-tickets", methods=["GET"])
def get_time_tickets(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/time-tickets
    Query params:
      jobNo         — filter by job number (exact)
      client        — filter by client name (partial match)
      crewChief     — filter by crew chief name (partial match)
      locationId    — filter by SiteDocs location UUID
      dateFrom      — ticket_date >= (YYYY-MM-DD)
      dateTo        — ticket_date <= (YYYY-MM-DD)
      isDeleted     — default false
      page, count
    """
    auth = _check_api_key(req)
    if auth: return auth

    count, offset = parse_pagination(req)
    page = offset // count if count else 0

    conditions = []
    params = []

    is_deleted = req.params.get("isDeleted", "false")
    conditions.append("tt.is_deleted = %s")
    params.append(is_deleted.lower() == "true")

    job_no = req.params.get("jobNo")
    if job_no:
        conditions.append("tt.job_no = %s")
        params.append(job_no)

    client = req.params.get("client")
    if client:
        conditions.append("tt.client ILIKE %s")
        params.append(f"%{client}%")

    crew_chief = req.params.get("crewChief")
    if crew_chief:
        conditions.append("tt.crew_chief ILIKE %s")
        params.append(f"%{crew_chief}%")

    location_id = req.params.get("locationId")
    if location_id:
        conditions.append("tt.location_id = %s")
        params.append(location_id)

    date_from = req.params.get("dateFrom")
    if date_from:
        conditions.append("tt.ticket_date >= %s")
        params.append(date_from)

    date_to = req.params.get("dateTo")
    if date_to:
        conditions.append("tt.ticket_date <= %s")
        params.append(date_to)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    total_rows = query(f"SELECT COUNT(*) as n FROM time_tickets tt {where}", tuple(params))
    total = total_rows[0]["n"] if total_rows else 0

    sql = f"""
        SELECT
            tt.form_id, tt.form_label, tt.submitted_on, tt.ticket_date,
            tt.location_id,
            l.name AS location_name,
            tt.job_no, tt.client, tt.client_field_rep,
            tt.wellsite_location, tt.project_manager,
            tt.crew_chief, tt.assistant,
            tt.truck_km, tt.truck_hours,
            tt.survey_equipment_day, tt.pipe_locator_hrs,
            tt.chainsaw_hrs, tt.jackhammer_hrs,
            tt.atv_utv_snowmobile, tt.marker_posts, tt.iron_posts,
            tt.cc_travel_hrs, tt.cc_work_hrs, tt.cc_notes_hrs,
            tt.cc_total_hrs, tt.cc_subsistence,
            tt.sa_travel_hrs, tt.sa_work_hrs, tt.sa_total_hrs, tt.sa_subsistence,
            tt.details, tt.approval, tt.approval_date,
            tt.signed_by, tt.signed_on,
            tt.signature_lat, tt.signature_lng,
            tt.etl_synced_on
        FROM time_tickets tt
        LEFT JOIN locations l ON l.id = tt.location_id
        {where}
        ORDER BY tt.ticket_date DESC, tt.submitted_on DESC
        LIMIT %s OFFSET %s
    """
    params.extend([count, offset])
    rows = query(sql, tuple(params))
    return ok(paginated_response(rows, total, page, count))


@app.route(route="time-tickets/{formId}", methods=["GET"])
def get_time_ticket(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/time-tickets/{formId}"""
    auth = _check_api_key(req)
    if auth: return auth

    form_id = req.route_params.get("formId")
    rows = query("""
        SELECT
            tt.*,
            l.name AS location_name
        FROM time_tickets tt
        LEFT JOIN locations l ON l.id = tt.location_id
        WHERE tt.form_id = %s
    """, (form_id,))

    if not rows:
        return not_found("Time Ticket")
    return ok(rows[0])



# ===========================================================================
# ETL STATUS
# ===========================================================================

@app.route(route="etl/status", methods=["GET"])
def get_etl_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/status
    Returns last sync timestamps, current record counts, and delta vs yesterday.
    Also saves today's snapshot for tomorrow's comparison.
    Used by Power Automate daily email report.
    """
    auth = _check_api_key(req)
    if auth: return auth

    from datetime import datetime, timezone, timedelta

    tables = ["locations","companies","workers","worker_locations",
              "certification_types","certifications",
              "form_types","forms","time_tickets"]

    sync_state = query("""
        SELECT entity, last_sync, last_count, updated_at
        FROM etl_sync_state ORDER BY entity
    """)

    # Current counts
    counts = {}
    for tbl in tables:
        try:
            result = query(f"SELECT COUNT(*) AS n FROM {tbl}")
            counts[tbl] = result[0]["n"] if result else 0
        except Exception:
            counts[tbl] = None

    # Yesterday's snapshot for delta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    prev = query("SELECT entity, record_count FROM etl_daily_snapshot WHERE snapshot_date = %s", (yesterday,))
    prev_counts = {r["entity"]: r["record_count"] for r in prev}

    # Build delta
    deltas = {}
    for tbl in tables:
        curr = counts.get(tbl)
        prev_val = prev_counts.get(tbl)
        if curr is not None and prev_val is not None:
            deltas[tbl] = curr - prev_val
        else:
            deltas[tbl] = None

    # Save today's snapshot
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for tbl, count in counts.items():
                if count is not None:
                    cur.execute("""
                        INSERT INTO etl_daily_snapshot (snapshot_date, entity, record_count)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (snapshot_date, entity) DO UPDATE
                        SET record_count = EXCLUDED.record_count, created_at = NOW()
                    """, (today, tbl, count))
        conn.commit()
    except Exception as e:
        logging.warning(f"Failed to save daily snapshot: {e}")
    finally:
        conn.close()

    return ok({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sync_state": sync_state,
        "record_counts": counts,
        "new_since_yesterday": deltas,
    })


# ===========================================================================
# FORM PDF PROXY
# ===========================================================================

@app.route(route="forms/{formId}/pdf", methods=["GET"])
def get_form_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/pdf
    Proxies the SiteDocs PDF export back to the caller.
    Query param: companyId (optional — defaults to SITEDOCS_COMPANY_ID env var)
    """
    auth = _check_api_key(req)
    if auth: return auth

    form_id    = req.route_params.get("formId")
    company_id = req.params.get("companyId") or os.environ.get("SITEDOCS_COMPANY_ID", "")

    if not company_id:
        return error(400, "companyId required — pass as query param or set SITEDOCS_COMPANY_ID env var")

    import requests as req_lib
    from shared.db import _get_secret

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url   = f"https://api-1.sitedocs.com/api/v1/export/pdf/company/{company_id}/form/{form_id}"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            headers={"Authorization": sitedocs_token},
            timeout=30,
        )
        if upstream.status_code == 404:
            return not_found("Form PDF")
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()

        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="form-{form_id}.pdf"',
                "Content-Length": str(len(upstream.content)),
                "Cache-Control": "private, max-age=300",
            }
        )

    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs PDF request timed out")
    except req_lib.exceptions.RequestException as e:
        logging.error(f"PDF proxy error for {form_id}: {e}")
        return error(502, "Failed to retrieve PDF from SiteDocs")


@app.route(route="forms/{formId}/viewer-url", methods=["GET"])
def get_form_viewer_url(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/viewer-url
    Returns the public SiteDocs web viewer URL + PDF URL for a form.
    Used by VG-SiteView to build Open in SiteDocs and View PDF links.
    """
    auth = _check_api_key(req)
    if auth: return auth

    form_id    = req.route_params.get("formId")
    company_id = req.params.get("companyId") or os.environ.get("SITEDOCS_COMPANY_ID", "")

    if not company_id:
        return error(400, "companyId required")

    return ok({
        "formId":    form_id,
        "viewerUrl": f"https://web.sitedocs.com/company/{company_id}/form/{form_id}",
        "pdfUrl":    f"/api/forms/{form_id}/pdf?companyId={company_id}",
    })


# ===========================================================================
# ATTACHMENT PROXY
# ===========================================================================

@app.route(route="attachments/{attachmentId}/download", methods=["GET"])
def download_attachment(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/attachments/{attachmentId}/download

    Looks up the attachment metadata in PostgreSQL, then proxies
    the binary content from SiteDocs back to the caller.
    Never stores the binary in the database.

    Optional query param: domainObjectId — required by SiteDocs if not
    already stored in the attachments table.
    """
    auth = _check_api_key(req)
    if auth: return auth

    attachment_id = req.route_params.get("attachmentId")

    # Look up metadata from our attachments table
    rows = query("""
        SELECT attachment_id, domain_object_id, kind,
               original_file_name, content_type, company_id
        FROM attachments
        WHERE attachment_id = %s
    """, (attachment_id,))

    if not rows:
        return not_found("Attachment")

    meta = rows[0]
    domain_object_id = meta["domain_object_id"]
    file_name        = meta["original_file_name"] or attachment_id
    content_type     = meta["content_type"] or "application/octet-stream"

    # Kind int → SiteDocs enum string
    kind_map = {
        0: "Contractor", 1: "Equipment", 2: "Incident",
        3: "Worker", 4: "Project", 5: "FormType",
        6: "Resource", 7: "ProcessRun"
    }
    kind_str = kind_map.get(meta["kind"], "Project")

    # Proxy the file from SiteDocs
    import requests as req_lib
    from shared.db import _get_secret

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url   = "https://api-1.sitedocs.com/api/v1/attachments/content"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            headers={"Authorization": sitedocs_token},
            params={
                "attachmentId":   attachment_id,
                "domainObjectId": domain_object_id,
                "kind":           kind_str,
                "fileName":       file_name,
            },
            timeout=30,
            stream=True,
        )

        if upstream.status_code == 404:
            return not_found("Attachment content")
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()

        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Content-Length": str(len(upstream.content)),
                "Cache-Control": "private, max-age=300",
            }
        )

    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs request timed out")
    except req_lib.exceptions.RequestException as e:
        logging.error(f"SiteDocs proxy error for {attachment_id}: {e}")
        return error(502, "Failed to retrieve attachment from SiteDocs")


# ===========================================================================
# INTERNAL HELPERS
# ===========================================================================

def _check_api_key(req: func.HttpRequest):
    """Returns an error response if API key is invalid, else None."""
    from shared.db import _get_secret, error as err
    expected = _get_secret("API_KEY")
    provided = req.headers.get("X-API-Key", "")
    if expected and provided != expected:
        return err(401, "Unauthorized — invalid or missing X-API-Key header")
    return None


# ===========================================================================
# SCHEDULED ETL TRIGGERS
# ===========================================================================

@app.timer_trigger(
    schedule="0 0 2 * * *",        # 2:00 AM UTC daily — full sync
    arg_name="nightlyTimer",
    run_on_startup=False,
)
def nightly_full_sync(nightlyTimer: func.TimerRequest) -> None:
    """Full ETL sync — runs nightly at 2AM UTC."""
    import subprocess, sys
    logging.info("Nightly full ETL sync starting...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "etl.run_etl"],
            capture_output=True, text=True, timeout=3600
        )
        logging.info(f"ETL stdout: {result.stdout[-2000:]}")
        if result.returncode != 0:
            logging.error(f"ETL stderr: {result.stderr[-1000:]}")
    except Exception as e:
        logging.error(f"Nightly ETL failed: {e}")


@app.timer_trigger(
    schedule="0 0 * * * *",        # Every hour at :00 — incremental forms + time tickets
    arg_name="hourlyTimer",
    run_on_startup=False,
)
def hourly_incremental_sync(hourlyTimer: func.TimerRequest) -> None:
    """Incremental forms + time tickets sync — runs every hour."""
    import subprocess, sys
    logging.info("Hourly incremental sync starting...")
    try:
        for stage in ["forms", "time_tickets"]:
            result = subprocess.run(
                [sys.executable, "-m", "etl.run_etl", "--only", stage],
                capture_output=True, text=True, timeout=600
            )
            logging.info(f"  {stage}: exit={result.returncode}")
            if result.returncode != 0:
                logging.error(f"  {stage} stderr: {result.stderr[-500:]}")
    except Exception as e:
        logging.error(f"Hourly sync failed: {e}")


@app.route(route="etl/trigger", methods=["POST"])
def manual_etl_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/etl/trigger
    Manually trigger the ETL. Optionally pass {"only": "workers"} in body.
    Admin use only — requires API key.
    """
    auth = _check_api_key(req)
    if auth: return auth

    import subprocess, sys, threading

    try:
        body = req.get_json() or {}
    except Exception:
        body = {}

    only = body.get("only")
    cmd = [sys.executable, "-m", "etl.run_etl"]
    if only:
        cmd += ["--only", only]

    # Run async so the HTTP response returns immediately
    def run():
        subprocess.run(cmd, timeout=3600)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return ok({
        "status": "triggered",
        "stage": only or "all",
        "message": "ETL started in background. Check Application Insights for progress."
    })


@app.timer_trigger(
    schedule="0 30 * * * *",     # Every hour at :30 — PDF sync to SharePoint
    arg_name="pdfSyncTimer",
    run_on_startup=False,
)
def hourly_pdf_sync(pdfSyncTimer: func.TimerRequest) -> None:
    """
    Upload new time ticket PDFs to SharePoint Invoice folders.
    Runs every hour at :30 (30 min after the incremental ETL at :00).
    Uses vg-ticket-sync.py logic inline — no subprocess needed.
    """
    import os, re, time
    import requests as req_lib
    import psycopg2
    import psycopg2.extras
    from shared.db import _get_secret

    logging.info("Hourly PDF sync starting...")

    # Credentials
    tenant_id     = os.environ.get("SP_TENANT_ID",     "9d71e5ea-6164-445b-99c7-cc5b6b1f9e3a")
    client_id     = os.environ.get("SP_CLIENT_ID",     "76d8a182-c87a-4df5-a888-a7f48ae9ff3e")
    client_secret = _get_secret("SP_CLIENT_SECRET")
    company_id    = os.environ.get("SITEDOCS_COMPANY_ID", "48651caf-50e4-45ab-875a-dfe02fa14441")
    sd_token      = _get_secret("SITEDOCS_API_TOKEN")

    if not client_secret:
        logging.error("SP_CLIENT_SECRET not set — skipping PDF sync")
        return

    GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
    SP_HOSTNAME = "velocitygeomaticsinc.sharepoint.com"

    # Get Graph token
    token_resp = req_lib.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id,
              "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"}
    )
    if not token_resp.ok:
        logging.error(f"PDF sync: failed to get SP token: {token_resp.text}")
        return
    sp_token = token_resp.json()["access_token"]
    headers  = {"Authorization": f"Bearer {sp_token}", "Accept": "application/json"}

    # Get drive ID
    site_resp = req_lib.get(f"{GRAPH_BASE}/sites/{SP_HOSTNAME}:/", headers=headers)
    if not site_resp.ok:
        logging.error(f"PDF sync: failed to get site: {site_resp.text}")
        return
    site_id = site_resp.json()["id"]

    drives_resp = req_lib.get(f"{GRAPH_BASE}/sites/{site_id}/drives", headers=headers)
    drive_id = None
    for d in drives_resp.json().get("value", []):
        if d.get("name", "").lower() in ("documents", "shared documents"):
            drive_id = d["id"]
            break
    if not drive_id:
        logging.error("PDF sync: Documents drive not found")
        return

    # Get pending tickets from DB
    conn = psycopg2.connect(
        host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"], password=_get_secret("POSTGRES_PASSWORD"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)), sslmode="require"
    )

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT tt.form_id, tt.form_label,
                   substring(l.name FROM '^([0-9]{6})') AS job_no
            FROM time_tickets tt
            JOIN locations l ON l.id = tt.location_id
            WHERE tt.is_deleted = false
              AND tt.submitted_on IS NOT NULL
              AND l.name ~ '^[0-9]{6}'
              AND (tt.sp_uploaded IS NULL OR tt.sp_uploaded = false)
            ORDER BY tt.submitted_on
        """)
        tickets = [dict(r) for r in cur.fetchall()]

    logging.info(f"PDF sync: {len(tickets)} ticket(s) to upload")
    success = failed = skipped = 0

    for ticket in tickets:
        fid    = str(ticket["form_id"])
        job_no = ticket["job_no"]
        label  = (ticket["form_label"] or fid).strip()
        for ch in r'/\\:*?"<>|':
            label = label.replace(ch, "-")
        year        = "20" + job_no[:2]
        folder_path = f"Job Files/{year}/{job_no}/Invoice"
        filename    = f"{label}.pdf"
        sp_path     = f"{folder_path}/{filename}"

        try:
            # Fetch PDF from SiteDocs
            pdf_resp = req_lib.get(
                f"https://api-1.sitedocs.com/api/v1/export/pdf/company/{company_id}/form/{fid}",
                headers={"Authorization": sd_token}, timeout=30
            )
            if pdf_resp.status_code == 404:
                skipped += 1
                continue
            pdf_resp.raise_for_status()

            # Ensure folder exists
            parts = folder_path.split("/")
            parent_id = "root"
            built = ""
            for part in parts:
                built = f"{built}/{part}" if built else part
                chk = req_lib.get(f"{GRAPH_BASE}/drives/{drive_id}/root:/{built}", headers=headers)
                if chk.status_code == 200:
                    parent_id = chk.json()["id"]
                elif chk.status_code == 404:
                    cr = req_lib.post(
                        f"{GRAPH_BASE}/drives/{drive_id}/items/{parent_id}/children",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
                    )
                    if cr.status_code in (200, 201):
                        parent_id = cr.json()["id"]

            # Upload PDF
            up = req_lib.put(
                f"{GRAPH_BASE}/drives/{drive_id}/root:/{sp_path}:/content",
                headers={**headers, "Content-Type": "application/pdf"},
                data=pdf_resp.content, timeout=60
            )
            up.raise_for_status()

            # Mark uploaded
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE time_tickets SET sp_uploaded=true, sp_uploaded_on=NOW(), sp_path=%s WHERE form_id=%s",
                    (sp_path, fid)
                )
            conn.commit()
            success += 1
            time.sleep(0.3)

        except Exception as e:
            logging.error(f"PDF sync: failed {fid}: {e}")
            failed += 1

    conn.close()
    logging.info(f"PDF sync complete: {success} uploaded / {skipped} skipped / {failed} failed")
