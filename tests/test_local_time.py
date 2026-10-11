"""Human text reads every instant in local time; records keep ISO 8601 with an offset."""
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from tests.support import SUITE  # noqa: F401  (a throwaway home before altitude imports)
from altitude import state as S


class TestLocalTime(unittest.TestCase):
    def setUp(self):
        old = os.environ.get("TZ")
        self.addCleanup(time.tzset)
        self.addCleanup(lambda: os.environ.update({"TZ": old}) if old is not None else os.environ.pop("TZ", None))
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()

    def test_an_instant_reads_as_a_clock_today_and_dated_otherwise(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(S.local(now.isoformat()), f"{now.astimezone():%H:%M %Z}")
        self.assertEqual(S.local(now.isoformat().replace("+00:00", "Z")), f"{now.astimezone():%H:%M %Z}")
        self.assertEqual(S.local("2036-09-25T04:00:00+00:00"), "Sep 24, 2036, 21:00 PDT")
        earlier = (now - timedelta(days=40)).astimezone()
        self.assertTrue(S.local((now - timedelta(days=40)).isoformat()).startswith(f"{earlier:%b} {earlier.day}, "))
        self.assertEqual(S.local("soon"), "soon")

    def test_text_converts_iso_instants_and_leaves_everything_else(self):
        self.assertEqual(S.localized("pending since 2036-01-14T23:58:42+00:00; ends 2036-01-15T00:10Z."),
                         "pending since Jan 14, 2036, 15:58 PST; ends Jan 14, 2036, 16:10 PST.")
        for text in ("10.0.0.1:8080", "2036-01-14", "2036-01-14T23:58:42 without an offset", "23:58Z", "v2036-01-14T23:58:42+00:00:1"):
            self.assertEqual(S.localized(text), text)
