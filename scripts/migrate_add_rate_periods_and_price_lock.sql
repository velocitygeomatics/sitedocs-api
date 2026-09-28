-- Effective-dated client schedules, and prices locked at export.
--
-- Two gaps, one migration, because both answer "what did this ticket cost":
--
--  1. vgt_client_schedule_map holds one schedule per client and nothing else.
--     Reassigning a client re-priced every ticket they ever had, billed or
--     not, and left no record that the assignment had ever been different.
--     Velocity decided on 2026-09-26 that a reassignment applies from a date
--     forward, starting with the four confirmed that day, which take effect
--     from 2026-09-01. vgt_client_schedule_periods is that history. The map's
--     schedule_id stays, and is kept equal to the open period, so anything
--     that reads only the map still sees the current assignment.
--
--  2. The IIF that goes to QuickBooks carries items and hours, no dollars, so
--     nothing recorded what VGA priced a row at when it was billed. A rate
--     edit afterwards silently changed the costed ticket for work already
--     invoiced. Velocity decided the price locks at export, so VG-Time now
--     sends the price of each row it exports and time_ticket_row_prices keeps
--     it. The first lock wins, like time_ticket_row_exports: a re-export never
--     rewrites what a row was billed at.
--
-- Safe to run twice. The seed only runs into an empty periods table.

CREATE TABLE IF NOT EXISTS vgt_client_schedule_periods (
    id                   SERIAL PRIMARY KEY,
    client_id            INTEGER NOT NULL
                         REFERENCES vgt_client_schedule_map(id) ON DELETE CASCADE,
    -- RESTRICT: a schedule that priced a period of someone's work cannot be
    -- deleted out from under that history. DELETE /rates/schedules/{id}
    -- checks this first and answers 409.
    schedule_id          INTEGER NOT NULL
                         REFERENCES vgt_rate_schedules(id) ON DELETE RESTRICT,
    -- Inclusive dates, compared against time_tickets.ticket_date. NULL
    -- valid_from means "since before periods existed"; NULL valid_to means
    -- "still in force".
    valid_from           DATE,
    valid_to             DATE,
    -- The schedule this period replaced. NULL on a client's first period.
    previous_schedule_id INTEGER
                         REFERENCES vgt_rate_schedules(id) ON DELETE RESTRICT,
    changed_by           TEXT        NOT NULL,
    changed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note                 TEXT,
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
);

-- One assignment in force per client. The API closes the old period and
-- opens the new one in a single transaction; this makes a second open
-- period an error rather than a silent tie.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_schedule_periods_open
    ON vgt_client_schedule_periods (client_id) WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_client_schedule_periods_client
    ON vgt_client_schedule_periods (client_id, valid_from);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM vgt_client_schedule_periods) THEN
        RAISE NOTICE 'vgt_client_schedule_periods already seeded - seed skipped';
        RETURN;
    END IF;

    -- Every mapped client starts with one open period on its current schedule.
    INSERT INTO vgt_client_schedule_periods
        (client_id, schedule_id, valid_from, valid_to, changed_by, note)
    SELECT c.id, c.schedule_id, NULL, NULL, 'Migration',
           'Assignment in force when schedule periods were introduced'
      FROM vgt_client_schedule_map c
     WHERE c.schedule_id IS NOT NULL;

    -- The reassignments Velocity confirmed on 2026-09-26, effective from
    -- 2026-09-01. Before that these clients priced on the schedule the costed
    -- ticket had embedded for them. EPFC (190319) moved with EPFC; it has no
    -- tickets, but its history should still say where it came from.
    --
    -- Other clients also sit on a different schedule from the old embedded
    -- map (Whitecap, Petrus, Surge and others, 7 -> vg_standard_2025), but
    -- only by gaining rates the old card lacked. Back-dating them would put
    -- their posts and pipe locator back to $0, so they keep one open period.
    --
    -- Each row is applied only when the client is still on the confirmed new
    -- schedule, so a map that has moved on since is left alone.
    CREATE TEMP TABLE _moved (client_name TEXT, old_key TEXT, new_key TEXT) ON COMMIT DROP;
    INSERT INTO _moved VALUES
        ('storm',         'strathcona_csv_astera', 'vg_standard_2023'),
        ('kelt',          'strathcona_csv_astera', 'vg_standard_2023'),
        ('birchcliff',    'whitecap_petrus_surge', 'vg_standard_2023'),
        ('epfc',          'whitecap_petrus_surge', 'construction_2024_spring'),
        ('epfc (190319)', 'spartan',               'construction_2024_spring'),
        ('burgess creek', 'spartan',               'vg_standard_2025');

    INSERT INTO vgt_client_schedule_periods
        (client_id, schedule_id, valid_from, valid_to, changed_by, note)
    SELECT c.id, o.id, NULL, DATE '2026-08-31', 'Migration',
           'Schedule before the reassignment confirmed 2026-09-26'
      FROM _moved m
      JOIN vgt_client_schedule_map c ON lower(c.client_name) = m.client_name
      JOIN vgt_rate_schedules o ON o.schedule_key = m.old_key
      JOIN vgt_rate_schedules n ON n.schedule_key = m.new_key
     WHERE c.schedule_id = n.id;

    UPDATE vgt_client_schedule_periods p
       SET valid_from = DATE '2026-09-01',
           previous_schedule_id = o.id,
           note = 'Reassignment confirmed 2026-09-26, effective 2026-09-01'
      FROM _moved m
      JOIN vgt_client_schedule_map c ON lower(c.client_name) = m.client_name
      JOIN vgt_rate_schedules o ON o.schedule_key = m.old_key
      JOIN vgt_rate_schedules n ON n.schedule_key = m.new_key
     WHERE p.client_id = c.id
       AND p.valid_to IS NULL
       AND c.schedule_id = n.id;
END $$;


-- What each exported row was priced at, at the moment it was exported.
-- row_key follows time_ticket_row_exports ('crew_chief', 'sa',
-- 'equip:<column>'), but never '*': a price belongs to a row, not a ticket.
CREATE TABLE IF NOT EXISTS time_ticket_row_prices (
    form_id       UUID          NOT NULL REFERENCES time_tickets(form_id) ON DELETE CASCADE,
    row_key       TEXT          NOT NULL,
    -- The run that locked it. SET NULL rather than CASCADE: a removed run
    -- log entry must not unlock what was billed.
    run_id        UUID          REFERENCES time_ticket_export_runs(run_id) ON DELETE SET NULL,
    -- Key and name both, so the lock still reads correctly after a schedule
    -- is renamed or deleted.
    schedule_key  TEXT,
    schedule_name TEXT,
    rate_key      TEXT,
    qb_item       TEXT,
    qty           NUMERIC(12,3),
    unit          TEXT,
    rate          NUMERIC(12,4),
    amount        NUMERIC(12,2) NOT NULL,
    -- computeCost's source: 'rated', 'manual-rate' (camp, priced at
    -- invoicing), 'no-rate', 'internal' and so on. A $0 lock is only
    -- meaningful next to the reason it is zero.
    price_source  TEXT          NOT NULL,
    priced_by     TEXT,
    priced_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (form_id, row_key),
    CHECK (row_key <> '*')
);

-- No backfill. The 1,121 tickets exported before this table existed were
-- never priced at export, and inventing a price for them now would be a
-- guess recorded as a fact. They keep pricing from the dated schedules above.
