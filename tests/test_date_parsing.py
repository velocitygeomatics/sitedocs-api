"""Tests for the time-ticket date parsing in etl_time_tickets.py.

Run with: python -m unittest discover -s tests

Background: 19 of 782 tickets on file had a null ticket_date, so they vanished
from every date-ranged view. Reading the raw form content showed two distinct
causes, and only one of them is a parsing bug:

  * 17 forms carry no "Date" field at all (only an empty "Approval Date") and
    their labels hold no date either, e.g. "250071-JSL-DFT". Nothing was ever
    recorded. These stay null, and the tests below pin that down so nobody
    "fixes" them by substituting submitted_on — the measured lag between
    submission and ticket date is 1 day for 517 of 759 tickets and 0 days for
    only 114, so that substitution would be wrong far more often than right.

  * 2 forms carry a Date field holding a bare YYMMDD string ("260211"), which
    no format in _date() matched, and their labels use the same YYMMDD
    convention, which _date_from_label() did not recognise either.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl_time_tickets import _date, _date_from_label, _yymmdd


class YYMMDDToken(unittest.TestCase):
    def test_reads_a_real_date(self):
        self.assertEqual(_yymmdd("260211"), "2026-02-11")

    def test_rejects_a_job_number(self):
        # 250071 is job 0071 of 2025, not the 71st day of month 00.
        self.assertIsNone(_yymmdd("250071"))

    def test_rejects_an_impossible_day(self):
        self.assertIsNone(_yymmdd("260231"))  # no 31st of February


class DateField(unittest.TestCase):
    def test_bare_yymmdd(self):
        self.assertEqual(_date("260211"), "2026-02-11")

    def test_two_digit_year_spellings(self):
        # Both of these were logged as unparseable against the live data.
        self.assertEqual(_date("Feb 20, 26"), "2026-02-20")
        self.assertEqual(_date("April 28/26"), "2026-04-28")

    def test_a_full_year_still_wins(self):
        self.assertEqual(_date("Feb 20, 2026"), "2026-02-20")
        self.assertEqual(_date("2026-02-20"), "2026-02-20")

    def test_six_digits_that_are_not_a_date_fall_through(self):
        self.assertIsNone(_date("250071"))

    def test_empty_is_none(self):
        self.assertIsNone(_date(None))
        self.assertIsNone(_date(""))


class LabelFallback(unittest.TestCase):
    def test_trailing_yymmdd_segment(self):
        self.assertEqual(_date_from_label("260009-SS-260216-Golden Base"),
                         "2026-02-16")
        self.assertEqual(_date_from_label("260053-SS-260211"), "2026-02-11")

    def test_leading_job_number_is_never_read_as_a_date(self):
        # The regression this guard exists for: 92 labels on file open with a
        # job number that parses as a valid calendar date. 260211 as a job
        # number must not become 2026-02-11 when nothing else carries a date.
        self.assertIsNone(_date_from_label("260211-JSL-DFT"))

    def test_existing_patterns_still_work(self):
        self.assertEqual(_date_from_label("130005-2025-10-28-ME-DFT"),
                         "2025-10-28")
        self.assertEqual(_date_from_label("150139-JS-03172026-FT"),
                         "2026-03-17")

    def test_a_label_with_no_date_stays_none(self):
        # The 17 unrecoverable tickets. Returning None is the correct answer;
        # see the module docstring on why submitted_on is not a substitute.
        self.assertIsNone(_date_from_label("250071-JSL-DFT"))
        self.assertIsNone(_date_from_label("250071-JSL-DFT (revision)"))
        self.assertIsNone(_date_from_label("Velocity Group Daily Field Ticket"))
        self.assertIsNone(_date_from_label(""))
        self.assertIsNone(_date_from_label(None))


if __name__ == "__main__":
    unittest.main()
