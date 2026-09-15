"""
Vendor timestamps are UTC but arrive with no zone.

The timestamptz columns they land in read a naive string using the session
time zone, which is America/Edmonton, so every vendor timestamp was stored
6-7 hours ahead of the real instant. as_utc closes that at the write path.
"""
import unittest
from datetime import datetime, timezone

from etl.utils import as_utc


class TestAsUtc(unittest.TestCase):

    def test_naive_vendor_timestamp_is_stamped_utc(self):
        # Real value from GET /forms.
        self.assertEqual(as_utc("2026-08-28T13:11:00.703"),
                         "2026-08-28T13:11:00.703000+00:00")

    def test_result_is_the_same_instant_the_vendor_meant(self):
        got = datetime.fromisoformat(as_utc("2026-08-27T22:28:43.647"))
        self.assertEqual(got, datetime(2026, 8, 27, 22, 28, 43, 647000,
                                       tzinfo=timezone.utc))
        self.assertEqual(got.utcoffset().total_seconds(), 0)

    def test_explicit_zone_is_left_alone(self):
        for ts in ("2026-08-28T13:11:00+00:00", "2026-08-28T13:11:00-06:00"):
            self.assertEqual(datetime.fromisoformat(as_utc(ts)),
                             datetime.fromisoformat(ts))

    def test_trailing_z_is_understood(self):
        self.assertEqual(datetime.fromisoformat(as_utc("2026-08-28T13:11:00Z")),
                         datetime(2026, 8, 28, 13, 11, tzinfo=timezone.utc))

    def test_date_only_values_are_untouched(self):
        # Forcing these to UTC midnight would render as the previous day in
        # Mountain time and move a date the user reads.
        self.assertEqual(as_utc("2026-01-01"), "2026-01-01")

    def test_empty_and_non_strings_pass_through(self):
        for v in (None, "", 0, 12345, {}):
            self.assertEqual(as_utc(v), v)

    def test_unrecognised_strings_pass_through(self):
        self.assertEqual(as_utc("not a timestamp"), "not a timestamp")

    def test_every_sync_module_that_maps_a_vendor_timestamp_uses_it(self):
        # Guards against a new stage reintroducing the bug.
        import glob
        import re
        for path in glob.glob("etl/sync_*.py"):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(
                    r'"(?:[a-z_]*on(?:_utc)?|[a-z_]*_at)"\s*:\s*([^,\n]+)', src):
                val = m.group(1)
                if 'get("' in val and ("On" in val or "_at" in val):
                    if "now_iso" in val:
                        continue
                    self.assertIn("as_utc", val, f"{path}: {val}")


if __name__ == "__main__":
    unittest.main()
