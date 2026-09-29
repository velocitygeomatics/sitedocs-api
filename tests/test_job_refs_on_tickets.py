"""AFE / Cost Centre and PO# on the time-ticket list, for the costed ticket.

The Latitude snapshot is the base. Job Setup's copy overrides it only when
the query found it newer than the snapshot (the SQL filters that), and each
source degrades on its own so a missing job_setup column never blanks the
Latitude refs.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

from routes import forms  # noqa: E402


class JobRefsOnTicketList(unittest.TestCase):
    def test_latitude_refs_by_trimmed_job_number(self):
        rows = [{"job_no": " 260393 "}, {"job_no": "260400"}, {"job_no": None}]
        lat = [{"job_no": "260393", "afe": "CAPL260019", "po": "4500123"}]
        with mock.patch.object(forms, "query", side_effect=[lat, []]) as q:
            forms._attach_job_refs(rows)
        self.assertEqual(q.call_args_list[0][0][1], (["260393", "260400"],))
        self.assertEqual((rows[0]["job_afe"], rows[0]["job_po"]), ("CAPL260019", "4500123"))
        self.assertIsNone(rows[1]["job_afe"])
        self.assertIsNone(rows[2]["job_po"])

    def test_a_newer_job_setup_copy_wins_even_when_it_cleared_a_ref(self):
        rows = [{"job_no": "260393"}, {"job_no": "260500"}]
        lat = [{"job_no": "260393", "afe": "OLD-AFE", "po": "OLD-PO"}]
        setup = [{"job_no": "260393", "afe": "NEW-AFE", "po": None},
                 {"job_no": "260500", "afe": None, "po": "PO-9"}]
        with mock.patch.object(forms, "query", side_effect=[lat, setup]) as q:
            forms._attach_job_refs(rows)
        self.assertIn("refs_updated_at >", q.call_args_list[1][0][0])
        self.assertIn("'tblJobs'", q.call_args_list[1][0][0])
        self.assertEqual((rows[0]["job_afe"], rows[0]["job_po"]), ("NEW-AFE", None))
        self.assertEqual((rows[1]["job_afe"], rows[1]["job_po"]), (None, "PO-9"))

    def test_job_setup_before_its_migration_keeps_the_latitude_refs(self):
        rows = [{"job_no": "260393"}]
        lat = [{"job_no": "260393", "afe": "CAPL260019", "po": None}]
        with mock.patch.object(forms, "query",
                               side_effect=[lat, RuntimeError("column refs_updated_at does not exist")]):
            forms._attach_job_refs(rows)
        self.assertEqual(rows[0]["job_afe"], "CAPL260019")

    def test_an_unreadable_latitude_copy_still_uses_job_setup(self):
        rows = [{"job_no": "260500"}]
        setup = [{"job_no": "260500", "afe": "CC 68573", "po": None}]
        with mock.patch.object(forms, "query", side_effect=[RuntimeError("boom"), setup]):
            forms._attach_job_refs(rows)
        self.assertEqual(rows[0]["job_afe"], "CC 68573")

    def test_no_job_numbers_skips_both_queries(self):
        rows = [{"job_no": " "}]
        with mock.patch.object(forms, "query") as q:
            forms._attach_job_refs(rows)
        q.assert_not_called()
        self.assertIsNone(rows[0]["job_afe"])
        self.assertIsNone(rows[0]["job_po"])


if __name__ == "__main__":
    unittest.main()
