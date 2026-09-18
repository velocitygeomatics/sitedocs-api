"""Guards on the rate-schedule admin routes (copy / delete).

Both touch billing, so the failure modes are the point:

  * Deleting a schedule that clients still map to would drop those clients onto
    the default rate card. That re-prices their work silently — no error, just
    different money — so the delete must refuse with a 409 and name them.
  * Copying a schedule without its rate rows would produce a card that costs
    everything at zero, which also fails silently.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_KEY", "test-key")

import azure.functions as func  # noqa: E402

from routes import rates  # noqa: E402


def _handler(route):
    """@bp.route binds the name to a FunctionBuilder, not the function itself."""
    return route.build().get_user_function()


def _req(method, url, body=None, route_params=None):
    return func.HttpRequest(
        method=method,
        url=url,
        headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
        params={},
        route_params=route_params or {},
        body=json.dumps(body).encode() if body is not None else None,
    )


class _FakeCursor:
    """Records executed SQL; serves scripted fetchone/rowcount values."""

    def __init__(self, log, new_id=99, rowcount=0):
        self.log = log
        self._new_id = new_id
        self.rowcount = rowcount
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (self._new_id,)


class _FakeConn:
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


class DeleteScheduleTests(unittest.TestCase):
    def test_refuses_while_clients_still_map_to_it(self):
        """The whole reason this route is not a plain DELETE."""
        def fake_query(sql, params=None):
            if "vgt_client_schedule_map" in sql:
                return [{"client_name": "Cenovus"}, {"client_name": "Pembina"}]
            return [{"1": 1}]

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection") as conn:
            res = _handler(rates.delete_schedule)(
                _req("DELETE", "/api/rates/schedules/4", route_params={"id": "4"})
            )

        self.assertEqual(res.status_code, 409)
        body = json.loads(res.get_body())
        # The operator has to be told which clients block the delete.
        self.assertIn("Cenovus", str(body))
        self.assertIn("Pembina", str(body))
        # Nothing may be written on the refusal path.
        conn.assert_not_called()

    def test_unknown_schedule_is_404_not_a_silent_success(self):
        with mock.patch.object(rates, "query", lambda *a, **k: []):
            res = _handler(rates.delete_schedule)(
                _req("DELETE", "/api/rates/schedules/999", route_params={"id": "999"})
            )
        self.assertEqual(res.status_code, 404)

    def test_deletes_rate_rows_before_the_schedule(self):
        log = []
        cur = _FakeCursor(log, rowcount=18)
        conn = _FakeConn(cur)

        def fake_query(sql, params=None):
            if "vgt_client_schedule_map" in sql:
                return []
            return [{"1": 1}]

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection", return_value=conn), \
             mock.patch.object(rates, "release_connection"):
            res = _handler(rates.delete_schedule)(
                _req("DELETE", "/api/rates/schedules/7", route_params={"id": "7"})
            )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(conn.committed)
        # Children first: the parent delete would otherwise hit an FK, or orphan rows.
        self.assertIn("DELETE FROM vgt_schedule_rates", log[0][0])
        self.assertIn("DELETE FROM vgt_rate_schedules", log[1][0])
        self.assertEqual(json.loads(res.get_body())["rates_removed"], 18)


class CopyScheduleTests(unittest.TestCase):
    def _run(self, body, existing_key=False):
        log = []
        cur = _FakeCursor(log, new_id=42, rowcount=21)
        conn = _FakeConn(cur)

        def fake_query(sql, params=None):
            if "schedule_key = %s" in sql:
                return [{"1": 1}] if existing_key else []
            return [{"id": 1, "notes": "Current default schedule"}]

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection", return_value=conn), \
             mock.patch.object(rates, "release_connection"):
            res = _handler(rates.copy_schedule)(
                _req("POST", "/api/rates/schedules/1/copy", body=body,
                     route_params={"id": "1"})
            )
        return res, log, conn

    def test_copies_the_rate_rows_not_just_the_header(self):
        res, log, conn = self._run({"name": "VG Standard 2027", "schedule_key": "vg_standard_2027"})

        self.assertEqual(res.status_code, 200)
        self.assertTrue(conn.committed)
        payload = json.loads(res.get_body())
        self.assertEqual(payload["id"], 42)
        self.assertEqual(payload["rates_copied"], 21)

        insert_rates = log[1][0]
        self.assertIn("INSERT INTO vgt_schedule_rates", insert_rates)
        # Rows must land on the NEW schedule, sourced from the old one.
        self.assertEqual(log[1][1], (42, 1))

    def test_duplicate_key_is_409_before_any_write(self):
        res, log, conn = self._run(
            {"name": "Dupe", "schedule_key": "vg_standard_2025"}, existing_key=True
        )
        self.assertEqual(res.status_code, 409)
        self.assertFalse(conn.committed)
        self.assertEqual(log, [])

    def test_name_and_key_are_required(self):
        res, _, _ = self._run({"name": "No key"})
        self.assertEqual(res.status_code, 400)

class DeleteRateTests(unittest.TestCase):
    """Unsetting one rate on one schedule.

    update_rate is an upsert, so a schedule could only ever gain lines. Pruning
    one meant editing the table by hand, which is how a rate nobody wanted
    stayed priced on a live schedule.
    """

    def _run(self, rowcount=1, item=True, sched=True):
        log = []
        cur = _FakeCursor(log, rowcount=rowcount)
        conn = _FakeConn(cur)

        def fake_query(sql, params=None):
            if "vgt_rate_items" in sql:
                return [{"id": 7}] if item else []
            if "vgt_rate_schedules" in sql:
                return [{"id": 3}] if sched else []
            return []

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection", return_value=conn), \
             mock.patch.object(rates, "release_connection", lambda c: None):
            res = _handler(rates.delete_rate)(
                _req(
                    "DELETE",
                    "/api/rates/items/field_assistant/environmental_2026",
                    route_params={"slug": "field_assistant", "sched_key": "environmental_2026"},
                )
            )
        return res, log, conn

    def test_removes_only_that_schedule_s_row(self):
        res, log, conn = self._run()

        self.assertEqual(res.status_code, 200)
        self.assertTrue(conn.committed)

        sql, params = log[0]
        self.assertIn("DELETE FROM vgt_schedule_rates", sql)
        # Both keys must be in the WHERE clause. Scoped to the item alone this
        # would strip the rate from every schedule that prices it.
        self.assertIn("schedule_id", sql)
        self.assertIn("item_id", sql)
        self.assertEqual(params, (3, 7))

    def test_unknown_slug_is_404_before_any_write(self):
        res, log, conn = self._run(item=False)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_unknown_schedule_is_404_before_any_write(self):
        res, log, conn = self._run(sched=False)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_a_rate_that_was_never_set_is_404_not_a_silent_success(self):
        """A mistyped slug must not read back as a completed prune."""
        res, _, conn = self._run(rowcount=0)
        self.assertEqual(res.status_code, 404)
        self.assertFalse(conn.committed)
        self.assertTrue(conn.rolled_back)




if __name__ == "__main__":
    unittest.main()


class UpdateClientScheduleTests(unittest.TestCase):
    """Repointing one client at a different rate schedule.

    The client map drives which schedule a ticket prices against and had no
    write path at all, so reassigning a client meant editing Postgres by hand.
    """

    def _run(self, payload=None, client=True, sched=True):
        log = []
        cur = _FakeCursor(log, rowcount=1)
        conn = _FakeConn(cur)

        def fake_query(sql, params=None):
            if "vgt_client_schedule_map" in sql:
                return [{"id": 11}] if client else []
            if "vgt_rate_schedules" in sql:
                return [{"id": 1}] if sched else []
            return []

        with mock.patch.object(rates, "query", fake_query), \
             mock.patch.object(rates, "get_connection", return_value=conn), \
             mock.patch.object(rates, "release_connection", lambda c: None):
            res = _handler(rates.update_client_schedule)(
                _req(
                    "PUT",
                    "/api/rates/clients/Whitecap",
                    body={"schedule_key": "vg_standard_2025"} if payload is None else payload,
                    route_params={"client_name": "Whitecap"},
                )
            )
        return res, log, conn

    def test_moves_the_client_to_the_named_schedule(self):
        res, log, conn = self._run()

        self.assertEqual(res.status_code, 200)
        self.assertTrue(conn.committed)

        sql, params = log[0]
        self.assertIn("UPDATE vgt_client_schedule_map", sql)
        self.assertIn("schedule_id", sql)
        # Scoped by the map row's own id. Keyed on schedule_id instead, this
        # would move every client that shared the old schedule.
        self.assertEqual(params, (1, 11))

    def test_unknown_client_is_404_before_any_write(self):
        res, log, conn = self._run(client=False)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_unknown_schedule_is_404_before_any_write(self):
        # A typo must not park a live client on a schedule that does not exist,
        # which would price every one of their lines at $0.
        res, log, conn = self._run(sched=False)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_missing_schedule_key_is_400(self):
        res, log, conn = self._run(payload={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(log, [])

