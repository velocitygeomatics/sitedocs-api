"""
sync_time_tickets_local.py
Run locally to sync all time ticket forms from SiteDocs into Postgres.

Usage:
    pip install requests psycopg2-binary python-dotenv
    python sync_time_tickets_local.py

Reads DB credentials from environment or .env file.
"""

import os, re, json, logging, time
from datetime import datetime, timezone
from typing import Optional

import requests
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — reads from environment / .env
# ---------------------------------------------------------------------------
SITEDOCS_TOKEN = os.environ["SITEDOCS_API_TOKEN"]
FORM_TYPE_ID   = "6c3f93b6-1326-478b-a6d6-59aba925a1c1"
API_BASE       = "https://api-1.sitedocs.com/api/v1"
PAGE_SIZE      = 100
RATE_LIMIT     = 0.5   # seconds between calls

DB_CONN = dict(
    host     = os.environ["POSTGRES_HOST"],
    dbname   = os.environ["POSTGRES_DB"],
    user     = os.environ["POSTGRES_USER"],
    password = os.environ["POSTGRES_PASSWORD"],
    port     = int(os.environ.get("POSTGRES_PORT", 5432)),
    sslmode  = "require",
)

HEADERS = {"Authorization": SITEDOCS_TOKEN, "Accept": "application/json"}

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def api_get(path: str, params: dict = None) -> dict:
    url = f"{API_BASE}{path}"
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            wait = 2 ** attempt * 5
            log.warning(f"Rate limited — waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(RATE_LIMIT)
        return r.json()
    raise RuntimeError(f"Failed after retries: {path}")

def fetch_all_forms() -> list:
    forms, page = [], 0
    while True:
        batch = api_get("/forms", {"formTypeId": FORM_TYPE_ID, "count": PAGE_SIZE, "page": page})
        if not batch:
            break
        forms.extend(batch)
        log.info(f"  Page {page}: {len(batch)} forms ({len(forms)} total)")
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return forms

def fetch_content(form_id: str) -> dict:
    result = api_get(f"/forms/content/{form_id}")
    if isinstance(result, str):
        result = json.loads(result)
    return result

# ---------------------------------------------------------------------------
# Field parser (matches fixed etl_time_tickets.py logic)
# ---------------------------------------------------------------------------
def _extract_field(content: dict, label: str) -> Optional[str]:
    label_clean = label.strip().rstrip(":").lower()
    for group in content.get("Groups", []):
        for item in group.get("Items", []):
            raw = item.get("Content") or ""
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                    item_label = (parsed.get("Label") or "").strip().rstrip(":").lower()
                except Exception:
                    item_label = raw.strip().rstrip(":").lower()
            else:
                item_label = raw.strip().rstrip(":").lower()
            if item_label == label_clean:
                val = item.get("Value")
                if val is None:
                    return None
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val if v)
                val = str(val).strip()
                return val if val and val.lower() != "no response" else None
    return None

def _worker_name(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    val = val.strip()
    if val.startswith("["):
        try:
            workers = json.loads(val)
            if workers and isinstance(workers, list):
                w = workers[0]
                name = f"{w.get('FirstName','').strip()} {w.get('LastName','').strip()}".strip()
                return name or None
        except Exception:
            pass
    return val or None

def _list_selection(val: Optional[str]) -> Optional[str]:
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
    return val or None

def _num(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None

def _safe_date(val):
    if not val:
        return None
    val = str(val).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", val):
        return val[:10]
    from datetime import datetime as _dt
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return _dt.strptime(val.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def _date_from_label(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    m = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', label)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{2})(\d{2})(20\d{2})\b', label)
    if m:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        try:
            datetime(int(yyyy), int(mm), int(dd))
            return f"{yyyy}-{mm}-{dd}"
        except ValueError:
            pass
    return None

def _extract_signature(content: dict):
    for group in content.get("Groups", []):
        if "signature" in (group.get("Title") or "").lower():
            for item in group.get("Items", []):
                name = item.get("SignerName") or item.get("signerName")
                dt   = item.get("SignedOn")   or item.get("signedOn")
                lat  = item.get("Latitude")   or item.get("latitude")
                lng  = item.get("Longitude")  or item.get("longitude")
                if name:
                    return str(name), str(dt) if dt else None, _num(str(lat) if lat else None), _num(str(lng) if lng else None)
    return None, None, None, None

def parse(form: dict, content: dict) -> dict:
    f = _extract_field
    label       = form.get("Label") or ""
    ticket_date = _safe_date(f(content, "Date")) or _date_from_label(label)
    signed_by, signed_on, sig_lat, sig_lng = _extract_signature(content)

    return {
        "form_id":              form["Id"],
        "form_label":           label,
        "location_id":          form.get("LocationId"),
        "location_name":        None,
        "submitted_on":         form.get("CreatedOn"),
        "is_deleted":           form.get("IsDeleted", False),
        "ticket_date":          ticket_date or None,
        "client":               f(content, "Client"),
        "client_field_rep":     f(content, "Client Field Representative"),
        "job_no":               f(content, "Job No"),
        "wellsite_location":    f(content, "Location"),
        "project_manager":      f(content, "Project Manager"),
        "crew_chief":           _worker_name(f(content, "Crew Chief")),
        "assistant":            _worker_name(f(content, "Assistant")),
        "survey_equipment_day": _num(f(content, "Survey Equipment (Day)")),
        "pipe_locator_hrs":     _num(f(content, "Pipe Locator (hrs)")),
        "chainsaw_hrs":         _num(f(content, "Chainsaw (hrs)")),
        "jackhammer_hrs":       _num(f(content, "Jackhammer (hrs)")),
        "truck_km":             _num(f(content, "Truck km")),
        "truck_hours":          _num(f(content, "Truck hours")),
        "atv_utv_snowmobile":   _list_selection(f(content, "ATV/UTV/Snowmobile")),
        "marker_posts":         _num(f(content, "Marker Posts")),
        "iron_posts":           _num(f(content, "Iron Posts")),
        "cc_travel_hrs":        _num(f(content, "Crew Chief Travel")),
        "cc_work_hrs":          _num(f(content, "Crew Chief Work")),
        "cc_notes_hrs":         _num(f(content, "Crew Chief Notes")),
        "cc_total_hrs":         _num(f(content, "Crew Chief Total")),
        "cc_subsistence":       _list_selection(f(content, "Crew Chief Subsistence")),
        "sa_travel_hrs":        _num(f(content, "SA Travel")),
        "sa_work_hrs":          _num(f(content, "SA Work")),
        "sa_total_hrs":         _num(f(content, "SA Total")),
        "sa_subsistence":       _list_selection(f(content, "SA Subsistence")),
        "details":              f(content, "Details"),
        "approval":             f(content, "Approval"),
        "approval_date":        _safe_date(f(content, "Approval Date")),
        "signed_by":            signed_by,
        "signed_on":            signed_on,
        "signature_lat":        sig_lat,
        "signature_lng":        sig_lng,
        "etl_synced_on":        datetime.now(timezone.utc).isoformat(),
    }

# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
UPSERT = """
INSERT INTO time_tickets (
    form_id, form_label, location_id, location_name, submitted_on, is_deleted,
    ticket_date, client, client_field_rep, job_no, wellsite_location,
    project_manager, crew_chief, assistant,
    survey_equipment_day, pipe_locator_hrs, chainsaw_hrs, jackhammer_hrs,
    truck_km, truck_hours, atv_utv_snowmobile, marker_posts, iron_posts,
    cc_travel_hrs, cc_work_hrs, cc_notes_hrs, cc_total_hrs, cc_subsistence,
    sa_travel_hrs, sa_work_hrs, sa_total_hrs, sa_subsistence,
    details, approval, approval_date,
    signed_by, signed_on, signature_lat, signature_lng, etl_synced_on
) VALUES (
    %(form_id)s, %(form_label)s, %(location_id)s, %(location_name)s,
    %(submitted_on)s, %(is_deleted)s,
    %(ticket_date)s, %(client)s, %(client_field_rep)s, %(job_no)s,
    %(wellsite_location)s, %(project_manager)s, %(crew_chief)s, %(assistant)s,
    %(survey_equipment_day)s, %(pipe_locator_hrs)s, %(chainsaw_hrs)s,
    %(jackhammer_hrs)s, %(truck_km)s, %(truck_hours)s, %(atv_utv_snowmobile)s,
    %(marker_posts)s, %(iron_posts)s,
    %(cc_travel_hrs)s, %(cc_work_hrs)s, %(cc_notes_hrs)s, %(cc_total_hrs)s,
    %(cc_subsistence)s, %(sa_travel_hrs)s, %(sa_work_hrs)s, %(sa_total_hrs)s,
    %(sa_subsistence)s, %(details)s, %(approval)s, %(approval_date)s,
    %(signed_by)s, %(signed_on)s, %(signature_lat)s, %(signature_lng)s,
    %(etl_synced_on)s
)
ON CONFLICT (form_id) DO UPDATE SET
    form_label=EXCLUDED.form_label, ticket_date=EXCLUDED.ticket_date,
    client=EXCLUDED.client, job_no=EXCLUDED.job_no,
    crew_chief=EXCLUDED.crew_chief, assistant=EXCLUDED.assistant,
    cc_work_hrs=EXCLUDED.cc_work_hrs, cc_travel_hrs=EXCLUDED.cc_travel_hrs,
    cc_total_hrs=EXCLUDED.cc_total_hrs, sa_work_hrs=EXCLUDED.sa_work_hrs,
    sa_travel_hrs=EXCLUDED.sa_travel_hrs, sa_total_hrs=EXCLUDED.sa_total_hrs,
    details=EXCLUDED.details, approval=EXCLUDED.approval,
    signed_by=EXCLUDED.signed_by, signed_on=EXCLUDED.signed_on,
    truck_km=EXCLUDED.truck_km, truck_hours=EXCLUDED.truck_hours,
    project_manager=EXCLUDED.project_manager,
    wellsite_location=EXCLUDED.wellsite_location,
    atv_utv_snowmobile=EXCLUDED.atv_utv_snowmobile,
    marker_posts=EXCLUDED.marker_posts, iron_posts=EXCLUDED.iron_posts,
    cc_subsistence=EXCLUDED.cc_subsistence, sa_subsistence=EXCLUDED.sa_subsistence,
    etl_synced_on=EXCLUDED.etl_synced_on;
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("Fetching all Time Ticket forms from SiteDocs...")
    forms = fetch_all_forms()
    log.info(f"Found {len(forms)} forms. Starting content fetch + upsert...")

    conn = psycopg2.connect(**DB_CONN)
    batch, ok_count, err_count = [], 0, 0

    for i, form in enumerate(forms):
        fid = form["Id"]
        try:
            content = fetch_content(fid)
            row = parse(form, content)
            batch.append(row)

            if len(batch) >= 50:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, UPSERT, batch, page_size=50)
                conn.commit()
                ok_count += len(batch)
                log.info(f"  Upserted {ok_count}/{len(forms)}")
                batch = []

        except Exception as e:
            log.error(f"  Failed {fid}: {e}")
            err_count += 1
            try:
                conn.rollback()
            except Exception:
                pass
            batch = []

    if batch:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT, batch, page_size=50)
        conn.commit()
        ok_count += len(batch)

    conn.close()
    log.info(f"Done. {ok_count} upserted, {err_count} errors.")

if __name__ == "__main__":
    main()
