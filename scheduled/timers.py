"""
scheduled/timers.py
Timer-triggered functions: nightly ETL stages, hourly sync, PDF sync,
keep-warm ping.
"""

import os
import time
import logging
import psycopg2.extras
import azure.functions as func
from shared.db import (
    query, get_connection, release_connection, require_api_key,
    ok, error, _get_secret,
)

bp = func.Blueprint()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ETL streaming helper
# ---------------------------------------------------------------------------

def _run_etl_streaming(label: str, args: list[str]) -> int:
    """Run an ETL subprocess and stream its stdout/stderr line-by-line into
    the host logger so each line lands in App Insights as it happens.
    Returns the subprocess exit code (or -1 if it failed to launch)."""
    import subprocess
    import sys
    cmd = [sys.executable, "-m", "etl.run_etl", *args]
    log.info(f"{label}: starting ({' '.join(args) or 'full'})")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        log.error(f"{label}: failed to launch: {e}")
        return -1

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log.info(f"{label} | {line}")

    rc = proc.wait()
    if rc == 0:
        log.info(f"{label}: completed (exit=0)")
    else:
        log.error(f"{label}: failed (exit={rc})")
    return rc


# ---------------------------------------------------------------------------
# Nightly ETL stages (staggered to fit within 10-min Consumption-plan budget)
# ---------------------------------------------------------------------------

@bp.timer_trigger(
    schedule="0 5 2 * * *",
    arg_name="nightlyEntitiesTimer",
    run_on_startup=False,
)
def nightly_entities_sync(nightlyEntitiesTimer: func.TimerRequest) -> None:
    """Nightly 1/5: lookups, companies, workers, equipment, certifications."""
    for stage in ("lookups", "companies", "workers", "equipment", "certifications"):
        _run_etl_streaming(f"nightly_entities_sync:{stage}", ["--only", stage])


@bp.timer_trigger(
    schedule="0 20 2 * * *",
    arg_name="nightlyFormsTimer",
    run_on_startup=False,
)
def nightly_forms_sync(nightlyFormsTimer: func.TimerRequest) -> None:
    """Nightly 2/5: form_types + forms."""
    _run_etl_streaming("nightly_forms_sync", ["--only", "forms"])


@bp.timer_trigger(
    schedule="0 35 2 * * *",
    arg_name="nightlyFormDetailsTimer",
    run_on_startup=False,
)
def nightly_form_details_sync(nightlyFormDetailsTimer: func.TimerRequest) -> None:
    """Nightly 3/5: form_contents, signatures, incidents."""
    for stage in ("form_contents", "signatures", "incidents"):
        _run_etl_streaming(f"nightly_form_details_sync:{stage}", ["--only", stage])


@bp.timer_trigger(
    schedule="0 5 3 * * *",
    arg_name="nightlyAttachmentsTimer",
    run_on_startup=False,
)
def nightly_attachments_sync(nightlyAttachmentsTimer: func.TimerRequest) -> None:
    """Nightly 4/5: attachments."""
    _run_etl_streaming("nightly_attachments_sync", ["--only", "attachments"])


@bp.timer_trigger(
    schedule="0 20 3 * * *",
    arg_name="nightlyTimeTicketsTimer",
    run_on_startup=False,
)
def nightly_time_tickets_sync(nightlyTimeTicketsTimer: func.TimerRequest) -> None:
    """Nightly 5/5: time tickets."""
    _run_etl_streaming("nightly_time_tickets_sync", ["--only", "time_tickets"])


@bp.timer_trigger(
    schedule="0 0 * * * *",
    arg_name="hourlyTimer",
    run_on_startup=False,
)
def hourly_incremental_sync(hourlyTimer: func.TimerRequest) -> None:
    """Incremental forms + time tickets sync - runs every hour."""
    _run_etl_streaming("hourly_incremental_sync", ["--only", "forms", "--mode", "new"])


# ---------------------------------------------------------------------------
# PDF sync (SharePoint upload)
# ---------------------------------------------------------------------------

def _run_pdf_sync() -> dict:
    """Upload new time ticket PDFs to SharePoint Invoice folders."""
    import requests as req_lib

    log.info("PDF sync starting...")

    tenant_id = os.environ.get("SP_TENANT_ID", "9d71e5ea-6164-445b-99c7-cc5b6b1f9e3a")
    client_id = os.environ.get("SP_CLIENT_ID", "76d8a182-c87a-4df5-a888-a7f48ae9ff3e")
    company_id = os.environ.get("SITEDOCS_COMPANY_ID", "48651caf-50e4-45ab-875a-dfe02fa14441")

    client_secret = _get_secret("SP_CLIENT_SECRET")
    sd_token = _get_secret("SITEDOCS_API_TOKEN")

    if not client_secret:
        raise RuntimeError("SP_CLIENT_SECRET is empty — check App Settings")
    if not sd_token:
        raise RuntimeError("SITEDOCS_API_TOKEN is empty — check App Settings")

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    SP_HOSTNAME = "velocitygeomaticsinc.sharepoint.com"

    token_resp = req_lib.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id,
              "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"}
    )
    if not token_resp.ok:
        raise RuntimeError(f"Failed to get SP token: {token_resp.text}")
    sp_token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {sp_token}", "Accept": "application/json"}

    site_resp = req_lib.get(f"{GRAPH_BASE}/sites/{SP_HOSTNAME}:/", headers=headers)
    if not site_resp.ok:
        raise RuntimeError(f"Failed to get SharePoint site: {site_resp.text}")
    site_id = site_resp.json()["id"]

    drives_resp = req_lib.get(f"{GRAPH_BASE}/sites/{site_id}/drives", headers=headers)
    drive_id = None
    for d in drives_resp.json().get("value", []):
        if d.get("name", "").lower() in ("documents", "shared documents"):
            drive_id = d["id"]
            break
    if not drive_id:
        raise RuntimeError("Documents drive not found in SharePoint site")

    conn = get_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT tt.form_id, tt.form_label,
                       substring(l.name FROM '^([0-9]{6})') AS job_no
                FROM time_tickets tt
                JOIN locations l ON l.id = tt.location_id
                WHERE tt.is_deleted = false
                  AND tt.submitted_on IS NOT NULL
                  AND l.name ~ '^[0-9]{6}'
                  AND (tt.sp_uploaded IS NULL OR tt.sp_uploaded = false)
                ORDER BY tt.submitted_on
            """)
            tickets = [dict(r) for r in cur.fetchall()]

        log.info(f"PDF sync: {len(tickets)} ticket(s) pending")
        success = failed = skipped = 0

        for ticket in tickets:
            fid = str(ticket["form_id"])
            job_no = ticket["job_no"]
            label = (ticket["form_label"] or fid).strip()
            for ch in r'/\:*?"<>|':
                label = label.replace(ch, "-")
            year = "20" + job_no[:2]
            folder_path = f"Job Files/{year}/{job_no}/Invoice"
            filename = f"{label}.pdf"
            sp_path = f"{folder_path}/{filename}"

            try:
                pdf_resp = req_lib.get(
                    f"https://api-1.sitedocs.com/api/v1/export/pdf/company/{company_id}/form/{fid}",
                    headers={"Authorization": sd_token}, timeout=30
                )
                if pdf_resp.status_code == 404:
                    skipped += 1
                    continue
                pdf_resp.raise_for_status()

                parts = folder_path.split("/")
                parent_id = "root"
                built = ""
                for part in parts:
                    built = f"{built}/{part}" if built else part
                    chk = req_lib.get(f"{GRAPH_BASE}/drives/{drive_id}/root:/{built}", headers=headers)
                    if chk.status_code == 200:
                        parent_id = chk.json()["id"]
                    elif chk.status_code == 404:
                        cr = req_lib.post(
                            f"{GRAPH_BASE}/drives/{drive_id}/items/{parent_id}/children",
                            headers={**headers, "Content-Type": "application/json"},
                            json={"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
                        )
                        if cr.status_code in (200, 201):
                            parent_id = cr.json()["id"]

                up = req_lib.put(
                    f"{GRAPH_BASE}/drives/{drive_id}/root:/{sp_path}:/content",
                    headers={**headers, "Content-Type": "application/pdf"},
                    data=pdf_resp.content, timeout=60
                )
                up.raise_for_status()

                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE time_tickets SET sp_uploaded=true, sp_uploaded_on=NOW(), sp_path=%s WHERE form_id=%s",
                        (sp_path, fid)
                    )
                conn.commit()
                success += 1
                time.sleep(0.3)

            except Exception as e:
                log.error(f"PDF sync: failed {fid}: {e}")
                failed += 1

    finally:
        release_connection(conn)

    result = {"success": success, "skipped": skipped, "failed": failed, "pending": len(tickets)}
    log.info(f"PDF sync complete: {success} uploaded / {skipped} skipped / {failed} failed")
    return result


@bp.timer_trigger(
    schedule="0 30 * * * *",
    arg_name="pdfSyncTimer",
    run_on_startup=False,
)
def hourly_pdf_sync(pdfSyncTimer: func.TimerRequest) -> None:
    """Upload new time ticket PDFs to SharePoint. Runs every hour at :30."""
    try:
        _run_pdf_sync()
    except Exception as e:
        log.error(f"PDF sync failed: {e}")


@bp.route(route="pdf-sync/trigger", methods=["POST"])
@require_api_key
def trigger_pdf_sync(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/pdf-sync/trigger - manually trigger the PDF sync."""
    try:
        result = _run_pdf_sync()
        return ok(result)
    except Exception as e:
        log.error(f"PDF sync trigger failed: {e}")
        return error(500, str(e))


# ---------------------------------------------------------------------------
# Keep-warm timer
# ---------------------------------------------------------------------------

@bp.timer_trigger(
    schedule="0 */4 * * * *",
    arg_name="warmTimer",
    run_on_startup=False,
)
def keep_warm(warmTimer: func.TimerRequest) -> None:
    """Ping Postgres to keep the Function App worker warm."""
    try:
        query("SELECT 1 AS ok")
        log.info("keep_warm: ok")
    except Exception as e:
        log.error(f"keep_warm failed: {e}")
