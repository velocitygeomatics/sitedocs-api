"""Guards on the two defects that stalled form ingestion from 2026-06-16.

The forms stage failed nightly for three months and nothing noticed: /formtypes
stopped returning a short final page, paginate() looped to page 4040, the vendor
dropped the connection, and the exception took sync_forms() down with it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl import utils
from etl.utils import PAGE_SIZE, MAX_PAGES, paginate


def _page(start, n):
    return [{"Id": f"id-{start + i}"} for i in range(n)]


class PaginateTermination(unittest.TestCase):
    def test_a_short_final_page_ends_paging(self):
        pages = [_page(0, PAGE_SIZE), _page(PAGE_SIZE, 7)]
        with mock.patch.object(utils, "api_get", side_effect=pages) as g:
            rows = paginate("/forms")
        self.assertEqual(len(rows), PAGE_SIZE + 7)
        self.assertEqual(g.call_count, 2)

    def test_an_empty_page_ends_paging(self):
        with mock.patch.object(utils, "api_get", side_effect=[_page(0, PAGE_SIZE), []]):
            self.assertEqual(len(paginate("/forms")), PAGE_SIZE)

    def test_an_endpoint_that_never_advances_does_not_loop_forever(self):
        # The /formtypes regression: every page is full and identical.
        with mock.patch.object(utils, "api_get", return_value=_page(0, PAGE_SIZE)) as g:
            rows = paginate("/formtypes")
        self.assertEqual(len(rows), PAGE_SIZE, "repeated rows must not accumulate")
        self.assertEqual(g.call_count, 2, "stop on the first page that adds nothing new")

    def test_rows_are_never_duplicated_when_pages_partly_overlap(self):
        pages = [_page(0, PAGE_SIZE), _page(PAGE_SIZE - 10, PAGE_SIZE), _page(0, 3)]
        with mock.patch.object(utils, "api_get", side_effect=pages):
            rows = paginate("/forms")
        ids = [r["Id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2 * PAGE_SIZE - 10)

    def test_a_page_ceiling_bounds_an_endpoint_with_no_repeats(self):
        # Pathological case the id check cannot catch: full pages, always new ids.
        counter = {"n": 0}

        def endless(path, params):
            counter["n"] += 1
            return _page(counter["n"] * PAGE_SIZE, PAGE_SIZE)

        with mock.patch.object(utils, "api_get", side_effect=endless):
            rows = paginate("/forms")
        self.assertEqual(len(rows), MAX_PAGES * PAGE_SIZE)


class FormTypesFailureIsNotFatal(unittest.TestCase):
    def test_forms_still_sync_when_form_types_fails(self):
        from etl import sync_forms as sf
        conn = mock.MagicMock()
        with mock.patch.object(sf, "sync_form_types", side_effect=RuntimeError("504")), \
             mock.patch.object(sf, "sync_forms") as forms:
            sf.run(conn)
        forms.assert_called_once_with(conn)
        conn.rollback.assert_called_once()

    def test_a_failed_form_types_sync_does_not_mask_a_forms_failure(self):
        from etl import sync_forms as sf
        conn = mock.MagicMock()
        with mock.patch.object(sf, "sync_form_types", side_effect=RuntimeError("504")), \
             mock.patch.object(sf, "sync_forms", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                sf.run(conn)


if __name__ == "__main__":
    unittest.main()
