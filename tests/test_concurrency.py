"""One persistent machine concurrency cap with operator-owned live changes."""
import json
import unittest
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, digest, dispatch, engines, server, state as S, tasks as T


class TestConcurrency(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.setenv("ALTITUDE_ACTOR", None)
        self.quiet_engines()
        for name in ("settings.json", "wip-request.json", "events.jsonl"):
            path = config.ROOT / name
            self.assertFalse(path.exists())
            self.addCleanup(path.unlink, missing_ok=True)

    def machine(self, value, reason="capacity decision"):
        result = self.alt("machine", "set", *(('--unset-wip',) if value is None else ('--wip', str(value))),
                          "--reason", reason)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def show(self):
        result = self.alt("machine", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_persistent_machine_override_reset_audit_and_retry(self):
        initial = self.show()
        self.assertEqual((initial["wip"], initial["default"]), (80, 80))
        self.assertNotIn("default_project", initial)
        self.assertNotIn("projects", initial)
        request = self.machine(120)["request"]
        self.assertEqual(self.show()["wip"], 80, "the CLI only queues the daemon request")
        self.assertEqual(self.show()["request"]["status"], "pending")
        self.assertEqual(self.machine(120)["request"]["id"], request["id"])
        dispatch.run_settings()
        active = self.show()  # separate process: no cached in-process setting
        self.assertEqual((active["wip"], active["override"], active["default"]), (120, 120, 80))
        self.assertEqual(active["request"]["status"], "done")
        self.assertEqual(S.read_json(config.ROOT / "settings.json", {}), {"wip": 120})
        self.assertTrue(self.machine(120)["idempotent"])
        events = config.ROOT / "events.jsonl"
        event = json.loads(events.read_text())
        self.assertEqual((event["kind"], event["actor"], event["reason"]),
                         ("machine-set", "burak", "capacity decision"))
        # The existing daemon protocol recovers a crash after the effect/audit before the receipt.
        S.write_json(config.ROOT / "wip-request.json", request)
        dispatch.run_settings()
        self.assertEqual(len(events.read_text().splitlines()), 1)
        self.machine(None, "restore machine default")
        dispatch.run_settings()
        self.assertEqual(self.show()["wip"], 80)
        self.assertIsNone(self.show()["override"])
        self.assertNotIn("wip", config.machine_settings())
        self.assertFalse(self.machine(120)["idempotent"])

    def test_machine_cap_validation_and_status(self):
        for value in (0, -1, True, 1.5, "8"):
            with self.subTest(value=value), self.assertRaisesRegex(T.TransitionError, "positive integer"):
                dispatch.request_setting(None, "wip", value, "test", actor="burak")
        self.machine(120)
        dispatch.run_settings()
        self.assertEqual(digest.wip()["limit_machine"], 120)
        self.machine(4, "lower aggregate")
        dispatch.run_settings()
        self.assertEqual(self.show()["wip"], 4)
        self.assertEqual(digest.wip()["limit_machine"], 4)
        self.assertNotIn("limits_per_project", digest.wip())
        self.assertNotIn("limit_project", digest.wip())

    def test_machine_mutation_requires_operator_and_reason_at_cli_and_backend(self):
        for actor in ("l2", "l3"):
            self.setenv("ALTITUDE_ACTOR", actor)
            result = self.alt("machine", "set", "--wip", "100", "--reason", "test")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not available", result.stderr)
            with self.assertRaisesRegex(T.TransitionError, "requires the operator"):
                dispatch.request_setting(None, "wip", 100, "test", actor=actor)
        with self.assertRaisesRegex(T.TransitionError, "nonempty reason"):
            dispatch.request_setting(None, "wip", 100, "  ", actor="burak")
        result = server.l3_verb_request(self.project, {
            "kind": "alt", "args": ["machine", "set", "--wip", "100", "--reason", "test"]})
        self.assertNotEqual(result["returncode"], 0)
        self.assertFalse((config.ROOT / "wip-request.json").exists())

    def test_lowering_machine_cap_preserves_workers_and_gates_dispatch_and_resume(self):
        make_repo(self.repo)
        self.register("another", wip=8)
        launches = []

        def launch(*args, **kwargs):
            launches.append(args)
            return {"returncode": 0, "agent": {"id": f"worker-{len(launches)}",
                                                "sessionId": f"session-{len(launches)}"}}

        with mock.patch.object(engines, "start_l2", side_effect=launch):
            for index in range(3):
                task = T.new(self.project, f"Running {index}", "fictional work")
                dispatch.run(self.project, task["slug"])
            snapshots = S.list_tasks(self.project)
            queued = T.new(self.project, "Waiting", "fictional work")
            resumable = T.new("another", "Resume waiting", "fictional work")
            resumable.update(state="blocked", agent_id="paused", session_id="paused-session")
            S.save_task("another", resumable)

            self.machine(2, "reduce machine load")
            dispatch.run_settings()
            self.assertEqual(dispatch.wip_hold("another"), "WIP limit: 3 running on this machine")
            server.dispatch_waiting(self.project)
            self.assertIn("3 running on this machine", dispatch.resume("another", resumable["slug"])["held"])
            self.assertEqual(len(launches), 3)
            self.assertEqual(S.load_task(self.project, queued["slug"])["state"], "queued")
            for snapshot in snapshots:
                self.assertEqual(S.load_task(self.project, snapshot["slug"]), snapshot)

            # The resume remains ineligible while the operator has asked it to wait.
            paused = S.load_task("another", resumable["slug"])
            paused.update(waiting_on="burak", resume_after=None)
            S.save_task("another", paused)
            # Completion frees capacity only once the running count falls below the lowered cap.
            for snapshot in snapshots[:2]:
                snapshot["state"] = "done"
                S.save_task(self.project, snapshot)
                server.dispatch_waiting(self.project)
            self.assertEqual(len(launches), 4)
            self.assertEqual(S.load_task(self.project, queued["slug"])["state"], "running")
            self.assertEqual(digest.wip()["machine"], 2)
            self.machine(80, "restore capacity")
            dispatch.run_settings()
            self.assertIsNone(dispatch.wip_hold("another"))

    def test_machine_request_is_applied_before_project_ticks_even_at_full_capacity(self):
        self.machine(1)
        dispatch.run_settings()
        task = T.new(self.project, "Active", "fictional work")
        task["state"] = "running"
        S.save_task(self.project, task)
        self.machine(120, "unblock capacity")
        seen = []
        with mock.patch.object(server.quota_codex, "refresh_if_due"), \
             mock.patch.object(server, "drain_hook_faults"), \
             mock.patch.object(server, "tick_project", side_effect=lambda p: seen.append(config.machine_wip())), \
             mock.patch.object(server, "auto_restart"), mock.patch.object(server, "morning_digest"):
            server.tick()
        self.assertEqual(seen, [120])
        self.assertEqual(S.load_task(self.project, task["slug"]), task)


if __name__ == "__main__":
    unittest.main()
