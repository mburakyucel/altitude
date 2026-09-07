"""`alt project` and `alt state` are the operator's commands; phase 4 lost their parsers, so pin them."""
import json
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, state as S, tasks as T


class TestOperatorCommands(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_ACTOR", None)   # the operator, not an automated caller
        self.addCleanup(self._forget, "op")

    def test_project_add_list_remove_and_state(self):
        added = self.alt("project", "add", "op", "--path", str(self.repo), "--l2-engine", "codex")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["l2_engine"], "codex")
        self.assertNotIn("wip", json.loads(added.stdout))
        listed = self.alt("project", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["op"]["path"], str(self.repo))
        state = self.alt("--project", "op", "state")
        self.assertEqual(state.returncode, 0, state.stderr)
        self.assertIn("# STATE — op", state.stdout)
        removed = self.alt("project", "remove", "op")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertNotIn("op", json.loads(self.alt("project", "list").stdout))

    def test_sept7_legacy_default_migrates_once_and_preserves_operator_choices(self):
        projects = config.load_projects()
        projects[self.project].update(wip=3, approval="manual", l2_engine="codex", l3_engine="codex")
        config.save_projects(projects)
        (config.ROOT / ".project-wip-default-migrated").unlink()
        with self.assertLogs("altitude.config", level="WARNING") as logs:
            migrated = config.load_projects()[self.project]
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("wip", migrated)
        self.assertEqual([migrated[k] for k in ("approval", "l2_engine", "l3_engine")],
                         ["manual", "codex", "codex"])
        added = self.alt("project", "add", "op", "--path", str(self.repo), "--wip", "3")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(config.project("op")["wip"], 3)
        with mock.patch.object(config, "WIP_PER_PROJECT", 8), \
             mock.patch.object(S, "list_tasks", return_value=[{"state": "running"}] * 3):
            self.assertIsNone(dispatch.wip_hold(self.project))

    def test_sept7_project_set_is_daemon_owned_and_retry_records_one_event(self):
        args = ("project", "set", self.project, "--wip", "5", "--reason", "test")
        result = self.alt(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)["request"]
        self.assertNotIn("wip", config.project(self.project), "CLI only persists the daemon request")
        self.assertEqual(json.loads(self.alt(*args).stdout)["request"]["id"], request["id"])
        dispatch.run_project_wip(self.project)  # the daemon tick, even when WIP is full
        self.assertEqual(config.project(self.project)["wip"], 5)
        self.assertTrue(json.loads(self.alt(*args).stdout)["idempotent"])
        events = config.project_dir(self.project) / "events.jsonl"
        event = json.loads(events.read_text())
        self.assertEqual((event["actor"], event["reason"], event["project"]),
                         ("burak", "test", self.project))
        # D7 crash after effect/audit but before receipt: replay completes without duplicating the event.
        S.write_json(config.project_dir(self.project) / "wip-request.json", request)
        dispatch.run_project_wip(self.project)
        self.assertEqual(len(events.read_text().splitlines()), 1)
        result = self.alt("project", "set", self.project, "--unset-wip", "--reason", "restore default")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_project_wip(self.project)
        self.assertNotIn("wip", config.project(self.project))
        self.assertFalse(json.loads(self.alt(*args).stdout)["idempotent"], "an intervening change gets a new request")

    def test_sept7_project_set_reregistration_is_last_write_wins_and_caps_are_validated(self):
        dispatch.request_project_wip(self.project, 5, "test", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "already pending"):
            dispatch.request_project_wip(self.project, 6, "different", actor="l3")
        config.remove_project(self.project)
        with config.add_project(self.project, path=self.repo):
            pass
        self.assertEqual(dispatch.run_project_wip(self.project)["status"], "done")
        self.assertEqual(config.project(self.project)["wip"], 5)
        with config.add_project(self.project, path=self.repo):
            pass
        self.assertNotIn("wip", config.project(self.project), "deliberate re-registration is the last write")
        for wip in (0, config.WIP_PER_MACHINE + 1):
            with self.subTest(wip=wip), self.assertRaisesRegex(T.TransitionError, "between"):
                dispatch.request_project_wip(self.project, wip, "test", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "nonempty reason"):
            dispatch.request_project_wip(self.project, 5, "  ", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "requires L3"):
            dispatch.request_project_wip(self.project, 5, "test", actor="l2")


if __name__ == "__main__":
    unittest.main()
