-- The export-run log for VG-Time's History tab.
--
-- time_tickets.exported_on and time_ticket_row_exports record that a ticket or
-- one of its components has gone out. Neither records the run: which tickets
-- went into one IIF together, under what filename, how many lines it carried,
-- or who pressed the button on that occasion. VG-Time's History tab was built
-- against a SharePoint list for exactly that, the four Power Automate flows
-- were never provisioned, and the tab has therefore been empty on every page
-- load since - the in-page array it renders from is reset by any reload.
--
-- The stamps cannot be grouped back into runs after the fact with any
-- confidence: two exports a second apart by the same person are one cluster,
-- and a partial ticket appears in several runs under the same timestamp. So
-- the run is recorded as its own fact, at the moment it happens.
CREATE TABLE IF NOT EXISTS time_ticket_export_runs (
    run_id       UUID        PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exported_by  TEXT        NOT NULL,
    file_name    TEXT,
    iif_lines    INTEGER,
    total_hours  NUMERIC(10,2),
    employees    TEXT[]      NOT NULL DEFAULT '{}',
    -- What the stamping actually did, not what the caller asked for. A run
    -- that re-exported tickets already spoken for is a different event from
    -- one that marked them fresh, and the History tab exists to make that
    -- visible rather than report a uniform success.
    tickets_marked  INTEGER NOT NULL DEFAULT 0,
    tickets_already INTEGER NOT NULL DEFAULT 0,
    tickets_missing INTEGER NOT NULL DEFAULT 0,
    rows_marked     INTEGER NOT NULL DEFAULT 0
);

-- History is read newest-first and nothing else queries this table.
CREATE INDEX IF NOT EXISTS idx_export_runs_run_at
    ON time_ticket_export_runs (run_at DESC);

-- What went into each run. Kept separate from time_ticket_row_exports, which
-- answers "has this component gone out at all" and is deliberately unique per
-- (form_id, row_key): a component re-exported in a later run keeps its first
-- stamp there, but must still appear in the later run here.
--
-- row_key follows the same convention as time_ticket_row_exports, including
-- '*' for a whole ticket. ON DELETE CASCADE on the run, because a run with its
-- contents removed would be a worse record than no run at all.
CREATE TABLE IF NOT EXISTS time_ticket_export_run_items (
    run_id   UUID NOT NULL REFERENCES time_ticket_export_runs(run_id) ON DELETE CASCADE,
    form_id  UUID NOT NULL REFERENCES time_tickets(form_id) ON DELETE CASCADE,
    row_key  TEXT NOT NULL,
    PRIMARY KEY (run_id, form_id, row_key)
);

-- No backfill. Runs before this table existed were never recorded and cannot
-- be reconstructed from the per-ticket stamps without inventing the grouping;
-- the History tab will fill from the next export onwards. The stamps
-- themselves are untouched, so nothing already known is lost.
