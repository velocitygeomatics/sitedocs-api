"""
routes/entities.py
Blueprints for: locations, lookups, workers, certifications
"""

import azure.functions as func
from shared.db import (
    query, require_api_key, ok, not_found,
    parse_pagination, paginated_response,
)

bp = func.Blueprint()


# ===========================================================================
# LOCATIONS
# ===========================================================================

@bp.route(route="locations", methods=["GET"])
@require_api_key
def get_locations(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/locations
    Query params: isArchived (bool), name (str), page, count
    """
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


@bp.route(route="locations/{id}", methods=["GET"])
@require_api_key
def get_location(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/locations/{id}"""
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


@bp.route(route="locations/{id}/forms", methods=["GET"])
@require_api_key
def get_location_forms(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/locations/{id}/forms
    Query params: formTypeId, submittedSince, isDeleted, page, count
    """
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


@bp.route(route="locations/{id}/attachments", methods=["GET"])
@require_api_key
def get_location_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/locations/{id}/attachments - kind=4 (Project)"""
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
# LOOKUPS
# ===========================================================================

@bp.route(route="lookup/form-types", methods=["GET"])
@require_api_key
def get_form_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/form-types"""
    name = req.params.get("name")
    type_filter = req.params.get("type")

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


@bp.route(route="lookup/attachment-types", methods=["GET"])
@require_api_key
def get_attachment_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/attachment-types"""
    rows = query("""
        SELECT attachment_type_id, name, created_on, modified_on
        FROM attachment_types
        WHERE is_deleted = false
        ORDER BY name
    """)
    return ok(rows)


@bp.route(route="lookup/company-types", methods=["GET"])
@require_api_key
def get_company_types(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/lookup/company-types"""
    rows = query("""
        SELECT company_type_id, name, created_on, modified_on
        FROM company_types
        WHERE is_deleted = false
        ORDER BY name
    """)
    return ok(rows)


@bp.route(route="lookup/companies", methods=["GET"])
@require_api_key
def get_companies(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/lookup/companies
    Query params: isActive, name, companyTypeId
    """
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

@bp.route(route="workers", methods=["GET"])
@require_api_key
def get_workers(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/workers
    Query params: active, isExternal, contractor, name
    """
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


@bp.route(route="workers/{id}", methods=["GET"])
@require_api_key
def get_worker(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/workers/{id}"""
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

@bp.route(route="certifications", methods=["GET"])
@require_api_key
def get_certifications(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/certifications
    Query params: workerId, typeId, isArchived, expiringWithin
    """
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


@bp.route(route="certifications/{id}", methods=["GET"])
@require_api_key
def get_certification(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/certifications/{id}"""
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
