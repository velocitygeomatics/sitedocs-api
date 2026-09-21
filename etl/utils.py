"""
etl/utils.py
Shared utilities for all SiteDocs ETL scripts.
"""

import os
import time
import logging
import json
import threading
from datetime import datetime, timezone
from typing import Optional

# Load .env for local dev before anything reads os.environ. override=True
# ensures .env wins over stale shell vars (e.g. leftover $env:POSTGRES_HOST
# from a previous PowerShell session). In Azure there's no .env file so this
# is a silent no-op.
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    # python-dotenv is in requirements.txt; warn loudly if missing locally
    import sys
    print(
        "WARNING: python-dotenv not installed — .env will NOT be loaded.\n"
        "Run: pip install python-dotenv",
        file=sys.stderr,
    )

import requests
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    """Read an env var; fail fast with a clear message if missing or a
    known placeholder value from .env.example."""
    val = os.environ.get(name, "").strip()
    placeholders = {"", "your-host", "your-db", "your-user", "your-password",
                    "replace-me", "your-api-key", "your-token"}
    if val.lower() in placeholders:
        raise RuntimeError(
            f"{name} is not set (or still has a placeholder value). "
            f"Edit .env at the project root and fill in a real value."
        )
    return val

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE     = "https://api-1.sitedocs.com/api/v1"
PAGE_SIZE    = 100
# Ceiling on pages per endpoint. The largest entity on file is worker_locations at
# ~20k rows (201 pages); 1000 leaves room to grow while still bounding a runaway.
MAX_PAGES    = 1000
RETRY_LIMIT  = 4
RATE_LIMIT_S = 0.5   # seconds between calls

_rate_lock = threading.Lock()
_last_request_time = 0.0


def get_token() -> str:
    return _require_env("SITEDOCS_API_TOKEN")


def get_db_conn():
    return psycopg2.connect(
        host     = _require_env("POSTGRES_HOST"),
        dbname   = _require_env("POSTGRES_DB"),
        user     = _require_env("POSTGRES_USER"),
        password = _require_env("POSTGRES_PASSWORD"),
        port     = int(os.environ.get("POSTGRES_PORT", 5432)),
        sslmode  = "require",
    )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None) -> list | dict:
    global _last_request_time
    url = f"{API_BASE}{path}"
    headers = {"Authorization": get_token(), "Accept": "application/json"}

    for attempt in range(RETRY_LIMIT):
        # Serialize rate-limiting across threads so 10 threads don't
        # fire 10 requests simultaneously despite RATE_LIMIT_S.
        with _rate_lock:
            elapsed = time.time() - _last_request_time
            if elapsed < RATE_LIMIT_S:
                time.sleep(RATE_LIMIT_S - elapsed)
            _last_request_time = time.time()

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 2 ** attempt * 5
                log.warning(f"Rate limited on {path}, waiting {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code == 404:
                return None

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.Timeout:
            log.warning(f"Timeout on {path} (attempt {attempt+1})")
            time.sleep(2 ** attempt)

    raise RuntimeError(f"API call failed after {RETRY_LIMIT} attempts: {path}")


def paginate(path: str, extra_params: dict = None) -> list:
    """Fetch all pages for a list endpoint."""
    results = []
    page = 0
    params = {**(extra_params or {}), "count": PAGE_SIZE}

    seen = set()

    while page < MAX_PAGES:
        params["page"] = page
        batch = api_get(path, params)

        if not batch:
            break

        # A short final page is how this API says "that was the last one". When an
        # endpoint stops honouring that contract it hands back a full page forever
        # and the loop never ends -- /formtypes did exactly that from June 2026,
        # reaching page 4040 of a 99-row entity before the vendor dropped the
        # connection, and the exception killed the whole forms stage nightly for
        # three months. Repeated ids are the reliable signal that paging has
        # stopped advancing, so stop on them regardless of page length.
        new_rows = [r for r in batch if r.get("Id") not in seen]
        seen.update(r.get("Id") for r in batch if r.get("Id") is not None)

        if not new_rows:
            log.warning(f"{path} page {page} repeated rows already seen -- "
                        f"endpoint is not advancing; stopping at {len(results)} rows")
            break

        results.extend(new_rows)
        log.debug(f"{path} page {page}: {len(batch)} rows ({len(new_rows)} new)")

        if len(batch) < PAGE_SIZE:
            break

        page += 1
    else:
        log.warning(f"{path} hit the {MAX_PAGES}-page ceiling; "
                    f"returning {len(results)} rows and moving on")

    return results


# ---------------------------------------------------------------------------
# Sync state — tracks last successful run per entity
# ---------------------------------------------------------------------------

ENSURE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS etl_sync_state (
    entity      TEXT PRIMARY KEY,
    last_sync   TIMESTAMPTZ,
    last_count  INT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

def get_last_sync(conn, entity: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT last_sync FROM etl_sync_state WHERE entity = %s", (entity,))
        row = cur.fetchone()
        if row and row[0]:
            return row[0].isoformat()
    return None


def set_last_sync(conn, entity: str, count: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO etl_sync_state (entity, last_sync, last_count, updated_at)
            VALUES (%s, NOW(), %s, NOW())
            ON CONFLICT (entity) DO UPDATE
            SET last_sync = NOW(), last_count = %s, updated_at = NOW()
        """, (entity, count, count))
    conn.commit()


ENSURE_ERRORS_TABLE = """
CREATE TABLE IF NOT EXISTS etl_errors (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ DEFAULT NOW(),
    stage           TEXT NOT NULL,
    entity_id       TEXT,
    error_type      TEXT,
    error_message   TEXT,
    payload         JSONB,
    resolved        BOOLEAN DEFAULT FALSE
);
"""


ENSURE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id      UUID        PRIMARY KEY,
    stage       TEXT        NOT NULL,
    trigger     TEXT        NOT NULL DEFAULT 'cli',
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    duration_s  NUMERIC(10,1),
    status      TEXT        NOT NULL,
    error       TEXT,
    entities    JSONB       NOT NULL DEFAULT '{}'::jsonb
);
"""

ENSURE_RUNS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_etl_runs_started_at ON etl_runs (started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_etl_runs_stage_started_at ON etl_runs (stage, started_at DESC);",
)


def ensure_state_table(conn):
    with conn.cursor() as cur:
        cur.execute(ENSURE_STATE_TABLE)
        cur.execute(ENSURE_ERRORS_TABLE)
        cur.execute(ENSURE_RUNS_TABLE)
        for stmt in ENSURE_RUNS_INDEXES:
            cur.execute(stmt)
    conn.commit()


def snapshot_sync_state(conn) -> dict:
    """entity -> (last_sync, last_count) for every row in etl_sync_state.

    Taken either side of a stage so the run log can record what the stage
    actually stamped. The stage functions return None and several of them
    (lookups, companies) touch more than one entity, so diffing the state
    table is the only honest source for this.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT entity, last_sync, last_count FROM etl_sync_state")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def diff_sync_state(before: dict, after: dict) -> dict:
    """entity -> count, for entities whose last_sync moved between snapshots.

    An entity the stage re-stamped with the same count still counts as
    touched, because last_sync advances; one it never reached does not appear
    at all. Both are more useful than a single number for the whole stage.
    """
    touched = {}
    for entity, (last_sync, count) in after.items():
        prior = before.get(entity)
        if prior is None or prior[0] != last_sync:
            touched[entity] = count
    return touched


def record_etl_run(conn, run_id, stage: str, trigger: str, started_at,
                   finished_at, status: str, error: str = None,
                   entities: dict = None) -> bool:
    """Write one stage's outcome to etl_runs. Best-effort.

    The sync itself is the operative fact and is already committed by the time
    this runs, so a failure to log must not fail the stage - losing a sync to a
    history write would be the worse outcome. Returns whether the row landed.
    """
    try:
        duration = (finished_at - started_at).total_seconds()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etl_runs (run_id, stage, trigger, started_at,
                                      finished_at, duration_s, status, error, entities)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (
                str(run_id), stage, trigger, started_at, finished_at,
                round(duration, 1), status,
                error[:2000] if error else None,
                json.dumps(entities or {}),
            ))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"Failed to record etl_run for stage {stage}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def log_etl_error(conn, stage: str, entity_id: str, error: Exception,
                  payload: dict = None):
    """Persist a failed row to etl_errors for later troubleshooting."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etl_errors (stage, entity_id, error_type, error_message, payload)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                stage,
                str(entity_id) if entity_id else None,
                type(error).__name__,
                str(error)[:2000],
                json.dumps(payload, default=str) if payload else None,
            ))
        conn.commit()
    except Exception as e:
        log.warning(f"Failed to log ETL error to DB: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

def upsert(conn, table: str, rows: list[dict], conflict_col: str = "id"):
    """Resilient batch upsert. On batch failure: rolls back, retries
    row-by-row, and writes each failed row to etl_errors. Returns the
    count of successfully upserted rows."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    col_str  = ", ".join(cols)
    val_str  = ", ".join(f"%({c})s" for c in cols)
    update_str = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols if c != conflict_col
    )

    sql = f"""
        INSERT INTO {table} ({col_str})
        VALUES ({val_str})
        ON CONFLICT ({conflict_col}) DO UPDATE SET {update_str}
    """

    # Fast path: batch upsert
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        log.warning(f"upsert({table}): batch of {len(rows)} failed ({e}); "
                    f"retrying row-by-row")

    # Slow path: one at a time so a single bad row doesn't lose the batch
    ok = 0
    for row in rows:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, row)
            conn.commit()
            ok += 1
        except Exception as e:
            conn.rollback()
            entity_id = row.get(conflict_col) or row.get("id")
            log.error(f"upsert({table}) failed for {conflict_col}={entity_id}: {e}")
            log_etl_error(conn, table, entity_id, e, row)
    return ok


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_utc(ts):
    """Stamp a vendor timestamp as UTC before it reaches a timestamptz column.

    SiteDocs sends timestamps with no zone at all ("2026-08-28T13:11:00.703");
    they are UTC. Postgres reads a naive string into a timestamptz using the
    session time zone, which here is America/Edmonton, so every vendor
    timestamp was stored 6-7 hours ahead of the real instant and read back
    that far in the future.

    Values that already carry a zone are returned untouched, and so are
    date-only values: "2026-01-01" has no time to reinterpret, and forcing it
    to UTC midnight would render as the previous day in Mountain time.
    """
    if not ts or not isinstance(ts, str):
        return ts
    if "T" not in ts and " " not in ts.strip():
        return ts                      # date-only, nothing to place in a zone
    try:
        parsed = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return ts                      # not a timestamp we recognise
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()
