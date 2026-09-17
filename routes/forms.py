"""
routes/forms.py
Blueprints for: forms, attachments, time_tickets
"""


import uuid

import azure.functions as func
from shared.db import (
    query, execute, require_api_key, ok, error, not_found,
    parse_pagination, paginated_response,
)

bp = func.Blueprint()


# ===========================================================================
# FORMS
# ===========================================================================

@bp.route(route="forms", methods=["GET"])
@require_api_key
def get_forms(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms
    Query params: locationId, formTypeId, submittedSince, isDeleted, page, count
    """
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


@bp.route(route="forms/{id}", methods=["GET"])
@require_api_key
def get_form(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/forms/{id}"""
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


@bp.route(route="forms/{id}/attachments", methods=["GET"])
@require_api_key
def get_form_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/forms/{id}/attachments - kind=5 (FormType)"""
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

@bp.route(route="attachments/{domainObjectId}", methods=["GET"])
@require_api_key
def get_attachments(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/attachments/{domainObjectId} - all kinds"""
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


@bp.route(route="attachments/{domainObjectId}/{kind}", methods=["GET"])
@require_api_key
def get_attachments_by_kind(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/attachments/{domainObjectId}/{kind}
    kind: 0=Contractor, 1=Equipment, 2=Incident, 3=Worker,
          4=Project, 5=FormType, 6=Resource, 7=ProcessRun
    """
    domain_object_id = req.route_params.get("domainObjectId")
    kind_str = req.route_params.get("kind")

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
# TIME TICKETS
# ===========================================================================

@bp.route(route="time-tickets", methods=["GET"])
@require_api_key
def get_time_tickets(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/time-tickets
    Query params: jobNo, client, crewChief, locationId,
                  dateFrom, dateTo, isDeleted, ticketType, page, count
    """
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

    # 'survey' | 'environmental'. Omit to get both templates.
    ticket_type = req.params.get("ticketType")
    if ticket_type:
        conditions.append("tt.ticket_type = %s")
        params.append(ticket_type)

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
            tt.form_id, tt.form_label, tt.ticket_type,
            tt.submitted_on, tt.ticket_date,
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
            tt.sa_travel_hrs, tt.sa_work_hrs, tt.sa_notes_hrs,
            tt.sa_total_hrs, tt.sa_subsistence,
            tt.details, tt.approval, tt.approval_date,
            tt.signed_by, tt.signed_on,
            tt.signature_lat, tt.signature_lng,
            tt.exported_on, tt.exported_by,
            -- What has already gone out for this ticket, by component.
            -- ['*'] means the whole ticket; a list of row keys means a
            -- partial export and the rest are still owed. Empty means
            -- nothing has been exported.
            COALESCE((
                SELECT array_agg(re.row_key ORDER BY re.row_key)
                  FROM time_ticket_row_exports re
                 WHERE re.form_id = tt.form_id
            ), '{{}}') AS exported_rows,   -- doubled: this SQL is an f-string
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


@bp.route(route="time-tickets/{formId}", methods=["GET"])
@require_api_key
def get_time_ticket(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/time-tickets/{formId}"""
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


@bp.route(route="time-tickets/export", methods=["POST"])
@require_api_key
def mark_time_tickets_exported(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/time-tickets/export
    Body: {"formIds": ["<uuid>", ...],
           "rows": [{"formId": "<uuid>", "rowKey": "crew_chief"}, ...],
           "exportedBy": "name"}

    Stamps exported_on/exported_by — the positive marker that something has
    been pulled into a QuickBooks payroll run. Anything already carrying a
    stamp keeps its original one, so re-running an export never rewrites
    who exported it or when.

    formIds is the whole-ticket stamp: the ticket is spoken for, every row
    of it. rows is the partial one, naming the components that actually went
    into the file so the rest stay in the queue. A caller exporting a mix
    sends both in one request; at least one must be present.

    The row set of a ticket is not derived here. VG-Time decides which
    components a ticket has and which of them went out, and a second copy of
    that rule in another language would drift from it.
    """
    try:
        body = req.get_json()
    except ValueError:
        return error(400, "Body must be JSON")

    form_ids = body.get("formIds") or []
    rows_in = body.get("rows") or []
    if not isinstance(form_ids, list) or not isinstance(rows_in, list):
        return error(400, "formIds and rows must be arrays")
    if not form_ids and not rows_in:
        return error(400, "formIds or rows must be a non-empty array")

    try:
        form_ids = [str(uuid.UUID(str(f))) for f in form_ids]
    except (ValueError, AttributeError):
        return error(400, "formIds must all be UUIDs")

    # A row with no key would land as a NULL primary key and take the whole
    # request down, so the shape is checked before anything is written.
    pairs = []
    for r in rows_in:
        if not isinstance(r, dict):
            return error(400, "rows must be objects with formId and rowKey")
        key = str(r.get("rowKey") or "").strip()
        if not key:
            return error(400, "every row needs a non-empty rowKey")
        if key == "*":
            return error(400, "rowKey \'*\' is the whole-ticket stamp — send it in formIds")
        try:
            pairs.append((str(uuid.UUID(str(r.get("formId")))), key))
        except (ValueError, AttributeError):
            return error(400, "every row needs a formId that is a UUID")

    exported_by = str(body.get("exportedBy") or "").strip() or "unknown"

    marked = found = 0
    if form_ids:
        # found vs marked separates "was already exported" from "no such
        # ticket" — the caller needs to tell those apart, they mean very
        # different things. The '*' insert records the same fact at row
        # granularity so one query answers what went out for a ticket.
        rows = execute("""
            WITH upd AS (
                UPDATE time_tickets
                   SET exported_on = NOW(),
                       exported_by = %s
                 WHERE form_id = ANY(%s::uuid[])
                   AND exported_on IS NULL
                RETURNING form_id
            ), ins AS (
                INSERT INTO time_ticket_row_exports (form_id, row_key, exported_by)
                SELECT form_id, '*', %s
                  FROM time_tickets
                 WHERE form_id = ANY(%s::uuid[])
                ON CONFLICT (form_id, row_key) DO NOTHING
            )
            SELECT (SELECT count(*) FROM upd) AS marked,
                   (SELECT count(*) FROM time_tickets
                     WHERE form_id = ANY(%s::uuid[])) AS found
        """, (exported_by, form_ids, exported_by, form_ids, form_ids))
        marked = rows[0]["marked"]
        found = rows[0]["found"]

    rows_marked = 0
    if pairs:
        # The ticket-level stamp is deliberately left alone: a partial export
        # must not mark the ticket done, which is the whole reason this table
        # exists. A component already exported keeps its original stamp, and
        # a form_id with no ticket is dropped by the join rather than failing
        # the request — the same tolerance formIds already gets above.
        ins = execute("""
            INSERT INTO time_ticket_row_exports (form_id, row_key, exported_by)
            SELECT tt.form_id, p.row_key, %s
              FROM unnest(%s::uuid[], %s::text[]) AS p(form_id, row_key)
              JOIN time_tickets tt ON tt.form_id = p.form_id
            ON CONFLICT (form_id, row_key) DO NOTHING
            RETURNING form_id
        """, (exported_by, [p[0] for p in pairs], [p[1] for p in pairs]))
        rows_marked = len(ins)

    total = len(set(form_ids))
    return ok({
        "marked": marked,
        "already": found - marked,
        "missing": total - found,
        "total": total,
        "rowsMarked": rows_marked,
        "rowsTotal": len(set(pairs)),
        "exportedBy": exported_by,
    })
