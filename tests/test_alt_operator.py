"""`alt project` and `alt state` are the operator's commands; phase 4 lost their parsers, so pin them."""
import json
import unittest

from tests.support import AltitudeCase


class TestOperatorCommands(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_ACTOR", None)   # the operator, not an automated caller
        self.addCleanup(self._forget, "op")

    def test_project_add_list_remove_and_state(self):
        added = self.alt("project", "add", "op", "--path", str(self.repo), "--l2-engine", "codex")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["l2_engine"], "codex")
        listed = self.alt("project", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["op"]["path"], str(self.repo))
        state = self.alt("--project", "op", "state")
        self.assertEqual(state.returncode, 0, state.stderr)
        self.assertIn("# STATE — op", state.stdout)
        removed = self.alt("project", "remove", "op")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertNotIn("op", json.loads(self.alt("project", "list").stdout))


if __name__ == "__main__":
    unittest.main()
