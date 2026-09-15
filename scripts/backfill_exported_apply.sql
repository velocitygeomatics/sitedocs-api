-- BACKFILL — marks historically exported time tickets.
--
-- Everything in time_tickets predates the exported_on column and was already
-- pulled into QuickBooks, EXCEPT the 16 tickets below, which Norm confirmed are
-- still outstanding as of 2026-09-15. Without this, every historical ticket
-- looks unexported and reappears in the next payroll run.
--
-- Labels are matched with btrim(): four of the sixteen are stored with a
-- trailing space, which is why an untrimmed match silently missed them.
--
-- The DO block aborts the whole transaction unless exactly 16 tickets resolve.
-- A hold-out that fails to match would be stamped as exported and drop out of
-- the next payroll run, so it must fail loudly instead.
--
-- Reversible: UPDATE time_tickets SET exported_on = NULL, exported_by = NULL
--             WHERE exported_by = 'Migration';

\set ON_ERROR_STOP on

BEGIN;

CREATE TEMP TABLE holdout (form_label text, ticket_date date) ON COMMIT DROP;

INSERT INTO holdout (form_label, ticket_date) VALUES
    ('250071-JSL-DFT-09/10/2026', '2026-09-10'),
    ('1009/2026_HE_DFT',          '2026-09-10'),
    ('2026-09-10-JM - DFT 270364','2026-09-10'),
    ('2026-09-11-JM',             '2026-09-11'),
    ('260386-2026-09-11-RL-DFT',  '2026-09-11'),
    ('11/09/2026_HE_DFT',         '2026-09-11'),
    ('250071-JSL-DFT-09/11/2026', '2026-09-11'),
    ('2026-09-12-JM-DFT',         '2026-09-12'),
    ('250071_RL_09122026_DFT',    '2026-09-12'),
    ('12/09/2026_HE_DFT',         '2026-09-12'),
    ('2026-09-13-JM DFT',         '2026-09-13'),
    ('250071-JSL-DFT-09/13/2026', '2026-09-13'),
    ('13-09-2026_HE&FY_DFT',      '2026-09-13'),
    ('2026-09-14-JM DFT',         '2026-09-14'),
    ('14-09-2026_HE&FY_DFT',      '2026-09-14'),
    ('230634-2026-09-14-RL-DFT',  '2026-09-14');

-- Resolve to primary keys once, then work from those. form_id cannot be NULL
-- and cannot carry a stray space, unlike the label it came from.
CREATE TEMP TABLE holdout_ids ON COMMIT DROP AS
SELECT tt.form_id
  FROM time_tickets tt
  JOIN holdout h
    ON btrim(tt.form_label) = h.form_label
   AND tt.ticket_date::date = h.ticket_date
 WHERE tt.is_deleted = false;

DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM holdout_ids;
    IF n <> 16 THEN
        RAISE EXCEPTION
            'Expected 16 hold-out tickets, resolved %. Refusing to stamp: an '
            'unmatched hold-out would be marked exported and lost from payroll.', n;
    END IF;
END $$;

UPDATE time_tickets
   SET exported_on = TIMESTAMPTZ '2026-09-15 00:00:00-06:00',
       exported_by = 'Migration'
 WHERE is_deleted = false
   AND exported_on IS NULL
   AND form_id NOT IN (SELECT form_id FROM holdout_ids);

\echo ''
\echo '=== Result: the 16 hold-outs must all still be unstamped ==='
SELECT count(*) FILTER (WHERE exported_on IS NULL)                        AS still_unstamped,
       count(*) FILTER (WHERE exported_by = 'Migration')                  AS stamped_migration,
       count(*) FILTER (WHERE exported_on IS NOT NULL
                          AND exported_by IS DISTINCT FROM 'Migration')   AS stamped_other
  FROM time_tickets
 WHERE is_deleted = false;

\echo ''
\echo '=== The tickets left outstanding (should be your 16) ==='
SELECT ticket_date, '[' || form_label || ']' AS label, job_no, client
  FROM time_tickets
 WHERE is_deleted = false
   AND exported_on IS NULL
 ORDER BY ticket_date, form_label;

COMMIT;
