"""Guards on POST /api/time-tickets/export — the QuickBooks export stamp.

Before this endpoint existed, VG-Time marked tickets exported by PATCHing a
field SiteDocs does not have, so nothing persisted and the same tickets came
back in the next payroll run. The stamp now lives on time_tickets, which makes
two things load-bearing: a re-export must not overwrite the original stamp,
and the response must distinguish "already exported" from "no such ticket".
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

import azure.functions as func  # noqa: E402

from routes import forms  # noqa: E402


def _req(body, key="test-key"):
    return func.HttpRequest(
        method="POST",
        url="/api/time-tickets/export",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        params={},
        body=json.dumps(body).encode(),
    )


def _call(body, marked=0, found=0, key="test-key"):
    """Runs the handler with the DB stubbed; returns (status, payload, sql, params)."""
    captured = {}

    def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"marked": marked, "found": found}]

    with mock.patch.object(forms, "execute", side_effect=fake_execute):
        res = forms.mark_time_tickets_exported.build().get_user_function()(_req(body, key))
    payload = json.loads(res.get_body())
    return res.status_code, payload, captured.get("sql"), captured.get("params")


ID_A = "6c3f93b6-1326-478b-a6d6-59aba925a1c1"
ID_B = "3d2119f9-4b12-4fd0-9e92-67a4b0b9f840"


class ExportStamp(unittest.TestCase):
    def test_marks_the_tickets_and_records_who(self):
        status, payload, sql, params = _call(
            {"formIds": [ID_A, ID_B], "exportedBy": "norm"}, marked=2, found=2
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["marked"], 2)
        self.assertEqual(payload["exportedBy"], "norm")
        self.assertEqual(params[0], "norm")

    def test_a_re_export_does_not_overwrite_the_original_stamp(self):
        # The guard is in SQL: only rows with exported_on IS NULL are touched.
        _, payload, sql, _ = _call({"formIds": [ID_A, ID_B]}, marked=0, found=2)
        self.assertIn("exported_on IS NULL", sql)
        self.assertEqual(payload["marked"], 0)
        self.assertEqual(payload["already"], 2, "both were already exported")
        self.assertEqual(payload["missing"], 0)

    def test_an_unknown_ticket_counts_as_missing_not_already_exported(self):
        _, payload, _, _ = _call({"formIds": [ID_A, ID_B]}, marked=1, found=1)
        self.assertEqual(payload["marked"], 1)
        self.assertEqual(payload["already"], 0)
        self.assertEqual(payload["missing"], 1)

    def test_an_absent_exporter_falls_back_rather_than_writing_null(self):
        _, payload, _, params = _call({"formIds": [ID_A]}, marked=1, found=1)
        self.assertEqual(payload["exportedBy"], "unknown")
        self.assertEqual(params[0], "unknown")

    def test_duplicate_ids_are_counted_once(self):
        _, payload, _, _ = _call({"formIds": [ID_A, ID_A]}, marked=1, found=1)
        self.assertEqual(payload["total"], 1)


class ExportStampRejects(unittest.TestCase):
    def test_a_bad_api_key(self):
        status, _, sql, _ = _call({"formIds": [ID_A]}, key="wrong")
        self.assertEqual(status, 401)
        self.assertIsNone(sql, "must not reach the database")

    def test_an_empty_list(self):
        status, _, sql, _ = _call({"formIds": []})
        self.assertEqual(status, 400)
        self.assertIsNone(sql)

    def test_a_missing_formids_key(self):
        status, _, sql, _ = _call({"exportedBy": "norm"})
        self.assertEqual(status, 400)
        self.assertIsNone(sql)

    def test_a_non_uuid_id(self):
        # Reaching the DB with this would raise a 500 out of psycopg2 instead.
        status, _, sql, _ = _call({"formIds": [ID_A, "'; DROP TABLE time_tickets;--"]})
        self.assertEqual(status, 400)
        self.assertIsNone(sql)


if __name__ == "__main__":
    unittest.main()
