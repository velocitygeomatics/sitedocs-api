-- ============================================================
-- SiteDocs PostgreSQL Schema
-- All 22 tables in FK-safe creation order
-- Run: psql -h <host> -U sitedocsadmin -d sitedocs -f schema.sql
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================
-- 1. LOOKUP / TYPE TABLES (no dependencies)
-- ============================================================

CREATE TABLE IF NOT EXISTS company_types (
    company_type_id  UUID PRIMARY KEY,
    company_id       UUID,
    name             TEXT NOT NULL,
    is_deleted       BOOLEAN DEFAULT FALSE,
    created_by       UUID,
    created_on       TIMESTAMPTZ,
    modified_on      TIMESTAMPTZ,
    etl_synced_on    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equipment_types (
    equipment_type_id UUID PRIMARY KEY,
    name              TEXT NOT NULL,
    account_id        UUID,
    is_deleted        BOOLEAN DEFAULT FALSE,
    created_by        UUID,
    created_on        TIMESTAMPTZ,
    modified_on       TIMESTAMPTZ,
    etl_synced_on     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attachment_types (
    attachment_type_id UUID PRIMARY KEY,
    name               TEXT NOT NULL,
    account_id         UUID,
    is_deleted         BOOLEAN DEFAULT FALSE,
    created_by         UUID,
    created_on         TIMESTAMPTZ,
    modified_on        TIMESTAMPTZ,
    etl_synced_on      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS certification_types (
    id               UUID PRIMARY KEY,
    name             TEXT NOT NULL,
    company_id       UUID,
    is_deleted       BOOLEAN DEFAULT FALSE,
    created_on       TIMESTAMPTZ,
    last_modified_on TIMESTAMPTZ,
    etl_synced_on    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 2. LOCATIONS (depends on: companies via FK — deferred)
-- ============================================================

CREATE TABLE IF NOT EXISTS locations (
    id                   UUID PRIMARY KEY,
    name                 TEXT NOT NULL,
    description          TEXT,
    address              TEXT,
    start_date           TIMESTAMPTZ,
    end_date             TIMESTAMPTZ,
    creating_company_id  UUID,
    is_archived          BOOLEAN DEFAULT FALSE,
    created_on           TIMESTAMPTZ,
    last_modified_on     TIMESTAMPTZ,
    etl_synced_on        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_locations_name       ON locations (name);
CREATE INDEX IF NOT EXISTS idx_locations_archived   ON locations (is_archived);

-- ============================================================
-- 3. COMPANIES (depends on: company_types)
-- ============================================================

CREATE TABLE IF NOT EXISTS companies (
    company_id       UUID PRIMARY KEY,
    name             TEXT NOT NULL,
    company_type_id  UUID REFERENCES company_types(company_type_id),
    is_active        BOOLEAN DEFAULT TRUE,
    projects_count   INT DEFAULT 0,
    codes_count      INT DEFAULT 0,
    created_on       TIMESTAMPTZ,
    etl_synced_on    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companies_name      ON companies (name);
CREATE INDEX IF NOT EXISTS idx_companies_active    ON companies (is_active);
CREATE INDEX IF NOT EXISTS idx_companies_type      ON companies (company_type_id);

CREATE TABLE IF NOT EXISTS company_details (
    company_id                      UUID PRIMARY KEY REFERENCES companies(company_id),
    legal_name_of_company           TEXT,
    address                         TEXT,
    business_contact_email          TEXT,
    business_contact_number         TEXT,
    website                         TEXT,
    primary_contact                 TEXT,
    secondary_contact               TEXT,
    pre_qualification_status        TEXT,
    pre_qualification_software      TEXT,
    pre_qualification_id            TEXT,
    safety_audit_name               TEXT,
    latest_safety_audit_date        TIMESTAMPTZ,
    latest_safety_audit_score       TEXT,
    liability_insurance_amount      TEXT,
    bonding_capacity                TEXT,
    insurance_notes                 TEXT,
    services_provided               TEXT,
    industry_sector                 TEXT,
    number_of_employees             INT,
    union_affiliations              TEXT,
    year_of_establishment           TEXT,
    license_number                  TEXT,
    member_association_affiliations TEXT,
    etl_synced_on                   TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 4. WORKERS (depends on: companies)
-- ============================================================

CREATE TABLE IF NOT EXISTS workers (
    id                 UUID PRIMARY KEY,
    active             BOOLEAN DEFAULT TRUE,
    first_name         TEXT NOT NULL,
    last_name          TEXT NOT NULL,
    job_title          TEXT,
    employer_id        UUID,
    contractor_id      UUID REFERENCES companies(company_id),
    contractor_name    TEXT,
    is_external        BOOLEAN DEFAULT FALSE,
    street_address     TEXT,
    city               TEXT,
    postal_code        TEXT,
    mobile_number      TEXT,
    phone_number       TEXT,
    email              TEXT,
    date_hired         TIMESTAMPTZ,
    employee_number    TEXT,
    emergency_contact1 TEXT,
    emergency_contact2 TEXT,
    emergency_notes    TEXT,
    created_on         TIMESTAMPTZ,
    last_modified_on   TIMESTAMPTZ,
    etl_synced_on      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workers_name      ON workers (last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_workers_active    ON workers (active);
CREATE INDEX IF NOT EXISTS idx_workers_company   ON workers (contractor_id);
CREATE INDEX IF NOT EXISTS idx_workers_email     ON workers (email);

-- ============================================================
-- 5. EQUIPMENT (depends on: equipment_types)
-- ============================================================

CREATE TABLE IF NOT EXISTS equipments (
    equipment_id       UUID PRIMARY KEY,
    name               TEXT NOT NULL,
    equipment_type_id  UUID REFERENCES equipment_types(equipment_type_id),
    equipment_type_name TEXT,
    account_id         UUID,
    is_deleted         BOOLEAN DEFAULT FALSE,
    created_by         UUID,
    created_on         TIMESTAMPTZ,
    modified_on        TIMESTAMPTZ,
    etl_synced_on      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_equipments_name   ON equipments (name);
CREATE INDEX IF NOT EXISTS idx_equipments_type   ON equipments (equipment_type_id);

CREATE TABLE IF NOT EXISTS equipment_details (
    equipment_id           UUID PRIMARY KEY REFERENCES equipments(equipment_id),
    description            TEXT,
    equipment_status       TEXT,
    manufacturer           TEXT,
    model_number           TEXT,
    scan_id                TEXT,
    serial_number          TEXT,
    fuel_type_consumption  TEXT,
    weight_and_dimensions  TEXT,
    noise_level            TEXT,
    power_requirements     TEXT,
    owner                  TEXT,
    operator               TEXT,
    responsible_person     TEXT,
    odometer               NUMERIC,
    hours                  NUMERIC,
    cost_value             NUMERIC,
    purchase_date          TIMESTAMPTZ,
    warranty_coverage      TIMESTAMPTZ,
    manufacture_date       TIMESTAMPTZ,
    last_calibration_date  TIMESTAMPTZ,
    decommissioning_date   TIMESTAMPTZ,
    etl_synced_on          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 6. CERTIFICATIONS (depends on: workers, certification_types)
-- ============================================================

CREATE TABLE IF NOT EXISTS certifications (
    id                      UUID PRIMARY KEY,
    certification_type_id   UUID REFERENCES certification_types(id),
    certification_type_name TEXT,
    issuer                  TEXT,
    ticket                  TEXT,
    worker_id               UUID REFERENCES workers(id),
    acquired                TIMESTAMPTZ,
    expires                 TIMESTAMPTZ,
    acknowledged_expiry_date TIMESTAMPTZ,
    is_archived             BOOLEAN DEFAULT FALSE,
    created_on              TIMESTAMPTZ,
    last_modified_on        TIMESTAMPTZ,
    etl_synced_on           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_certs_worker      ON certifications (worker_id);
CREATE INDEX IF NOT EXISTS idx_certs_type        ON certifications (certification_type_id);
CREATE INDEX IF NOT EXISTS idx_certs_expires     ON certifications (expires);
CREATE INDEX IF NOT EXISTS idx_certs_archived    ON certifications (is_archived);

CREATE TABLE IF NOT EXISTS certification_attachments (
    id               UUID PRIMARY KEY,
    certification_id UUID REFERENCES certifications(id),
    file_name        TEXT,
    content_type     TEXT,
    created_on       TIMESTAMPTZ,
    etl_synced_on    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 7. FORM TYPES (no entity dependencies)
-- ============================================================

CREATE TABLE IF NOT EXISTS form_types (
    id                      UUID PRIMARY KEY,
    name                    TEXT,
    type                    TEXT CHECK (type IN ('form', 'followup', 'resource')),
    document_template_id    UUID,
    hidden                  BOOLEAN DEFAULT FALSE,
    can_duplicate           BOOLEAN DEFAULT FALSE,
    is_private              BOOLEAN DEFAULT FALSE,
    is_resource             BOOLEAN DEFAULT FALSE,
    is_followup             BOOLEAN DEFAULT FALSE,
    is_deleted              BOOLEAN DEFAULT FALSE,
    created_by              UUID,
    last_modified_by        UUID,
    created_on              TIMESTAMPTZ,
    last_modified_on        TIMESTAMPTZ,
    etl_synced_on           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_form_types_name   ON form_types (name);
CREATE INDEX IF NOT EXISTS idx_form_types_type   ON form_types (type);

-- ============================================================
-- 8. FORMS (depends on: form_types, locations, companies)
-- ============================================================

CREATE TABLE IF NOT EXISTS forms (
    id                            UUID PRIMARY KEY,
    label                         TEXT,
    document_template_id          UUID REFERENCES form_types(id),
    document_template_version_id  UUID,
    document_id                   UUID,
    location_id                   UUID REFERENCES locations(id),
    creating_company_id           UUID REFERENCES companies(company_id),
    preceding_version_id          UUID,
    has_good_data                 BOOLEAN DEFAULT FALSE,
    is_deleted                    BOOLEAN DEFAULT FALSE,
    is_private                    BOOLEAN DEFAULT FALSE,
    due                           TIMESTAMPTZ,
    created_by                    UUID,
    last_modified_by              UUID,
    created_on                    TIMESTAMPTZ,
    last_modified_on              TIMESTAMPTZ,
    etl_synced_on                 TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forms_template    ON forms (document_template_id);
CREATE INDEX IF NOT EXISTS idx_forms_location    ON forms (location_id);
CREATE INDEX IF NOT EXISTS idx_forms_company     ON forms (creating_company_id);
CREATE INDEX IF NOT EXISTS idx_forms_created     ON forms (created_on);
CREATE INDEX IF NOT EXISTS idx_forms_deleted     ON forms (is_deleted);

-- ============================================================
-- 9. INCIDENT FOLDERS
-- ============================================================

CREATE TABLE IF NOT EXISTS incident_folder_types (
    folder_type_id      UUID PRIMARY KEY,
    name                TEXT NOT NULL,
    summary             TEXT,
    kind                TEXT,
    is_user_generated   BOOLEAN DEFAULT FALSE,
    is_hidden           BOOLEAN DEFAULT FALSE,
    has_private_reports BOOLEAN DEFAULT FALSE,
    created_on          TIMESTAMPTZ,
    etl_synced_on       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS incident_folders (
    id              UUID PRIMARY KEY,
    folder_type_id  UUID REFERENCES incident_folder_types(folder_type_id),
    name            TEXT,
    type_name       TEXT,
    latest_status   TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_by      UUID,
    created_on      TIMESTAMPTZ,
    modified_on     TIMESTAMPTZ,
    etl_synced_on   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inc_folders_type    ON incident_folders (folder_type_id);
CREATE INDEX IF NOT EXISTS idx_inc_folders_status  ON incident_folders (latest_status);

CREATE TABLE IF NOT EXISTS incident_folder_statuses (
    status_id               UUID PRIMARY KEY,
    folder_id               UUID REFERENCES incident_folders(id),
    company_id              UUID,
    status                  TEXT,
    comment                 TEXT,
    occurred_on_utc         TIMESTAMPTZ,
    is_final_checkpoint     BOOLEAN DEFAULT FALSE,
    triggered_by_user_id    UUID,
    requires_intervention   BOOLEAN DEFAULT FALSE,
    report_count            INT DEFAULT 0,
    etl_synced_on           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inc_statuses_folder ON incident_folder_statuses (folder_id);

-- ============================================================
-- 10. ATTACHMENTS (polymorphic)
-- ============================================================

CREATE TABLE IF NOT EXISTS attachments (
    attachment_id       UUID PRIMARY KEY,
    domain_object_id    UUID NOT NULL,
    kind                INT NOT NULL,
    original_file_name  TEXT,
    file_size_in_kb     INT,
    content_type        TEXT,
    name                TEXT,
    company_id          UUID,
    general_type_id     UUID REFERENCES attachment_types(attachment_type_id),
    effective_on        TIMESTAMPTZ,
    expires_on          TIMESTAMPTZ,
    created_by          UUID,
    updated_by          UUID,
    created_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ,
    etl_synced_on       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attachments_domain  ON attachments (domain_object_id);
CREATE INDEX IF NOT EXISTS idx_attachments_kind    ON attachments (kind);
CREATE INDEX IF NOT EXISTS idx_attachments_expires ON attachments (expires_on);

-- ============================================================
-- 11. LISTS
-- ============================================================

CREATE TABLE IF NOT EXISTS lists (
    id                  UUID PRIMARY KEY,
    name                TEXT,
    selectable_tree_id  UUID,
    json                TEXT,
    creating_company_id UUID,
    is_deleted          BOOLEAN DEFAULT FALSE,
    created_by          UUID,
    last_modified_by    UUID,
    created_on          TIMESTAMPTZ,
    last_modified_on    TIMESTAMPTZ,
    etl_synced_on       TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 12. JUNCTION TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS company_locations (
    company_id   UUID REFERENCES companies(company_id),
    location_id  UUID REFERENCES locations(id),
    PRIMARY KEY (company_id, location_id)
);

CREATE TABLE IF NOT EXISTS equipment_locations (
    equipment_id UUID REFERENCES equipments(equipment_id),
    location_id  UUID REFERENCES locations(id),
    PRIMARY KEY (equipment_id, location_id)
);

CREATE TABLE IF NOT EXISTS equipment_workers (
    equipment_id UUID REFERENCES equipments(equipment_id),
    worker_id    UUID REFERENCES workers(id),
    PRIMARY KEY (equipment_id, worker_id)
);

CREATE TABLE IF NOT EXISTS worker_locations (
    worker_id    UUID REFERENCES workers(id),
    location_id  UUID REFERENCES locations(id),
    PRIMARY KEY (worker_id, location_id)
);

-- ============================================================
-- 13. TIME TICKETS (parsed form content)
-- ============================================================

CREATE TABLE IF NOT EXISTS time_tickets (
    form_id                 UUID PRIMARY KEY,
    form_label              TEXT,
    location_id             UUID REFERENCES locations(id),
    location_name           TEXT,
    submitted_on            TIMESTAMPTZ,
    is_deleted              BOOLEAN DEFAULT FALSE,
    ticket_date             DATE,
    client                  TEXT,
    client_field_rep        TEXT,
    job_no                  TEXT,
    wellsite_location       TEXT,
    project_manager         TEXT,
    crew_chief              TEXT,
    assistant               TEXT,
    survey_equipment_day    NUMERIC,
    pipe_locator_hrs        NUMERIC,
    chainsaw_hrs            NUMERIC,
    jackhammer_hrs          NUMERIC,
    truck_km                NUMERIC,
    truck_hours             NUMERIC,
    atv_utv_snowmobile      TEXT,
    marker_posts            NUMERIC,
    iron_posts              NUMERIC,
    cc_travel_hrs           NUMERIC,
    cc_work_hrs             NUMERIC,
    cc_notes_hrs            NUMERIC,
    cc_total_hrs            NUMERIC,
    cc_subsistence          TEXT,
    sa_travel_hrs           NUMERIC,
    sa_work_hrs             NUMERIC,
    sa_total_hrs            NUMERIC,
    sa_subsistence          TEXT,
    details                 TEXT,
    approval                TEXT,
    approval_date           DATE,
    signed_by               TEXT,
    signed_on               TIMESTAMPTZ,
    signature_lat           NUMERIC,
    signature_lng           NUMERIC,
    etl_synced_on           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tt_ticket_date  ON time_tickets (ticket_date);
CREATE INDEX IF NOT EXISTS idx_tt_job_no       ON time_tickets (job_no);
CREATE INDEX IF NOT EXISTS idx_tt_client       ON time_tickets (client);
CREATE INDEX IF NOT EXISTS idx_tt_crew_chief   ON time_tickets (crew_chief);
CREATE INDEX IF NOT EXISTS idx_tt_location_id  ON time_tickets (location_id);
CREATE INDEX IF NOT EXISTS idx_tt_submitted_on ON time_tickets (submitted_on);

-- ============================================================
-- 14. FORM SIGNATURES
-- ============================================================

CREATE TABLE IF NOT EXISTS form_signatures (
    id                       UUID PRIMARY KEY,
    form_id                  UUID NOT NULL,
    employee_id              UUID,
    image_id                 UUID,
    created_on               TIMESTAMPTZ,
    last_modified_on         TIMESTAMPTZ,
    is_deleted               BOOLEAN DEFAULT FALSE,
    latitude                 DOUBLE PRECISION,
    longitude                DOUBLE PRECISION,
    signatory_first_name     TEXT,
    signatory_last_name      TEXT,
    signatory_title          TEXT,
    signatory_type           INTEGER,
    signatory_contractor_id  UUID,
    signatory_contractor_name TEXT,
    approval_status          INTEGER,
    etl_synced_on            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_form_signatures_form_id ON form_signatures (form_id);

-- ============================================================
-- 15. ETL STATE TRACKING
-- ============================================================

CREATE TABLE IF NOT EXISTS etl_sync_state (
    entity      TEXT PRIMARY KEY,
    last_sync   TIMESTAMPTZ,
    last_count  INT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 15. ETL ERROR LOG (dead-letter table for failed rows)
-- ============================================================

CREATE TABLE IF NOT EXISTS etl_errors (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ DEFAULT NOW(),
    stage           TEXT NOT NULL,
    entity_id       TEXT,
    error_type      TEXT,
    error_message   TEXT,
    payload         JSONB,
    resolved        BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_etl_errors_stage      ON etl_errors (stage);
CREATE INDEX IF NOT EXISTS idx_etl_errors_occurred    ON etl_errors (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_etl_errors_unresolved  ON etl_errors (resolved) WHERE resolved = FALSE;
