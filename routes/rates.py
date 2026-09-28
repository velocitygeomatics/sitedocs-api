"""
routes/rates.py
Blueprints for rate engine: schedules and rate items (VG-Time).
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
            "CLIENT_PERIODS": _client_periods(),
        })

    except Exception as e:
        logging.error(f"get_rates failed: {e}")
        return error(503, str(e))


def _client_periods():
    """
    Which schedule each client was on over which dates, oldest first. A ticket
    prices on the period covering its ticket_date, so a reassignment applies
    from its effective date forward and leaves earlier work where it was.

    Best-effort: without the periods table every client prices on its current
    map entry, which is how pricing worked before periods existed, and that
    is a better failure than taking the whole rate payload down with it.
    Who made each change is left to the authenticated change log; this
    payload is served without a key.
    """
    try:
        return query("""
            SELECT c.client_name, s.schedule_key,
                   p.valid_from::text AS valid_from, p.valid_to::text AS valid_to
              FROM vgt_client_schedule_periods p
              JOIN vgt_client_schedule_map c ON c.id = p.client_id
              JOIN vgt_rate_schedules      s ON s.id = p.schedule_id
             ORDER BY c.client_name, p.valid_from NULLS FIRST
        """)
    except Exception as exc:  # noqa: BLE001 - degrade to undated map
        logging.warning("client schedule periods unavailable: %s", exc)
        return []


@bp.route(route="rates/client-changes", methods=["GET"])
@require_api_key
def list_client_schedule_changes(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/rates/client-changes - the client reassignment log, newest first.

    One entry per reassignment: client, old and new schedule, the date it took
    effect, who made it and when. A client's first period is not a change and
    is left out.
    """
    try:
        rows = query("""
            SELECT c.client_name,
                   ps.schedule_key AS previous_schedule_key,
                   ps.name         AS previous_schedule_name,
                   s.schedule_key, s.name AS schedule_name,
                   p.valid_from::text AS effective_from,
                   p.changed_by, p.changed_at, p.note
              FROM vgt_client_schedule_periods p
              JOIN vgt_client_schedule_map c  ON c.id  = p.client_id
              JOIN vgt_rate_schedules      s  ON s.id  = p.schedule_id
              JOIN vgt_rate_schedules      ps ON ps.id = p.previous_schedule_id
             ORDER BY p.changed_at DESC, p.id DESC
        """)
        return ok(rows)
    except Exception as e:
        return error(500, str(e))


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


@bp.route(route="rates/items/{slug}/{sched_key}", methods=["DELETE"])
@require_api_key
def delete_rate(req: func.HttpRequest) -> func.HttpResponse:
    """
    DELETE /api/rates/items/{slug}/{sched_key} - unset one rate on one schedule.

    update_rate is an upsert, so until this existed there was no way to take a
    line back off a schedule: setting it to 0 leaves the row in place and the
    item still reads as priced. Pruning a schedule meant editing the table by
    hand.

    Unsetting is not the same as pricing at zero. A slug with no row costs the
    line at the schedule's own default and the UI greys it as a placeholder; a
    slug priced at 0.00 bills nothing and looks deliberate. The distinction is
    the reason this is a DELETE and not `PUT {rate: 0}`.

    404s when the schedule carries no such rate, rather than reporting success,
    so a mistyped slug cannot read as a completed prune.
    """
    try:
        slug = req.route_params.get("slug")
        sk = req.route_params.get("sched_key")

        item = query("SELECT id FROM vgt_rate_items WHERE slug = %s", (slug,))
        sched = query("SELECT id FROM vgt_rate_schedules WHERE schedule_key = %s", (sk,))

        if not item or not sched:
            return not_found("Item or Schedule")

        iid = item[0]["id"]
        sid = sched[0]["id"]

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vgt_schedule_rates WHERE schedule_id = %s AND item_id = %s",
                    (sid, iid),
                )
                removed = cur.rowcount
            if not removed:
                conn.rollback()
                return not_found("Rate")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)

        return ok({"message": "Rate removed", "slug": slug, "schedule_key": sk})
    except Exception as e:
        return error(500, str(e))


@bp.route(route="rates/clients/{client_name}", methods=["PUT"])
@require_api_key
def update_client_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    PUT /api/rates/clients/{client_name} - point one client at a schedule.

    The client map decides which schedule a survey ticket prices against, and
    until now it was readable but not writable: /api/rates returned it and
    nothing could change it short of editing Postgres by hand.

    Matching is on the exact stored client_name, not the substring test
    matchRateSchedule() uses at ticket time. "EPFC" and "EPFC (190319)" are two
    rows and have to be moved separately, or one of them silently keeps its old
    schedule while the other reports success.

    404s on an unknown client or schedule rather than inserting, so a typo
    cannot quietly create a new mapping that shadows a real one.

    Body: {"schedule_key": "...", "effective_from": "YYYY-MM-DD",
           "changed_by": "name", "note": "..."}

    The change applies from effective_from forward (default: today, Mountain
    time). Tickets dated before it keep the schedule they were on, and a row
    already exported keeps its locked price whatever the date. The open period
    is closed the day before and a new one opened, in one transaction with the
    map update, so the history and the map cannot disagree.

    effective_from must fall after the start of the period it replaces: a
    change that reaches back past an earlier change would rewrite that change
    rather than follow it, and history is not edited from here.
    """
    try:
        client_name = req.route_params.get("client_name")
        try:
            body = req.get_json()
        except ValueError:
            return error(400, "Body must be JSON")
        sk = body.get("schedule_key")

        if not sk:
            return error(400, "schedule_key is required")

        raw_date = str(body.get("effective_from") or "").strip()
        try:
            effective = (date.fromisoformat(raw_date) if raw_date
                         else datetime.now(ZoneInfo("America/Edmonton")).date())
        except ValueError:
            return error(400, "effective_from must be a date, YYYY-MM-DD")
        changed_by = str(body.get("changed_by") or "").strip() or "unknown"
        note = str(body.get("note") or "").strip() or None

        client = query(
            "SELECT id, schedule_id FROM vgt_client_schedule_map WHERE client_name = %s",
            (client_name,),
        )
        sched = query("SELECT id FROM vgt_rate_schedules WHERE schedule_key = %s", (sk,))

        if not client or not sched:
            return not_found("Client or Schedule")

        client_id = client[0]["id"]
        new_id = sched[0]["id"]
        old_id = client[0]["schedule_id"]
        if old_id == new_id:
            return error(400, f"{client_name} is already on {sk}")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, valid_from FROM vgt_client_schedule_periods "
                    "WHERE client_id = %s AND valid_to IS NULL FOR UPDATE",
                    (client_id,),
                )
                open_period = cur.fetchone()
                if open_period and open_period[1] is not None and effective <= open_period[1]:
                    conn.rollback()
                    return error(
                        409,
                        f"{client_name}'s current schedule took effect on "
                        f"{open_period[1].isoformat()}. A new change has to "
                        "take effect after that date.",
                    )
                day_before = effective - timedelta(days=1)
                if open_period:
                    cur.execute(
                        "UPDATE vgt_client_schedule_periods SET valid_to = %s WHERE id = %s",
                        (day_before, open_period[0]),
                    )
                elif old_id is not None:
                    # A client mapped before periods were seeded, or added
                    # since without one: record what it was on until now.
                    cur.execute(
                        "INSERT INTO vgt_client_schedule_periods "
                        "(client_id, schedule_id, valid_from, valid_to, changed_by, note) "
                        "VALUES (%s, %s, NULL, %s, %s, %s)",
                        (client_id, old_id, day_before, changed_by,
                         "Assignment before the first recorded change"),
                    )
                cur.execute(
                    "INSERT INTO vgt_client_schedule_periods "
                    "(client_id, schedule_id, valid_from, valid_to, "
                    " previous_schedule_id, changed_by, note) "
                    "VALUES (%s, %s, %s, NULL, %s, %s, %s)",
                    (client_id, new_id, effective, old_id, changed_by, note),
                )
                cur.execute(
                    "UPDATE vgt_client_schedule_map SET schedule_id = %s, "
                    "updated_at = NOW() WHERE id = %s",
                    (new_id, client_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)

        return ok({
            "message": "Client reassigned",
            "client_name": client_name,
            "schedule_key": sk,
            "effective_from": effective.isoformat(),
        })
    except Exception as e:
        return error(500, str(e))


# What a client with no entry prices on: VG-Time's DEFAULT_SCHEDULE_ID.
DEFAULT_SCHEDULE_KEY = "vg_standard_2025"


@bp.route(route="rates/clients", methods=["POST"])
@require_api_key
def add_client(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/rates/clients
    Body: {"client_name": "...", "schedule_key": "...", "effective_from": "YYYY-MM-DD",
           "changed_by": "...", "note": "..."}

    Gives a client its own entry. Until now its tickets priced on the default
    schedule, so the history records exactly that: the default up to the day
    before effective_from, the chosen schedule from then on, logged as a
    change from the default like any reassignment.

    The name is matched as a substring of the ticket's client, so a name that
    already contains an existing entry's name is refused: those tickets price
    through that entry today, and the longer new name would quietly take them.
    """
    try:
        try:
            body = req.get_json()
        except ValueError:
            return error(400, "Body must be JSON")
        client_name = " ".join(str(body.get("client_name") or "").split())
        sk = body.get("schedule_key")
        if not client_name:
            return error(400, "client_name is required")
        if not sk:
            return error(400, "schedule_key is required")

        raw_date = str(body.get("effective_from") or "").strip()
        try:
            effective = (date.fromisoformat(raw_date) if raw_date
                         else datetime.now(ZoneInfo("America/Edmonton")).date())
        except ValueError:
            return error(400, "effective_from must be a date, YYYY-MM-DD")
        changed_by = str(body.get("changed_by") or "").strip() or "unknown"
        note = str(body.get("note") or "").strip() or "Client added"

        lowered = client_name.lower()
        for row in query("SELECT client_name FROM vgt_client_schedule_map"):
            existing = row["client_name"]
            if existing.lower() == lowered:
                return error(409, f"{existing} already has an entry")
            if existing.lower() in lowered:
                return error(409, f"{client_name} already prices through {existing}. "
                                  f"Change {existing} instead.")

        scheds = {r["schedule_key"]: r["id"] for r in query(
            "SELECT id, schedule_key FROM vgt_rate_schedules WHERE schedule_key IN (%s, %s)",
            (sk, DEFAULT_SCHEDULE_KEY))}
        if sk not in scheds:
            return not_found("Schedule")
        new_id = scheds[sk]
        default_id = scheds.get(DEFAULT_SCHEDULE_KEY, new_id)

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vgt_client_schedule_map (client_name, schedule_id, notes) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (client_name, new_id, "Added in VG-Time"),
                )
                client_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO vgt_client_schedule_periods "
                    "(client_id, schedule_id, valid_from, valid_to, changed_by, note) "
                    "VALUES (%s, %s, NULL, %s, %s, %s)",
                    (client_id, default_id, effective - timedelta(days=1), changed_by,
                     "Default schedule before the client had an entry"),
                )
                cur.execute(
                    "INSERT INTO vgt_client_schedule_periods "
                    "(client_id, schedule_id, valid_from, valid_to, "
                    " previous_schedule_id, changed_by, note) "
                    "VALUES (%s, %s, %s, NULL, %s, %s, %s)",
                    (client_id, new_id, effective, default_id, changed_by, note),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)

        return ok({
            "message": "Client added",
            "client_name": client_name,
            "schedule_key": sk,
            "effective_from": effective.isoformat(),
        })
    except Exception as e:
        return error(500, str(e))


# The category list is closed on purpose. These are the six headings the rate
# workbook publishes, and the costed-ticket PDF groups line items by them, so a
# typo here would silently create a seventh group that renders as its own
# section. Adding a category is a deliberate change, not a side effect of a
# rename.
RATE_CATEGORIES = {
    "PERSONNEL",
    "EQUIPMENT",
    "VEHICLES & TRANSPORT",
    "DISBURSEMENTS",
    "PLANS",
    "FIXED DAY RATES",
}


@bp.route(route="rates/items/{slug}", methods=["PUT"])
@require_api_key
def update_rate_item(req: func.HttpRequest) -> func.HttpResponse:
    """
    PUT /api/rates/items/{slug} - rename a rate item or move it to another
    category.

    The slug is the item's identity and is never changed here. VG-Time's
    SERVICE_ITEMS table joins to vgt_rate_items.slug to cost a ticket, so
    re-slugging on rename would silently break costing for every item that
    referenced the old value. Only the display label and category move.
    """
    try:
        slug = req.route_params.get("slug")
        data = req.get_json()

        item = query("SELECT id, line_item, category FROM vgt_rate_items WHERE slug = %s", (slug,))
        if not item:
            return not_found("Item")

        line_item = data.get("lineItem", item[0]["line_item"])
        category = data.get("category", item[0]["category"])

        line_item = str(line_item or "").strip()
        if not line_item:
            return error(400, "lineItem cannot be empty")

        category = str(category or "").strip().upper()
        if category not in RATE_CATEGORIES:
            return error(
                400,
                f"Unknown category {category!r}. Expected one of: "
                + ", ".join(sorted(RATE_CATEGORIES)),
            )

        # A duplicate label is not a hard error — two schedules can legitimately
        # carry similarly named lines — but an exact collision makes the grid
        # ambiguous, so reject it rather than let two rows look identical.
        clash = query(
            "SELECT slug FROM vgt_rate_items WHERE lower(line_item) = lower(%s) AND slug <> %s",
            (line_item, slug),
        )
        if clash:
            return error(409, f"Another item already uses that name (slug {clash[0]['slug']})")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE vgt_rate_items SET line_item = %s, category = %s WHERE slug = %s",
                    (line_item, category, slug),
                )
            conn.commit()
        finally:
            release_connection(conn)

        return ok({"slug": slug, "lineItem": line_item, "category": category})
    except Exception as e:
        logging.exception("update_rate_item failed")
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

        # A schedule that priced any client's past work is part of that
        # history; the foreign key would refuse the delete with a bare 500.
        try:
            used = query(
                "SELECT DISTINCT c.client_name FROM vgt_client_schedule_periods p "
                "JOIN vgt_client_schedule_map c ON c.id = p.client_id "
                "WHERE p.schedule_id = %s OR p.previous_schedule_id = %s "
                "ORDER BY c.client_name",
                (sid, sid),
            )
        except Exception:  # noqa: BLE001 - no periods table, no history to guard
            used = []
        if used:
            names = [c["client_name"] for c in used]
            return error(
                409,
                "Schedule priced past work for "
                f"{len(names)} client(s): {', '.join(names)}. "
                "It is kept so that history still reads correctly.",
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
