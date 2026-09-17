-- Row-level QuickBooks export state for time tickets.
--
-- time_tickets.exported_on marks a whole ticket. VG-Time selects per row, so
-- exporting part of a ticket stamped the rows that were deliberately left out
-- and they disappeared from the payroll queue on the next load. The client
-- side of that was fixed by refusing to stamp a partial ticket, which trades
-- the silent loss for the opposite risk: the ticket comes back with every row
-- unexported, including ones already delivered in an IIF.
--
-- This table is the missing granularity. One row per exported component of a
-- ticket, so a partial export records exactly what went out.
--
-- row_key is the component's stable identity, not a database id: 'crew_chief',
-- 'sa', or the equipment column the row was built from ('truck_km',
-- 'pipe_locator_hrs', ...). VG-Time rows are derived fresh on every fetch and
-- their in-page ids are a session counter, so the column name is the only
-- thing that survives a reload.
--
-- '*' is reserved for a whole-ticket stamp: what the endpoint has always
-- recorded, and what the backfill below writes for tickets exported before
-- this table existed. It means "all of it", not "one row called star".
CREATE TABLE IF NOT EXISTS time_ticket_row_exports (
    form_id     UUID        NOT NULL REFERENCES time_tickets(form_id) ON DELETE CASCADE,
    row_key     TEXT        NOT NULL,
    exported_on TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exported_by TEXT        NOT NULL,
    PRIMARY KEY (form_id, row_key)
);

-- The queue reads "what has already gone out for this ticket", always by
-- form_id. The primary key serves that, so no second index is needed.

-- Backfill: every ticket already carrying a stamp goes in as a whole-ticket
-- export under its original timestamp and exporter. Which rows actually went
-- into those files is not recorded anywhere, so it is not invented here — '*'
-- says the granularity is unknown and the whole ticket is spoken for, which
-- is exactly what the old column meant.
INSERT INTO time_ticket_row_exports (form_id, row_key, exported_on, exported_by)
SELECT form_id, '*', exported_on, COALESCE(exported_by, 'unknown')
  FROM time_tickets
 WHERE exported_on IS NOT NULL
ON CONFLICT (form_id, row_key) DO NOTHING;
