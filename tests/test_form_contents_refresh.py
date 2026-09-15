"""Guards on which forms the form_contents stage re-fetches.

On 2026-09-14 the nightly stage logged "3207 forms need content fetch" with
3202 already cached, ran for the full 10-minute function timeout, and never
reached the one time ticket submitted that afternoon.
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.sync_form_contents import needs_fetch

UTC = timezone.utc
MDT = timezone(timedelta(hours=-6))
FETCHED = datetime(2026, 9, 14, 15, 55, 26, tzinfo=MDT)   # 21:55:26Z, as TIMESTAMPTZ returns it


class NeedsFetch(unittest.TestCase):
    def test_uncached_form_is_fetched(self):
        self.assertTrue(needs_fetch("2026-08-28T13:11:00.703", None))

    def test_vendor_timestamp_without_zone_is_read_as_utc(self):
        # Modified 13:11Z on Aug 28, cached Sep 14: nothing to do. This is
        # the comparison that raised TypeError and forced a re-fetch of
        # every form.
        self.assertFalse(needs_fetch("2026-08-28T13:11:00.703", FETCHED))

    def test_form_modified_after_cache_is_fetched(self):
        self.assertTrue(needs_fetch("2026-09-14T22:30:00.000", FETCHED))   # 22:30Z > 21:55Z

    def test_form_modified_just_before_cache_is_not_fetched(self):
        self.assertFalse(needs_fetch("2026-09-14T21:50:00.000", FETCHED))  # 21:50Z < 21:55Z

    def test_explicit_zulu_suffix_is_accepted(self):
        self.assertTrue(needs_fetch("2026-09-14T22:30:00Z", FETCHED))
        self.assertFalse(needs_fetch("2026-08-28T13:11:00Z", FETCHED))

    def test_naive_cache_timestamp_is_read_as_utc(self):
        naive = datetime(2026, 9, 14, 21, 55, 26)
        self.assertFalse(needs_fetch("2026-08-28T13:11:00.703", naive))
        self.assertTrue(needs_fetch("2026-09-14T22:30:00.000", naive))

    def test_missing_last_modified_keeps_the_cache(self):
        self.assertFalse(needs_fetch(None, FETCHED))
        self.assertFalse(needs_fetch("", FETCHED))

    def test_unparseable_timestamp_refetches(self):
        self.assertTrue(needs_fetch("yesterday", FETCHED))

    def test_a_cached_backlog_is_not_refetched(self):
        # The 3202-form case: everything cached this afternoon, all modified
        # earlier. Zero should be pending.
        mods = [f"2026-0{m}-1{d}T1{d}:00:00.000" for m in range(3, 9) for d in range(0, 9)]
        self.assertEqual(sum(needs_fetch(m, FETCHED) for m in mods), 0)
