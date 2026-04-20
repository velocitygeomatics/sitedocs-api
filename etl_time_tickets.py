"""
etl_time_tickets.py
Fetches all Time Ticket forms from SiteDocs and populates the time_tickets table.

Form Type ID: 6c3f93b6-1326-478b-a6d6-59aba925a1c1

Run standalone or import sync_time_tickets() into your main ETL.
"""

import os
import re
import logging
import time
import json
from datetime import datetime, timezone
from typing import Optional

import requests
import psycopg2
import psycopg2.extras

# Load .env for local dev (no-op in Azure). override=True so .env wins
# over any stale shell env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}'
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FORM_TYPE_ID    = "6c3f93b6-1326-478b-a6d6-59aba925a1c1"
API_BASE        = "https://api-1.sitedocs.com/api/v1"
PAGE_SIZE       = 100
RATE_LIMIT_WAIT = 1.0   # seconds between API calls


def _get_token() -> str:
    token = os.environ.get("SITEDOCS_API_TOKEN")
    if not token:
        raise RuntimeError("SITEDOCS_API_TOKEN is not set — add it to your .env or environment")
    return token


def _get_db_conn_kwargs() -> dict:
    required = ["POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return dict(
        host     = os.environ["POSTGRES_HOST"],
        dbname   = os.environ["POSTGRES_DB"],
        user     = os.environ["POSTGRES_USER"],
        password = os.environ["POSTGRES_PASSWORD"],
        port     = int(os.environ.get("POSTGRES_PORT", 5432)),
        sslmode  = "require",
    )


def _get_headers() -> dict:
    return {
        "Authorization": _get_token(),
        "Accept": "application/json",
    }

def _log_error(conn, entity_id, error, payload=None):
    """Persist failed row to etl_errors table."""
    try:
        from etl.utils import log_etl_error
        log_etl_error(conn, "time_tickets", entity_id, error, payload)
    except Exception as e:
        log.warning(f"Could not persist ETL error: {e}")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None) -> dict | list:
    url = f"{API_BASE}{path}"
    for attempt in range(4):
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt * 5
            log.warning(f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(RATE_LIMIT_WAIT)
        return resp.json()
    raise RuntimeError(f"Failed after retries: {path}")


def fetch_all_time_ticket_forms(since: Optional[str] = None) -> list[dict]:
    """Page through all forms for the Time Ticket form type."""
    forms = []
    page = 0
    while True:
        params = {
            "formTypeId": FORM_TYPE_ID,
            "count": PAGE_SIZE,
            "page": page,
        }
        if since:
            params["submittedSince"] = since

        batch = api_get("/forms", params)
        if not batch:
            break
        forms.extend(batch)
        log.info(f"Fetched page {page}: {len(batch)} forms (total so far: {len(forms)})")
        if len(batch) < PAGE_SIZE:
            break
        page += 1

    return forms


def fetch_form_content(form_id: str) -> dict:
    """Fetch the field-level content for a single form."""
    return api_get(f"/forms/content/{form_id}")


# ---------------------------------------------------------------------------
# Content parser — FIXED
# ---------------------------------------------------------------------------

def _extract_field(content: dict, label: str) -> Optional[str]:
    """
    Walk the Groups → Items tree and find a field by its Content label.
    Strips trailing colon and whitespace from the Content key before comparing.
    Returns the raw value string, or None if not found / empty.
    """
    label_clean = label.strip().rstrip(":").lower()
    for group in content.get("Groups", []):
        for item in group.get("Items", []):
            raw_content = item.get("Content") or ""
            # Content may be a JSON string for complex fields e.g. {"Label":"ATV/UTV/Snowmobile:","ListId":"..."}
            if raw_content.startswith("{"):
                try:
                    parsed = json.loads(raw_content)
                    item_label = (parsed.get("Label") or "").strip().rstrip(":").lower()
                except Exception:
                    item_label = raw_content.strip().rstrip(":").lower()
            else:
                item_label = raw_content.strip().rstrip(":").lower()

            if item_label == label_clean:
                val = item.get("Value")
                if val is None:
                    return None
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val if v)
                val = str(val).strip()
                return val if val and val.lower() != "no response" else None
    return None


def _extract_worker_name(val: Optional[str]) -> Optional[str]:
    """
    Worker fields (Type=12/20) store a JSON array of worker objects:
    [{"Id":"...","FirstName":"JS","LastName":"Lomness","JobTitle":"..."}]
    Returns "FirstName LastName" of the first worker, or plain string if not JSON.
    """
    if not val:
        return None
    val = val.strip()
    if val.startswith("["):
        try:
            workers = json.loads(val)
            if workers and isinstance(workers, list):
                w = workers[0]
                first = (w.get("FirstName") or "").strip()
                last  = (w.get("LastName")  or "").strip()
                name  = f"{first} {last}".strip()
                return name if name else None
        except Exception:
            pass
    return val if val else None


def _extract_list_selection(val: Optional[str]) -> Optional[str]:
    """
    Dropdown/checklist fields store a JSON object with Contents array.
    Returns the title of the selected item(s), or None if "None" selected.
    """
    if not val:
        return None
    val = val.strip()
    if val.startswith("{"):
        try:
            obj = json.loads(val)
            selected = [c["title"] for c in obj.get("Contents", []) if c.get("selected")]
            result = ", ".join(selected)
            return result if result and result.lower() != "none" else None
        except Exception:
            pass
    return val if val else None


def _num(val: Optional[str]) -> Optional[float]:
    """Convert string to float, return None if blank."""
    if val is None:
        return None
    try:
        v = float(val)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _date(val: Optional[str]) -> Optional[str]:
    """Parse a date string into YYYY-MM-DD for Postgres.
    Handles ISO, natural language ('March 3', 'Jan 22, 2026.', 'Feb 3rd, 2026',
    'Feb 21,2026', 'Feb 4/26', '2026 01 25'). Returns None if unparseable.
    """
    if not val:
        return None
    val = val.strip().rstrip(".")

    # Already ISO — pass through
    if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
        return val

    # Insert missing space after comma: 'Feb 22,2026' → 'Feb 22, 2026'
    val = re.sub(r',(\d)', r', \1', val)
    # Insert missing space between month and day: 'March10, 2026' → 'March 10, 2026'
    val = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', val)

    # Strip ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', val)

    # Try common formats
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                "%B %d", "%b %d", "%m/%d/%Y", "%m-%d-%Y",
                "%d %B %Y", "%d %b %Y", "%Y/%m/%d",
                "%b %d/%y", "%Y %m %d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            # If no year was in the format, infer from current year
            if "%Y" not in fmt and "%y" not in fmt:
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    log.warning(f"Unparseable date value, setting to NULL: '{val}'")
    return None


def _date_from_label(label: Optional[str]) -> Optional[str]:
    """
    Parse a date from a form label as fallback.
    Handles patterns like: 2026-04-07-JSL-DFT, 260095-JS-04072026-FT
    Returns YYYY-MM-DD string or None.
    """
    if not label:
        return None
    # Pattern 1: YYYY-MM-DD at start of label
    m = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', label)
    if m:
        return m.group(1)
    # Pattern 2: MMDDYYYY embedded e.g. 04072026
    m = re.search(r'\b(\d{2})(\d{2})(20\d{2})\b', label)
    if m:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        try:
            datetime(int(yyyy), int(mm), int(dd))
            return f"{yyyy}-{mm}-{dd}"
        except ValueError:
            pass
    return None


def _extract_signature(content: dict) -> tuple[Optional[str], Optional[str], Optional[float], Optional[float]]:
    """
    Extract signature block: returns (name, datetime_str, lat, lng).
    Signatures live in a dedicated Signatures section or as signature-type items.
    """
    for group in content.get("Groups", []):
        group_title = (group.get("Title") or "").lower()
        if "signature" in group_title:
            for item in group.get("Items", []):
                name = item.get("SignerName") or item.get("signerName")
                dt   = item.get("SignedOn")   or item.get("signedOn")
                lat  = item.get("Latitude")   or item.get("latitude")
                lng  = item.get("Longitude")  or item.get("longitude")
                if name:
                    return (
                        str(name),
                        str(dt) if dt else None,
                        _num(str(lat) if lat else None),
                        _num(str(lng) if lng else None),
                    )
    return None, None, None, None


def parse_time_ticket(form: dict, content: dict) -> dict:
    """
    Combine the FormViewModel and Content into a flat time_tickets row.
    """
    f = _extract_field
    label = form.get("Label") or ""

    # Date: try form field first, fall back to parsing the label
    ticket_date = _date(f(content, "Date")) or _date_from_label(label)

    # Worker fields are JSON arrays — extract name
    crew_chief = _extract_worker_name(f(content, "Crew Chief"))
    assistant  = _extract_worker_name(f(content, "Assistant"))

    # Dropdown/checklist fields
    atv_utv    = _extract_list_selection(f(content, "ATV/UTV/Snowmobile"))
    cc_subs    = _extract_list_selection(f(content, "Crew Chief Subsistence"))
    sa_subs    = _extract_list_selection(f(content, "SA Subsistence"))

    return {
        "form_id":              form["Id"],
        "form_label":           label,
        "location_id":          form.get("LocationId"),
        "location_name":        None,               # enriched by JOIN at query time
        "submitted_on":         form.get("CreatedOn"),
        "is_deleted":           form.get("IsDeleted", False),

        # General Information
        "ticket_date":          ticket_date,
        "client":               f(content, "Client"),
        "client_field_rep":     f(content, "Client Field Representative"),
        "job_no":               f(content, "Job No"),
        "wellsite_location":    f(content, "Location"),
        "project_manager":      f(content, "Project Manager"),
        "crew_chief":           crew_chief,
        "assistant":            assistant,

        # Equipment
        "survey_equipment_day": _num(f(content, "Survey Equipment (Day)")),
        "pipe_locator_hrs":     _num(f(content, "Pipe Locator (hrs)")),
        "chainsaw_hrs":         _num(f(content, "Chainsaw (hrs)")),
        "jackhammer_hrs":       _num(f(content, "Jackhammer (hrs)")),
        "truck_km":             _num(f(content, "Truck km")),
        "truck_hours":          _num(f(content, "Truck hours")),
        "atv_utv_snowmobile":   atv_utv,
        "marker_posts":         _num(f(content, "Marker Posts")),
        "iron_posts":           _num(f(content, "Iron Posts")),

        # Crew Chief Labour
        "cc_travel_hrs":        _num(f(content, "Crew Chief Travel")),
        "cc_work_hrs":          _num(f(content, "Crew Chief Work")),
        "cc_notes_hrs":         _num(f(content, "Crew Chief Notes")),
        "cc_total_hrs":         _num(f(content, "Crew Chief Total")),
        "cc_subsistence":       cc_subs,

        # Assistant Labour
        "sa_travel_hrs":        _num(f(content, "SA Travel")),
        "sa_work_hrs":          _num(f(content, "SA Work")),
        "sa_total_hrs":         _num(f(content, "SA Total")),
        "sa_subsistence":       sa_subs,

        # Notes / Approval
        "details":              f(content, "Details"),
        "approval":             f(content, "Approval"),
        "approval_date":        _date(f(content, "Approval Date")),

        # Signature — populated by UPDATE from form_signatures after sync
        "signed_by":            None,
        "signed_on":            None,
        "signature_lat":        None,
        "signature_lng":        None,

        "etl_synced_on":        datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Database upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO time_tickets (
    form_id, form_label, location_id, location_name, submitted_on, is_deleted,
    ticket_date, client, client_field_rep, job_no, wellsite_location,
    project_manager, crew_chief, assistant,
    survey_equipment_day, pipe_locator_hrs, chainsaw_hrs, jackhammer_hrs,
    truck_km, truck_hours, atv_utv_snowmobile, marker_posts, iron_posts,
    cc_travel_hrs, cc_work_hrs, cc_notes_hrs, cc_total_hrs, cc_subsistence,
    sa_travel_hrs, sa_work_hrs, sa_total_hrs, sa_subsistence,
    details, approval, approval_date,
    signed_by, signed_on, signature_lat, signature_lng,
    etl_synced_on
)
VALUES (
    %(form_id)s, %(form_label)s, %(location_id)s, %(location_name)s,
    %(submitted_on)s, %(is_deleted)s,
    %(ticket_date)s, %(client)s, %(client_field_rep)s, %(job_no)s,
    %(wellsite_location)s, %(project_manager)s, %(crew_chief)s, %(assistant)s,
    %(survey_equipment_day)s, %(pipe_locator_hrs)s, %(chainsaw_hrs)s,
    %(jackhammer_hrs)s, %(truck_km)s, %(truck_hours)s, %(atv_utv_snowmobile)s,
    %(marker_posts)s, %(iron_posts)s,
    %(cc_travel_hrs)s, %(cc_work_hrs)s, %(cc_notes_hrs)s, %(cc_total_hrs)s,
    %(cc_subsistence)s,
    %(sa_travel_hrs)s, %(sa_work_hrs)s, %(sa_total_hrs)s, %(sa_subsistence)s,
    %(details)s, %(approval)s, %(approval_date)s,
    %(signed_by)s, %(signed_on)s, %(signature_lat)s, %(signature_lng)s,
    %(etl_synced_on)s
)
ON CONFLICT (form_id) DO UPDATE SET
    form_label           = EXCLUDED.form_label,
    location_id          = EXCLUDED.location_id,
    submitted_on         = EXCLUDED.submitted_on,
    is_deleted           = EXCLUDED.is_deleted,
    ticket_date          = EXCLUDED.ticket_date,
    client               = EXCLUDED.client,
    client_field_rep     = EXCLUDED.client_field_rep,
    job_no               = EXCLUDED.job_no,
    wellsite_location    = EXCLUDED.wellsite_location,
    project_manager      = EXCLUDED.project_manager,
    crew_chief           = EXCLUDED.crew_chief,
    assistant            = EXCLUDED.assistant,
    survey_equipment_day = EXCLUDED.survey_equipment_day,
    pipe_locator_hrs     = EXCLUDED.pipe_locator_hrs,
    chainsaw_hrs         = EXCLUDED.chainsaw_hrs,
    jackhammer_hrs       = EXCLUDED.jackhammer_hrs,
    truck_km             = EXCLUDED.truck_km,
    truck_hours          = EXCLUDED.truck_hours,
    atv_utv_snowmobile   = EXCLUDED.atv_utv_snowmobile,
    marker_posts         = EXCLUDED.marker_posts,
    iron_posts           = EXCLUDED.iron_posts,
    cc_travel_hrs        = EXCLUDED.cc_travel_hrs,
    cc_work_hrs          = EXCLUDED.cc_work_hrs,
    cc_notes_hrs         = EXCLUDED.cc_notes_hrs,
    cc_total_hrs         = EXCLUDED.cc_total_hrs,
    cc_subsistence       = EXCLUDED.cc_subsistence,
    sa_travel_hrs        = EXCLUDED.sa_travel_hrs,
    sa_work_hrs          = EXCLUDED.sa_work_hrs,
    sa_total_hrs         = EXCLUDED.sa_total_hrs,
    sa_subsistence       = EXCLUDED.sa_subsistence,
    details              = EXCLUDED.details,
    approval             = EXCLUDED.approval,
    approval_date        = EXCLUDED.approval_date,
    signed_by            = EXCLUDED.signed_by,
    signed_on            = EXCLUDED.signed_on,
    signature_lat        = EXCLUDED.signature_lat,
    signature_lng        = EXCLUDED.signature_lng,
    etl_synced_on        = EXCLUDED.etl_synced_on;
"""


def upsert_batch(conn, rows: list[dict]):
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=50)
    conn.commit()


def upsert_one(conn, row: dict) -> bool:
    """Upsert a single row. Returns True on success, False on failure
    (and rolls back so the connection stays usable)."""
    try:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, row)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"Row upsert failed for form {row.get('form_id')}: {e}")
        _log_error(conn, row.get("form_id"), e, row)
        return False


def flush_batch(conn, batch: list[dict]) -> int:
    """Flush a batch; fall back to row-by-row on failure so one bad row
    doesn't poison the whole batch or leave the connection aborted.
    Returns the number of rows successfully upserted."""
    if not batch:
        return 0
    try:
        upsert_batch(conn, batch)
        return len(batch)
    except Exception as e:
        conn.rollback()
        log.warning(f"Batch of {len(batch)} failed ({e}); retrying row-by-row")
        return sum(1 for row in batch if upsert_one(conn, row))


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_time_tickets(since: Optional[str] = None):
    """
    Build time_tickets from form_contents table (no API calls).
    form_contents must be synced first via sync_form_contents stage.
    """
    log.info("Starting time ticket sync (from form_contents cache)...")

    conn = psycopg2.connect(**_get_db_conn_kwargs())

    # Load all time ticket forms + their cached content in one query
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                f.id AS "Id", f.label AS "Label", f.location_id AS "LocationId",
                f.created_on AS "CreatedOn", f.is_deleted AS "IsDeleted",
                fc.raw_content
            FROM forms f
            JOIN form_contents fc ON fc.form_id = f.id
            WHERE f.document_template_id = %s
              AND f.is_deleted = false
            ORDER BY f.created_on
        """, (FORM_TYPE_ID,))
        rows = cur.fetchall()

    log.info(f"Found {len(rows)} time ticket forms in form_contents cache")

    batch = []
    errors = 0
    upserted = 0

    for i, db_row in enumerate(rows):
        form_id = str(db_row["Id"])
        try:
            form = {
                "Id":         form_id,
                "Label":      db_row["Label"],
                "LocationId": str(db_row["LocationId"]) if db_row["LocationId"] else None,
                "CreatedOn":  db_row["CreatedOn"].isoformat() if db_row["CreatedOn"] else None,
                "IsDeleted":  db_row["IsDeleted"],
            }
            content = db_row["raw_content"]
            if isinstance(content, str):
                content = json.loads(content)
            row = parse_time_ticket(form, content)
            batch.append(row)

            if len(batch) >= 50:
                upserted += flush_batch(conn, batch)
                log.info(f"Processed {i+1}/{len(rows)} forms (upserted={upserted})")
                batch = []

        except Exception as e:
            log.error(f"Failed form {form_id}: {e}")
            errors += 1
            _log_error(conn, form_id, e)
            try:
                conn.rollback()
            except Exception:
                pass
            continue

    if batch:
        upserted += flush_batch(conn, batch)

    # Populate signature fields from form_signatures table
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE time_tickets tt
                SET signed_by     = fs.signatory_first_name || ' ' || fs.signatory_last_name,
                    signed_on     = fs.created_on,
                    signature_lat = fs.latitude,
                    signature_lng = fs.longitude
                FROM form_signatures fs
                WHERE fs.form_id = tt.form_id
                  AND fs.is_deleted = false
            """)
        conn.commit()
        log.info("  time_tickets: signature fields updated from form_signatures")
    except Exception as e:
        conn.rollback()
        log.warning(f"  time_tickets: signature update failed: {e}")

    # Record sync time so --mode new works correctly next run
    try:
        from etl.utils import set_last_sync, ensure_state_table
        ensure_state_table(conn)
        set_last_sync(conn, "time_tickets", upserted)
    except Exception as e:
        log.warning(f"Could not update etl_sync_state for time_tickets: {e}")

    conn.close()
    log.info(f"Sync complete. upserted={upserted}, fetch_errors={errors}, total_forms={len(forms)}")


if __name__ == "__main__":
    import sys
    since_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sync_time_tickets(since=since_arg)
