"""Incident ids are UTC timestamps reserved with an exclusive create, so concurrent filers never share a file."""
import re
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, incidents

ID = re.compile(r"^I-\d{8}-\d{6}(-\d+)?$")
PROJECT = "demo"


class TestIncidentIds(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)

    def file(self, what: str) -> dict:
        return incidents.new_incident(PROJECT, title=what, task=None, what=what, evidence="events.log",
                                      cause="not yet analysed", tags=["system-fault"])

    def body(self, iid: str) -> str:
        return (config.project_dir(PROJECT) / "incidents" / f"{iid}.md").read_text()

    def test_ids_are_timestamps_and_same_second_neighbours_stay_distinct(self):
        ids = [self.file(f"incident {i}")["id"] for i in range(3)]
        self.assertTrue(all(ID.match(iid) for iid in ids), ids)
        self.assertEqual(len(set(ids)), 3)
        for i, iid in enumerate(ids):
            self.assertIn(f"incident {i}", self.body(iid))

    def test_a_failed_write_leaves_no_reserved_file(self):
        with mock.patch.object(incidents.S, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.file("doomed")
        self.assertEqual(list((config.project_dir(PROJECT) / "incidents").glob("I-*.md")), [])


if __name__ == "__main__":
    unittest.main()
