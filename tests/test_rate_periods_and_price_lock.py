"""Effective-dated client schedules and prices locked at export.

Both exist so that a ticket's price stops moving once it should:

  * A client reassignment applies from its effective date forward. The PUT
    has to close the old period and open the new one together with the map
    update, refuse a date that reaches back past an earlier change, and keep
    who/when for the change log.
  * A row's price locks when it is exported. Only rows this request actually
    exported can be locked, and a lock failure must not fail the export - the
    stamps are already committed by then.
"""
import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

import azure.functions as func  # noqa: E402

from routes import forms, rates  # noqa: E402


def _handler(route):
    return route.build().get_user_function()


def _req(method, url, body=None, route_params=None, params=None):
    return func.HttpRequest(
        method=method,
        url=url,
        headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
        params=params or {},
        route_params=route_params or {},
        body=json.dumps(body).encode() if body is not None else b"",
    )


class _Cursor:
    def __init__(self, log, open_period):
        self.log = log
        self._open = open_period

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._open


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self, *a, **kw):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _reassign(body, open_period=(11, date(2026, 9, 1)), current_schedule=2, client="Storm"):
    log = []
    conn = _Conn(_Cursor(log, open_period))

    def fake_query(sql, params=None):
        if "vgt_client_schedule_map" in sql:
            return [{"id": 5, "schedule_id": current_schedule}]
        if "vgt_rate_schedules" in sql:
            return [{"id": 1}]
        return []

    with mock.patch.object(rates, "query", fake_query), \
         mock.patch.object(rates, "get_connection", return_value=conn), \
         mock.patch.object(rates, "release_connection"):
        res = _handler(rates.update_client_schedule)(
            _req("PUT", f"/api/rates/clients/{client}", body, route_params={"client_name": client})
        )
    return res.status_code, json.loads(res.get_body()), log, conn


class ReassignWithEffectiveDate(unittest.TestCase):
    def test_closes_the_open_period_the_day_before_and_opens_the_new_one(self):
        status, payload, log, conn = _reassign({
            "schedule_key": "vg_standard_2025", "effective_from": "2026-10-15",
            "changed_by": "Norm", "note": "new MSA",
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(conn.committed)
        sqls = [s for s, _ in log]
        close = next(p for s, p in log if s.startswith("UPDATE vgt_client_schedule_periods"))
        self.assertEqual(close, (date(2026, 10, 14), 11))
        opened = next(p for s, p in log if s.startswith("INSERT INTO vgt_client_schedule_periods"))
        # client, new schedule, effective date, previous schedule, who, note
        self.assertEqual(opened, (5, 1, date(2026, 10, 15), 2, "Norm", "new MSA"))
        # The map follows the open period, in the same transaction.
        self.assertTrue(any(s.startswith("UPDATE vgt_client_schedule_map") for s in sqls))
        self.assertEqual(payload["effective_from"], "2026-10-15")

    def test_a_date_on_or_before_the_current_period_start_is_refused(self):
        status, payload, log, conn = _reassign({
            "schedule_key": "vg_standard_2025", "effective_from": "2026-09-01",
        })
        self.assertEqual(status, 409)
        self.assertIn("2026-09-01", payload["error"])
        self.assertFalse(conn.committed)
        self.assertFalse(any(s.startswith(("UPDATE", "INSERT")) for s, _ in log))

    def test_same_schedule_is_a_400_not_an_empty_history_entry(self):
        status, _, log, _ = _reassign({"schedule_key": "vg_standard_2023"}, current_schedule=1)
        self.assertEqual(status, 400)
        self.assertEqual(log, [])

    def test_a_bad_date_is_a_400(self):
        status, payload, log, _ = _reassign({"schedule_key": "x", "effective_from": "15/10/2026"})
        self.assertEqual(status, 400)
        self.assertIn("YYYY-MM-DD", payload["error"])
        self.assertEqual(log, [])

    def test_a_client_with_no_period_gets_its_old_schedule_recorded(self):
        status, _, log, _ = _reassign(
            {"schedule_key": "vg_standard_2025", "effective_from": "2026-10-15"},
            open_period=None)
        self.assertEqual(status, 200)
        inserts = [p for s, p in log if s.startswith("INSERT INTO vgt_client_schedule_periods")]
        self.assertEqual(len(inserts), 2)
        # Old schedule, open start, closed the day before.
        self.assertEqual(inserts[0][:3], (5, 2, date(2026, 10, 14)))

    def test_default_date_is_today(self):
        status, payload, _, _ = _reassign(
            {"schedule_key": "vg_standard_2025"}, open_period=(11, None))
        self.assertEqual(status, 200)
        self.assertRegex(payload["effective_from"], r"^\d{4}-\d{2}-\d{2}$")


def _add(body, existing=("Whitecap", "Storm")):
    log = []
    conn = _Conn(_Cursor(log, (77,)))

    def fake_query(sql, params=None):
        if "vgt_client_schedule_map" in sql:
            return [{"client_name": n} for n in existing]
        if "vgt_rate_schedules" in sql:
            ids = {"vg_standard_2025": 1, "municipal": 6}
            return [{"id": ids[k], "schedule_key": k} for k in params if k in ids]
        return []

    with mock.patch.object(rates, "query", fake_query), \
         mock.patch.object(rates, "get_connection", return_value=conn), \
         mock.patch.object(rates, "release_connection"):
        res = _handler(rates.add_client)(_req("POST", "/api/rates/clients", body))
    return res.status_code, json.loads(res.get_body()), log, conn


class AddClient(unittest.TestCase):
    def test_default_until_the_day_before_then_the_chosen_schedule_logged_as_a_change(self):
        status, payload, log, conn = _add({
            "client_name": "  Town  of ", "schedule_key": "municipal",
            "effective_from": "2026-09-01", "changed_by": "Norm",
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(conn.committed)
        inserts = [p for s, p in log if s.startswith("INSERT")]
        self.assertEqual(inserts[0], ("Town of", 6, "Added in VG-Time"))
        # client, default schedule, up to the day before, who, note
        self.assertEqual(inserts[1][:3], (77, 1, date(2026, 8, 31)))
        # client, chosen schedule, from, previous (the default), who, note
        self.assertEqual(inserts[2], (77, 6, date(2026, 9, 1), 1, "Norm", "Client added"))

    def test_a_name_an_existing_entry_already_catches_is_refused(self):
        status, payload, log, conn = _add({"client_name": "Whitecap Resources",
                                           "schedule_key": "municipal"})
        self.assertEqual(status, 409)
        self.assertIn("Whitecap", payload["error"])
        self.assertEqual(log, [])

    def test_an_existing_name_in_another_case_is_refused(self):
        status, _, log, _ = _add({"client_name": "STORM", "schedule_key": "municipal"})
        self.assertEqual(status, 409)
        self.assertEqual(log, [])

    def test_unknown_schedule_and_missing_name(self):
        self.assertEqual(_add({"client_name": "Teine", "schedule_key": "nope"})[0], 404)
        self.assertEqual(_add({"client_name": " ", "schedule_key": "municipal"})[0], 400)


class RatesPayloadPeriods(unittest.TestCase):
    def test_periods_ride_along_and_a_missing_table_does_not_take_rates_down(self):
        def fake_query(sql, params=None):
            if "vgt_client_schedule_periods" in sql:
                raise RuntimeError('relation "vgt_client_schedule_periods" does not exist')
            return []

        with mock.patch.object(rates, "query", fake_query):
            res = _handler(rates.get_rates)(_req("GET", "/api/rates"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.get_body())["CLIENT_PERIODS"], [])

    def test_periods_carry_no_names_on_the_unauthenticated_payload(self):
        seen = []

        def fake_query(sql, params=None):
            if "vgt_client_schedule_periods" in sql:
                seen.append(sql)
            return []

        with mock.patch.object(rates, "query", fake_query):
            _handler(rates.get_rates)(_req("GET", "/api/rates"))
        self.assertNotIn("changed_by", seen[0])


class DeleteGuardsHistory(unittest.TestCase):
    def test_a_schedule_used_by_a_past_period_is_kept(self):
        def fake_query(sql, params=None):
            if "vgt_client_schedule_periods" in sql:
                return [{"client_name": "Storm"}]
            if "vgt_client_schedule_map" in sql:
                return []
            return [{"1": 1}]

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection") as conn:
            res = _handler(rates.delete_schedule)(
                _req("DELETE", "/api/rates/schedules/9", route_params={"id": "9"}))
        self.assertEqual(res.status_code, 409)
        self.assertIn("Storm", res.get_body().decode())
        conn.assert_not_called()


ID_A = "6c3f93b6-1326-478b-a6d6-59aba925a1c1"
ID_B = "3d2119f9-4b12-4fd0-9e92-67a4b0b9f840"


def _export(body, price_error=None):
    locks = []

    def fake_execute(sql, params=None):
        if "time_ticket_row_prices" in sql:
            if price_error:
                raise RuntimeError(price_error)
            locks.append(params)
            return [{"form_id": f} for f in params[2]]
        if "time_ticket_export_runs" in sql and "INSERT" in sql:
            return []
        if "time_ticket_export_run_items" in sql:
            return []
        if "INSERT INTO time_ticket_row_exports" in sql and "unnest" in sql:
            return [{"form_id": ID_B}]
        return [{"marked": 1, "found": 1}]

    with mock.patch.object(forms, "execute", side_effect=fake_execute):
        res = _handler(forms.mark_time_tickets_exported)(
            _req("POST", "/api/time-tickets/export", body))
    return res.status_code, json.loads(res.get_body()), locks


def _price(form_id, key, amount=100.0, **kw):
    return {"formId": form_id, "rowKey": key, "amount": amount, "rate": 93.5,
            "qty": 8, "unit": "hourly", "scheduleKey": "vg_standard_2025",
            "scheduleName": "VG Standard 2025", "source": "rated", **kw}


class PriceLockOnExport(unittest.TestCase):
    def test_locks_rows_of_whole_tickets_and_of_named_partial_rows_only(self):
        status, payload, locks = _export({
            "formIds": [ID_A],
            "rows": [{"formId": ID_B, "rowKey": "sa"}],
            "exportedBy": "norm",
            "prices": [
                _price(ID_A, "crew_chief"),
                _price(ID_A, "equip:truck_km", 55.2),
                _price(ID_B, "sa", 352.0),
                _price(ID_B, "crew_chief"),        # not exported by this request
                _price(ID_A, "*"),                 # a price belongs to a row
                {"formId": "nope", "rowKey": "sa", "amount": 1},
                {"formId": ID_A, "rowKey": "sa"},  # no amount
            ],
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(locks), 1)
        params = locks[0]
        pairs = list(zip(params[2], params[3]))
        self.assertEqual(pairs, [(ID_A, "crew_chief"), (ID_A, "equip:truck_km"), (ID_B, "sa")])
        self.assertEqual(params[1], "norm")
        self.assertEqual(payload["pricesLocked"], 3)
        self.assertIsNone(payload["priceLockError"])

    def test_first_lock_wins(self):
        # A re-export must never rewrite what a row was billed at.
        seen = []

        def fake_execute(sql, params=None):
            if "time_ticket_row_prices" in sql:
                seen.append(" ".join(sql.split()))
                return []
            return [{"marked": 0, "found": 1}]

        with mock.patch.object(forms, "execute", side_effect=fake_execute):
            res = _handler(forms.mark_time_tickets_exported)(
                _req("POST", "/api/time-tickets/export",
                     {"formIds": [ID_A], "prices": [_price(ID_A, "crew_chief")]}))
        self.assertIn("ON CONFLICT (form_id, row_key) DO NOTHING", seen[0])
        self.assertEqual(json.loads(res.get_body())["pricesLocked"], 0)

    def test_a_failed_lock_is_reported_and_the_export_still_succeeds(self):
        status, payload, _ = _export(
            {"formIds": [ID_A], "prices": [_price(ID_A, "crew_chief")]},
            price_error="relation does not exist")
        self.assertEqual(status, 200)
        self.assertEqual(payload["marked"], 1)
        self.assertEqual(payload["pricesLocked"], 0)
        self.assertIn("does not exist", payload["priceLockError"])

    def test_no_prices_sent_writes_nothing(self):
        _, payload, locks = _export({"formIds": [ID_A]})
        self.assertEqual(locks, [])
        self.assertEqual(payload["pricesLocked"], 0)


class LockedPricesOnTicketList(unittest.TestCase):
    def test_locks_attach_to_their_ticket_by_row_key(self):
        rows = [{"form_id": ID_A}, {"form_id": ID_B}]
        lock = {"form_id": ID_A, "row_key": "crew_chief", "run_id": None,
                "schedule_key": "vg_standard_2025", "schedule_name": "VG Standard 2025",
                "rate_key": "crew_chief", "qb_item": "Crew Chief", "qty": 8,
                "unit": "hourly", "rate": 93.5, "amount": 748, "price_source": "rated",
                "priced_by": "norm", "priced_at": None}
        with mock.patch.object(forms, "query", return_value=[lock]):
            forms._attach_locked_prices(rows)
        self.assertEqual(rows[0]["locked_prices"]["crew_chief"]["amount"], 748.0)
        self.assertEqual(rows[0]["locked_prices"]["crew_chief"]["scheduleName"], "VG Standard 2025")
        self.assertEqual(rows[1]["locked_prices"], {})

    def test_an_unreadable_price_table_leaves_the_list_intact(self):
        rows = [{"form_id": ID_A}]
        with mock.patch.object(forms, "query", side_effect=RuntimeError("boom")):
            forms._attach_locked_prices(rows)
        self.assertEqual(rows[0]["locked_prices"], {})


class JobClientsOnTicketList(unittest.TestCase):
    def test_each_ticket_gets_its_jobs_client_by_trimmed_job_number(self):
        rows = [{"job_no": " 260393 "}, {"job_no": "260393"}, {"job_no": "N/A"}, {"job_no": None}]
        found = [
            {"job_no": "260393", "name": "Whitecap Resources Inc.", "code": "2015G0013", "source": "latitude"},
            {"job_no": "N/A", "name": None, "code": None, "source": None},
        ]
        with mock.patch.object(forms, "query", return_value=found) as q:
            forms._attach_job_clients(rows)
        self.assertEqual(q.call_args[0][1], (["260393", "N/A"],))
        self.assertEqual(rows[0]["job_client"], "Whitecap Resources Inc.")
        self.assertEqual(rows[1]["job_client_source"], "latitude")
        self.assertIsNone(rows[2]["job_client"])
        self.assertIsNone(rows[3]["job_client_code"])

    def test_no_job_numbers_skips_the_query(self):
        rows = [{"job_no": ""}]
        with mock.patch.object(forms, "query") as q:
            forms._attach_job_clients(rows)
        q.assert_not_called()
        self.assertIsNone(rows[0]["job_client"])

    def test_an_unreadable_latitude_copy_leaves_the_typed_client(self):
        rows = [{"job_no": "260393", "client": "Whitecap"}]
        with mock.patch.object(forms, "query", side_effect=RuntimeError("boom")):
            forms._attach_job_clients(rows)
        self.assertIsNone(rows[0]["job_client"])
        self.assertEqual(rows[0]["client"], "Whitecap")


if __name__ == "__main__":
    unittest.main()
