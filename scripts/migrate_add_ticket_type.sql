-- time_tickets now carries two SiteDocs templates, not one.
--   survey        = *Time Ticket                        (6c3f93b6-1326-478b-a6d6-59aba925a1c1)
--   environmental = Environmental Scientist Time Ticket (3d2119f9-4b12-4fd0-9e92-67a4b0b9f840)
-- VG-Time reads ticket_type to decide whether the two labour rows are priced
-- as Crew Chief / Survey Assistant or as Field Technician / Field Assistant.
-- Existing rows are all survey, which is what the default gives them.
ALTER TABLE time_tickets
    ADD COLUMN IF NOT EXISTS ticket_type  TEXT NOT NULL DEFAULT 'survey',
    -- Environmental Scientist 2 has a Notes field; the survey template's
    -- assistant does not, so this column stays NULL for survey tickets.
    ADD COLUMN IF NOT EXISTS sa_notes_hrs NUMERIC;

CREATE INDEX IF NOT EXISTS idx_tt_ticket_type ON time_tickets (ticket_type);
