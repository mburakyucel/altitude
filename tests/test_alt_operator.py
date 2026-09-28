"""`alt project` and `alt state` are the operator's commands; phase 4 lost their parsers, so pin them."""
import json
import unittest

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

    def test_stored_project_caps_are_absent_from_configuration_and_cli(self):
        for cap in (1, 3, 50):
            with self.subTest(cap=cap):
                projects = config.load_projects()
                projects[self.project].update(wip=cap, approval="manual", l2_engine="codex", l3_engine="codex")
                config.save_projects(projects)
                entry = config.project(self.project)
                self.assertNotIn("wip", entry)
                self.assertEqual([entry[k] for k in ("approval", "l2_engine", "l3_engine")],
                                 ["manual", "codex", "codex"])
                self.assertNotIn("wip", json.loads(self.alt("project", "list").stdout)[self.project])
        self.assertFalse((config.ROOT / ".project-wip-default-migrated").exists())
        for args in (("project", "add", "op", "--path", str(self.repo), "--wip", "3"),
                     ("project", "set", self.project, "--wip", "5", "--reason", "test"),
                     ("project", "set", self.project, "--unset-wip", "--reason", "test")):
            self.assertNotEqual(self.alt(*args).returncode, 0)
        with self.assertRaisesRegex(T.TransitionError, "unknown project setting"):
            dispatch.request_setting(self.project, "wip", 5, "test", actor="burak")
        S.write_json(config.project_dir(self.project) / "wip-request.json", {"status": "pending", "wip": 1})
        self.assertNotIn("wip", dispatch.run_settings(self.project))
        self.assertNotIn("wip", config.project(self.project))

    def test_sept7_project_set_is_daemon_owned_and_retry_records_one_event(self):
        args = ("project", "set", self.project, "--routing", "codex", "--reason", "test")
        result = self.alt(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)["request"]
        self.assertNotIn("routing", config.project(self.project), "CLI only persists the daemon request")
        self.assertEqual(json.loads(self.alt(*args).stdout)["request"]["id"], request["id"])
        dispatch.run_settings(self.project)  # the daemon tick, even when WIP is full
        self.assertEqual(config.project(self.project)["routing"], config.parse_routing("codex"))
        self.assertTrue(json.loads(self.alt(*args).stdout)["idempotent"])
        events = config.project_dir(self.project) / "events.jsonl"
        event = json.loads(events.read_text())
        self.assertEqual((event["actor"], event["reason"], event["project"]),
                         ("burak", "test", self.project))
        # D7 crash after effect/audit but before receipt: replay completes without duplicating the event.
        S.write_json(config.project_dir(self.project) / "routing-request.json", request)
        dispatch.run_settings(self.project)
        self.assertEqual(len(events.read_text().splitlines()), 1)
        result = self.alt("project", "set", self.project, "--unset-routing", "--reason", "restore default")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_settings(self.project)
        self.assertNotIn("routing", config.project(self.project))
        self.assertFalse(json.loads(self.alt(*args).stdout)["idempotent"], "an intervening change gets a new request")

    def test_l2_preference_is_a_daemon_owned_project_setting(self):
        result = self.alt("project", "set", self.project, "--l2-preference", "claude", "--reason", "use Claude more for L2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("l2_preference", config.project(self.project))
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["l2_preference"], "claude")
        self.assertNotEqual(self.alt("project", "set", self.project, "--l2-preference", "other", "--reason", "x").returncode, 0)
        result = self.alt("project", "set", self.project, "--unset-l2-preference", "--reason", "Auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_settings(self.project)
        self.assertNotIn("l2_preference", config.project(self.project))

    def test_project_set_reregistration_is_last_write_wins_and_authority_is_validated(self):
        dispatch.request_setting(self.project, "routing", "codex", "test", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "already pending"):
            dispatch.request_setting(self.project, "routing", "codex", "different", actor="l3")
        config.remove_project(self.project)
        with config.add_project(self.project, path=self.repo):
            pass
        self.assertEqual(dispatch.run_settings(self.project)["routing"]["status"], "done")
        self.assertEqual(config.project(self.project)["routing"], config.parse_routing("codex"))
        with config.add_project(self.project, path=self.repo):
            pass
        self.assertNotIn("routing", config.project(self.project), "deliberate re-registration is the last write")
        with self.assertRaisesRegex(T.TransitionError, "nonempty reason"):
            dispatch.request_setting(self.project, "routing", "codex", "  ", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "requires L3"):
            dispatch.request_setting(self.project, "routing", "codex", "test", actor="l2")

    def test_routing_is_reason_bearing_daemon_state_and_unset_restores_default(self):
        args = ("project", "set", self.project, "--routing", "claude:fable,codex>claude:opus", "--reason", "my preferences")
        result = self.alt(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)["request"]
        self.assertNotIn("routing", config.project(self.project))
        dispatch.run_settings(self.project)
        entry = config.project(self.project)
        self.assertEqual(entry["routing"], config.parse_routing("claude:fable,codex>claude:opus"))
        self.assertEqual(json.loads(self.alt(*args).stdout)["request"]["id"], request["id"])
        events = [json.loads(row) for row in (config.project_dir(self.project) / "events.jsonl").read_text().splitlines()]
        routing_event = next(row for row in events if "routing" in row)
        self.assertEqual((routing_event["actor"], routing_event["reason"]), ("burak", "my preferences"))
        result = self.alt("project", "set", self.project, "--unset-routing", "--reason", "use defaults")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch.run_settings(self.project)
        self.assertNotIn("routing", config.project(self.project))

    def test_invalid_tiers_are_refused_without_changing_preferences(self):
        for value in ("", "unknown", "claude:", "codex,", ">codex", "codex>>claude:opus", "codex,codex", "claude:model name"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dispatch.request_setting(self.project, "routing", value, "test", actor="l3")
        self.assertNotIn("routing", config.project(self.project))


if __name__ == "__main__":
    unittest.main()
