"""Declared staging scope parsing in `dispatch`."""
import unittest

from tests.support import AltitudeCase
from altitude import dispatch


class TestLeases(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(self.project, wip=5)
        self.quiet_engines()

    def test_expand_entry(self):
        cases = [
            ("altitude/dispatch.py (lease logic), tests/test_leases.py (new tests)",
             ["altitude/dispatch.py", "tests/test_leases.py"]),
            ("web/app.js (deleted), web/style.css", ["web/app.js", "web/style.css"]),
            ("web/app.js (removed, generated), web/style.css", ["web/app.js", "web/style.css"]),
            ("my dir/file.py", ["my dir/file.py"]),
            ("web/img (1).png", ["web/img (1).png"]),
            ("altitude/server.py and web/app.js", ["altitude/server.py and web/app.js"]),
            ("(all files under web/)", []),
            ("- altitude/dispatch.py", ["- altitude/dispatch.py"]),
            ("web/{a,b}/{c,d}", ["web/a/c", "web/a/d", "web/b/c", "web/b/d"]),
            ("web/{a,b/{c,d}}", ["web/a", "web/b/c", "web/b/d"]),
            ("web/src/{a.tsx,b.tsx", []),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(dispatch._expand_entry(entry), expected)

    def test_task_paths_preserves_literal_scope_and_expands_annotations(self):
        task = {"paths": ["my dir/file.py", "web/img (1).png",
                          "web/src/{App.tsx,App.test.tsx} (new)"]}
        self.assertEqual(dispatch.task_paths(self.project, task),
                         ["my dir/file.py", "web/img (1).png", "web/src/App.tsx", "web/src/App.test.tsx"])


if __name__ == "__main__":
    unittest.main()
