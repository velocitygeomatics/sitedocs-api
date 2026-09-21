"""Guards on the etl_runs log behind VG-Time's History tab.

etl_sync_state keeps one row per entity that each run overwrites, so it can say
when time_tickets last landed but not what the ETL has been doing. That gap is
what let hourly_incremental_sync run every hour for months on the wrong stage
list: it succeeded every time, on forms only, and time tickets went ~22 hours
between syncs with no signal anywhere that said so.

These cover the two things the log has to get right to be worth having: that
what a stage touched is derived from the state table rather than guessed, and
that a failed stage still leaves a row.
"""
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.utils import diff_sync_state, record_etl_run

T0 = datetime(2026, 9, 20, 2, 20, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=41)


class DiffSyncState(unittest.TestCase):
    """What a stage touched, read off etl_sync_state either side of it."""

    def test_an_entity_the_stage_stamped_is_reported_with_its_count(self):
        before = {"forms": (T0, 5)}
        after = {"forms": (T1, 11)}
        self.assertEqual(diff_sync_state(before, after), {"forms": 11})

    def test_an_entity_the_stage_never_reached_is_absent(self):
        before = {"forms": (T0, 5), "workers": (T0, 3)}
        after = {"forms": (T1, 11), "workers": (T0, 3)}
        self.assertEqual(diff_sync_state(before, after), {"forms": 11})

    def test_an_entity_seen_for_the_first_time_counts_as_touched(self):
        self.assertEqual(diff_sync_state({}, {"time_tickets": (T1, 1160)}),
                         {"time_tickets": 1160})

    def test_a_restamp_with_an_unchanged_count_still_counts_as_touched(self):
        # last_sync advancing is the fact that matters: the stage ran and
        # found nothing new, which is different from not running at all.
        before = {"forms": (T0, 11)}
        after = {"forms": (T1, 11)}
        self.assertEqual(diff_sync_state(before, after), {"forms": 11})

    def test_a_stage_that_stamped_nothing_reports_an_empty_dict(self):
        state = {"forms": (T0, 11)}
        self.assertEqual(diff_sync_state(state, dict(state)), {})

    def test_a_multi_entity_stage_reports_every_entity_it_moved(self):
        # lookups and companies each touch several entities in one stage.
        before = {"company_types": (T0, 4), "companies": (T0, 11), "workers": (T0, 60)}
        after = {"company_types": (T1, 4), "companies": (T1, 12), "workers": (T0, 60)}
        self.assertEqual(diff_sync_state(before, after),
                         {"company_types": 4, "companies": 12})


class _FakeCursor:
    def __init__(self, sink, fail=False):
        self.sink, self.fail = sink, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("relation \"etl_runs\" does not exist")
        self.sink.append((sql, params))


class _FakeConn:
    def __init__(self, fail=False):
        self.statements, self.commits, self.rollbacks, self.fail = [], 0, 0, fail

    def cursor(self):
        return _FakeCursor(self.statements, self.fail)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class RecordEtlRun(unittest.TestCase):
    def _record(self, conn, status="ok", error=None, entities=None):
        return record_etl_run(conn, uuid.uuid4(), "time_tickets",
                              "hourly_incremental_sync", T0, T1,
                              status, error, entities or {"time_tickets": 1160})

    def test_a_successful_stage_is_written_and_committed(self):
        conn = _FakeConn()
        self.assertTrue(self._record(conn))
        self.assertEqual(conn.commits, 1)
        sql, params = conn.statements[0]
        self.assertIn("INSERT INTO etl_runs", sql)
        self.assertIn("time_tickets", params)
        self.assertIn("hourly_incremental_sync", params)

    def test_the_duration_is_recorded_from_the_two_timestamps(self):
        conn = _FakeConn()
        self._record(conn)
        self.assertIn(41.0, conn.statements[0][1])

    def test_a_failed_stage_is_recorded_with_its_message(self):
        conn = _FakeConn()
        self.assertTrue(self._record(conn, status="error", error="API 500"))
        params = conn.statements[0][1]
        self.assertIn("error", params)
        self.assertIn("API 500", params)

    def test_a_long_error_is_truncated_rather_than_rejected(self):
        conn = _FakeConn()
        self._record(conn, status="error", error="x" * 5000)
        self.assertIn("x" * 2000, conn.statements[0][1])
        self.assertNotIn("x" * 2001, conn.statements[0][1])

    def test_logging_failure_does_not_raise_and_reports_false(self):
        # The sync is already committed by the time this runs. Losing a sync to
        # a history write would be the worse outcome, so it must stay quiet.
        conn = _FakeConn(fail=True)
        with mock.patch("etl.utils.log"):
            self.assertFalse(self._record(conn))
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
