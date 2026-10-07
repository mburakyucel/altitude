"""Release allowance generation across real task dispatch/resume with fictional workers."""
from copy import deepcopy

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, state as S, tasks as T


class ReleasePermissions(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, routing=config.parse_routing("claude:opus"))
        self.task = T.new(self.project, "Publish approved release", "Publish the approved release.")
        self.slug = self.task["slug"]
        self.task.update(state="running", attempt=1, l2_engine="claude",
                         release_grant={"id": "grant", "attempt": 1, "expires_at": None})
        S.save_task(self.project, self.task)

    def settings(self, task=None, *, attempt=1):
        S.save_task(self.project, task or self.task)
        return S.read_json(dispatch.session_settings(
            self.project, self.slug, S.session_key(self.project, self.slug, attempt)))

    def assert_allowance(self, settings):
        self.assertEqual(settings["permissions"], {"allow": [
            f"Bash(alt task publish {self.slug})", f"Bash(alt task publish {self.slug} --check)"]})

    def test_only_two_exact_native_commands_and_existing_hooks_and_environment(self):
        settings = self.settings()
        self.assert_allowance(settings)
        self.assertEqual(set(settings), {"permissions", "hooks", "env", "autoCompactWindow"})
        self.assertEqual(set(settings["hooks"]), {"PostToolUse", "Stop"})
        self.assertEqual(settings["env"]["ALTITUDE_TASK"], self.slug)
        self.assertEqual(settings["env"]["ALTITUDE_ACTOR"], "l2")
        self.assertEqual(settings["autoCompactWindow"], config.AUTOCOMPACT_WINDOW)
        self.assertEqual(engines.release_permissions("codex", self.slug), {})
        self.assertEqual(engines.release_permissions("future-engine", self.slug), {})
        with self.assertRaises(ValueError):
            engines.release_permissions("claude", "other-task; gh release create")

    def test_refresh_removes_inactive_grants_and_cross_attempt_permission(self):
        variants = [
            {"release_grant": None},
            {"release_grant": {**self.task["release_grant"], "revoked_at": S.now()}},
            {"release_grant": {**self.task["release_grant"], "completed_at": S.now()}},
            {"release_grant": {**self.task["release_grant"], "expires_at": "2000-01-01T00:00:00+00:00"}},
            {"release_grant": {**self.task["release_grant"], "attempt": 2}},
            {"state": "done"}, {"state": "rejected"}, {"state": "queued"},
            {"l2_engine": "codex"},
        ]
        for updates in variants:
            with self.subTest(updates=updates):
                self.assert_allowance(self.settings())
                self.assertNotIn("permissions", self.settings({**self.task, **updates}))
        self.assertNotIn("permissions", self.settings(attempt=2))
        other = T.new(self.project, "Another task", "No publication approval.")
        other.update(state="running", attempt=1, l2_engine="claude")
        S.save_task(self.project, other)
        settings = S.read_json(dispatch.session_settings(
            self.project, other["slug"], S.session_key(self.project, other["slug"], 1)))
        self.assertNotIn("permissions", settings)

    def test_dispatch_message_resume_and_revocation_keep_one_session_and_hold(self):
        make_repo(self.repo)
        worker = FakeL2()
        worker.install(self)
        self.task.update(state="queued", attempt=0, release_grant=None, hold_merge="Operator review")
        S.save_task(self.project, self.task)
        dispatch.run(self.project, self.slug)
        self.assertNotIn("permissions", S.read_json(worker.calls[-1]["settings"]))
        task = S.load_task(self.project, self.slug)
        original = deepcopy(task)
        task["release_grant"] = {"id": "grant", "attempt": task["attempt"], "expires_at": None}
        S.save_task(self.project, task)
        T.block(self.project, self.slug, "Load the approved release permission", actor="altd")
        message = T.message(self.project, self.slug, "l3", "Continue the approved publication.")
        dispatch.resume(self.project, self.slug)
        self.assert_allowance(S.read_json(worker.calls[-1]["settings"]))
        self.assertIn(message["text"], worker.calls[-1]["prompt"])
        current = S.load_task(self.project, self.slug)
        for name in ("attempt", "session_id", "hold_merge"):
            self.assertEqual(current[name], original[name])
        current["release_grant"]["revoked_at"] = S.now()
        S.save_task(self.project, current)
        T.block(self.project, self.slug, "Approval revoked", actor="altd")
        dispatch.resume(self.project, self.slug)
        self.assertNotIn("permissions", S.read_json(worker.calls[-1]["settings"]))

    def test_fresh_attempt_never_loads_previous_attempt_allowance(self):
        make_repo(self.repo)
        worker = FakeL2()
        worker.install(self)
        self.task.update(state="queued")
        S.save_task(self.project, self.task)
        dispatch.run(self.project, self.slug)
        self.assertEqual(S.load_task(self.project, self.slug)["attempt"], 2)
        self.assertNotIn("permissions", S.read_json(worker.calls[-1]["settings"]))
