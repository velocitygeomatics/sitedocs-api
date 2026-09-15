-- DRY RUN — writes nothing. Verifies the 16 hold-out tickets before any backfill.
--
-- Everything already in time_tickets was exported to QuickBooks before the
-- exported_on column existed, EXCEPT the 16 tickets listed below. The backfill
-- stamps the rest as historically exported so they stop reappearing in payroll
-- runs. That makes the hold-out list load-bearing: a ticket that fails to match
-- here gets stamped by mistake and silently drops out of the next run.

CREATE TEMP TABLE not_exported (form_label text, ticket_date date);

INSERT INTO not_exported (form_label, ticket_date) VALUES
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

\echo '=== 1. Hold-out tickets: how many rows does each match? ==='
\echo '    Every line must read 1. A 0 means the label/date does not exist as'
\echo '    typed. Anything above 1 means the pair is ambiguous.'
SELECT ne.form_label,
       ne.ticket_date,
       count(tt.form_id) AS matched
  FROM not_exported ne
  LEFT JOIN time_tickets tt
         ON tt.form_label = ne.form_label
        AND tt.ticket_date::date = ne.ticket_date
        AND tt.is_deleted = false
 GROUP BY ne.form_label, ne.ticket_date
 ORDER BY ne.ticket_date, ne.form_label;

\echo ''
\echo '=== 2. Scope of the backfill ==='
SELECT count(*) FILTER (WHERE exported_on IS NULL)                      AS unstamped_now,
       count(*) FILTER (WHERE is_deleted)                               AS deleted_excluded,
       min(ticket_date) FILTER (WHERE is_deleted = false)               AS oldest,
       max(ticket_date) FILTER (WHERE is_deleted = false)               AS newest
  FROM time_tickets;

\echo ''
\echo '=== 3. Rows that WOULD be stamped (count only) ==='
SELECT count(*) AS would_stamp
  FROM time_tickets tt
 WHERE tt.is_deleted = false
   AND tt.exported_on IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM not_exported ne
        WHERE ne.form_label = tt.form_label
          AND ne.ticket_date = tt.ticket_date::date);

\echo ''
\echo '=== 4. The 10 most recent that WOULD be stamped ==='
\echo '    Sanity check: nothing here should be a ticket you still need to run.'
SELECT tt.ticket_date, tt.form_label, tt.job_no, tt.client
  FROM time_tickets tt
 WHERE tt.is_deleted = false
   AND tt.exported_on IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM not_exported ne
        WHERE ne.form_label = tt.form_label
          AND ne.ticket_date = tt.ticket_date::date)
 ORDER BY tt.ticket_date DESC
 LIMIT 10;

DROP TABLE not_exported;
