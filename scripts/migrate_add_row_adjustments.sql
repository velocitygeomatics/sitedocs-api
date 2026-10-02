-- Client-ticket adjustments: hide a line from the costed ticket, or change the
-- hours / quantity / rate it prints with (vg-dashboard/costed-ticket-edits-spec.md).
--
-- Only the client's costed ticket reads these. The SiteDocs ticket is never
-- changed, the QuickBooks export keeps the recorded durations (a hidden line
-- goes out as not billable), and time_ticket_row_prices keeps what the rate
-- engine priced at export.
--
-- row_key follows time_ticket_row_exports: 'crew_chief', 'sa', or
-- 'equip:<column>' (subsistence is 'equip:cc_subsistence' / 'equip:sa_subsistence').
-- Never '*': an adjustment belongs to a line, not a ticket.
--
-- Idempotent; safe to re-run.

BEGIN;

-- The current adjustment per line. No row = the line prints as recorded.
-- NULL in a value column = that value as recorded / as priced.
CREATE TABLE IF NOT EXISTS time_ticket_row_adjustments (
    form_id     UUID          NOT NULL REFERENCES time_tickets(form_id) ON DELETE CASCADE,
    row_key     TEXT          NOT NULL CHECK (row_key <> '*'),
    hidden      BOOLEAN       NOT NULL DEFAULT FALSE,
    travel_hrs  NUMERIC(8,2)  CHECK (travel_hrs >= 0),   -- crew lines
    work_hrs    NUMERIC(8,2)  CHECK (work_hrs   >= 0),   -- crew lines
    qty         NUMERIC(12,3) CHECK (qty        >= 0),   -- equipment lines
    rate        NUMERIC(12,4) CHECK (rate       >= 0),   -- any line
    reason      TEXT          NOT NULL CHECK (btrim(reason) <> ''),
    updated_by  TEXT          NOT NULL,
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (form_id, row_key)
);

-- Append-only history of every set and revert. No foreign key: the history
-- outlives the ticket and the adjustment it describes.
CREATE TABLE IF NOT EXISTS time_ticket_row_adjustment_log (
    id          BIGSERIAL     PRIMARY KEY,
    form_id     UUID          NOT NULL,
    row_key     TEXT          NOT NULL,
    action      TEXT          NOT NULL CHECK (action IN ('set', 'revert')),
    before      JSONB,        -- the adjustment before; NULL if there was none
    after       JSONB,        -- the adjustment after; NULL on revert
    reason      TEXT,
    changed_by  TEXT          NOT NULL,
    changed_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS time_ticket_row_adjustment_log_form
    ON time_ticket_row_adjustment_log (form_id, changed_at);

COMMIT;
