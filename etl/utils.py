"""
etl/utils.py
Shared utilities for all SiteDocs ETL scripts.
"""

import os
import time
import logging
import json
from datetime import datetime, timezone
from typing import Optional

import requests
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE     = "https://api-1.sitedocs.com/api/v1"
PAGE_SIZE    = 100
RETRY_LIMIT  = 4
RATE_LIMIT_S = 0.5   # seconds between calls


def get_token() -> str:
    return os.environ["SITEDOCS_API_TOKEN"]


def get_db_conn():
    return psycopg2.connect(
        host     = os.environ["POSTGRES_HOST"],
        dbname   = os.environ["POSTGRES_DB"],
        user     = os.environ["POSTGRES_USER"],
        password = os.environ["POSTGRES_PASSWORD"],
        port     = int(os.environ.get("POSTGRES_PORT", 5432)),
        sslmode  = "require",
    )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None) -> list | dict:
    url = f"{API_BASE}{path}"
    headers = {"Authorization": get_token(), "Accept": "application/json"}

    for attempt in range(RETRY_LIMIT):
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
            time.sleep(RATE_LIMIT_S)
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

    while True:
        params["page"] = page
        batch = api_get(path, params)

        if not batch:
            break

        results.extend(batch)
        log.debug(f"{path} page {page}: {len(batch)} rows")

        if len(batch) < PAGE_SIZE:
            break

        page += 1

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


def ensure_state_table(conn):
    with conn.cursor() as cur:
        cur.execute(ENSURE_STATE_TABLE)
    conn.commit()


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

def upsert(conn, table: str, rows: list[dict], conflict_col: str = "id"):
    if not rows:
        return

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

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
