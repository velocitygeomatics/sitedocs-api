-- The ETL run log for VG-Time's History tab.
--
-- etl_sync_state answers "when did this entity last land, and how many rows".
-- It is a single row per entity that each run overwrites, so it cannot answer
-- what the ETL has been doing: a stage that failed at 02:20 and succeeded at
-- 03:20 looks identical to one that has never failed, and a stage that stopped
-- running altogether looks like one that ran and found nothing.
--
-- That gap is what let the hourly job go ~22 hours without syncing a time
-- ticket while reporting nothing wrong: it ran every hour, successfully, on
-- the wrong stage list. Recording the run itself is what makes that visible.
--
-- One row per stage per invocation. Stages are logged individually rather than
-- per-process because that is the unit that succeeds or fails, and a chained
-- run (forms -> form_contents -> time_tickets) is three separate facts.
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id      UUID        PRIMARY KEY,
    stage       TEXT        NOT NULL,
    -- Who started it: the timer function name (hourly_incremental_sync,
    -- nightly_time_tickets_sync, ...), 'manual' for POST /api/etl/trigger, or
    -- 'cli' for a local python -m etl.run_etl. Without this a 03:20 row and a
    -- 03:20 manual re-run are indistinguishable.
    trigger     TEXT        NOT NULL DEFAULT 'cli',
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    duration_s  NUMERIC(10,1),
    -- 'ok' | 'error'. A stage that raised is recorded with its message rather
    -- than dropped: a failed run is the row the History tab most needs.
    status      TEXT        NOT NULL,
    error       TEXT,
    -- What etl_sync_state actually changed while this stage ran, as
    -- {entity: count}. Derived by diffing the state table around the stage
    -- rather than by trusting a return value, because the stage functions
    -- return None and several stages (lookups, companies) touch more than one
    -- entity. An empty object means the stage ran and stamped nothing.
    entities    JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- History is read newest-first, and the freshness panel filters by stage.
CREATE INDEX IF NOT EXISTS idx_etl_runs_started_at
    ON etl_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_etl_runs_stage_started_at
    ON etl_runs (stage, started_at DESC);

-- No backfill. Runs before this table existed were never recorded, and the
-- only trace of them is etl_sync_state's single last_sync per entity, which
-- cannot be expanded into a run history without inventing the runs. The tab
-- fills from the next run onwards; etl_sync_state is untouched, so nothing
-- already known is lost.
