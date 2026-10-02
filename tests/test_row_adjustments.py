"""Client-ticket adjustments: hide a line or change what the costed ticket prints.

Load-bearing: every change is logged in the same statement as the change, a
line may only take the values that belong to its kind, a reason is required,
and a missing migration costs the adjustments, never the ticket list.
"""
import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

import azure.functions as func  # noqa: E402
from psycopg2.errors import UndefinedTable  # noqa: E402

from routes import ticket_adjustments as adj  # noqa: E402

FORM = "11111111-2222-3333-4444-555555555555"


def _put(body, key="test-key"):
    req = func.HttpRequest(
        method="PUT",
        url=f"/api/time-tickets/{FORM}/adjustments",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        params={},
        route_params={"formId": FORM},
        body=json.dumps(body).encode(),
    )
    res = adj.set_row_adjustment.build().get_user_function()(req)
    return res.status_code, json.loads(res.get_body())


def _delete(params):
    req = func.HttpRequest(
        method="DELETE",
        url=f"/api/time-tickets/{FORM}/adjustments",
        headers={"X-API-Key": "test-key"},
        params=params,
        route_params={"formId": FORM},
        body=b"",
    )
    res = adj.revert_row_adjustment.build().get_user_function()(req)
    return res.status_code, json.loads(res.get_body())


def _stored(**over):
    row = {"form_id": FORM, "row_key": "equip:cc_subsistence", "hidden": True,
           "travel_hrs": None, "work_hrs": None, "qty": None, "rate": None,
           "reason": "Local job", "updated_by": "Lisa",
           "updated_at": datetime.datetime(2026, 10, 2, 9, 30, tzinfo=datetime.timezone.utc)}
    row.update(over)
    return row


class AttachToTicketList(unittest.TestCase):
    def test_adjustments_land_on_their_ticket_by_row_key(self):
        rows = [{"form_id": FORM}, {"form_id": "99999999-2222-3333-4444-555555555555"}]
        with mock.patch.object(adj, "query", return_value=[_stored(), _stored(
                row_key="crew_chief", hidden=False, work_hrs=8, rate=95.5)]) as q:
            adj.attach_row_adjustments(rows)
        self.assertEqual(q.call_args[0][1], ([FORM, "99999999-2222-3333-4444-555555555555"],))
        sub = rows[0]["row_adjustments"]["equip:cc_subsistence"]
        self.assertTrue(sub["hidden"])
        self.assertEqual(sub["reason"], "Local job")
        self.assertEqual(sub["updatedAt"], "2026-10-02T09:30:00+00:00")
        cc = rows[0]["row_adjustments"]["crew_chief"]
        self.assertEqual((cc["workHrs"], cc["rate"], cc["travelHrs"]), (8.0, 95.5, None))
        self.assertEqual(rows[1]["row_adjustments"], {})

    def test_before_the_migration_every_line_prints_as_recorded(self):
        rows = [{"form_id": FORM}]
        with mock.patch.object(adj, "query", side_effect=UndefinedTable("no table")):
            adj.attach_row_adjustments(rows)
        self.assertEqual(rows[0]["row_adjustments"], {})

    def test_no_tickets_skips_the_query(self):
        rows = [{"form_id": None}]
        with mock.patch.object(adj, "query") as q:
            adj.attach_row_adjustments(rows)
        q.assert_not_called()
        self.assertEqual(rows[0]["row_adjustments"], {})


class SetValidation(unittest.TestCase):
    def _rejects(self, body, fragment):
        with mock.patch.object(adj, "execute") as ex:
            status, payload = _put(body)
        ex.assert_not_called()
        self.assertEqual(status, 400, payload)
        self.assertIn(fragment, payload["error"])

    def test_reason_is_required(self):
        self._rejects({"rowKey": "equip:cc_subsistence", "hidden": True, "reason": "  "},
                      "reason is required")

    def test_reason_has_a_limit(self):
        self._rejects({"rowKey": "sa", "hidden": True, "reason": "x" * 501}, "500 characters")

    def test_unknown_row_key(self):
        self._rejects({"rowKey": "*", "hidden": True, "reason": "r"}, "rowKey must be")
        self._rejects({"rowKey": "equip:Bad Key", "hidden": True, "reason": "r"}, "rowKey must be")

    def test_quantity_does_not_apply_to_a_crew_line(self):
        self._rejects({"rowKey": "crew_chief", "qty": 2, "reason": "r"},
                      "qty does not apply to a crew line")

    def test_hours_do_not_apply_to_an_equipment_line(self):
        self._rejects({"rowKey": "equip:truck_km", "travelHrs": 1, "reason": "r"},
                      "travelHrs does not apply to an equipment line")

    def test_values_cannot_be_negative(self):
        self._rejects({"rowKey": "sa", "workHrs": -1, "reason": "r"}, "zero or more")

    def test_values_must_be_numbers(self):
        self._rejects({"rowKey": "sa", "rate": True, "reason": "r"}, "rate must be a number")
        self._rejects({"rowKey": "sa", "rate": "abc", "reason": "r"}, "rate must be a number")
        self._rejects({"rowKey": "sa", "rate": "nan", "reason": "r"}, "zero or more")

    def test_hidden_must_be_a_boolean(self):
        self._rejects({"rowKey": "sa", "hidden": "yes", "reason": "r"}, "true or false")

    def test_an_adjustment_must_change_something(self):
        self._rejects({"rowKey": "sa", "hidden": False, "reason": "r"}, "Nothing to adjust")

    def test_wrong_key_is_unauthorized(self):
        with mock.patch.object(adj, "execute") as ex:
            status, _ = _put({"rowKey": "sa", "hidden": True, "reason": "r"}, key="nope")
        ex.assert_not_called()
        self.assertEqual(status, 401)


class SetWrite(unittest.TestCase):
    def test_upsert_and_log_in_one_statement(self):
        stored = _stored(row_key="crew_chief", hidden=False, work_hrs=7.5, rate=95)
        with mock.patch.object(adj, "execute", return_value=[stored]) as ex:
            status, payload = _put({"rowKey": "crew_chief", "workHrs": "7.5", "rate": 95,
                                    "travelHrs": "", "reason": " Client agreed 7.5 ",
                                    "updatedBy": "Lisa"})
        self.assertEqual(status, 200, payload)
        ex.assert_called_once()
        sql, params = ex.call_args[0]
        self.assertIn("INSERT INTO time_ticket_row_adjustments", sql)
        self.assertIn("ON CONFLICT (form_id, row_key) DO UPDATE", sql)
        self.assertIn("INSERT INTO time_ticket_row_adjustment_log", sql)
        self.assertIn("'set'", sql)
        self.assertEqual(params["form_id"], FORM)
        self.assertEqual(params["row_key"], "crew_chief")
        self.assertEqual((params["work_hrs"], params["rate"], params["travel_hrs"], params["qty"]),
                         (7.5, 95.0, None, None))
        self.assertIs(params["hidden"], False)
        self.assertEqual(params["reason"], "Client agreed 7.5")
        self.assertEqual(params["updated_by"], "Lisa")
        self.assertEqual(payload["rowKey"], "crew_chief")
        self.assertEqual(payload["adjustment"]["workHrs"], 7.5)

    def test_hide_without_a_name_is_recorded_as_unknown(self):
        with mock.patch.object(adj, "execute", return_value=[_stored(updated_by="unknown")]) as ex:
            status, _ = _put({"rowKey": "equip:cc_subsistence", "hidden": True, "reason": "Local"})
        self.assertEqual(status, 200)
        self.assertEqual(ex.call_args[0][1]["updated_by"], "unknown")

    def test_no_such_ticket_is_404(self):
        with mock.patch.object(adj, "execute", return_value=[]):
            status, payload = _put({"rowKey": "sa", "hidden": True, "reason": "r"})
        self.assertEqual(status, 404)
        self.assertIn("Time Ticket", payload["error"])

    def test_before_the_migration_is_503(self):
        with mock.patch.object(adj, "execute", side_effect=UndefinedTable("no table")):
            status, payload = _put({"rowKey": "sa", "hidden": True, "reason": "r"})
        self.assertEqual(status, 503)
        self.assertIn("migrate_add_row_adjustments.sql", payload["error"])


class Revert(unittest.TestCase):
    def test_revert_deletes_and_logs(self):
        with mock.patch.object(adj, "execute", return_value=[{"reverted": 1}]) as ex:
            status, payload = _delete({"rowKey": "equip:cc_subsistence", "by": "Lisa"})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["reverted"])
        sql, params = ex.call_args[0]
        self.assertIn("DELETE FROM time_ticket_row_adjustments", sql)
        self.assertIn("'revert'", sql)
        self.assertEqual(params, {"form_id": FORM, "row_key": "equip:cc_subsistence", "by": "Lisa"})

    def test_nothing_to_revert_is_404(self):
        with mock.patch.object(adj, "execute", return_value=[{"reverted": 0}]):
            status, _ = _delete({"rowKey": "sa"})
        self.assertEqual(status, 404)

    def test_bad_row_key_is_400(self):
        with mock.patch.object(adj, "execute") as ex:
            status, _ = _delete({"rowKey": "equip:"})
        ex.assert_not_called()
        self.assertEqual(status, 400)

    def test_before_the_migration_is_503(self):
        with mock.patch.object(adj, "execute", side_effect=UndefinedTable("no table")):
            status, _ = _delete({"rowKey": "sa"})
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
