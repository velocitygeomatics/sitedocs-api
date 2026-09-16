"""Guards on renaming a rate item / moving it between categories.

The dangerous part of this route is what it must NOT touch. VG-Time's
SERVICE_ITEMS table joins to vgt_rate_items.slug to cost a ticket, so if a
rename re-slugged the row, every ticket referencing the old slug would stop
costing — silently, at zero, with no error anywhere. The slug is therefore
identity and the label is just a label.

The category list is closed for a similar reason: the costed-ticket PDF groups
line items by category, so a typo would render as a new section rather than
fail.
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
    return route.build().get_user_function()


def _req(body, slug="field_technician"):
    return func.HttpRequest(
        method="PUT",
        url=f"/api/rates/items/{slug}",
        headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
        params={},
        route_params={"slug": slug},
        body=json.dumps(body).encode(),
    )


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self.description = None
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, *a, **kw):
        return self._cursor

    def commit(self):
        self.committed = True


EXISTING = [{"id": 7, "line_item": "Field Technician", "category": "PERSONNEL"}]


def _query_ok(sql, params=None):
    """Item exists; no name clash."""
    if "lower(line_item)" in sql:
        return []
    return EXISTING


def _run(body, fake_query=_query_ok):
    log = []
    conn = _FakeConn(_FakeCursor(log))
    with mock.patch.object(rates, "query", fake_query), \
         mock.patch.object(rates, "get_connection", lambda: conn), \
         mock.patch.object(rates, "release_connection", lambda c: None):
        res = _handler(rates.update_rate_item)(_req(body))
    return res, log, conn


class RenameTests(unittest.TestCase):
    def test_renaming_does_not_change_the_slug(self):
        """The point of the whole route. Costing joins on slug."""
        res, log, conn = _run({"lineItem": "Field Tech II", "category": "PERSONNEL"})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.get_body())["slug"], "field_technician")

        writes = [sql for sql, _ in log if sql.startswith("UPDATE")]
        self.assertEqual(len(writes), 1)
        # No statement may assign a new slug.
        self.assertNotIn("SET slug", writes[0])
        self.assertNotIn("slug = %s, line_item", writes[0])
        self.assertTrue(conn.committed)

    def test_the_slug_is_only_used_to_locate_the_row(self):
        res, log, _ = _run({"lineItem": "Field Tech II", "category": "EQUIPMENT"})
        update_sql, params = [e for e in log if e[0].startswith("UPDATE")][0]
        self.assertEqual(params, ("Field Tech II", "EQUIPMENT", "field_technician"))
        self.assertEqual(res.status_code, 200)

    def test_category_moves_with_the_rename(self):
        res, log, _ = _run({"lineItem": "Field Technician", "category": "EQUIPMENT"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.get_body())["category"], "EQUIPMENT")

    def test_category_is_normalised_to_upper_case(self):
        # The grid sends whatever the select holds; the PDF groups on an exact
        # match, so 'Equipment' and 'EQUIPMENT' must not become two groups.
        res, _, _ = _run({"lineItem": "Field Technician", "category": "equipment"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.get_body())["category"], "EQUIPMENT")

    def test_omitted_fields_keep_their_current_values(self):
        res, log, _ = _run({"lineItem": "Field Tech II"})
        _, params = [e for e in log if e[0].startswith("UPDATE")][0]
        self.assertEqual(params[1], "PERSONNEL")
        self.assertEqual(res.status_code, 200)


class RefusalTests(unittest.TestCase):
    def test_unknown_category_is_rejected_and_names_the_valid_ones(self):
        res, log, conn = _run({"lineItem": "Field Technician", "category": "PERSONELL"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("PERSONNEL", str(json.loads(res.get_body())))
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_empty_name_is_rejected(self):
        res, log, conn = _run({"lineItem": "   ", "category": "PERSONNEL"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_a_duplicate_name_is_refused(self):
        def clashing(sql, params=None):
            if "lower(line_item)" in sql:
                return [{"slug": "survey_assistant"}]
            return EXISTING

        res, log, conn = _run({"lineItem": "Survey Assistant", "category": "PERSONNEL"}, clashing)
        self.assertEqual(res.status_code, 409)
        self.assertIn("survey_assistant", str(json.loads(res.get_body())))
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_unknown_slug_is_404(self):
        res, log, conn = _run({"lineItem": "Whatever", "category": "PERSONNEL"},
                              lambda *a, **k: [])
        self.assertEqual(res.status_code, 404)
        self.assertEqual(log, [])
        self.assertFalse(conn.committed)

    def test_the_route_requires_the_api_key(self):
        req = func.HttpRequest(
            method="PUT",
            url="/api/rates/items/field_technician",
            headers={"Content-Type": "application/json"},
            params={},
            route_params={"slug": "field_technician"},
            body=json.dumps({"lineItem": "x", "category": "PERSONNEL"}).encode(),
        )
        with mock.patch.object(rates, "get_connection") as conn:
            res = _handler(rates.update_rate_item)(req)
        self.assertEqual(res.status_code, 401)
        conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
