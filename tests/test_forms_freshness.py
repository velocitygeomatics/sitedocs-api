"""Guards on the freshness block that /etl/status and /etl/summary report.

The nightly ETL reported success from 2026-06-16 to 2026-09-14 while zero
forms arrived, and the dashboard showed "operational" throughout because every
health signal watched whether the job ran, not whether data landed. The
freshness block is the signal that would have gone amber on day four.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import etl
from routes.etl import FORMS_MAX_AGE_HOURS, freshness_of

NOW = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)


class FreshnessOf(unittest.TestCase):
    def test_a_form_from_last_night_is_fresh(self):
        f = freshness_of(NOW - timedelta(hours=9), NOW)
        self.assertFalse(f["stale"])
        self.assertEqual(f["age_hours"], 9.0)
        self.assertEqual(f["max_age_hours"], FORMS_MAX_AGE_HOURS)

    def test_a_weekend_gap_is_not_stale(self):
        # Friday 17:00 MDT -> Monday 03:00 UTC (Sunday 21:00 MDT): ~52 h.
        self.assertFalse(freshness_of(NOW - timedelta(hours=52), NOW)["stale"])

    def test_just_past_the_limit_is_stale(self):
        f = freshness_of(NOW - timedelta(hours=FORMS_MAX_AGE_HOURS, minutes=6), NOW)
        self.assertTrue(f["stale"])

    def test_exactly_at_the_limit_is_still_fresh(self):
        self.assertFalse(freshness_of(NOW - timedelta(hours=FORMS_MAX_AGE_HOURS), NOW)["stale"])

    def test_the_june_outage_would_have_read_as_stale(self):
        latest = datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc)
        f = freshness_of(latest, NOW)
        self.assertTrue(f["stale"])
        self.assertGreater(f["age_hours"], 90 * 24)

    def test_an_empty_table_is_stale_not_a_crash(self):
        f = freshness_of(None, NOW)
        self.assertTrue(f["stale"])
        self.assertIsNone(f["latest_created_on"])
        self.assertIsNone(f["age_hours"])

    def test_latest_is_serialised_as_iso8601(self):
        f = freshness_of(NOW - timedelta(hours=1), NOW)
        self.assertEqual(f["latest_created_on"], "2026-09-15T02:00:00+00:00")


class FormsFreshnessQuery(unittest.TestCase):
    def test_reads_max_created_on_from_forms(self):
        # Off the real clock, not NOW: _forms_freshness calls
        # datetime.now() itself, so a latest built from the frozen NOW
        # aged past the 72 h threshold three days after NOW and the test
        # began failing on its own.
        latest = datetime.now(timezone.utc) - timedelta(hours=5)
        with mock.patch.object(etl, "query", return_value=[{"latest": latest}]) as q:
            out = etl._forms_freshness()
        self.assertIn("MAX(created_on)", q.call_args[0][0])
        self.assertIn("FROM forms", q.call_args[0][0])
        self.assertFalse(out["forms"]["stale"])

    def test_no_rows_means_stale(self):
        with mock.patch.object(etl, "query", return_value=[]):
            self.assertTrue(etl._forms_freshness()["forms"]["stale"])
