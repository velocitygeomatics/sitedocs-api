"""
etl/sync_companies.py
Syncs:
  - locations
  - companies
  - company_details
  - company_locations (junction)
"""

import logging
from .utils import paginate, api_get, upsert, get_last_sync, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_locations(conn):
    log.info("Syncing locations...")
    rows = paginate("/locations")

    mapped = [{
        "id":                  r["Id"],
        "name":                r.get("Name"),
        "description":         r.get("Description"),
        "address":             r.get("Address"),
        "start_date":          r.get("StartDate"),
        "end_date":            r.get("EndDate"),
        "creating_company_id": r.get("CreatingCompanyId"),
        "is_archived":         r.get("IsArchived", False),
        "created_on":          r.get("CreatedOn"),
        "last_modified_on":    r.get("LastModifiedOn"),
        "etl_synced_on":       now_iso(),
    } for r in rows]

    upsert(conn, "locations", mapped, conflict_col="id")
    set_last_sync(conn, "locations", len(mapped))
    log.info(f"  locations: {len(mapped)} rows")
    return rows


def sync_companies(conn):
    log.info("Syncing companies...")
    rows = paginate("/companies")

    mapped = [{
        "company_id":      r["CompanyId"],
        "name":            r.get("Name"),
        "company_type_id": r.get("CompanyTypeId"),
        "is_active":       r.get("IsActive", True),
        "projects_count":  r.get("ProjectsCount", 0),
        "codes_count":     r.get("CodesCount", 0),
        "created_on":      r.get("CreatedOn"),
        "etl_synced_on":   now_iso(),
    } for r in rows]

    upsert(conn, "companies", mapped, conflict_col="company_id")
    set_last_sync(conn, "companies", len(mapped))
    log.info(f"  companies: {len(mapped)} rows")
    return rows


def sync_company_details(conn, companies: list):
    """Fetch the /details endpoint for each company."""
    log.info("Syncing company_details...")
    mapped = []
    errors = 0

    for company in companies:
        cid = company["CompanyId"]
        try:
            r = api_get(f"/companies/details/{cid}")
            if not r:
                continue

            mapped.append({
                "company_id":                     r.get("CompanyId") or cid,
                "legal_name_of_company":          r.get("LegalNameOfCompany"),
                "address":                        r.get("Address"),
                "business_contact_email":         r.get("BusinessContactEmail"),
                "business_contact_number":        r.get("BusinessContactNumber"),
                "website":                        r.get("Website"),
                "primary_contact":                r.get("PrimaryContact"),
                "secondary_contact":              r.get("SecondaryContact"),
                "pre_qualification_status":       r.get("PreQualificationStatus"),
                "pre_qualification_software":     r.get("PreQualificationSoftware"),
                "pre_qualification_id":           r.get("PreQualificationID"),
                "safety_audit_name":              r.get("SafetyAuditName"),
                "latest_safety_audit_date":       r.get("LatestSafetyAuditDate"),
                "latest_safety_audit_score":      r.get("LatestSafetyAuditScore"),
                "liability_insurance_amount":     r.get("LiabilityInsuranceAmount"),
                "bonding_capacity":               r.get("BondingCapacity"),
                "insurance_notes":                r.get("InsuranceNotes"),
                "services_provided":              r.get("ServicesProvided"),
                "industry_sector":                r.get("IndustrySector"),
                "number_of_employees":            r.get("NumberOfEmployees"),
                "union_affiliations":             r.get("UnionAffiliations"),
                "year_of_establishment":          r.get("YearOfEstablishment"),
                "license_number":                 r.get("LicenseNumber"),
                "member_association_affiliations": r.get("MemberAssociation_Affiliations"),
                "etl_synced_on":                  now_iso(),
            })

        except Exception as e:
            log.warning(f"  company_details failed for {cid}: {e}")
            errors += 1

    upsert(conn, "company_details", mapped, conflict_col="company_id")
    set_last_sync(conn, "company_details", len(mapped))
    log.info(f"  company_details: {len(mapped)} rows ({errors} errors)")


def sync_company_locations(conn, locations: list):
    """
    Build company_locations junction from location.CreatingCompanyId.
    Skips any company_id not present in the companies table
    (e.g. the account owner company which is not returned by /companies).
    """
    log.info("Syncing company_locations...")

    # Get valid company IDs already in DB
    with conn.cursor() as cur:
        cur.execute("SELECT company_id::text FROM companies")
        valid_company_ids = {row[0] for row in cur.fetchall()}

    mapped = []
    seen = set()
    skipped = 0

    for loc in locations:
        company_id  = loc.get("CreatingCompanyId")
        location_id = loc.get("Id")
        if company_id and location_id:
            if company_id not in valid_company_ids:
                skipped += 1
                continue
            key = (company_id, location_id)
            if key not in seen:
                mapped.append({
                    "company_id":  company_id,
                    "location_id": location_id,
                })
                seen.add(key)

    sql = """
        INSERT INTO company_locations (company_id, location_id)
        VALUES (%(company_id)s, %(location_id)s)
        ON CONFLICT (company_id, location_id) DO NOTHING
    """
    if mapped:
        import psycopg2.extras
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, mapped, page_size=100)
        conn.commit()

    set_last_sync(conn, "company_locations", len(mapped))
    log.info(f"  company_locations: {len(mapped)} rows ({skipped} skipped — owner company not in contractors list)")


def run(conn):
    locations = sync_locations(conn)
    companies = sync_companies(conn)
    sync_company_details(conn, companies)
    sync_company_locations(conn, locations)
