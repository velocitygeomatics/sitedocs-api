-- DIAGNOSTIC — writes nothing.
-- The dry run showed two things that block the backfill: four hold-out labels
-- matched nothing, and ticket_date is NULL on an unknown number of rows.

\echo '=== A. How bad is the NULL ticket_date problem? ==='
SELECT count(*)                                        AS total,
       count(*) FILTER (WHERE ticket_date IS NULL)      AS null_date,
       count(*) FILTER (WHERE ticket_date IS NOT NULL)  AS has_date
  FROM time_tickets
 WHERE is_deleted = false;

\echo ''
\echo '=== B. Every JM ticket in the period, exactly as stored ==='
\echo '    Brackets expose leading/trailing spaces. Compare against the four'
\echo '    that matched nothing: 2026-09-11-JM, 2026-09-12-JM-DFT,'
\echo '    2026-09-13-JM DFT, 2026-09-14-JM DFT'
SELECT '[' || form_label || ']' AS label_exact,
       ticket_date,
       submitted_on::date AS submitted,
       job_no,
       client,
       form_id
  FROM time_tickets
 WHERE is_deleted = false
   AND form_label ILIKE '%JM%'
   AND (ticket_date >= '2026-09-08' OR ticket_date IS NULL)
 ORDER BY ticket_date NULLS LAST, form_label;

\echo ''
\echo '=== C. Anything undated that looks recent ==='
\echo '    If the four missing tickets are here, ticket_date is the problem,'
\echo '    not the label.'
SELECT '[' || form_label || ']' AS label_exact,
       submitted_on::date AS submitted,
       job_no,
       client,
       form_id
  FROM time_tickets
 WHERE is_deleted = false
   AND ticket_date IS NULL
 ORDER BY submitted_on DESC NULLS LAST
 LIMIT 40;
