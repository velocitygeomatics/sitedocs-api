-- =============================================================
-- time_tickets
-- Flattened representation of the SiteDocs *Time Ticket form
-- Form Type ID: 6c3f93b6-1326-478b-a6d6-59aba925a1c1
-- Populated by ETL parsing GET /api/v1/forms/content/{formId}
-- =============================================================

CREATE TABLE time_tickets (

    -- -------------------------------------------------------
    -- Form record metadata (from FormViewModel)
    -- -------------------------------------------------------
    form_id                 UUID PRIMARY KEY,
    form_label              TEXT,               -- e.g. 260121-JS-03222026-FT
    location_id             UUID,               -- FK to locations table (SiteDocs project)
    location_name           TEXT,               -- e.g. "260121 (Astara Energy Environmental)"
    submitted_on            TIMESTAMPTZ,        -- form created/signed datetime
    is_deleted              BOOLEAN DEFAULT FALSE,

    -- -------------------------------------------------------
    -- General Information
    -- -------------------------------------------------------
    ticket_date             DATE,               -- "Date" field, ISO-normalized
    date_source             TEXT,               -- 'exact' | 'label' | 'inferred' | 'unknown'
                                                -- where ticket_date came from (see etl/utils.resolve_ticket_date)
    client                  TEXT,               -- "Client"
    client_field_rep        TEXT,               -- "Client Field Representative"
    job_no                  TEXT,               -- "Job No"
    wellsite_location       TEXT,               -- "Location" (wellsite, e.g. 7-76-10w6m)
    project_manager         TEXT,               -- "Project Manager"
    crew_chief              TEXT,               -- "Crew Chief"
    assistant               TEXT,               -- "Assistant"

    -- -------------------------------------------------------
    -- Equipment
    -- -------------------------------------------------------
    survey_equipment_day    NUMERIC,            -- "Survey Equipment (Day)"
    pipe_locator_hrs        NUMERIC,            -- "Pipe Locator (hrs)"
    chainsaw_hrs            NUMERIC,            -- "Chainsaw (hrs)"
    jackhammer_hrs          NUMERIC,            -- "Jackhammer (hrs)"
    truck_km                NUMERIC,            -- "Truck km"
    truck_hours             NUMERIC,            -- "Truck hours"
    atv_utv_snowmobile      TEXT,               -- "ATV/UTV/Snowmobile" (checklist, stored as text)
    marker_posts            NUMERIC,            -- "Marker Posts"
    iron_posts              NUMERIC,            -- "Iron Posts"

    -- -------------------------------------------------------
    -- Crew Chief Labour
    -- -------------------------------------------------------
    cc_travel_hrs           NUMERIC,            -- "Crew Chief Travel"
    cc_work_hrs             NUMERIC,            -- "Crew Chief Work"
    cc_notes_hrs            NUMERIC,            -- "Crew Chief Notes"
    cc_total_hrs            NUMERIC,            -- "Crew Chief Total"
    cc_subsistence          TEXT,               -- "Crew Chief Subsistence" (e.g. "1 Meal")

    -- -------------------------------------------------------
    -- Assistant (SA) Labour
    -- -------------------------------------------------------
    sa_travel_hrs           NUMERIC,            -- "SA Travel"
    sa_work_hrs             NUMERIC,            -- "SA Work"
    sa_total_hrs            NUMERIC,            -- "SA Total"
    sa_subsistence          TEXT,               -- "SA Subsistence"

    -- -------------------------------------------------------
    -- Notes & Approval
    -- -------------------------------------------------------
    details                 TEXT,               -- "Details" free-text field
    approval                TEXT,               -- "Approval"
    approval_date           DATE,               -- "Approval Date"

    -- -------------------------------------------------------
    -- Signature
    -- -------------------------------------------------------
    signed_by               TEXT,               -- signer name from signature block
    signed_on               TIMESTAMPTZ,        -- signature datetime
    signature_lat           NUMERIC,            -- GPS latitude at signing
    signature_lng           NUMERIC,            -- GPS longitude at signing

    -- -------------------------------------------------------
    -- ETL housekeeping
    -- -------------------------------------------------------
    etl_synced_on           TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX idx_tt_ticket_date    ON time_tickets (ticket_date);
CREATE INDEX idx_tt_job_no         ON time_tickets (job_no);
CREATE INDEX idx_tt_client         ON time_tickets (client);
CREATE INDEX idx_tt_crew_chief     ON time_tickets (crew_chief);
CREATE INDEX idx_tt_location_id    ON time_tickets (location_id);
CREATE INDEX idx_tt_submitted_on   ON time_tickets (submitted_on);

-- =============================================================
-- Idempotent migrations (safe to run against an existing table)
-- =============================================================

-- Added 2026-04-18: track where ticket_date came from so the UI can flag
-- inferred (not exact) dates for human verification.
ALTER TABLE time_tickets
    ADD COLUMN IF NOT EXISTS date_source TEXT;
