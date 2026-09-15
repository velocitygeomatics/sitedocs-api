"""
Numeric ticket dates come in both day-first and month-first order.

Four tickets carry values like '01/24/2026' that can only be month-first, so
the parser cannot simply be flipped to day-first. Form 'Test' carries
'12-01-2026' and was submitted 2026-01-12: both readings are real dates, and
it was the only ticket in the table dated in the future. The submission date
is what separates them.
"""
import unittest
from datetime import date

from etl_time_tickets import _date, _submitted_date, parse_time_ticket


class TestNumericDateOrder(unittest.TestCase):

    def test_the_test_form_reads_as_january_not_december(self):
        self.assertEqual(_date("12-01-2026", date(2026, 1, 12)), "2026-01-12")

    def test_unambiguous_month_first_values_are_unchanged(self):
        # The day-first reading of these is not a real date, so there is
        # nothing to choose and they must stay as they are.
        self.assertEqual(_date("01/24/2026", date(2026, 1, 25)), "2026-01-24")
        self.assertEqual(_date("01/26/2026", date(2026, 1, 26)), "2026-01-26")

    def test_month_first_still_wins_when_it_matches_the_submission(self):
        # 03/04/2026 submitted on the 4th of March is March 4, not April 3.
        self.assertEqual(_date("03/04/2026", date(2026, 3, 4)), "2026-03-04")

    def test_day_first_wins_when_it_matches_the_submission(self):
        self.assertEqual(_date("03/04/2026", date(2026, 4, 3)), "2026-04-03")

    def test_without_a_submission_date_month_first_is_kept(self):
        self.assertEqual(_date("12-01-2026"), "2026-12-01")

    def test_a_ticket_is_never_dated_months_after_it_was_submitted(self):
        got = _date("12-01-2026", date(2026, 1, 12))
        self.assertLessEqual(date.fromisoformat(got), date(2026, 1, 12))

    def test_other_date_formats_are_untouched(self):
        sub = date(2026, 9, 14)
        self.assertEqual(_date("September 14, 2026", sub), "2026-09-14")
        self.assertEqual(_date("2026-09-14", sub), "2026-09-14")
        self.assertEqual(_date("260211", sub), "2026-02-11")
        self.assertIsNone(_date(None, sub))

    def test_submitted_date_is_read_off_the_form(self):
        self.assertEqual(_submitted_date({"CreatedOn": "2026-01-12T18:30:11.5-07:00"}),
                         date(2026, 1, 12))
        self.assertEqual(_submitted_date({"CreatedOn": "2026-01-12T18:30:11.5"}),
                         date(2026, 1, 12))
        self.assertIsNone(_submitted_date({}))
        self.assertIsNone(_submitted_date({"CreatedOn": "not a date"}))

    def test_the_real_test_form_parses_to_january(self):
        form = {"Id": "a1b48c55-e255-4a92-9bd0-536849b9a432", "Label": "Test",
                "LocationId": None, "CreatedOn": "2026-01-12T18:30:11.500000-07:00",
                "IsDeleted": False}
        content = {"Groups": [{"Title": "General Information",
                               "Items": [{"Content": "Date:", "Value": "12-01-2026"}]}]}
        self.assertEqual(parse_time_ticket(form, content)["ticket_date"], "2026-01-12")


if __name__ == "__main__":
    unittest.main()
