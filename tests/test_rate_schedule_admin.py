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


if __name__ == "__main__":
    unittest.main()
