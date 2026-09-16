"""
routes/rates.py
Blueprints for rate engine: schedules and rate items (VG-Time).
"""

import logging
import azure.functions as func
from shared.db import (
    query, get_connection, release_connection, require_api_key,
    ok, error, not_found,
)

bp = func.Blueprint()


@bp.route(route="rates", methods=["GET"])
def get_rates(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/rates
    Returns the full rate engine payload. No auth required -- rates are
    read-only reference data consumed by VG-Time.
    """
    try:
        schedules = query(
            "SELECT id, schedule_key, name, valid_from, valid_to, notes "
            "FROM vgt_rate_schedules ORDER BY id"
        )

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
                    "rate": float(row["rate"]) if row["rate"] is not None else None,
                    "unit": row["unit"],
                    "minimum_qty": float(row["minimum_qty"]) if row["minimum_qty"] is not None else 0,
                    "ot_multiplier": float(row["ot_multiplier"]) if row["ot_multiplier"] is not None else None,
                    "ot_threshold": float(row["ot_threshold"]) if row["ot_threshold"] is not None else None,
                    "markup_percentage": (
                        float(row["markup_percentage"]) if row["markup_percentage"] is not None else None
                    ),
                    "day_rate_threshold": (
                        float(row["day_rate_threshold"]) if row["day_rate_threshold"] is not None else None
                    ),
                }

        client_map = query("""
            SELECT c.client_name, s.schedule_key, s.name AS schedule_name,
                   c.modifier_percentage, c.notes
            FROM vgt_client_schedule_map c
            LEFT JOIN vgt_rate_schedules s ON c.schedule_id = s.id
            ORDER BY c.client_name
        """)

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
                "rate": float(row["rate"]) if row["rate"] is not None else None,
                "unit": row["unit"],
                "minimum_qty": float(row["minimum_qty"]) if row["minimum_qty"] is not None else 0,
                "ot_multiplier": float(row["ot_multiplier"]) if row["ot_multiplier"] is not None else None,
                "ot_threshold": float(row["ot_threshold"]) if row["ot_threshold"] is not None else None,
                "markup_percentage": (
                    float(row["markup_percentage"]) if row["markup_percentage"] is not None else None
                ),
                "day_rate_threshold": (
                    float(row["day_rate_threshold"]) if row["day_rate_threshold"] is not None else None
                ),
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


@bp.route(route="rates/schedules", methods=["POST"])
@require_api_key
def add_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/rates/schedules - create a new rate schedule."""
    try:
        data = req.get_json()
        name = data.get("name")
        key = data.get("schedule_key")
        if not name or not key:
            return error(400, "name and schedule_key required")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vgt_rate_schedules (name, schedule_key) VALUES (%s, %s)",
                    (name, key)
                )
            conn.commit()
        finally:
            release_connection(conn)
        return ok({"message": "Schedule created"})
    except Exception as e:
        return error(500, str(e))


@bp.route(route="rates/schedules/{id}", methods=["PUT"])
@require_api_key
def update_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/rates/schedules/{id} - update schedule name/key."""
    try:
        sid = req.route_params.get("id")
        data = req.get_json()
        name = data.get("name")
        key = data.get("schedule_key")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE vgt_rate_schedules SET name = %s, schedule_key = %s WHERE id = %s",
                    (name, key, sid)
                )
            conn.commit()
        finally:
            release_connection(conn)
        return ok({"message": "Schedule updated"})
    except Exception as e:
        return error(500, str(e))


@bp.route(route="rates/items/{slug}/{sched_key}", methods=["PUT"])
@require_api_key
def update_rate(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/rates/items/{slug}/{sched_key} - update a specific rate."""
    try:
        slug = req.route_params.get("slug")
        sk = req.route_params.get("sched_key")
        data = req.get_json()
        rate = data.get("rate")
        unit = data.get("unit")

        item = query("SELECT id FROM vgt_rate_items WHERE slug = %s", (slug,))
        sched = query("SELECT id FROM vgt_rate_schedules WHERE schedule_key = %s", (sk,))

        if not item or not sched:
            return not_found("Item or Schedule")

        iid = item[0]["id"]
        sid = sched[0]["id"]

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vgt_schedule_rates (schedule_id, item_id, rate, unit)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (schedule_id, item_id)
                    DO UPDATE SET rate = EXCLUDED.rate, unit = EXCLUDED.unit
                """, (sid, iid, rate, unit))
            conn.commit()
        finally:
            release_connection(conn)

        return ok({"message": "Rate updated"})
    except Exception as e:
        return error(500, str(e))


@bp.route(route="rates/schedules/{id}/copy", methods=["POST"])
@require_api_key
def copy_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/rates/schedules/{id}/copy - clone a schedule and all its rates.

    This is how a new year's card gets built: copy last year's, then edit the
    handful of rates that moved. Copying only the header would leave the new
    schedule costing everything at zero, so the rate rows come with it.
    """
    try:
        sid = req.route_params.get("id")
        data = req.get_json()
        name = data.get("name")
        key = data.get("schedule_key")
        if not name or not key:
            return error(400, "name and schedule_key required")

        src = query(
            "SELECT id, notes FROM vgt_rate_schedules WHERE id = %s", (sid,)
        )
        if not src:
            return not_found("Schedule")

        if query("SELECT 1 FROM vgt_rate_schedules WHERE schedule_key = %s", (key,)):
            return error(409, f"schedule_key '{key}' already exists")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vgt_rate_schedules (name, schedule_key, notes) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (name, key, src[0]["notes"]),
                )
                new_id = cur.fetchone()[0]
                # Column list is explicit: a SELECT * would carry the source's
                # primary key into the insert and collide.
                cur.execute("""
                    INSERT INTO vgt_schedule_rates
                        (schedule_id, item_id, rate, unit, minimum_qty,
                         ot_multiplier, ot_threshold, markup_percentage,
                         day_rate_threshold)
                    SELECT %s, item_id, rate, unit, minimum_qty,
                           ot_multiplier, ot_threshold, markup_percentage,
                           day_rate_threshold
                      FROM vgt_schedule_rates
                     WHERE schedule_id = %s
                """, (new_id, src[0]["id"]))
                copied = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)

        return ok({"message": "Schedule copied", "id": new_id, "rates_copied": copied})
    except Exception as e:
        return error(500, str(e))


@bp.route(route="rates/schedules/{id}", methods=["DELETE"])
@require_api_key
def delete_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    DELETE /api/rates/schedules/{id} - remove a schedule and its rate rows.

    Refuses while any client still maps to the schedule. A mapped client whose
    schedule vanished falls through to the default rate card, which silently
    re-prices their work instead of failing — so this has to be a loud 409 and
    not a cascade. Unmap the clients first.
    """
    try:
        sid = req.route_params.get("id")

        if not query("SELECT 1 FROM vgt_rate_schedules WHERE id = %s", (sid,)):
            return not_found("Schedule")

        clients = query(
            "SELECT client_name FROM vgt_client_schedule_map "
            "WHERE schedule_id = %s ORDER BY client_name",
            (sid,),
        )
        if clients:
            names = [c["client_name"] for c in clients]
            return error(
                409,
                "Schedule is still mapped to "
                f"{len(names)} client(s): {', '.join(names)}. "
                "Reassign them before deleting.",
            )

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vgt_schedule_rates WHERE schedule_id = %s", (sid,))
                removed = cur.rowcount
                cur.execute("DELETE FROM vgt_rate_schedules WHERE id = %s", (sid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)

        return ok({"message": "Schedule deleted", "rates_removed": removed})
    except Exception as e:
        return error(500, str(e))
