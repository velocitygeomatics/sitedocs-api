-- Client-name cleanup decided 2026-09-28.
--
-- Tickets now price on their job's Latitude client, so the typed-name
-- misspellings no longer matter. What is left are two entries no ticket has
-- ever matched, because their names are labels rather than names that appear
-- in a client:
--
--  1. "Gold Base (260009)" was meant for Golden Base Contracting. It stays on
--     VG Standard 2025 (decided 2026-09-28), so renaming it changes no price;
--     it only makes the entry match and show against Golden Base's tickets.
--     The job-specific "Gold Base 260009" schedule stays unused.
--
--  2. "Municipal clients" never matched, so municipalities priced on the
--     default. From 2026-09-01 they price on the Municipal schedule (decided
--     2026-09-28): one entry per name form Latitude uses, each on the default
--     up to 2026-08-31 and on Municipal from 2026-09-01, logged as a change
--     from the default, the same history POST /rates/clients writes.
--     "Town of" does not catch "Town & Country Realty"; "County" does not
--     catch "Country".
--
-- Safe to run twice. Run in one transaction.

BEGIN;

UPDATE vgt_client_schedule_map
   SET client_name = 'Golden Base',
       notes = 'Golden Base Contracting. Was "Gold Base (260009)", which no ticket matched; renamed 2026-09-28',
       updated_at = NOW()
 WHERE client_name = 'Gold Base (260009)'
   AND NOT EXISTS (SELECT 1 FROM vgt_client_schedule_map WHERE lower(client_name) = 'golden base');

-- Nothing hangs off it but its migration period (removed with it).
DELETE FROM vgt_client_schedule_map
 WHERE client_name = 'Municipal clients'
   AND NOT EXISTS (SELECT 1 FROM vgt_client_rate_overrides o WHERE o.client_id = vgt_client_schedule_map.id);

CREATE TEMP TABLE _municipal (client_name TEXT) ON COMMIT DROP;
INSERT INTO _municipal VALUES
    ('County'), ('Town of'), ('City of'), ('Village of'),
    ('MD of'), ('Municipal District'), ('SV of');

INSERT INTO vgt_client_schedule_map (client_name, schedule_id, notes)
SELECT m.client_name, s.id, 'Municipality; added 2026-09-28'
  FROM _municipal m
  JOIN vgt_rate_schedules s ON s.schedule_key = 'municipal'
 WHERE NOT EXISTS (SELECT 1 FROM vgt_client_schedule_map c WHERE lower(c.client_name) = lower(m.client_name));

INSERT INTO vgt_client_schedule_periods (client_id, schedule_id, valid_from, valid_to, changed_by, note)
SELECT c.id, d.id, NULL, DATE '2026-08-31', 'Norm Dickson', 'Default schedule before the client had an entry'
  FROM _municipal m
  JOIN vgt_client_schedule_map c ON c.client_name = m.client_name
  JOIN vgt_rate_schedules d ON d.schedule_key = 'vg_standard_2025'
 WHERE NOT EXISTS (SELECT 1 FROM vgt_client_schedule_periods p WHERE p.client_id = c.id);

INSERT INTO vgt_client_schedule_periods
       (client_id, schedule_id, valid_from, valid_to, previous_schedule_id, changed_by, note)
SELECT c.id, s.id, DATE '2026-09-01', NULL, d.id, 'Norm Dickson',
       'Municipalities on the Municipal schedule, decided 2026-09-28'
  FROM _municipal m
  JOIN vgt_client_schedule_map c ON c.client_name = m.client_name
  JOIN vgt_rate_schedules s ON s.schedule_key = 'municipal'
  JOIN vgt_rate_schedules d ON d.schedule_key = 'vg_standard_2025'
 WHERE NOT EXISTS (SELECT 1 FROM vgt_client_schedule_periods p WHERE p.client_id = c.id AND p.valid_to IS NULL);

-- What the run left: expect Golden Base on VG Standard 2025, no
-- "Municipal clients", and seven municipal entries with two periods each.
SELECT c.client_name, s.name AS schedule, p.valid_from, p.valid_to
  FROM vgt_client_schedule_map c
  JOIN vgt_client_schedule_periods p ON p.client_id = c.id
  JOIN vgt_rate_schedules s ON s.id = p.schedule_id
 WHERE c.client_name IN ('Golden Base', 'Municipal clients', 'County', 'Town of', 'City of',
                         'Village of', 'MD of', 'Municipal District', 'SV of')
 ORDER BY c.client_name, p.valid_from NULLS FIRST;

COMMIT;
