"""Guards on the `exported` filter of GET /api/time-tickets.

VG-Time's first fetch is the payroll queue: of 1143 tickets in production only
22 are still owed, so without a server-side filter the page pulled the whole
table over ~11 pages and discarded 98% of it in the browser.

The filter tests exported_on, deliberately, and not the row-level stamp table.
exported_on is set only when a ticket goes out whole, so a partly-exported
ticket has it NULL and stays in the unexported result carrying the rows it
still owes. Filtering on "has any row stamp" would drop that ticket and lose
those rows silently — the failure the row-level work exists to prevent.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

import azure.functions as func  # noqa: E402

from routes import forms  # noqa: E402


def _call(params):
    """Runs the list handler with the DB stubbed; returns every SQL it ran."""
    req = func.HttpRequest(
        method="GET",
        url="/api/time-tickets",
        headers={"X-API-Key": "test-key"},
        params=params,
        body=None,
    )
    calls = []

    def fake_query(sql, args=None):
        calls.append((sql, args))
        return [{"n": 0}] if "COUNT(*)" in sql else []

    with mock.patch.object(forms, "query", side_effect=fake_query):
        res = forms.get_time_tickets.build().get_user_function()(req)
    return res.status_code, calls


class ExportedFilter(unittest.TestCase):
    def test_unexported_only_asks_for_tickets_with_no_stamp(self):
        status, calls = _call({"exported": "false"})
        self.assertEqual(status, 200)
        for sql, _ in calls:
            self.assertIn("tt.exported_on IS NULL", sql)
            self.assertNotIn("tt.exported_on IS NOT NULL", sql)

    def test_exported_only_asks_for_stamped_tickets(self):
        _, calls = _call({"exported": "true"})
        for sql, _ in calls:
            self.assertIn("tt.exported_on IS NOT NULL", sql)

    def test_omitting_the_param_returns_both(self):
        # The default must stay "everything": other callers page this endpoint
        # for reporting, not just the payroll queue.
        _, calls = _call({})
        for sql, _ in calls:
            self.assertNotIn("exported_on IS", sql)

    def test_the_filter_reaches_the_count_as_well_as_the_rows(self):
        # The count feeds pagination. If it ignored the filter the client would
        # page through a total it can never reach.
        _, calls = _call({"exported": "false"})
        self.assertTrue(any("COUNT(*)" in sql for sql, _ in calls))
        self.assertGreaterEqual(len(calls), 2)

    def test_the_filter_never_becomes_a_bound_parameter(self):
        # It is SQL, not a value. A stray param here would shift every other
        # placeholder in the statement, so the bound-argument count has to be
        # identical with and without the filter.
        _, without = _call({})
        _, with_it = _call({"exported": "false"})
        self.assertEqual(
            [len(a or ()) for _, a in with_it],
            [len(a or ()) for _, a in without],
        )

    def test_it_does_not_consult_the_row_stamp_table(self):
        # A partly-exported ticket must stay in the unexported result.
        _, calls = _call({"exported": "false"})
        for sql, _ in calls:
            before_select = sql.split("SELECT", 2)[0] + sql.split("WHERE")[-1]
            self.assertNotIn("time_ticket_row_exports", before_select)

    def test_any_value_other_than_true_means_unexported(self):
        # exported=0 / exported=no are the same ask as exported=false.
        for value in ("0", "no", "False", "FALSE"):
            _, calls = _call({"exported": value})
            for sql, _ in calls:
                self.assertIn("tt.exported_on IS NULL", sql)


if __name__ == "__main__":
    unittest.main()
