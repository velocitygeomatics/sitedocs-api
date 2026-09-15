-- QuickBooks export state for time tickets.
--
-- exported_on is the positive marker: NULL means the ticket has never been
-- pulled into a payroll run, a timestamp means it has. VG-Time's "Hide
-- Already Exported" filter and its duplicate guard both read it, so the
-- state has to outlive the browser session that set it.
--
-- Existing rows stay NULL, which is correct — nothing was durably marked
-- before this, so every ticket is fair game for the next run.
ALTER TABLE time_tickets
    ADD COLUMN IF NOT EXISTS exported_on TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS exported_by TEXT;

-- Payroll runs filter on "not yet exported", so index the nulls too.
CREATE INDEX IF NOT EXISTS idx_tt_exported_on ON time_tickets (exported_on);
