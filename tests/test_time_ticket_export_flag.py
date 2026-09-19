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


# The handler also writes an export-run log. That is a separate concern with
# its own tests below, and it must not displace what these helpers capture:
# every test here is about the stamping SQL.
def _is_run_log(sql):
    return "time_ticket_export_run" in sql


def _call(body, marked=0, found=0, key="test-key"):
    """Runs the handler with the DB stubbed; returns (status, payload, sql, params)."""
    captured = {}

    def fake_execute(sql, params=None):
        if _is_run_log(sql):
            return []
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


# ── Row-level stamps ────────────────────────────────────────────────
#
# VG-Time selects per row but a ticket was stamped whole, so exporting part
# of one marked the rows deliberately left out and they vanished from the
# payroll queue. time_ticket_row_exports records what actually went out.


def _call_all(body, marked=0, found=0, inserted=0, key="test-key"):
    """Like _call but keeps every execute() call — a mixed run makes two."""
    calls = []

    def fake_execute(sql, params=None):
        if _is_run_log(sql):
            return []
        calls.append((sql, params))
        if "RETURNING form_id" in sql and "UPDATE time_tickets" not in sql:
            return [{"form_id": "x"}] * inserted
        return [{"marked": marked, "found": found}]

    with mock.patch.object(forms, "execute", side_effect=fake_execute):
        res = forms.mark_time_tickets_exported.build().get_user_function()(_req(body, key))
    return res.status_code, json.loads(res.get_body()), calls


class RowLevelStamp(unittest.TestCase):
    def test_rows_are_recorded_without_stamping_the_ticket(self):
        # The whole point: a partial export must leave the ticket in the queue.
        status, payload, calls = _call_all(
            {"rows": [{"formId": ID_A, "rowKey": "crew_chief"}], "exportedBy": "norm"},
            inserted=1,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["rowsMarked"], 1)
        self.assertEqual(len(calls), 1)
        sql = calls[0][0]
        self.assertIn("time_ticket_row_exports", sql)
        self.assertNotIn("UPDATE time_tickets", sql)

    def test_a_whole_ticket_stamp_also_records_a_star_row(self):
        _, payload, calls = _call_all({"formIds": [ID_A]}, marked=1, found=1)
        sql = calls[0][0]
        self.assertIn("UPDATE time_tickets", sql)
        self.assertIn("time_ticket_row_exports", sql)
        self.assertIn("'*'", sql)
        self.assertEqual(payload["marked"], 1)

    def test_a_mixed_run_stamps_complete_tickets_and_rows_of_partial_ones(self):
        status, payload, calls = _call_all(
            {
                "formIds": [ID_A],
                "rows": [{"formId": ID_B, "rowKey": "sa"},
                         {"formId": ID_B, "rowKey": "truck_km"}],
                "exportedBy": "norm",
            },
            marked=1, found=1, inserted=2,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["marked"], 1)
        self.assertEqual(payload["rowsMarked"], 2)
        self.assertEqual(payload["rowsTotal"], 2)
        self.assertEqual(len(calls), 2)

    def test_an_already_exported_row_keeps_its_original_stamp(self):
        # Same rule the ticket-level stamp has always had, one level down.
        _, _, calls = _call_all(
            {"rows": [{"formId": ID_A, "rowKey": "sa"}]}, inserted=0
        )
        self.assertIn("ON CONFLICT (form_id, row_key) DO NOTHING", calls[0][0])

    def test_duplicate_row_keys_are_counted_once(self):
        _, payload, _ = _call_all(
            {"rows": [{"formId": ID_A, "rowKey": "sa"},
                      {"formId": ID_A, "rowKey": "sa"}]},
            inserted=1,
        )
        self.assertEqual(payload["rowsTotal"], 1)

    def test_the_row_key_reaches_the_query_as_a_parameter(self):
        # Never interpolated: row keys come from the client.
        _, _, calls = _call_all(
            {"rows": [{"formId": ID_A, "rowKey": "truck_km"}]}, inserted=1
        )
        sql, params = calls[0]
        self.assertNotIn("truck_km", sql)
        self.assertIn("truck_km", params[2])


class RowLevelStampRejects(unittest.TestCase):
    def test_neither_formids_nor_rows(self):
        status, _, calls = _call_all({"exportedBy": "norm"})
        self.assertEqual(status, 400)
        self.assertEqual(calls, [], "must not reach the database")

    def test_a_row_with_no_key(self):
        status, _, calls = _call_all({"rows": [{"formId": ID_A, "rowKey": "  "}]})
        self.assertEqual(status, 400)
        self.assertEqual(calls, [])

    def test_a_row_with_a_non_uuid_form_id(self):
        status, _, calls = _call_all({"rows": [{"formId": "nope", "rowKey": "sa"}]})
        self.assertEqual(status, 400)
        self.assertEqual(calls, [])

    def test_the_star_key_is_reserved_for_the_whole_ticket_stamp(self):
        # Accepting it here would mark a ticket complete through the partial
        # path, which is exactly the bug this table exists to fix.
        status, _, calls = _call_all({"rows": [{"formId": ID_A, "rowKey": "*"}]})
        self.assertEqual(status, 400)
        self.assertEqual(calls, [])

    def test_rows_that_is_not_a_list(self):
        status, _, calls = _call_all({"rows": "crew_chief"})
        self.assertEqual(status, 400)
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()


# ── The export-run log ──────────────────────────────────────────────
#
# The stamps say a ticket has gone out. They do not say which tickets went
# into one IIF together, under what filename, or who pressed the button on
# that occasion, and two runs a second apart cannot be told apart after the
# fact. VG-Time's History tab was built against a SharePoint list for this;
# the flows were never provisioned, so the tab was empty on every load.


def _call_log(body, marked=0, found=0, inserted=0, fail_log=False):
    """Runs the handler and returns (payload, the run-log SQL calls)."""
    log_calls = []

    def fake_execute(sql, params=None):
        if _is_run_log(sql):
            if fail_log:
                raise RuntimeError("log is down")
            log_calls.append((sql, params))
            return []
        if "RETURNING form_id" in sql and "UPDATE time_tickets" not in sql:
            return [{"form_id": "x"}] * inserted
        return [{"marked": marked, "found": found}]

    with mock.patch.object(forms, "execute", side_effect=fake_execute):
        res = forms.mark_time_tickets_exported.build().get_user_function()(_req(body))
    return json.loads(res.get_body()), log_calls


class ExportRunLog(unittest.TestCase):
    def test_a_run_is_logged_with_what_only_vg_time_knows(self):
        payload, calls = _call_log(
            {"formIds": [ID_A, ID_B], "exportedBy": "norm",
             "fileName": "VGT-2026-09-19.iif", "iifLines": 14,
             "totalHours": 24.5, "employees": ["Haley Ehler", "Fred Young"]},
            marked=2, found=2,
        )
        self.assertTrue(payload["runLogged"])
        self.assertIsNotNone(payload["runId"])
        run_sql, run_params = calls[0]
        self.assertIn("INSERT INTO time_ticket_export_runs", run_sql)
        self.assertEqual(run_params[0], payload["runId"])
        self.assertEqual(run_params[1], "norm")
        self.assertEqual(run_params[2], "VGT-2026-09-19.iif")
        self.assertEqual(run_params[3], 14)
        self.assertEqual(run_params[4], 24.5)
        self.assertEqual(run_params[5], ["Haley Ehler", "Fred Young"])

    def test_the_run_records_the_outcome_not_the_request(self):
        # Re-exporting two tickets already spoken for is a different event
        # from marking them fresh, and the History tab exists to show that.
        _, calls = _call_log({"formIds": [ID_A, ID_B]}, marked=0, found=2)
        _, run_params = calls[0]
        self.assertEqual(run_params[6], 0, "tickets_marked")
        self.assertEqual(run_params[7], 2, "tickets_already")
        self.assertEqual(run_params[8], 0, "tickets_missing")

    def test_whole_tickets_and_partial_rows_both_land_in_the_contents(self):
        _, calls = _call_log(
            {"formIds": [ID_A], "rows": [{"formId": ID_B, "rowKey": "crew_chief"}]},
            marked=1, found=1, inserted=1,
        )
        items_sql, items_params = calls[1]
        self.assertIn("time_ticket_export_run_items", items_sql)
        pairs = sorted(zip(items_params[1], items_params[2]))
        self.assertEqual(pairs, sorted([(ID_A, "*"), (ID_B, "crew_chief")]))

    def test_a_logging_failure_does_not_fail_the_export(self):
        # The stamps are the operative fact and are already committed. Losing
        # the export to a history write would be the worse outcome by far.
        payload, _ = _call_log({"formIds": [ID_A]}, marked=1, found=1, fail_log=True)
        self.assertEqual(payload["marked"], 1)
        self.assertFalse(payload["runLogged"])
        self.assertIsNone(payload["runId"])

    def test_junk_descriptive_fields_are_dropped_not_fatal(self):
        payload, calls = _call_log(
            {"formIds": [ID_A], "iifLines": "lots", "totalHours": None,
             "employees": "Haley", "fileName": "   "},
            marked=1, found=1,
        )
        self.assertTrue(payload["runLogged"])
        _, run_params = calls[0]
        self.assertIsNone(run_params[2], "a blank filename is NULL, not ''")
        self.assertIsNone(run_params[3])
        self.assertIsNone(run_params[4])
        self.assertEqual(run_params[5], [], "a non-list employees is dropped")


class ExportRunList(unittest.TestCase):
    """GET /api/time-tickets/exports — what the History tab hydrates from."""

    def _get(self, runs, items):
        def fake_query(sql, params=None):
            if "count(*) AS n" in sql:
                return [{"n": len(runs)}]
            if "FROM time_ticket_export_run_items" in sql and "SELECT run_id" in sql:
                return items
            return runs

        req = func.HttpRequest(
            method="GET", url="/api/time-tickets/exports",
            headers={"X-API-Key": "test-key"}, params={}, body=b"",
        )
        with mock.patch.object(forms, "query", side_effect=fake_query):
            res = forms.list_time_ticket_export_runs.build().get_user_function()(req)
        return res.status_code, json.loads(res.get_body())

    def test_runs_come_back_with_their_contents_attached(self):
        import datetime as dt
        run = {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "run_at": dt.datetime(2026, 9, 19, 14, 30),
            "exported_by": "norm", "file_name": "VGT.iif", "iif_lines": 14,
            "total_hours": 24.5, "employees": ["Fred Young"],
            "tickets_marked": 2, "tickets_already": 0, "tickets_missing": 0,
            "rows_marked": 0, "ticket_count": 2,
        }
        items = [
            {"run_id": run["run_id"], "form_id": ID_A, "row_key": "*"},
            {"run_id": run["run_id"], "form_id": ID_B, "row_key": "crew_chief"},
        ]
        status, payload = self._get([run], items)
        self.assertEqual(status, 200)
        entry = payload["data"][0]
        self.assertEqual(entry["fileName"], "VGT.iif")
        self.assertEqual(entry["ticketCount"], 2)
        self.assertEqual(entry["totalHours"], 24.5)
        self.assertEqual(entry["runAt"], "2026-09-19T14:30:00")
        self.assertEqual(
            sorted(i["rowKey"] for i in entry["items"]), ["*", "crew_chief"]
        )

    def test_no_runs_yet_is_an_empty_list_not_an_error(self):
        # The state every install starts in, and the one the tab has been
        # stuck in. It must render as "no exports", not as a failure.
        status, payload = self._get([], [])
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"], [])
