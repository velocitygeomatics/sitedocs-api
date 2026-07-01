# SiteDocs API

REST API and ETL pipeline that syncs data from the
[SiteDocs](https://www.sitedocs.com/) safety-management platform into an
Azure PostgreSQL database exposed via Azure Functions.

## Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/locations` | List locations (filter by `isArchived`, `name`) |
| GET | `/api/locations/{id}` | Single location |
| GET | `/api/locations/{id}/forms` | Forms for a location |
| GET | `/api/locations/{id}/attachments` | Attachments for a location |
| GET | `/api/forms` | List forms |
| GET | `/api/forms/{id}` | Single form |
| GET | `/api/forms/{id}/attachments` | Attachments for a form |
| GET | `/api/attachments/{domainObjectId}` | Attachments by domain object |
| GET | `/api/attachments/{domainObjectId}/{kind}` | Attachments filtered by kind |
| GET | `/api/lookup/form-types` | Form-type lookup |
| GET | `/api/lookup/attachment-types` | Attachment-type lookup |
| GET | `/api/lookup/company-types` | Company-type lookup |
| GET | `/api/lookup/companies` | Company lookup |

All endpoints require an `x-api-key` header.

## ETL pipeline

The ETL pulls data from the SiteDocs REST API and upserts it into PostgreSQL.
Run order respects foreign-key dependencies:

1. Lookups
2. Companies
3. Workers
4. Equipment
5. Certifications
6. Forms
7. Form contents
8. Incidents
9. Signatures
10. Attachments

```bash
# Full sync
python -m etl.run_etl

# Incremental (only changed records)
python -m etl.run_etl --incremental

# Single entity
python -m etl.run_etl --only workers

# Dry run (connection test only)
python -m etl.run_etl --dry-run
```

A separate `etl_time_tickets.py` script syncs time-ticket data for VG-Time.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real values
func start
```

See `.env.example` for the full list of required environment variables
(PostgreSQL, SiteDocs API token, API key, Key Vault toggle).

## Deployment

Deploys to **Azure Functions** (`sitedocs-api`) on push to `master` via
GitHub Actions. The workflow uses Oryx remote build so pip dependencies are
installed on Azure.

## Repository layout

```
function_app.py          Azure Functions v2 entrypoint (all REST routes)
shared/db.py             Connection pool, auth middleware, response helpers
etl/                     ETL modules (one per SiteDocs entity)
etl/run_etl.py           ETL orchestrator CLI
etl_time_tickets.py      Time-ticket ETL (standalone)
schema.sql               PostgreSQL schema
time_tickets_schema.sql  Time-ticket table definitions
scripts/                 Ad-hoc utilities (signature probe, ETL trigger)
.github/workflows/       CI/CD (deploy on push to master)
```
