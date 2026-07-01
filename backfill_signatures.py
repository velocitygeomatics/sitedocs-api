"""
DEPRECATED — This script is no longer functional.

The sync_time_tickets() function in etl_time_tickets.py now populates
signed_by / signed_on / signature_lat / signature_lng directly from the
form_signatures table (synced by etl/sync_signatures.py). There is no
need for a separate backfill step.

To re-populate signature columns, simply re-run the time_tickets ETL stage:
    python -m etl.run_etl --only time_tickets

History
-------
This script previously called fetch_signatures_batch() and
_pick_crew_chief_signature() from etl_time_tickets.py to backfill
signature data via the SiteDocs API. Those functions were removed when
the ETL was refactored to use the DB-cached form_signatures table.
"""

import sys


def main():
    print(
        "ERROR: backfill_signatures.py is deprecated.\n"
        "Signature columns are now populated automatically by the time_tickets ETL stage.\n"
        "Run instead:  python -m etl.run_etl --only time_tickets",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
