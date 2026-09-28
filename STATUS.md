# SiteDocs API — Status

_Snapshot 2026-09-28 · Branch `main`, level with `origin/main` · Latest commit `26f6a72`
(2026-09-21) · First commit 2026-04-10._

> Built from git history, `routes/`, the timer triggers and GitHub Actions. Endpoint detail is
> in [README.md](README.md) (2026-07-01, predates the rates and time-ticket routes).
> Unconfirmed claims are marked ⚠.

## What it is

An Azure Functions (Python) app at `sitedocs-api.azurewebsites.net` that syncs the SiteDocs
safety platform into Azure PostgreSQL and serves it, plus the VG-Time rate tables and time-ticket
export state, to `vg-dashboard`. The dashboard reaches it through its `sitedocs-proxy` Function.

## Where it is

**Endpoints (38 routes in `routes/`):** health; locations, workers, certifications, forms (PDF,
signatures, viewer URL, attachments); attachment download; lookups; ETL status, summary, runs,
errors, trigger; **rates** (items, schedules including copy and delete, per-client assignment);
**time tickets** (list filtered to what payroll still owes, single ticket, export, export history).

**Schedules:** eight timer triggers. Time tickets sync hourly since 2026-09-20, not just nightly.
Forms freshness is reported on `/etl/status` and `/etl/summary`.

**September 2026 changes (25 commits, 09-14 to 09-21):**
- Ingestion fixes: crews' YYMMDD date convention (#14), a runaway `/formtypes` page loop (#15),
  cached forms no longer re-fetched nightly, vendor timestamps stored as UTC, the Environmental
  Scientist Time Ticket template ingested.
- QuickBooks export: durable export marking per time-ticket row, backfill recorded, export runs
  and ETL runs recorded so the dashboard's History tab has data.
- Rates: rename or move items, take a rate off a schedule, copy and delete schedules,
  `PUT /api/rates/clients/{client_name}`.
- Security: a second API key is accepted so keys rotate without an outage (2026-09-16).
- CI: tests run in `ci.yml`, and the deploy is blocked when they fail (2026-09-16). Workflows
  point at `main` since the `master` branch was removed.

**Tests and CI:** 13 test files, Ruff lint. `ci.yml` plus `master_sitedocs-api.yml` (the deploy;
file name is historical, it triggers on `main`). Last CI and deploy both passed on 2026-09-21.

**Working tree:** one untracked file, `scripts/seed_qb_service_item_map.sql`. It moves the QB
service item to rate slug to time-ticket column crosswalk out of the browser
(`vg-dashboard/src/logic.mjs`) into the database. Unfinished work, not committed.

## Open

- **README drift.** It predates the rates and time-ticket routes and the History tab support.
- **Root clutter**, git-ignored: `Untitled.txt`, `etl.zip`, `local.settings.json`. Two April
  notes and `function_app.py.bak` held key-like strings in plaintext; moved to the Recycle Bin
  2026-09-28. **The keys they held should be treated as exposed and rotated** if still in use ⚠
  (the second-key rotation support from 2026-09-16 makes that outage-free).
- **The QB crosswalk** (untracked SQL above) is the next piece: until it lands, the server
  cannot answer "was this column invoiced, and at what rate?"
