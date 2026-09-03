"""Incident ids are UTC timestamps reserved with an exclusive create, so concurrent filers never share a file."""
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-iid-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, incidents  # noqa: E402

ID = re.compile(r"^I-\d{8}-\d{6}(-\d+)?$")


class TestIncidentIds(unittest.TestCase):
    def setUp(self):
        """config froze paths out of ALTITUDE_HOME at import and unittest discover shares one process: patch them."""
        self.root = Path(tempfile.mkdtemp(prefix="altitude-iid-case-"))
        saved = [(config, "ROOT", config.ROOT), (config, "INCIDENT_INDEX", config.INCIDENT_INDEX),
                 (config, "PROJECTS_FILE", config.PROJECTS_FILE), (config, "MONITOR_DIR", config.MONITOR_DIR),
                 (incidents, "FAULTS", incidents.FAULTS)]
        config.ROOT, config.INCIDENT_INDEX = self.root, self.root / "incidents.jsonl"
        config.PROJECTS_FILE, config.MONITOR_DIR = self.root / "projects.json", self.root / "monitor"
        incidents.FAULTS = self.root / "monitor" / "faults.json"
        self.addCleanup(lambda: [setattr(m, k, v) for m, k, v in saved])
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "repo" / "docs").mkdir(parents=True)
        config.save_projects({"demo": {"name": "demo", "path": str(self.root / "repo")}})

    def file(self, what: str) -> dict:
        return incidents.new_incident("demo", title=what, task=None, what=what, evidence="events.log",
                                      cause="not yet analysed", tags=["system-fault"])

    def body(self, iid: str) -> str:
        return (config.project_dir("demo") / "incidents" / f"{iid}.md").read_text()

    def test_ids_are_timestamps_and_same_second_neighbours_stay_distinct(self):
        ids = [self.file(f"incident {i}")["id"] for i in range(3)]
        self.assertTrue(all(ID.match(iid) for iid in ids), ids)
        self.assertEqual(len(set(ids)), 3)
        for i, iid in enumerate(ids):
            self.assertIn(f"incident {i}", self.body(iid))

    def test_concurrent_filers_keep_their_own_bodies(self):
        results, errors, barrier = [], [], threading.Barrier(6)

        def run(i):
            barrier.wait()
            try:
                results.append((i, self.file(f"racer {i}")["id"]))
            except BaseException as e:  # noqa: BLE001 — the test reports, never swallows
                errors.append(repr(e))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({iid for _, iid in results}), 6)
        for i, iid in results:
            self.assertIn(f"racer {i}", self.body(iid))

    def test_a_failed_write_leaves_no_reserved_file(self):
        with mock.patch.object(incidents.S, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.file("doomed")
        self.assertEqual(list((config.project_dir("demo") / "incidents").glob("I-*.md")), [])


if __name__ == "__main__":
    unittest.main()
