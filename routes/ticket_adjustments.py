"""
routes/ticket_adjustments.py
Client-ticket adjustments for time tickets (vg-dashboard/costed-ticket-edits-spec.md).

A line of a ticket can be hidden from the client's costed ticket, or printed
with different hours / quantity / rate. The SiteDocs ticket is never changed
and the QuickBooks export keeps the recorded durations; only the costed ticket
reads these. Tables: scripts/migrate_add_row_adjustments.sql.

    PUT    /api/time-tickets/{formId}/adjustments          set one line
    DELETE /api/time-tickets/{formId}/adjustments?rowKey=  revert one line

GET /api/time-tickets carries the current adjustments as row_adjustments
(attach_row_adjustments, called from routes/forms.py).

The row key travels in the body or query string, not the path: 'equip:...'
holds a colon, which does not belong in a URL path segment.
"""

import logging
import math
import re

import azure.functions as func
from psycopg2.errors import UndefinedTable
from shared.db import query, execute, require_api_key, ok, error, not_found

bp = func.Blueprint()

NOT_SET_UP = "Ticket adjustments are not set up yet: apply scripts/migrate_add_row_adjustments.sql"

CREW_KEYS = ("crew_chief", "sa")
_EQUIP_KEY = re.compile(r"^equip:[a-z0-9_]+$")
REASON_MAX = 500

# Which value columns each kind of line may set. Hours belong to crew lines
# (the costed ticket prints travel and work separately and totals them), a
# quantity to equipment lines; any line may take a rate.
CREW_FIELDS = ("travel_hrs", "work_hrs", "rate")
EQUIP_FIELDS = ("qty", "rate")
_BODY_NAMES = {"travel_hrs": "travelHrs", "work_hrs": "workHrs", "qty": "qty", "rate": "rate"}


def _kind(row_key):
    if row_key in CREW_KEYS:
        return "crew"
    if _EQUIP_KEY.match(row_key):
        return "equipment"
    return None


def _num(v):
    return float(v) if v is not None else None


def _to_json(a):
    """One adjustment row as GET /time-tickets returns it."""
    return {
        "hidden": bool(a["hidden"]),
        "travelHrs": _num(a.get("travel_hrs")),
        "workHrs": _num(a.get("work_hrs")),
        "qty": _num(a.get("qty")),
        "rate": _num(a.get("rate")),
        "reason": a.get("reason"),
        "updatedBy": a.get("updated_by"),
        "updatedAt": a["updated_at"].isoformat() if a.get("updated_at") else None,
    }


def attach_row_adjustments(rows):
    """
    Give each ticket a row_adjustments object: row_key -> its adjustment.

    Best-effort, like the locked prices: a missing table (migration not yet
    applied) or a failed read costs the adjustments, not the ticket list, and
    every line then prints as recorded.
    """
    for r in rows:
        r["row_adjustments"] = {}
    ids = [str(r["form_id"]) for r in rows if r.get("form_id")]
    if not ids:
        return
    try:
        adjs = query("""
            SELECT form_id, row_key, hidden, travel_hrs, work_hrs, qty, rate,
                   reason, updated_by, updated_at
              FROM time_ticket_row_adjustments
             WHERE form_id = ANY(%s::uuid[])
        """, (ids,))
    except Exception as exc:  # noqa: BLE001 - degrade to "as recorded"
        logging.warning("row adjustments unavailable: %s", exc)
        return
    by_form = {str(r["form_id"]): r["row_adjustments"] for r in rows if r.get("form_id")}
    for a in adjs:
        target = by_form.get(str(a["form_id"]))
        if target is not None:
            target[a["row_key"]] = _to_json(a)


def _parse_set(body):
    """Validate a PUT body. Returns (values, None) or (None, message)."""
    if not isinstance(body, dict):
        return None, "Body must be a JSON object"
    row_key = str(body.get("rowKey") or "").strip()
    kind = _kind(row_key)
    if kind is None:
        return None, "rowKey must be 'crew_chief', 'sa' or 'equip:<column>'"

    hidden = body.get("hidden", False)
    if not isinstance(hidden, bool):
        return None, "hidden must be true or false"

    allowed = CREW_FIELDS if kind == "crew" else EQUIP_FIELDS
    values = {"row_key": row_key, "hidden": hidden}
    for col, name in _BODY_NAMES.items():
        v = body.get(name)
        if v is None or v == "":
            values[col] = None
            continue
        if col not in allowed:
            what = "a crew line" if kind == "crew" else "an equipment line"
            return None, f"{name} does not apply to {what}"
        if isinstance(v, bool):
            return None, f"{name} must be a number"
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None, f"{name} must be a number"
        if not math.isfinite(n) or n < 0:
            return None, f"{name} must be zero or more"
        values[col] = n

    reason = str(body.get("reason") or "").strip()
    if not reason:
        return None, "reason is required"
    if len(reason) > REASON_MAX:
        return None, f"reason must be {REASON_MAX} characters or fewer"
    values["reason"] = reason

    if not hidden and all(values[c] is None for c in _BODY_NAMES):
        return None, "Nothing to adjust: hide the line or change a value (to undo an adjustment, revert it)"

    values["updated_by"] = str(body.get("updatedBy") or "").strip() or "unknown"
    return values, None


@bp.route(route="time-tickets/{formId:guid}/adjustments", methods=["PUT"])
@require_api_key
def set_row_adjustment(req: func.HttpRequest) -> func.HttpResponse:
    """
    PUT /api/time-tickets/{formId}/adjustments
    Body: {"rowKey": "equip:cc_subsistence", "hidden": true,
           "travelHrs": null, "workHrs": null, "qty": null, "rate": null,
           "reason": "Local job - hotel not billable", "updatedBy": "name"}

    Replaces the line's adjustment and appends the before/after to the log in
    the same statement, so an adjustment can never change unlogged.
    """
    form_id = req.route_params.get("formId")
    try:
        body = req.get_json()
    except ValueError:
        return error(400, "Body must be JSON")
    v, problem = _parse_set(body)
    if problem:
        return error(400, problem)

    try:
        rows = _write_adjustment(v, form_id)
    except UndefinedTable:
        return error(503, NOT_SET_UP)
    if not rows:
        return not_found("Time Ticket")
    return ok({"formId": form_id, "rowKey": v["row_key"], "adjustment": _to_json(rows[0])})


def _write_adjustment(v, form_id):
    # Every CTE reads the same snapshot, so prev is the row before the upsert.
    # No ticket, no insert: the empty result is the caller's 404.
    return execute("""
        WITH prev AS (
            SELECT to_jsonb(a) - 'form_id' - 'row_key' AS j
              FROM time_ticket_row_adjustments a
             WHERE a.form_id = %(form_id)s AND a.row_key = %(row_key)s
        ), up AS (
            INSERT INTO time_ticket_row_adjustments
                   (form_id, row_key, hidden, travel_hrs, work_hrs, qty, rate,
                    reason, updated_by, updated_at)
            SELECT tt.form_id, %(row_key)s, %(hidden)s, %(travel_hrs)s, %(work_hrs)s,
                   %(qty)s, %(rate)s, %(reason)s, %(updated_by)s, NOW()
              FROM time_tickets tt
             WHERE tt.form_id = %(form_id)s
            ON CONFLICT (form_id, row_key) DO UPDATE
               SET hidden = EXCLUDED.hidden, travel_hrs = EXCLUDED.travel_hrs,
                   work_hrs = EXCLUDED.work_hrs, qty = EXCLUDED.qty,
                   rate = EXCLUDED.rate, reason = EXCLUDED.reason,
                   updated_by = EXCLUDED.updated_by, updated_at = NOW()
            RETURNING *
        ), log AS (
            INSERT INTO time_ticket_row_adjustment_log
                   (form_id, row_key, action, before, after, reason, changed_by)
            SELECT up.form_id, up.row_key, 'set', (SELECT j FROM prev),
                   to_jsonb(up) - 'form_id' - 'row_key', up.reason, up.updated_by
              FROM up
        )
        SELECT * FROM up
    """, {**v, "form_id": form_id})


@bp.route(route="time-tickets/{formId:guid}/adjustments", methods=["DELETE"])
@require_api_key
def revert_row_adjustment(req: func.HttpRequest) -> func.HttpResponse:
    """
    DELETE /api/time-tickets/{formId}/adjustments?rowKey=equip:cc_subsistence&by=name

    The line prints as recorded again. The removed adjustment is kept in the
    log. Query string, not a body: the dashboard proxy forwards no body on DELETE.
    """
    form_id = req.route_params.get("formId")
    row_key = str(req.params.get("rowKey") or "").strip()
    if _kind(row_key) is None:
        return error(400, "rowKey must be 'crew_chief', 'sa' or 'equip:<column>'")
    by = str(req.params.get("by") or "").strip() or "unknown"

    try:
        rows = _delete_adjustment(form_id, row_key, by)
    except UndefinedTable:
        return error(503, NOT_SET_UP)
    if not rows or not rows[0]["reverted"]:
        return not_found("Adjustment")
    return ok({"formId": form_id, "rowKey": row_key, "reverted": True})


def _delete_adjustment(form_id, row_key, by):
    return execute("""
        WITH del AS (
            DELETE FROM time_ticket_row_adjustments
             WHERE form_id = %(form_id)s AND row_key = %(row_key)s
            RETURNING *
        ), log AS (
            INSERT INTO time_ticket_row_adjustment_log
                   (form_id, row_key, action, before, after, reason, changed_by)
            SELECT del.form_id, del.row_key, 'revert',
                   to_jsonb(del) - 'form_id' - 'row_key', NULL, NULL, %(by)s
              FROM del
        )
        SELECT count(*) AS reverted FROM del
    """, {"form_id": form_id, "row_key": row_key, "by": by})
