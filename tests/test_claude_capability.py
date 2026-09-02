"""Phase 1A closes every autonomous Claude ingress without hiding legacy evidence."""
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BOOT = tempfile.mkdtemp(prefix="altitude-claude-capability-bootstrap-")
os.environ["ALTITUDE_HOME"] = _BOOT

from altitude import actions, config, dispatch, engines, incidents, l1, l3, l3_actions, land, recovery, route, server, state as S, tasks as T  # noqa: E402


class TestClaudeCapability(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="altitude-claude-capability-")
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.patches = mock.patch.multiple(
            config,
            ROOT=self.root,
            MONITOR_DIR=self.root / "monitor",
            PROJECTS_FILE=self.root / "projects.json",
        )
        self.patches.start()
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": str(self.repo)}})

    def tearDown(self):
        self.patches.stop()
        self.tempdir.cleanup()

    @staticmethod
    def _healthy_quota() -> dict:
        row = {"weekly_used": 1.0, "short_used": 1.0, "raw": {"known": True}}
        return {"claude": dict(row), "codex": dict(row)}

    def test_one_closed_fact_keeps_codex_as_the_only_autonomous_engine(self):
        self.assertEqual(config.AUTONOMOUS_ENGINES, ("codex",))
        with mock.patch.object(route, "quota_snapshot", return_value=self._healthy_quota()):
            self.assertEqual(route.pick_engine("l2")["engine"], "codex")
            forced = route.pick_engine("l2", forced="claude")
        self.assertIsNone(forced["engine"])
        self.assertEqual(forced["requested_engine"], "claude")
        self.assertIn("capability disabled", forced["why"])

    def test_every_claude_transport_refuses_before_cli_or_registry_access(self):
        kwargs = {
            "cwd": self.repo, "persona": self.repo / "persona", "model": None,
            "settings": self.repo / "settings", "extra_env": {}, "job_root": self.root / "jobs",
        }
        with mock.patch.object(engines.subprocess, "Popen") as popen, \
             mock.patch.object(engines, "claude_agents") as agents, \
             mock.patch.object(engines, "usage_hold") as usage:
            with self.assertRaises(engines.EngineCapabilityError):
                engines.claude_print("prompt", cwd=self.repo)
            with self.assertRaises(engines.EngineCapabilityError):
                engines.claude_bg("name", "prompt", cwd=self.repo)
            with self.assertRaises(engines.EngineCapabilityError):
                engines.claude_resume_bg("name", "session", "prompt", cwd=self.repo)
            with self.assertRaises(engines.EngineCapabilityError):
                engines.start_l2("claude", "name", "prompt", **kwargs)
            with self.assertRaises(engines.EngineCapabilityError):
                engines.resume_l2("claude", "name", "session", "prompt", **kwargs)
        popen.assert_not_called(); agents.assert_not_called(); usage.assert_not_called()

    def test_new_task_rejects_claude_or_a_claude_model_without_state(self):
        with self.assertRaisesRegex(T.TransitionError, "no autonomous/mutating launch capability"):
            T.new("p", "forced Claude", "request", engine="claude")
        with self.assertRaisesRegex(T.TransitionError, "no autonomous/mutating launch capability"):
            T.new("p", "implicit Claude", "request", model="opus")
        self.assertEqual(S.list_tasks("p"), [])

    def _legacy_task(self, *, state="running", resume_after=None) -> dict:
        task = T.new("p", "legacy", "request")
        worktree = self.repo / ".claude" / "worktrees" / task["slug"]
        worktree.mkdir(parents=True)
        task.update({
            "state": state, "dispatch_id": f"{task['slug']}-1", "session_id": "session-old",
            "agent_id": "agent-old", "l2_engine": "claude", "l2_token": "token",
            "worktree": str(worktree),
        })
        if resume_after is not None:
            task["resume_after"] = resume_after
        S.save_task("p", task)
        return task

    def _assert_rejected_ingress_is_inert(self, task: dict, invoke) -> object:
        paths = (
            S.task_dir("p", task["slug"]) / "status.json",
            S.task_dir("p", task["slug"]) / "events.log",
            S.task_dir("p", task["slug"]) / "conversation.jsonl",
            recovery.hold_path(),
        )
        before = {path: path.read_bytes() if path.exists() else None for path in paths}
        with mock.patch.object(engines, "stop_l2_worker") as stop, \
             mock.patch.object(engines, "resume_l2") as resume, \
             mock.patch.object(incidents, "system_fault") as fault, \
             mock.patch.object(recovery, "hold") as recovery_hold:
            result = invoke()
        stop.assert_not_called(); resume.assert_not_called(); fault.assert_not_called(); recovery_hold.assert_not_called()
        after = {path: path.read_bytes() if path.exists() else None for path in paths}
        self.assertEqual(after, before)
        return result

    def _tree(self, root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    def test_disabled_claude_message_and_direct_resume_are_rejected_without_mutation(self):
        task = self._legacy_task()

        def message():
            with self.assertRaisesRegex(T.TransitionError, "engine hold:.*foreground ownership is unproved"):
                dispatch.message_l2("p", task["slug"], "continue")

        self._assert_rejected_ingress_is_inert(task, message)

        def resume():
            with self.assertRaisesRegex(T.TransitionError, "engine hold:.*foreground ownership is unproved"):
                dispatch.resume_session("p", task["slug"], "continue")

        self._assert_rejected_ingress_is_inert(task, resume)

    def test_disabled_claude_manual_and_due_blocked_resume_stay_persistently_held(self):
        task = self._legacy_task(state="blocked", resume_after="1970-01-01T00:00:00+00:00")

        result = self._assert_rejected_ingress_is_inert(
            task, lambda: dispatch.resume_blocked("p", task["slug"], "continue"))
        self.assertTrue(result["deferred"])
        self.assertIn("engine hold:", result["hold"])

        due = self._assert_rejected_ingress_is_inert(task, lambda: dispatch.resume_due("p"))
        self.assertEqual(due, [])
        self.assertEqual(S.load_task("p", task["slug"])["resume_after"],
                         "1970-01-01T00:00:00+00:00")

    def test_legacy_claude_token_is_inert_at_task_helper_and_action_boundaries(self):
        task = self._legacy_task()
        brief = self.root / "helper.md"
        brief.write_text("inspect only\n")
        before = self._tree(config.project_dir("p"))

        rejected = (
            lambda: T.append_task_message(
                "p", task["slug"], "l2", "done", expected_dispatch_id=task["dispatch_id"],
                expected_session_id=task["session_id"], expected_l2_token=task["l2_token"], actor="l2"),
            lambda: T.block("p", task["slug"], "blocked", actor="l2"),
            lambda: T.done(
                "p", task["slug"], actor="l2", digest="done",
                expected_dispatch_id=task["dispatch_id"], expected_l2_token=task["l2_token"]),
            lambda: l1.start(
                "p", task["slug"], brief, expected_dispatch_id=task["dispatch_id"],
                expected_l2_token=task["l2_token"]),
        )
        with mock.patch.object(engines, "start_l2") as start, \
             mock.patch.object(recovery, "hold") as recovery_hold:
            for invoke in rejected:
                with self.subTest(invoke=invoke), self.assertRaisesRegex(
                    T.TransitionError, "engine hold:.*foreground ownership is unproved"
                ):
                    invoke()
            with self.assertRaisesRegex(actions.ActionError, "engine hold:"):
                actions.process_l2("p", {
                    "task": task,
                    "action": {"action": "continue"},
                    "agent": {"action": {"action": "continue"}},
                })
        start.assert_not_called()
        recovery_hold.assert_not_called()
        self.assertEqual(self._tree(config.project_dir("p")), before)
        self.assertFalse((S.task_dir("p", task["slug"]) / "l1").exists())

    def test_spoofed_task_actors_cannot_mutate_a_legacy_owner(self):
        task = self._legacy_task(state="blocked")
        before = self._tree(config.project_dir("p"))
        calls = (
            lambda: T.brief("p", task["slug"], "spoofed", actor="burak"),
            lambda: T.reject("p", task["slug"], "spoofed", actor="burak"),
            lambda: T.block("p", task["slug"], "spoofed", actor="altd"),
            lambda: T.resume("p", task["slug"], actor="burak"),
            lambda: T.done("p", task["slug"], actor="burak", digest="spoofed"),
            lambda: T.fyi("p", task["slug"], "spoofed", actor="burak"),
            lambda: T.set_hold_merge("p", task["slug"], "spoofed", actor="burak"),
            lambda: T.set_spend("p", task["slug"], turns=99),
        )
        for invoke in calls:
            with self.subTest(invoke=invoke), self.assertRaisesRegex(
                T.TransitionError, "engine hold:.*foreground ownership is unproved"
            ):
                invoke()
        self.assertEqual(self._tree(config.project_dir("p")), before)

    def test_legacy_claude_token_cannot_land_or_reach_git_or_github_effects(self):
        task = self._legacy_task()
        with mock.patch.object(land, "_git") as git, mock.patch.object(land, "_pr_view") as github:
            for actor in ("l2", "burak", None):
                authority = {
                    "actor": actor, "dispatch_id": task["dispatch_id"],
                    "l2_token": task["l2_token"],
                }
                with self.subTest(actor=actor), self.assertRaisesRegex(
                    land.LandError, "engine hold:.*foreground ownership is unproved"
                ):
                    land._require_current_publisher("p", task["slug"], task, authority=authority)
        git.assert_not_called()
        github.assert_not_called()

    def test_l3_resume_paths_and_result_brokers_hold_legacy_claude_byte_inert(self):
        task = self._legacy_task(state="blocked")
        task["paths"] = ["src/old.py"]
        S.save_task("p", task)
        before = self._tree(config.project_dir("p"))
        with self.assertRaisesRegex(l3_actions.L3ActionError, "engine hold:"):
            l3_actions._resume_paths("p", task, ["src/new.py"])

        with mock.patch.object(server.verify, "verify") as verify, \
             mock.patch.object(server.incidents, "system_fault") as fault, \
             mock.patch.object(server.actions, "process_l2") as action:
            server.on_l2_finished("p", {"task": task, "agent": {"state": "done", "status": "exited"}})
            server.report_turn("p", task, {"verdict": "ok", "problems": [], "signals": []})
        verify.assert_not_called(); fault.assert_not_called(); action.assert_not_called()
        self.assertEqual(self._tree(config.project_dir("p")), before)

    def test_http_sessionless_resume_holds_legacy_claude_before_task_resume(self):
        task = self._legacy_task(state="blocked")
        task.pop("session_id", None); task.pop("agent_id", None)
        S.save_task("p", task)
        before = self._tree(config.project_dir("p"))
        handler = object.__new__(server.Handler)
        handler.path = "/api/decide"
        handler._body = lambda: {"project": "p", "slug": task["slug"], "option": 0}
        responses = []
        handler._json = lambda payload, status=200: responses.append((status, payload))
        with mock.patch.object(T, "resume") as resume:
            handler.do_POST()
        resume.assert_not_called()
        self.assertEqual(responses[0][0], 409)
        self.assertIn("engine hold:", responses[0][1]["hold"])
        self.assertEqual(self._tree(config.project_dir("p")), before)

    def test_targeted_cli_mutations_are_held_for_exact_legacy_token(self):
        task = self._legacy_task(state="blocked")
        env = {
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": "p", "ALTITUDE_TASK": task["slug"],
            "ALTITUDE_DISPATCH_ID": task["dispatch_id"], "ALTITUDE_L2_TOKEN": task["l2_token"],
        }
        before = self._tree(config.project_dir("p"))
        script = Path(__file__).resolve().parent.parent / "bin" / "alt"
        with mock.patch.dict(os.environ, env, clear=False):
            module = runpy.run_path(str(script))
            main = module["main"]
            for argv in (
                ["--project", "p", "task", "brief", task["slug"], "changed"],
                ["--project", "p", "task", "hold-merge", task["slug"], "--why", "wait"],
                ["--project", "p", "fyi", task["slug"], "changed"],
                ["--project", "p", "task", "resume", task["slug"]],
            ):
                with self.subTest(argv=argv), self.assertRaisesRegex(
                    T.TransitionError, "engine hold:.*foreground ownership is unproved"
                ):
                    main(argv)
        self.assertEqual(self._tree(config.project_dir("p")), before)
        self.assertFalse(recovery.hold_path().exists())

    def test_cli_with_all_owner_markers_cleared_cannot_mutate_legacy_target(self):
        task = self._legacy_task(state="blocked")
        before = self._tree(config.project_dir("p"))
        script = Path(__file__).resolve().parent.parent / "bin" / "alt"
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        effect = self.root / "external-effect"
        for name in ("git", "gh"):
            executable = fake_bin / name
            executable.write_text(f"#!/bin/sh\ntouch {effect}\nexit 0\n")
            executable.chmod(0o755)
        base_env = {**os.environ, "ALTITUDE_HOME": str(self.root), "PATH": str(fake_bin)}
        for name in (
            "ALTITUDE_ACTOR", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_DISPATCH_ID",
            "ALTITUDE_L2_TOKEN", "ALTITUDE_L2_CAPABILITY", "ALTITUDE_SESSION_KEY",
        ):
            base_env.pop(name, None)
        commands = (
            ["--project", "p", "task", "brief", task["slug"], "spoofed"],
            ["--project", "p", "task", "hold-merge", task["slug"], "--why", "spoofed"],
            ["--project", "p", "fyi", task["slug"], "spoofed"],
            ["--project", "p", "task", "resume", task["slug"]],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                result = subprocess.run(
                    [sys.executable, str(script), *argv], cwd=self.repo,
                    env=base_env, capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("engine hold:", result.stderr)
        self.assertEqual(self._tree(config.project_dir("p")), before)
        self.assertFalse(recovery.hold_path().exists())
        self.assertFalse(effect.exists())

    def test_cli_does_not_claim_actor_or_same_uid_markers_are_operator_authentication(self):
        script = Path(__file__).resolve().parent.parent / "bin" / "alt"
        source = script.read_text()
        self.assertNotIn("_require_l2_command_capability", source)
        self.assertNotIn("isatty", source)
        self.assertNotIn("geteuid", source)

    def test_persisted_queued_claude_owner_is_fenced_before_any_dispatch_effect(self):
        task = self._legacy_task(state="queued")
        before = self._tree(config.project_dir("p"))
        with mock.patch.object(dispatch.github_intake, "ensure_snapshot") as intake, \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch, \
             mock.patch.object(dispatch, "_task_worktree") as worktree, \
             mock.patch.object(engines, "start_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "engine hold:.*foreground ownership is unproved"):
                dispatch.run("p", task["slug"])
        intake.assert_not_called(); fetch.assert_not_called(); worktree.assert_not_called(); launch.assert_not_called()
        self.assertEqual(S.load_task("p", task["slug"])["l2_engine"], "claude")
        self.assertEqual(self._tree(config.project_dir("p")), before)

    def test_disabled_legacy_owner_does_not_consume_wip_but_lease_and_ownership_stay_protected(self):
        legacy = self._legacy_task()
        legacy["paths"] = ["./legacy/owned.py (legacy exact scope)"]
        S.save_task("p", legacy)
        candidate = T.new("p", "candidate", "request", paths=["new/free.py"])
        projects = config.load_projects(); projects["p"]["wip"] = 1; config.save_projects(projects)
        with mock.patch.object(config, "WIP_PER_MACHINE", 1):
            self.assertIsNone(dispatch.wip_hold("p", candidate))

        candidate["paths"] = ["legacy/owned.py"]
        S.save_task("p", candidate)
        self.assertIn("file lease:", dispatch.wip_hold("p", candidate))
        self.assertIn(legacy["slug"], {row["slug"] for row in dispatch._lease_tasks("p")})
        self.assertEqual(S.load_task("p", legacy["slug"])["state"], "running")

    def test_disabled_owner_missing_empty_malformed_or_broad_scope_is_repository_uncertain(self):
        legacy = self._legacy_task()
        candidate = T.new("p", "candidate", "request", paths=["new/free.py"])
        cases = (
            ("missing", None),
            ("empty", []),
            ("malformed raw type", "legacy/owned.py"),
            ("malformed member", [{"path": "legacy/owned.py"}]),
            ("broad only", ["altitude", "docs/"]),
        )
        for label, paths in cases:
            with self.subTest(label=label):
                if paths is None:
                    legacy.pop("paths", None)
                else:
                    legacy["paths"] = paths
                S.save_task("p", legacy)
                hold = dispatch.wip_hold("p", candidate)
                self.assertIn("repository-uncertain path scope", hold)

        legacy.update({"state": "blocked", "resume_after": "1970-01-01T00:00:00+00:00",
                       "paths": []})
        S.save_task("p", legacy)
        self.assertIn("repository-uncertain path scope", dispatch.wip_hold("p", candidate))

    def test_disabled_exact_holder_still_blocks_candidate_without_narrow_scope(self):
        legacy = self._legacy_task()
        legacy["paths"] = ["legacy/owned.py"]
        S.save_task("p", legacy)
        for paths in (None, [], ["docs/"]):
            candidate = T.new("p", f"candidate {paths}", "request", paths=paths)
            with self.subTest(paths=paths):
                self.assertIn("repository-uncertain path scope", dispatch.wip_hold("p", candidate))

    def test_newer_disabled_pending_resume_is_not_skipped_by_enabled_resume_ordering(self):
        candidate = T.new("p", "older enabled resume", "request", paths=["legacy/owned.py"])
        candidate.update({"state": "blocked", "l2_engine": "codex",
                          "resume_after": "1970-01-01T00:00:00+00:00",
                          "created": "2026-01-01T00:00:00+00:00"})
        S.save_task("p", candidate)
        legacy = self._legacy_task(state="blocked", resume_after="1970-01-01T00:00:00+00:00")
        legacy.update({"created": "2026-01-02T00:00:00+00:00", "paths": ["legacy/owned.py"]})
        S.save_task("p", legacy)
        self.assertIn("file lease:", dispatch.wip_hold("p", candidate))

        legacy["paths"] = []
        S.save_task("p", legacy)
        self.assertIn("repository-uncertain path scope", dispatch.wip_hold("p", candidate))

    def test_codex_capability_remains_dispatchable(self):
        engines.require_autonomous_engine("codex")
        with mock.patch.object(route, "quota_snapshot", return_value=self._healthy_quota()):
            self.assertEqual(route.pick_engine("l2")["engine"], "codex")

    def test_forced_claude_l3_is_held_without_transport_or_chat_write(self):
        projects = config.load_projects()
        projects["p"]["l3_engine"] = "claude"
        config.save_projects(projects)
        with mock.patch.object(route, "quota_snapshot", return_value=self._healthy_quota()), \
             mock.patch.object(engines, "claude_print") as transport:
            result = l3.turn("p", "hello")
        self.assertIn("engine hold", result["error"])
        transport.assert_not_called()
        self.assertFalse((config.project_dir("p") / "chat.jsonl").exists())

    def test_operator_agents_inspection_remains_read_only(self):
        completed = mock.Mock(returncode=0, stdout=json.dumps([{"id": "legacy"}]), stderr="")
        with mock.patch.object(engines.subprocess, "run", return_value=completed) as run:
            self.assertEqual(engines.claude_agents(), [{"id": "legacy"}])
        self.assertIn("agents", run.call_args.args[0])
        self.assertIn("--json", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
