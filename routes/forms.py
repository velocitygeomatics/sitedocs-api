"""
routes/forms.py
Blueprints for: forms, attachments, time_tickets
"""


import logging
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
                  dateFrom, dateTo, isDeleted, ticketType, exported,
                  page, count
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

    # exported=false is what the payroll queue asks for: it is the difference
    # between fetching the whole table and fetching the handful still owed.
    #
    # The test is exported_on, not the row table. exported_on is only set when
    # a ticket went out whole, so a partly-exported ticket has it NULL and
    # stays in the unexported result with the rows it still owes. Filtering on
    # "has any row stamp" would drop it and lose those rows silently.
    exported = req.params.get("exported")
    if exported is not None:
        if exported.lower() == "true":
            conditions.append("tt.exported_on IS NOT NULL")
        else:
            conditions.append("tt.exported_on IS NULL")

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
    _attach_locked_prices(rows)
    _attach_job_clients(rows)
    _attach_job_refs(rows)
    return ok(paginated_response(rows, total, page, count))


def _attach_job_clients(rows):
    """
    Give each ticket the client its job belongs to: job_client (the Latitude
    company name), job_client_code, and job_client_source ('latitude' or
    'job_setup'). A ticket prices on this client, not the typed one, so
    "Vermillion" and "Petrus" land on the right schedule.

    Latitude answers first: it carries any correction made after the job was
    set up (job 260393 was created for Allwest and moved to Whitecap there).
    Job Setup covers jobs created since the last nightly Latitude snapshot.
    The job number must match exactly - "160176" is not job 160176P, which is
    a different client's.

    No match leaves the three fields null and the ticket prices on its typed
    client, which VG-Time flags for review. Best-effort for the same reason as
    the locked prices: without it every ticket prices as it did before.
    """
    for r in rows:
        r["job_client"] = r["job_client_code"] = r["job_client_source"] = None
    jobs = sorted({(r.get("job_no") or "").strip() for r in rows} - {""})
    if not jobs:
        return
    try:
        found = query("""
            SELECT j.job_no,
                   CASE WHEN lc."Company_Name" IS NOT NULL THEN lc."Company_Name"
                        ELSE sc."Company_Name" END AS name,
                   CASE WHEN lc."Company_Name" IS NOT NULL THEN lj."Client"
                        ELSE js.client_code END AS code,
                   CASE WHEN lc."Company_Name" IS NOT NULL THEN 'latitude'
                        WHEN sc."Company_Name" IS NOT NULL THEN 'job_setup' END AS source
              FROM unnest(%s::text[]) AS j(job_no)
              LEFT JOIN LATERAL (SELECT "Client" FROM latitude."tblJobs"
                                  WHERE "Job_Number" = j.job_no LIMIT 1) lj ON true
              LEFT JOIN latitude."tblClients" lc ON lc."Client_Code" = lj."Client"
              LEFT JOIN public.job_setup js ON js.job_number = j.job_no
              LEFT JOIN latitude."tblClients" sc ON sc."Client_Code" = js.client_code
        """, (jobs,))
    except Exception as exc:  # noqa: BLE001 - degrade to the typed client
        logging.warning("job clients unavailable: %s", exc)
        return
    by_job = {f["job_no"]: f for f in found if f["name"]}
    for r in rows:
        f = by_job.get((r.get("job_no") or "").strip())
        if f:
            r["job_client"] = f["name"]
            r["job_client_code"] = f["code"]
            r["job_client_source"] = f["source"]


def _attach_job_refs(rows):
    """
    Give each ticket its job's AFE / Cost Centre (job_afe) and PO# (job_po)
    for the costed ticket header. Latitude holds them in txtJobUserField3 and
    txtJobUserField4.

    Job Setup keeps its own copy, stamped refs_updated_at, whenever a job is
    created or its refs are edited there. That copy wins only when it is newer
    than the last Latitude snapshot of tblJobs, so an AFE added on Job Detail
    prints on the next ticket pulled while a later change made in Latitude
    itself still wins once it has been copied. Blank is null.

    Best-effort, like the job clients: each source degrades on its own, and
    without either the ticket prints no reference line.
    """
    for r in rows:
        r["job_afe"] = r["job_po"] = None
    jobs = sorted({(r.get("job_no") or "").strip() for r in rows} - {""})
    if not jobs:
        return
    refs = {}
    try:
        for f in query("""
            SELECT j.job_no,
                   NULLIF(TRIM(lj."txtJobUserField3"), '') AS afe,
                   NULLIF(TRIM(lj."txtJobUserField4"), '') AS po
              FROM unnest(%s::text[]) AS j(job_no)
              JOIN LATERAL (SELECT "txtJobUserField3", "txtJobUserField4"
                              FROM latitude."tblJobs"
                             WHERE "Job_Number" = j.job_no LIMIT 1) lj ON true
        """, (jobs,)):
            refs[f["job_no"]] = (f["afe"], f["po"])
    except Exception as exc:  # noqa: BLE001 - print no reference line
        logging.warning("job refs unavailable from Latitude: %s", exc)
    try:
        # Separate query: before the job_setup migration adds these columns
        # it fails here and the Latitude copy still prints.
        for f in query("""
            SELECT job_number AS job_no,
                   NULLIF(TRIM(afe_cost_centre), '') AS afe,
                   NULLIF(TRIM(po_number), '') AS po
              FROM public.job_setup
             WHERE job_number = ANY(%s)
               AND refs_updated_at > COALESCE(
                       (SELECT max(snapped_at) FROM latitude._table_watch
                         WHERE table_name = 'tblJobs'), '-infinity')
        """, (jobs,)):
            refs[f["job_no"]] = (f["afe"], f["po"])
    except Exception as exc:  # noqa: BLE001 - keep the Latitude copy
        logging.warning("job refs unavailable from Job Setup: %s", exc)
    for r in rows:
        afe, po = refs.get((r.get("job_no") or "").strip(), (None, None))
        r["job_afe"], r["job_po"] = afe, po


def _attach_locked_prices(rows):
    """
    Give each ticket a locked_prices object: row_key -> the price that row was
    exported at. A row with a lock is billed; VG-Time and the costed ticket
    show the lock instead of re-pricing it from today's rates.

    A separate query, and best-effort, so a missing or unreadable price table
    costs the locks and not the ticket list: without them every row prices
    live, which is what happened before locks existed.
    """
    for r in rows:
        r["locked_prices"] = {}
    ids = [str(r["form_id"]) for r in rows if r.get("form_id")]
    if not ids:
        return
    try:
        prices = query("""
            SELECT form_id, row_key, run_id, schedule_key, schedule_name,
                   rate_key, qb_item, qty, unit, rate, amount, price_source,
                   priced_by, priced_at
              FROM time_ticket_row_prices
             WHERE form_id = ANY(%s::uuid[])
        """, (ids,))
    except Exception as exc:  # noqa: BLE001 - degrade to live pricing
        logging.warning("locked prices unavailable: %s", exc)
        return
    by_form = {str(r["form_id"]): r["locked_prices"] for r in rows}
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    for p in prices:
        target = by_form.get(str(p["form_id"]))
        if target is None:
            continue
        target[p["row_key"]] = {
            "scheduleKey": p["schedule_key"],
            "scheduleName": p["schedule_name"],
            "rateKey": p["rate_key"],
            "qbItem": p["qb_item"],
            "qty": num(p["qty"]),
            "unit": p["unit"],
            "rate": num(p["rate"]),
            "amount": num(p["amount"]),
            "source": p["price_source"],
            "runId": str(p["run_id"]) if p["run_id"] else None,
            "pricedBy": p["priced_by"],
            "pricedAt": p["priced_at"].isoformat() if p["priced_at"] else None,
        }


# The :guid constraint is what keeps this off /time-tickets/exports. Without it
# the host matched "exports" here first, Postgres rejected it as a uuid, and the
# History tab's hydration got a 500 it swallowed - so the tab stayed empty.
@bp.route(route="time-tickets/{formId:guid}", methods=["GET"])
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

    # The run itself. Recorded after the stamping so it carries what actually
    # happened rather than what was asked for, and in the same request so a
    # run can never go unlogged. A logging failure must not fail the export -
    # the stamps are the operative fact and are already committed - so this is
    # best-effort and reports itself in the response instead.
    run_id = None
    try:
        run_id = _log_export_run(
            body, exported_by, form_ids, pairs,
            marked=marked, already=found - marked,
            missing=total - found, rows_marked=rows_marked,
        )
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        logging.exception("export run log failed: %s", exc)

    # The price of each row at the moment it was billed. Best-effort for the
    # same reason as the run log: the stamps are committed and are what keeps
    # a row out of a second payroll run, so a price that fails to lock is
    # reported, not allowed to fail the export after the fact.
    prices_locked, price_error = 0, None
    try:
        prices_locked = _lock_row_prices(
            body.get("prices"), form_ids, pairs, run_id, exported_by)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        logging.exception("price lock failed: %s", exc)
        price_error = str(exc)

    return ok({
        "marked": marked,
        "already": found - marked,
        "missing": total - found,
        "total": total,
        "rowsMarked": rows_marked,
        "rowsTotal": len(set(pairs)),
        "exportedBy": exported_by,
        "runId": run_id,
        "runLogged": run_id is not None,
        "pricesLocked": prices_locked,
        "priceLockError": price_error,
    })


def _lock_row_prices(prices, form_ids, pairs, run_id, exported_by):
    """
    Insert one time_ticket_row_prices row per priced row, and return how many
    were new. The first lock wins: a row re-exported later keeps the price it
    was first billed at.

    Only rows this request actually exported can be locked - any row of a
    whole ticket in formIds, or exactly the (formId, rowKey) pairs in rows -
    so a stray entry cannot put a price on work that was never billed.
    Malformed entries are skipped rather than failing the batch.
    """
    if not isinstance(prices, list) or not prices:
        return 0
    whole = set(form_ids)
    partial = set(pairs)

    def _num(v):
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    def _txt(v):
        s = str(v).strip() if v is not None else ""
        return s or None

    cols = {k: [] for k in ("form_id", "row_key", "schedule_key", "schedule_name",
                            "rate_key", "qb_item", "qty", "unit", "rate",
                            "amount", "price_source")}
    seen = set()
    for p in prices:
        if not isinstance(p, dict):
            continue
        key = _txt(p.get("rowKey"))
        amount = _num(p.get("amount"))
        try:
            fid = str(uuid.UUID(str(p.get("formId"))))
        except (ValueError, AttributeError):
            continue
        if not key or key == "*" or amount is None or (fid, key) in seen:
            continue
        if fid not in whole and (fid, key) not in partial:
            continue
        seen.add((fid, key))
        for col, val in (("form_id", fid), ("row_key", key),
                         ("schedule_key", _txt(p.get("scheduleKey"))),
                         ("schedule_name", _txt(p.get("scheduleName"))),
                         ("rate_key", _txt(p.get("rateKey"))),
                         ("qb_item", _txt(p.get("qbItem"))),
                         ("qty", _num(p.get("qty"))),
                         ("unit", _txt(p.get("unit"))),
                         ("rate", _num(p.get("rate"))),
                         ("amount", round(amount, 2)),
                         ("price_source", _txt(p.get("source")) or "rated")):
            cols[col].append(val)
    if not seen:
        return 0

    inserted = execute("""
        INSERT INTO time_ticket_row_prices
            (form_id, row_key, run_id, schedule_key, schedule_name, rate_key,
             qb_item, qty, unit, rate, amount, price_source, priced_by)
        SELECT tt.form_id, p.row_key, %s::uuid, p.schedule_key, p.schedule_name,
               p.rate_key, p.qb_item, p.qty, p.unit, p.rate, p.amount,
               p.price_source, %s
          FROM unnest(%s::uuid[], %s::text[], %s::text[], %s::text[], %s::text[],
                      %s::text[], %s::numeric[], %s::text[], %s::numeric[],
                      %s::numeric[], %s::text[])
               AS p(form_id, row_key, schedule_key, schedule_name, rate_key,
                    qb_item, qty, unit, rate, amount, price_source)
          JOIN time_tickets tt ON tt.form_id = p.form_id
        ON CONFLICT (form_id, row_key) DO NOTHING
        RETURNING form_id
    """, (run_id, exported_by, cols["form_id"], cols["row_key"],
          cols["schedule_key"], cols["schedule_name"], cols["rate_key"],
          cols["qb_item"], cols["qty"], cols["unit"], cols["rate"],
          cols["amount"], cols["price_source"]))
    return len(inserted)


def _log_export_run(body, exported_by, form_ids, pairs, *,
                    marked, already, missing, rows_marked):
    """
    Write one time_ticket_export_runs row plus its contents, and return the
    run_id. The caller supplies the descriptive fields - filename, IIF line
    count, hours, employees - because only VG-Time knows them; none of them
    can be derived from the stamps.
    """
    run_id = str(uuid.uuid4())

    def _int(name):
        try:
            v = body.get(name)
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    try:
        total_hours = float(body.get("totalHours")) if body.get("totalHours") is not None else None
    except (TypeError, ValueError):
        total_hours = None

    employees = body.get("employees")
    employees = [str(e) for e in employees] if isinstance(employees, list) else []
    file_name = str(body.get("fileName") or "").strip() or None

    execute("""
        INSERT INTO time_ticket_export_runs
            (run_id, exported_by, file_name, iif_lines, total_hours, employees,
             tickets_marked, tickets_already, tickets_missing, rows_marked)
        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (run_id, exported_by, file_name, _int("iifLines"), total_hours,
          employees, marked, already, missing, rows_marked))

    # Whole tickets go in as '*', matching time_ticket_row_exports. A form_id
    # with no ticket is dropped by the join, the same tolerance the stamping
    # above gives it, so a stale id cannot fail the log.
    items = [(f, "*") for f in set(form_ids)] + [(f, k) for f, k in set(pairs)]
    if items:
        execute("""
            INSERT INTO time_ticket_export_run_items (run_id, form_id, row_key)
            SELECT %s::uuid, tt.form_id, p.row_key
              FROM unnest(%s::uuid[], %s::text[]) AS p(form_id, row_key)
              JOIN time_tickets tt ON tt.form_id = p.form_id
            ON CONFLICT DO NOTHING
        """, (run_id, [i[0] for i in items], [i[1] for i in items]))

    return run_id


@bp.route(route="time-tickets/exports", methods=["GET"])
@require_api_key
def list_time_ticket_export_runs(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/time-tickets/exports
    Query params: page, count

    The export-run log behind VG-Time's History tab, newest first.
    """
    count, offset = parse_pagination(req)
    page = offset // count if count else 0

    total = query("SELECT count(*) AS n FROM time_ticket_export_runs")[0]["n"]
    runs = query("""
        SELECT r.run_id, r.run_at, r.exported_by, r.file_name, r.iif_lines,
               r.total_hours, r.employees, r.tickets_marked, r.tickets_already,
               r.tickets_missing, r.rows_marked,
               (SELECT count(DISTINCT i.form_id)
                  FROM time_ticket_export_run_items i
                 WHERE i.run_id = r.run_id) AS ticket_count
          FROM time_ticket_export_runs r
         ORDER BY r.run_at DESC
         LIMIT %s OFFSET %s
    """, (count, offset))

    ids = [r["run_id"] for r in runs]
    by_run = {}
    if ids:
        for row in query("""
            SELECT run_id, form_id, row_key
              FROM time_ticket_export_run_items
             WHERE run_id = ANY(%s::uuid[])
        """, (ids,)):
            by_run.setdefault(str(row["run_id"]), []).append(
                {"formId": str(row["form_id"]), "rowKey": row["row_key"]}
            )

    data = []
    for r in runs:
        rid = str(r["run_id"])
        data.append({
            "runId": rid,
            "runAt": r["run_at"].isoformat() if r["run_at"] else None,
            "exportedBy": r["exported_by"],
            "fileName": r["file_name"],
            "iifLines": r["iif_lines"],
            "totalHours": float(r["total_hours"]) if r["total_hours"] is not None else None,
            "employees": list(r["employees"] or []),
            "ticketCount": r["ticket_count"],
            "marked": r["tickets_marked"],
            "already": r["tickets_already"],
            "missing": r["tickets_missing"],
            "rowsMarked": r["rows_marked"],
            "items": by_run.get(rid, []),
        })

    return ok(paginated_response(data, total, page, count))
