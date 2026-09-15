"""
routes/etl.py
Blueprints for ETL management: status, summary, errors, trigger.
"""

import logging
import azure.functions as func
from shared.db import (
    query, get_connection, release_connection, require_api_key,
    ok, not_found,
)

bp = func.Blueprint()

# The June-to-September 2026 forms outage: the nightly run reported success
# every night while /formtypes looped and no form landed for three months.
# Watching whether the ETL *ran* cannot catch that. Watching whether data
# *arrived* can. 72 h rides over a normal weekend without a false alarm.
FORMS_MAX_AGE_HOURS = 72


def freshness_of(latest, now, max_age_hours=FORMS_MAX_AGE_HOURS) -> dict:
    """Describe how old the newest row is. `latest` may be None (empty table)."""
    if latest is None:
        return {"latest_created_on": None, "age_hours": None,
                "max_age_hours": max_age_hours, "stale": True}
    age = (now - latest).total_seconds() / 3600
    return {"latest_created_on": latest.isoformat(),
            "age_hours": round(age, 1),
            "max_age_hours": max_age_hours,
            "stale": age > max_age_hours}


def _forms_freshness() -> dict:
    from datetime import datetime, timezone
    row = query("SELECT MAX(created_on) AS latest FROM forms")
    latest = row[0]["latest"] if row else None
    return {"forms": freshness_of(latest, datetime.now(timezone.utc))}



@bp.route(route="etl/status", methods=["GET"])
@require_api_key
def get_etl_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/status
    Returns last sync timestamps, current record counts, and delta vs yesterday.
    Also saves today's snapshot for tomorrow's comparison.
    """
    from datetime import datetime, timezone

    tables = ["locations", "companies", "workers", "worker_locations",
              "certification_types", "certifications",
              "form_types", "forms", "time_tickets"]

    sync_state = query("""
        SELECT entity, last_sync, last_count, updated_at
        FROM etl_sync_state ORDER BY entity
    """)

    counts = {}
    for tbl in tables:
        try:
            result = query(f"SELECT COUNT(*) AS n FROM {tbl}")
            counts[tbl] = result[0]["n"] if result else 0
        except Exception:
            counts[tbl] = None

    today_iso = datetime.now(timezone.utc).date().isoformat()
    prev = query(
        "SELECT entity, record_count FROM etl_daily_snapshot "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM etl_daily_snapshot "
        "WHERE snapshot_date < %s)",
        (today_iso,))
    prev_counts = {r["entity"]: r["record_count"] for r in prev}

    deltas = {}
    for tbl in tables:
        curr = counts.get(tbl)
        prev_val = prev_counts.get(tbl)
        if curr is not None and prev_val is not None:
            deltas[tbl] = curr - prev_val
        else:
            deltas[tbl] = None

    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for tbl, cnt in counts.items():
                if cnt is not None:
                    cur.execute("""
                        INSERT INTO etl_daily_snapshot (snapshot_date, entity, record_count)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (snapshot_date, entity) DO UPDATE
                        SET record_count = EXCLUDED.record_count, created_at = NOW()
                    """, (today, tbl, cnt))
        conn.commit()
    except Exception as e:
        logging.warning(f"Failed to save daily snapshot: {e}")
    finally:
        release_connection(conn)

    return ok({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sync_state": sync_state,
        "record_counts": counts,
        "new_since_yesterday": deltas,
        "freshness": _forms_freshness(),
    })


@bp.route(route="etl/summary", methods=["GET"])
@require_api_key
def get_etl_summary(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/summary
    One-shot digest for Power Automate / email reports.
    """
    from datetime import datetime, timezone

    tables = ["locations", "companies", "workers", "worker_locations",
              "certification_types", "certifications",
              "form_types", "forms", "time_tickets"]

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
        "freshness": _forms_freshness(),
        "errors": {
            "unresolved_total": total_unresolved,
            "by_stage": error_counts,
            "recent": recent_errors,
        },
    })


@bp.route(route="etl/errors", methods=["GET"])
@require_api_key
def get_etl_errors(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/etl/errors
    Query params: stage, resolved (default false), limit (default 50, max 500)
    """
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


@bp.route(route="etl/errors/{id}/resolve", methods=["POST"])
@require_api_key
def resolve_etl_error(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/etl/errors/{id}/resolve - mark an error as resolved."""
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
        release_connection(conn)


@bp.route(route="etl/trigger", methods=["POST"])
@require_api_key
def manual_etl_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/etl/trigger
    Manually trigger the ETL. Optionally pass {"only": "workers"} in body.
    """
    import subprocess
    import sys
    import threading

    try:
        body = req.get_json() or {}
    except Exception:
        body = {}

    only = body.get("only")
    mode = body.get("mode", "all")
    cmd = [sys.executable, "-m", "etl.run_etl"]
    if only:
        cmd += ["--only", only]
    if mode in ("new", "all"):
        cmd += ["--mode", mode]

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
