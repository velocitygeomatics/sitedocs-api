"""
etl/run_etl.py
Main ETL orchestrator for SiteDocs → PostgreSQL.

Run order (respects FK dependencies):
  1. Lookups          (no dependencies)
  2. Locations        (depends on: companies for FK — handled gracefully)
  3. Companies        (depends on: company_types)
  4. Workers          (depends on: companies, locations)
  5. Equipment        (depends on: equipment_types, locations, workers)
  6. Certifications   (depends on: workers, certification_types)
  7. Form Types       (no entity dependencies)
  8. Forms            (depends on: form_types, locations, companies)
  9. Incidents        (no entity dependencies)
  10. Attachments     (depends on: all entities being loaded first)
  11. Time Tickets    (depends on: forms, locations)

Usage:
  Full sync:
    python -m etl.run_etl

  Incremental sync:
    python -m etl.run_etl --incremental

  Single entity:
    python -m etl.run_etl --only workers

  Dry run (connect test only):
    python -m etl.run_etl --dry-run
"""

import sys
import logging
import argparse
from datetime import datetime, timezone

# Load .env for local dev. `override=True` so .env wins over any stale
# shell vars (e.g. leftover `$env:POSTGRES_HOST` from a previous session).
# No-op in Azure where no .env file exists.
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("etl.orchestrator")

from .utils import get_db_conn, ensure_state_table, log_etl_error
from . import (
    sync_lookups,
    sync_companies,
    sync_workers,
    sync_equipment,
    sync_certifications,
    sync_forms,
    sync_form_contents,
    sync_signatures,
    sync_incidents,
    sync_attachments,
)
from etl_time_tickets import sync_time_tickets


# ---------------------------------------------------------------------------
# Run order definition
# ---------------------------------------------------------------------------

def _build_stages(mode: str, conn):
    return [
        ("lookups",        sync_lookups.run),
        ("companies",      sync_companies.run),
        ("workers",        sync_workers.run),
        ("equipment",      sync_equipment.run),
        ("certifications", sync_certifications.run),
        ("forms",          sync_forms.run),
        ("form_contents",  sync_form_contents.run),
        ("signatures",     sync_signatures.run),
        ("incidents",      sync_incidents.run),
        ("attachments",    sync_attachments.run),
        ("time_tickets",   lambda conn: sync_time_tickets()),
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_etl(only: str = None, dry_run: bool = False, mode: str = "all"):
    start = datetime.now(timezone.utc)
    log.info(f"ETL started at {start.isoformat()}")

    conn = get_db_conn()
    ensure_state_table(conn)

    if dry_run:
        log.info("Dry run — connection OK, exiting.")
        conn.close()
        return

    stages = _build_stages(mode, conn)
    log.info(f"ETL mode: {mode}")
    results = {}

    for stage_name, stage_fn in stages:
        if only and stage_name != only:
            continue

        log.info(f"{'='*50}")
        log.info(f"STAGE: {stage_name}")
        stage_start = datetime.now(timezone.utc)

        try:
            stage_fn(conn)
            elapsed = (datetime.now(timezone.utc) - stage_start).total_seconds()
            results[stage_name] = {"status": "ok", "elapsed_s": round(elapsed, 1)}
            log.info(f"STAGE {stage_name} completed in {elapsed:.1f}s")

        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - stage_start).total_seconds()
            results[stage_name] = {"status": "error", "error": str(e), "elapsed_s": round(elapsed, 1)}
            log.error(f"STAGE {stage_name} FAILED: {e}", exc_info=True)
            try:
                conn.rollback()
            except Exception:
                pass
            log_etl_error(conn, stage_name, None, e)

    conn.close()

    # Summary
    total = (datetime.now(timezone.utc) - start).total_seconds()
    log.info(f"{'='*50}")
    log.info(f"ETL complete in {total:.1f}s")
    for stage, result in results.items():
        status = result["status"].upper()
        elapsed = result["elapsed_s"]
        err = f" — {result['error']}" if result.get("error") else ""
        log.info(f"  {stage:<20} {status:<8} {elapsed}s{err}")

    failed = [s for s, r in results.items() if r["status"] == "error"]
    if failed:
        log.warning(f"{len(failed)} stage(s) failed: {', '.join(failed)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SiteDocs ETL Orchestrator")
    parser.add_argument(
        "--only",
        help="Run a single stage only (e.g. --only workers)",
        choices=["lookups","companies","workers","equipment","certifications",
                 "forms","form_contents","signatures","incidents","attachments","time_tickets"],
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test DB connection only, no API calls",
    )
    parser.add_argument(
        "--mode",
        choices=["new", "all"],
        default="all",
        help="new = only time tickets modified since last sync; all = full sync (default)",
    )
    args = parser.parse_args()
    run_etl(only=args.only, dry_run=args.dry_run, mode=args.mode)
