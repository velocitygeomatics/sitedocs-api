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
# WORKERS
# ===========================================================================

@app.route(route="workers", methods=["GET"])
def get_workers(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/workers
    Query params:
      active     — true/false (default: any)
      isExternal — true/false (default: any)
      contractor — substring match on contractor_name
      name       — substring match on first_name/last_name
    Returns full list (table is small, < 200 rows).
    """
    auth = _check_api_key(req)
    if auth: return auth

    conditions = []
    params = []

    active = req.params.get("active")
    if active is not None:
        conditions.append("w.active = %s")
        params.append(active.lower() == "true")

    is_external = req.params.get("isExternal")
    if is_external is not None:
        conditions.append("w.is_external = %s")
        params.append(is_external.lower() == "true")

    contractor = req.params.get("contractor")
    if contractor:
        conditions.append("w.contractor_name ILIKE %s")
        params.append(f"%{contractor}%")

    name = req.params.get("name")
    if name:
        conditions.append("(w.first_name ILIKE %s OR w.last_name ILIKE %s)")
        params.extend([f"%{name}%", f"%{name}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = query(f"""
        SELECT
            w.id, w.first_name, w.last_name, w.job_title,
            w.contractor_id, w.contractor_name,
            w.email, w.mobile_number, w.phone_number,
            w.active, w.is_external,
            w.employee_number, w.date_hired,
            w.created_on, w.last_modified_on
        FROM workers w
        {where}
        ORDER BY w.last_name, w.first_name
    """, tuple(params))
    return ok(rows)


@app.route(route="workers/{id}", methods=["GET"])
def get_worker(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/workers/{id}"""
    auth = _check_api_key(req)
    if auth: return auth

    worker_id = req.route_params.get("id")
    rows = query("""
        SELECT
            w.id, w.first_name, w.last_name, w.job_title,
            w.contractor_id, w.contractor_name,
            w.email, w.mobile_number, w.phone_number,
            w.active, w.is_external,
            w.employee_number, w.date_hired,
            w.street_address, w.city, w.postal_code,
            w.emergency_contact1, w.emergency_contact2, w.emergency_notes,
            w.created_on, w.last_modified_on
        FROM workers w
        WHERE w.id = %s
    """, (worker_id,))

    if not rows:
        return not_found("Worker")
    return ok(rows[0])


# ===========================================================================
# CERTIFICATIONS
# ===========================================================================

@app.route(route="certifications", methods=["GET"])
def get_certifications(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/certifications
    Query params:
      workerId       — filter by worker UUID
      typeId         — filter by certification_type_id
      isArchived     — true/false (default: any)
      expiringWithin — integer days; e.g. 30 returns certs expiring within 30 days
    Returns full list with worker name joined.
    """
    auth = _check_api_key(req)
    if auth: return auth

    conditions = []
    params = []

    worker_id = req.params.get("workerId")
    if worker_id:
        conditions.append("c.worker_id = %s")
        params.append(worker_id)

    type_id = req.params.get("typeId")
    if type_id:
        conditions.append("c.certification_type_id = %s")
        params.append(type_id)

    is_archived = req.params.get("isArchived")
    if is_archived is not None:
        conditions.append("c.is_archived = %s")
        params.append(is_archived.lower() == "true")

    expiring_within = req.params.get("expiringWithin")
    if expiring_within:
        try:
            days = int(expiring_within)
            conditions.append("c.expires IS NOT NULL AND c.expires <= NOW() + (%s || ' days')::interval")
            params.append(str(days))
        except ValueError:
            pass

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = query(f"""
        SELECT
            c.id,
            c.worker_id,
            w.first_name AS worker_first_name,
            w.last_name  AS worker_last_name,
            (w.first_name || ' ' || w.last_name) AS worker_name,
            c.certification_type_id,
            c.certification_type_name,
            c.issuer, c.ticket,
            c.acquired, c.expires, c.acknowledged_expiry_date,
            c.is_archived,
            c.created_on, c.last_modified_on
        FROM certifications c
        LEFT JOIN workers w ON w.id = c.worker_id
        {where}
        ORDER BY c.expires NULLS LAST, w.last_name, w.first_name
    """, tuple(params))
    return ok(rows)


@app.route(route="certifications/{id}", methods=["GET"])
def get_certification(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/certifications/{id}"""
    auth = _check_api_key(req)
    if auth: return auth

    cert_id = req.route_params.get("id")
    rows = query("""
        SELECT
            c.id,
            c.worker_id,
            w.first_name AS worker_first_name,
            w.last_name  AS worker_last_name,
            (w.first_name || ' ' || w.last_name) AS worker_name,
            c.certification_type_id,
            c.certification_type_name,
            c.issuer, c.ticket,
            c.acquired, c.expires, c.acknowledged_expiry_date,
            c.is_archived,
            c.created_on, c.last_modified_on
        FROM certifications c
        LEFT JOIN workers w ON w.id = c.worker_id
        WHERE c.id = %s
    """, (cert_id,))

    if not rows:
        return not_found("Certification")
    return ok(rows[0])


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

    from datetime import datetime, timezone

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

    # Most recent snapshot before today, for the delta. Snapshots are written
    # only when this endpoint runs, so they're sparse; requiring an exact
    # "yesterday" row yields all-null whenever yesterday wasn't captured. Use
    # the latest snapshot strictly before today instead.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    prev = query(
        "SELECT entity, record_count FROM etl_daily_snapshot "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM etl_daily_snapshot "
        "WHERE snapshot_date < %s)",
        (today_iso,))
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


@app.route(route="etl/summary", methods=["GET"])
def get_etl_summary(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/summary
    One-shot digest for Power Automate / email reports: sync state,
    record counts, 24h delta, unresolved error counts per stage, and
    the most recent 20 errors. Does not save a snapshot (use /status).
    """
    auth = _check_api_key(req)
    if auth: return auth

    from datetime import datetime, timezone

    tables = ["locations","companies","workers","worker_locations",
              "certification_types","certifications",
              "form_types","forms","time_tickets"]

    sync_state = query("""
        SELECT entity, last_sync, last_count
        FROM etl_sync_state ORDER BY entity
    """)

    counts = {}
    for tbl in tables:
        try:
            result = query(f"SELECT COUNT(*) AS n FROM {tbl}")
            counts[tbl] = result[0]["n"] if result else 0
        except Exception:
            counts[tbl] = None

    # Latest snapshot strictly before today (snapshots are sparse — see /status).
    today_iso = datetime.now(timezone.utc).date().isoformat()
    prev = query(
        "SELECT entity, record_count FROM etl_daily_snapshot "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM etl_daily_snapshot "
        "WHERE snapshot_date < %s)",
        (today_iso,))
    prev_counts = {r["entity"]: r["record_count"] for r in prev}
    deltas = {tbl: (counts.get(tbl) - prev_counts.get(tbl))
              if counts.get(tbl) is not None and prev_counts.get(tbl) is not None
              else None for tbl in tables}

    error_counts = query("""
        SELECT stage, COUNT(*) AS n
        FROM etl_errors WHERE resolved = false
        GROUP BY stage ORDER BY n DESC
    """)
    recent_errors = query("""
        SELECT id, occurred_at, stage, entity_id, error_type, error_message
        FROM etl_errors WHERE resolved = false
        ORDER BY occurred_at DESC LIMIT 20
    """)
    total_unresolved = sum(r["n"] for r in error_counts)

    return ok({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sync_state": sync_state,
        "record_counts": counts,
        "new_since_yesterday": deltas,
        "errors": {
            "unresolved_total": total_unresolved,
            "by_stage": error_counts,
            "recent": recent_errors,
        },
    })


# ===========================================================================
# ETL ERRORS
# ===========================================================================

@app.route(route="etl/errors", methods=["GET"])
def get_etl_errors(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/errors
    Query params:
      stage      — filter by ETL stage (e.g. time_tickets, forms)
      resolved   — true/false (default: false — show unresolved only)
      limit      — max rows (default 50, max 500)
    """
    auth = _check_api_key(req)
    if auth: return auth

    conditions = []
    params = []

    stage = req.params.get("stage")
    if stage:
        conditions.append("stage = %s")
        params.append(stage)

    resolved = req.params.get("resolved", "false")
    conditions.append("resolved = %s")
    params.append(resolved.lower() == "true")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    try:
        limit = min(int(req.params.get("limit", 50)), 500)
    except ValueError:
        limit = 50

    rows = query(f"""
        SELECT id, occurred_at, stage, entity_id, error_type,
               error_message, payload, resolved
        FROM etl_errors
        {where}
        ORDER BY occurred_at DESC
        LIMIT %s
    """, tuple(params + [limit]))

    return ok(rows)


@app.route(route="etl/errors/{id}/resolve", methods=["POST"])
def resolve_etl_error(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/etl/errors/{id}/resolve — mark an error as resolved."""
    auth = _check_api_key(req)
    if auth: return auth

    error_id = req.route_params.get("id")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE etl_errors SET resolved = true WHERE id = %s RETURNING id",
                (error_id,))
            row = cur.fetchone()
        conn.commit()
        if not row:
            return not_found("ETL error")
        return ok({"id": int(error_id), "resolved": True})
    finally:
        conn.close()


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


@app.route(route="forms/{formId}/signatures", methods=["GET"])
def get_form_signatures(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/signatures

    Thin proxy over SiteDocs `GET /api/v1/signatures?formId={formId}`.
    VG-Time's live path (browser) uses this to determine whether a
    ticket has a Crew Chief signature — signatures don't live inside
    /forms/content/{id}, they're on this separate endpoint.

    Response: the raw SignatureViewModel array from SiteDocs.
    """
    auth = _check_api_key(req)
    if auth: return auth

    form_id = req.route_params.get("formId")
    if not form_id:
        return error(400, "formId required")

    import requests as req_lib
    from shared.db import _get_secret

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url   = "https://api-1.sitedocs.com/api/v1/signatures"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            params={"formId": form_id},
            headers={"Authorization": sitedocs_token, "Accept": "application/json"},
            timeout=20,
        )
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()
        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype="application/json",
            headers={"Cache-Control": "private, max-age=60"},
        )
    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs signatures request timed out")
    except req_lib.exceptions.RequestException as e:
        logging.error(f"Signatures proxy error for {form_id}: {e}")
        return error(502, "Failed to retrieve signatures from SiteDocs")


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

def _run_etl_streaming(label: str, args: list[str]) -> int:
    """Run an ETL subprocess and stream its stdout/stderr line-by-line into
    the host logger so each line lands in App Insights as it happens.
    Returns the subprocess exit code (or -1 if it failed to launch)."""
    import subprocess
    import sys
    cmd = [sys.executable, "-m", "etl.run_etl", *args]
    logging.info(f"{label}: starting ({' '.join(args) or 'full'})")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        logging.error(f"{label}: failed to launch: {e}")
        return -1

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            logging.info(f"{label} | {line}")

    rc = proc.wait()
    if rc == 0:
        logging.info(f"{label}: completed (exit=0)")
    else:
        logging.error(f"{label}: failed (exit={rc})")
    return rc


# The old single nightly_full_sync timer ran every stage in one invocation.
# Once the forms/form_contents backlog pushed the run past the 10-minute
# Consumption-plan functionTimeout, the host killed it mid-forms every night
# (observed 2026-06-08 onward), so the stages after forms never ran and the
# backlog compounded. Stages now run as separate invocations, each with its
# own 10-minute budget, staggered so they never overlap each other or the
# :00 hourly incremental sync.

@app.timer_trigger(
    schedule="0 5 2 * * *",        # 2:05 AM UTC — core entities (~1 min)
    arg_name="nightlyEntitiesTimer",
    run_on_startup=False,
)
def nightly_entities_sync(nightlyEntitiesTimer: func.TimerRequest) -> None:
    """Nightly 1/5: lookups, companies, workers, equipment, certifications."""
    for stage in ("lookups", "companies", "workers", "equipment", "certifications"):
        _run_etl_streaming(f"nightly_entities_sync:{stage}", ["--only", stage])


@app.timer_trigger(
    schedule="0 20 2 * * *",       # 2:20 AM UTC — forms (incremental)
    arg_name="nightlyFormsTimer",
    run_on_startup=False,
)
def nightly_forms_sync(nightlyFormsTimer: func.TimerRequest) -> None:
    """Nightly 2/5: form_types + forms."""
    _run_etl_streaming("nightly_forms_sync", ["--only", "forms"])


@app.timer_trigger(
    schedule="0 35 2 * * *",       # 2:35 AM UTC — form details
    arg_name="nightlyFormDetailsTimer",
    run_on_startup=False,
)
def nightly_form_details_sync(nightlyFormDetailsTimer: func.TimerRequest) -> None:
    """Nightly 3/5: form_contents, signatures, incidents."""
    for stage in ("form_contents", "signatures", "incidents"):
        _run_etl_streaming(f"nightly_form_details_sync:{stage}", ["--only", stage])


@app.timer_trigger(
    schedule="0 5 3 * * *",        # 3:05 AM UTC — attachments
    arg_name="nightlyAttachmentsTimer",
    run_on_startup=False,
)
def nightly_attachments_sync(nightlyAttachmentsTimer: func.TimerRequest) -> None:
    """Nightly 4/5: attachments."""
    _run_etl_streaming("nightly_attachments_sync", ["--only", "attachments"])


@app.timer_trigger(
    schedule="0 20 3 * * *",       # 3:20 AM UTC — time tickets (~7 min)
    arg_name="nightlyTimeTicketsTimer",
    run_on_startup=False,
)
def nightly_time_tickets_sync(nightlyTimeTicketsTimer: func.TimerRequest) -> None:
    """Nightly 5/5: time tickets."""
    _run_etl_streaming("nightly_time_tickets_sync", ["--only", "time_tickets"])


@app.timer_trigger(
    schedule="0 0 * * * *",        # Every hour at :00 — incremental forms + time tickets
    arg_name="hourlyTimer",
    run_on_startup=False,
)
def hourly_incremental_sync(hourlyTimer: func.TimerRequest) -> None:
    """Incremental forms + time tickets sync — runs every hour."""
    _run_etl_streaming("hourly_incremental_sync", ["--only", "forms", "--mode", "new"])


@app.route(route="etl/trigger", methods=["POST"])
def manual_etl_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/etl/trigger
    Manually trigger the ETL. Optionally pass {"only": "workers"} in body.
    Admin use only — requires API key.
    """
    auth = _check_api_key(req)
    if auth: return auth

    import subprocess
    import sys
    import threading

    try:
        body = req.get_json() or {}
    except Exception:
        body = {}

    only = body.get("only")
    mode = body.get("mode", "all")  # "new" or "all"
    cmd = [sys.executable, "-m", "etl.run_etl"]
    if only:
        cmd += ["--only", only]
    if mode in ("new", "all"):
        cmd += ["--mode", mode]

    # Run async so the HTTP response returns immediately
    def run():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        logging.info(f"ETL trigger stdout: {result.stdout[-2000:]}")
        if result.returncode != 0:
            logging.error(f"ETL trigger stderr: {result.stderr[-1000:]}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return ok({
        "status": "triggered",
        "stage": only or "all",
        "message": "ETL started in background. Check Application Insights for progress."
    })


def _run_pdf_sync() -> dict:
    """
    Upload new time ticket PDFs to SharePoint Invoice folders.
    Returns {"success": n, "skipped": n, "failed": n, "pending": n}.
    """
    import os
    import time
    import requests as req_lib
    import psycopg2
    import psycopg2.extras
    from shared.db import _get_secret

    logging.info("PDF sync starting...")

    tenant_id  = os.environ.get("SP_TENANT_ID",        "9d71e5ea-6164-445b-99c7-cc5b6b1f9e3a")
    client_id  = os.environ.get("SP_CLIENT_ID",        "76d8a182-c87a-4df5-a888-a7f48ae9ff3e")
    company_id = os.environ.get("SITEDOCS_COMPANY_ID", "48651caf-50e4-45ab-875a-dfe02fa14441")

    client_secret = _get_secret("SP_CLIENT_SECRET")
    sd_token      = _get_secret("SITEDOCS_API_TOKEN")

    if not client_secret:
        raise RuntimeError("SP_CLIENT_SECRET is empty — check App Settings")
    if not sd_token:
        raise RuntimeError("SITEDOCS_API_TOKEN is empty — check App Settings")

    GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
    SP_HOSTNAME = "velocitygeomaticsinc.sharepoint.com"

    token_resp = req_lib.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id,
              "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"}
    )
    if not token_resp.ok:
        raise RuntimeError(f"Failed to get SP token: {token_resp.text}")
    sp_token = token_resp.json()["access_token"]
    headers  = {"Authorization": f"Bearer {sp_token}", "Accept": "application/json"}

    site_resp = req_lib.get(f"{GRAPH_BASE}/sites/{SP_HOSTNAME}:/", headers=headers)
    if not site_resp.ok:
        raise RuntimeError(f"Failed to get SharePoint site: {site_resp.text}")
    site_id = site_resp.json()["id"]

    drives_resp = req_lib.get(f"{GRAPH_BASE}/sites/{site_id}/drives", headers=headers)
    drive_id = None
    for d in drives_resp.json().get("value", []):
        if d.get("name", "").lower() in ("documents", "shared documents"):
            drive_id = d["id"]
            break
    if not drive_id:
        raise RuntimeError("Documents drive not found in SharePoint site")

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

    logging.info(f"PDF sync: {len(tickets)} ticket(s) pending")
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
            pdf_resp = req_lib.get(
                f"https://api-1.sitedocs.com/api/v1/export/pdf/company/{company_id}/form/{fid}",
                headers={"Authorization": sd_token}, timeout=30
            )
            if pdf_resp.status_code == 404:
                skipped += 1
                continue
            pdf_resp.raise_for_status()

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

            up = req_lib.put(
                f"{GRAPH_BASE}/drives/{drive_id}/root:/{sp_path}:/content",
                headers={**headers, "Content-Type": "application/pdf"},
                data=pdf_resp.content, timeout=60
            )
            up.raise_for_status()

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
    result = {"success": success, "skipped": skipped, "failed": failed, "pending": len(tickets)}
    logging.info(f"PDF sync complete: {success} uploaded / {skipped} skipped / {failed} failed")
    return result


@app.timer_trigger(
    schedule="0 30 * * * *",
    arg_name="pdfSyncTimer",
    run_on_startup=False,
)
def hourly_pdf_sync(pdfSyncTimer: func.TimerRequest) -> None:
    """Upload new time ticket PDFs to SharePoint. Runs every hour at :30."""
    try:
        _run_pdf_sync()
    except Exception as e:
        logging.error(f"PDF sync failed: {e}")


@app.route(route="pdf-sync/trigger", methods=["POST"])
@require_api_key
def trigger_pdf_sync(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/pdf-sync/trigger
    Manually trigger the PDF sync. Runs synchronously and returns results.
    """
    try:
        result = _run_pdf_sync()
        return ok(result)
    except Exception as e:
        logging.error(f"PDF sync trigger failed: {e}")
        return error(500, str(e))


# ===========================================================================
# KEEP-WARM TIMER
# ===========================================================================

@app.timer_trigger(
    schedule="0 */4 * * * *",      # every 4 minutes — keep the worker warm
    arg_name="warmTimer",
    run_on_startup=False,
)
def keep_warm(warmTimer: func.TimerRequest) -> None:
    """
    Pings Postgres to keep the Function App worker warm and the DB
    connection pool primed. Avoids 5-15s cold starts on Consumption plan.
    """
    try:
        query("SELECT 1 AS ok")
        logging.info("keep_warm: ok")
    except Exception as e:
        logging.error(f"keep_warm failed: {e}")


# ===========================================================================
# HEALTH
# ===========================================================================

@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/health — unauthenticated lightweight check, also serves as warm ping."""
    try:
        query("SELECT 1")
        return ok({"status": "ok"})
    except Exception as e:
        return error(503, f"db unreachable: {e}")


# ===========================================================================
# RATES  (VG Rate Engine)
# ===========================================================================

@app.route(route="rates", methods=["GET"])
def get_rates(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/rates
    Returns the full rate engine payload:
      SCHEDULES   — vgt_rate_schedules
      RATE_ITEMS  — vgt_rate_items joined to vgt_schedule_rates (grouped by slug)
      CLIENT_MAP  — vgt_client_schedule_map joined to schedules
      OVERRIDES   — vgt_client_rate_overrides per client (empty until populated)
    No auth required — rates are read-only reference data consumed by VG-Time.
    """
    try:
        # 1. Schedules
        schedules = query(
            "SELECT id, schedule_key, name, valid_from, valid_to, notes "
            "FROM vgt_rate_schedules ORDER BY id"
        )

        # 2. Rate items + their per-schedule rates (grouped in Python)
        items_raw = query("""
            SELECT i.slug, i.line_item, i.category,
                   s.schedule_key, sr.rate, sr.unit, sr.minimum_qty,
                   sr.ot_multiplier, sr.ot_threshold,
                   sr.markup_percentage, sr.day_rate_threshold
            FROM vgt_rate_items i
            LEFT JOIN vgt_schedule_rates sr ON i.id = sr.item_id
            LEFT JOIN vgt_rate_schedules s  ON sr.schedule_id = s.id
            ORDER BY i.id
        """)

        items_dict = {}
        for row in items_raw:
            slug = row["slug"]
            if slug not in items_dict:
                items_dict[slug] = {
                    "slug":     slug,
                    "lineItem": row["line_item"],
                    "category": row["category"],
                    "rates":    {}
                }
            if row["schedule_key"]:
                items_dict[slug]["rates"][row["schedule_key"]] = {
                    "rate":              float(row["rate"])              if row["rate"]              is not None else None,
                    "unit":              row["unit"],
                    "minimum_qty":       float(row["minimum_qty"])       if row["minimum_qty"]       is not None else 0,
                    "ot_multiplier":     float(row["ot_multiplier"])     if row["ot_multiplier"]     is not None else None,
                    "ot_threshold":      float(row["ot_threshold"])      if row["ot_threshold"]      is not None else None,
                    "markup_percentage": float(row["markup_percentage"]) if row["markup_percentage"] is not None else None,
                    "day_rate_threshold":float(row["day_rate_threshold"])if row["day_rate_threshold"]is not None else None,
                }

        # 3. Client → schedule map
        client_map = query("""
            SELECT c.client_name, s.schedule_key, s.name AS schedule_name,
                   c.modifier_percentage, c.notes
            FROM vgt_client_schedule_map c
            LEFT JOIN vgt_rate_schedules s ON c.schedule_id = s.id
            ORDER BY c.client_name
        """)

        # 4. Per-client item overrides
        overrides_raw = query("""
            SELECT c.client_name, i.slug, o.rate, o.unit, o.minimum_qty,
                   o.ot_multiplier, o.ot_threshold,
                   o.markup_percentage, o.day_rate_threshold
            FROM vgt_client_rate_overrides o
            JOIN vgt_client_schedule_map c ON o.client_id = c.id
            JOIN vgt_rate_items          i ON o.item_id   = i.id
            ORDER BY c.client_name, i.slug
        """)

        overrides = {}
        for row in overrides_raw:
            client = row["client_name"]
            if client not in overrides:
                overrides[client] = {}
            overrides[client][row["slug"]] = {
                "rate":              float(row["rate"])              if row["rate"]              is not None else None,
                "unit":              row["unit"],
                "minimum_qty":       float(row["minimum_qty"])       if row["minimum_qty"]       is not None else 0,
                "ot_multiplier":     float(row["ot_multiplier"])     if row["ot_multiplier"]     is not None else None,
                "ot_threshold":      float(row["ot_threshold"])      if row["ot_threshold"]      is not None else None,
                "markup_percentage": float(row["markup_percentage"]) if row["markup_percentage"] is not None else None,
                "day_rate_threshold":float(row["day_rate_threshold"])if row["day_rate_threshold"]is not None else None,
            }

        return ok({
            "SCHEDULES":  schedules,
            "RATE_ITEMS": list(items_dict.values()),
            "CLIENT_MAP": client_map,
            "OVERRIDES":  overrides,
        })

    except Exception as e:
        logging.error(f"get_rates failed: {e}")
        return error(503, str(e))


@app.route(route="rates/schedules", methods=["POST"])
def add_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/rates/schedules — create a new rate schedule."""
    try:
        data = req.get_json()
        name = data.get("name")
        key  = data.get("schedule_key")
        if not name or not key:
            return error(400, "name and schedule_key required")
        
        query(
            "INSERT INTO vgt_rate_schedules (name, schedule_key) VALUES (%s, %s)",
            (name, key)
        )
        return ok({"message": "Schedule created"})
    except Exception as e:
        return error(500, str(e))


@app.route(route="rates/schedules/{id}", methods=["PUT"])
def update_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/rates/schedules/{id} — update schedule name/key."""
    try:
        sid = req.route_params.get("id")
        data = req.get_json()
        name = data.get("name")
        key  = data.get("schedule_key")
        
        query(
            "UPDATE vgt_rate_schedules SET name = %s, schedule_key = %s WHERE id = %s",
            (name, key, sid)
        )
        return ok({"message": "Schedule updated"})
    except Exception as e:
        return error(500, str(e))


@app.route(route="rates/items/{slug}/{sched_key}", methods=["PUT"])
def update_rate(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/rates/items/{slug}/{sched_key} — update a specific rate."""
    try:
        slug = req.route_params.get("slug")
        sk   = req.route_params.get("sched_key")
        data = req.get_json()
        rate = data.get("rate")
        unit = data.get("unit")
        
        # Get IDs
        item = query("SELECT id FROM vgt_rate_items WHERE slug = %s", (slug,))
        sched = query("SELECT id FROM vgt_rate_schedules WHERE schedule_key = %s", (sk,))
        
        if not item or not sched:
            return error(404, "Item or Schedule not found")
        
        iid = item[0]["id"]
        sid = sched[0]["id"]
        
        # Upsert into junction table
        query("""
            INSERT INTO vgt_schedule_rates (schedule_id, item_id, rate, unit)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (schedule_id, item_id) 
            DO UPDATE SET rate = EXCLUDED.rate, unit = EXCLUDED.unit
        """, (sid, iid, rate, unit))
        
        return ok({"message": "Rate updated"})
    except Exception as e:
        return error(500, str(e))
