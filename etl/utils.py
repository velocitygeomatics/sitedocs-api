"""
etl/utils.py
Shared utilities for all SiteDocs ETL scripts.
"""

import os
import re
import time
import logging
import json
from datetime import datetime, timedelta, timezone
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
RETRY_LIMIT  = 4
RATE_LIMIT_S = 0.5   # seconds between calls


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


# ---------------------------------------------------------------------------
# Date helpers — shared across ETL stages.
#
# SiteDocs's DatePicker serializes picked dates as the user's locale string
# (e.g. "January 6, 2026" or "06-Jan-26") instead of ISO. These helpers accept
# every locale format dateutil can parse, and fall back to label parsing or
# signature timestamp for forms that omit the Date item entirely.
#
# Returns ISO YYYY-MM-DD strings (safe for Postgres DATE columns) or None.
# ---------------------------------------------------------------------------

_ISO_IN_LABEL       = re.compile(r'\b(20\d{2}-\d{2}-\d{2})\b')
_MMDDYYYY_IN_LABEL  = re.compile(r'\b(\d{2})(\d{2})(20\d{2})\b')
_YEAR_IN_LABEL      = re.compile(r'\b(20\d{2})\b')


def parse_date(val: Optional[str], fallback_year: Optional[int] = None) -> Optional[str]:
    """
    Strict ISO first; else tolerant locale parse via dateutil.

    Handles: '2026-01-06', 'January 6, 2026', 'Feb 22,2026', '06-Jan-26',
             '1/6/2026', 'January 30th, 2026', 'Feb 3/26', 'March10, 2026'.

    If the value has no year (e.g. 'March 4') and fallback_year is supplied,
    we use it. Otherwise we reject ambiguous no-year values (return None) so
    callers can fall back to label or signature timestamp.
    """
    if not val:
        return None
    s = str(val).strip().rstrip('.')
    if not s:
        return None

    # Already ISO?
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass

    # Tolerant parse via dateutil. Use a sentinel default year (1900) so we
    # can detect "no year in input" cases and fail them out.
    try:
        from dateutil import parser as dp
    except ImportError:
        log.warning("python-dateutil not installed; parse_date() can only handle strict ISO")
        return None

    # Pre-clean common SiteDocs-user typos: 'Feb 22,2026' (no space after comma),
    # 'March10, 2026' (no space after month name).
    cleaned = re.sub(r',(\d)', r', \1', s)
    cleaned = re.sub(r'([A-Za-z])(\d)', r'\1 \2', cleaned)

    sentinel_year = fallback_year or 1900
    try:
        d = dp.parse(cleaned, default=datetime(sentinel_year, 1, 1))
    except (ValueError, TypeError, OverflowError):
        return None

    # If dateutil used our sentinel year AND no fallback was provided, the
    # input didn't carry a year — refuse rather than guess silently.
    if d.year == 1900 and fallback_year is None:
        return None
    return d.date().isoformat()


def date_from_label(label: Optional[str]) -> Optional[str]:
    """
    Extract YYYY-MM-DD from a form label when the Date field is missing.
    Handles common label patterns like:
        2026-04-07-JSL-DFT
        260095-JS-04072026-FT
    """
    if not label:
        return None
    m = _ISO_IN_LABEL.search(label)
    if m:
        return m.group(1)
    m = _MMDDYYYY_IN_LABEL.search(label)
    if m:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        try:
            datetime(int(yyyy), int(mm), int(dd))
            return f"{yyyy}-{mm}-{dd}"
        except ValueError:
            return None
    return None


def year_from_label(label: Optional[str]) -> Optional[int]:
    """Pull a 4-digit year from a label, useful as parse_date()'s fallback_year."""
    if not label:
        return None
    m = _YEAR_IN_LABEL.search(label)
    return int(m.group(1)) if m else None


def date_from_submitted_on(form: dict, offset_days: int = -1) -> Optional[str]:
    """
    Last-resort proxy for work date: the form's CreatedOn minus 1 day.
    Field workers typically sign the morning after the work was done.
    Returns ISO YYYY-MM-DD or None.
    """
    raw = form.get("CreatedOn") or form.get("SubmittedOn")
    if not raw:
        return None
    try:
        # SiteDocs sometimes returns no tz suffix; fromisoformat handles both
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (d + timedelta(days=offset_days)).date().isoformat()
    except (ValueError, AttributeError):
        return None


def resolve_ticket_date(form: dict, raw_value: Optional[str]) -> tuple[Optional[str], str]:
    """
    Apply the full fallback chain and report where the date came from.

    Returns (iso_date_or_None, source) where source is one of:
      'exact'     — parse_date() succeeded on the raw field value
      'label'     — recovered from the form label regex
      'inferred'  — fell back to signature timestamp minus 1 day
      'unknown'   — nothing worked (returns None, 'unknown')
    """
    label = form.get("Label") or ""

    # 1. Try the field value exactly
    iso = parse_date(raw_value)
    if iso:
        return iso, "exact"

    # 2. Try parse_date again with year hint from the label (catches 'March 4')
    yr = year_from_label(label)
    if yr:
        iso = parse_date(raw_value, fallback_year=yr)
        if iso:
            return iso, "label"   # year came from label

    # 3. Label regex
    iso = date_from_label(label)
    if iso:
        return iso, "label"

    # 4. Signature timestamp - 1 day
    iso = date_from_submitted_on(form)
    if iso:
        return iso, "inferred"

    return None, "unknown"
