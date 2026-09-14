"""One execution contract: Codex keeps its own sandbox, both engines use the `alt` door, a window switch is a fresh attempt."""
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo
from altitude import config, dispatch, engines, state as S, tasks as T

PROJECT = "door"


class _Stdin(io.BytesIO):
    def close(self):  # keep the prompt readable after codex_bg closes the pipe
        pass


class FakeProcess:
    """A Codex launch that announces its thread and stays alive until told otherwise."""

    def __init__(self, cmd, *, stdout, thread):
        self.cmd, self.pid, self.stdin, self.alive = cmd, 4242, _Stdin(), True
        stdout.write((json.dumps({"type": "thread.started", "thread_id": thread}) + "\n").encode())

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False

    def kill(self):
        self.alive = False


class TestCodexAdapter(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.worktree = add_worktree(self.repo, "wt")
        self.job_root = self.tmp / "jobs"

    def test_sandbox_roots_are_the_worktree_both_git_dirs_and_altitude_home(self):
        # Codex leaves a linked worktree's own metadata read-only unless it is a root of its own, which blocked
        # `git fetch` for task give-the-chat-section-its-own-scrollbar on 2026-09-03.
        settings = engines.codex_sandbox(self.worktree, extra_roots=engines._git_dirs(self.worktree))
        roots_setting = next(s for s in settings if s.startswith("sandbox_workspace_write.writable_roots="))
        roots = json.loads(roots_setting.split("=", 1)[1])
        self.assertEqual(roots, [str(self.worktree.resolve()), str((self.repo / ".git").resolve()),
                                 str((self.repo / ".git" / "worktrees" / "wt").resolve()),
                                 str(config.ROOT.resolve())])
        self.assertTrue(all(Path(r).is_dir() for r in roots), "Codex bind-mounts writable roots; they must exist")
        self.assertIn('sandbox_mode="workspace-write"', settings)
        self.assertIn("sandbox_workspace_write.network_access=true", settings)
        self.assertIn('approval_policy="never"', settings)

    def _launch(self, *, thread="thr-1", **kw):
        procs = []

        def popen(cmd, **pkw):
            procs.append(FakeProcess(cmd, stdout=pkw["stdout"], thread=thread))
            return procs[-1]

        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(engines, "codex_sandbox", return_value=["s1", "s2"]), \
             mock.patch.object(engines, "_git_dirs", return_value=[self.repo / ".git"]), \
             mock.patch.object(engines, "_codex_service_command",
                               side_effect=lambda unit, command, env: ["svc", unit, *command]), \
             mock.patch.object(engines, "_unit_active", return_value=False):
            res = engines.codex_bg("door/t-1", "brief", cwd=self.worktree, job_root=self.job_root,
                                   extra_env={"ALTITUDE_TASK": "t", "ALTITUDE_ATTEMPT": "1"}, **kw)
        return res, procs

    def test_fresh_turn_runs_codex_exec_in_the_worktree_with_the_persona_in_front(self):
        persona = self.tmp / "l2.md"
        persona.write_text("PERSONA")
        res, procs = self._launch(persona=persona, model="gpt-x")
        self.assertEqual(res["returncode"], 0)
        row = res["agent"]
        self.assertEqual(procs[0].cmd, ["svc", engines._codex_unit(row["id"]), config.CODEX_BIN, "exec", "--json",
                                        "--strict-config", "--skip-git-repo-check", "-C", str(self.worktree),
                                        "-m", "gpt-x", "-c", "s1", "-c", "s2", "-"])
        sent = procs[0].stdin.getvalue().decode()
        self.assertTrue(sent.startswith("PERSONA\n\n"))
        self.assertIn(engines.CODEX_PATCH_NOTE, sent)
        self.assertTrue(sent.endswith("\n\nbrief"))
        self.assertEqual((row["sessionId"], row["state"], row["status"], row["engine"], row["pid"], row["name"]),
                         ("thr-1", "working", "busy", "codex", 4242, "door/t-1"))
        self.assertEqual(json.loads((self.job_root / f"{row['id']}.json").read_text())["session_id"], "thr-1")

    def test_resume_continues_the_same_thread_and_refuses_another(self):
        res, procs = self._launch(resume="thr-1")
        self.assertEqual(procs[0].cmd[2:5], [config.CODEX_BIN, "exec", "resume"])
        self.assertNotIn("-C", procs[0].cmd)
        self.assertEqual(procs[0].cmd[-2:], ["thr-1", "-"])
        self.assertEqual(procs[0].stdin.getvalue(), b"brief", "the thread already holds the persona")
        self.assertEqual((res["returncode"], res["agent"]["sessionId"]), (0, "thr-1"))

        with mock.patch.object(engines, "codex_stop", return_value="stopped") as stop:
            res, _ = self._launch(resume="thr-1", thread="thr-2")
        self.assertEqual(res["returncode"], 1)
        self.assertIn("different Codex thread", res["stderr"])
        stop.assert_called_once()

    def test_worker_row_states_follow_the_unit_and_the_events(self):
        wid = "w-states"
        paths = engines._codex_paths(self.job_root, wid)
        S.write_json(paths["record"], {"id": wid, "name": "n", "pid": 1, "unit": "u.service", "started_at": "t",
                                       "session_id": None, "stopped": None})
        paths["stdout"].write_text('{"type":"thread.started","thread_id":"thr-9"}\n')
        paths["stderr"].write_text("boom\n")
        with mock.patch.object(engines, "_unit_active", return_value=True):
            self.assertEqual(engines.codex_worker(wid, job_root=self.job_root)["state"], "working")
        with mock.patch.object(engines, "_unit_active", return_value=False):
            row = engines.codex_worker(wid, job_root=self.job_root)
            self.assertEqual((row["state"], row["status"], row["detail"], row["sessionId"]),
                             ("failed", "exited", "boom\n", "thr-9"))
            with paths["stdout"].open("a") as f:
                f.write('{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}\n')
            row = engines.codex_worker(wid, job_root=self.job_root)
            self.assertEqual((row["state"], row["usage"]["input_tokens"]), ("done", 5))
        self.assertIsNone(engines.codex_worker("missing", job_root=self.job_root))
        self.assertIsNone(engines.codex_worker(None, job_root=self.job_root))

    def test_stop_ends_the_unit_and_stamps_the_record(self):
        wid = "w-stop"
        paths = engines._codex_paths(self.job_root, wid)
        S.write_json(paths["record"], {"id": wid, "engine": "codex", "name": "n", "pid": 1, "unit": "altitude-codex-w-stop.service",
                                       "started_at": "t", "session_id": "thr-3", "stopped": None})
        paths["stdout"].write_text("")
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(engines.subprocess, "run", return_value=done) as run, \
             mock.patch.object(engines, "_unit_active", return_value=False):
            self.assertEqual(engines.codex_stop(wid, job_root=self.job_root), "Codex worker stopped")
            self.assertEqual(engines.codex_worker(wid, job_root=self.job_root)["state"], "stopped")
        self.assertEqual(run.call_args.args[0], [engines.SYSTEMCTL_BIN, "--user", "stop", "altitude-codex-w-stop.service"])
        with mock.patch.object(engines.subprocess, "run", return_value=done), \
             mock.patch.object(engines, "_unit_active", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                engines.codex_stop(wid, job_root=self.job_root)

    def test_service_launcher_is_synchronous_and_scrubs_the_user_bus_before_codex_starts(self):
        child_env = {"PATH": os.environ["PATH"], "ALTITUDE_PROJECT": "altitude", "ALTITUDE_TASK": "task"}
        script = ('import json, os; print(json.dumps({'
                  '"project": os.environ.get("ALTITUDE_PROJECT"), "task": os.environ.get("ALTITUDE_TASK"), '
                  '"secret": os.environ.get("MANAGER_FAKE_SECRET"), "bus": os.environ.get("DBUS_SESSION_BUS_ADDRESS")}))')
        command = engines._codex_service_command("altitude-codex-test.service", [sys.executable, "-c", script], child_env)
        for flag in ("--wait", "--pipe", "--property=NoNewPrivileges=no", "--property=KillMode=control-group"):
            self.assertIn(flag, command)
        child = command[command.index("--") + 1:]
        self.assertEqual(child[:2], [engines.ENV_BIN, "-i"])
        result = subprocess.run(child, capture_output=True, text=True, check=True,
                                env={"PATH": os.environ["PATH"], "MANAGER_FAKE_SECRET": "must-not-cross",
                                     "DBUS_SESSION_BUS_ADDRESS": "unix:path=/manager/bus"})
        self.assertEqual(json.loads(result.stdout), {"project": "altitude", "task": "task", "secret": None, "bus": None})

    def test_synchronous_turn_sends_the_prompt_on_stdin_and_reads_the_last_message(self):
        stdout = "\n".join(json.dumps(e) for e in (
            {"type": "thread.started", "thread_id": "thr-l3"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "the answer"}},
            {"type": "turn.completed", "usage": {"input_tokens": 7}})) + "\n"
        seen = {}

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            return SimpleNamespace(pid=9, returncode=0,
                                   communicate=lambda text, timeout=None: seen.update(stdin=text) or (stdout, ""))

        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(engines, "_codex_service_command", side_effect=lambda unit, command, env: command):
            out = engines.codex_exec("hello", cwd=self.worktree, effort="high", resume="thr-l3",
                                     extra_env={"ALTITUDE_ACTOR": "l3"})
        self.assertEqual(seen["cmd"][:3], [config.CODEX_BIN, "exec", "resume"])
        self.assertEqual(seen["cmd"][-2:], ["thr-l3", "-"])
        self.assertIn('model_reasoning_effort="high"', seen["cmd"])
        self.assertEqual(seen["stdin"], "hello")
        self.assertEqual((out["text"], out["reported_session_id"], out["usage"], out["error"]),
                         ("the answer", "thr-l3", {"input_tokens": 7}, None))

    def test_window_hold_belongs_to_claude_only(self):
        with mock.patch.object(engines, "usage_hold", return_value="2030-01-01T00:00:00+00:00"):
            self.assertEqual(engines.window_hold("claude"), "2030-01-01T00:00:00+00:00")
            self.assertIsNone(engines.window_hold("codex"))


class TestDoor(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(PROJECT, path=self.repo)

    def _alt(self, *args, env=None):
        return self.alt("--project", PROJECT, *args, env={"ALTITUDE_ACTOR": "l2", **(env or {})})

    def test_l2_has_only_the_worker_commands(self):
        for args in (("task", "new", "--title", "x", "y"), ("dispatch", "s"),
                     ("task", "resume", "s", "--reason", "L3 daemon handoff only"),
                     ("task", "stop", "s", "--reason", "L3 daemon handoff only"), ("chat", "hi")):
            with self.subTest(args=args):
                out = self._alt(*args)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn("not available to an L2 worker", out.stderr,
                              "the required reason is present, so the role door decides before any daemon handoff")
        out = self._alt("task", "list")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_l2_block_is_fenced_to_its_own_task_and_current_attempt(self):
        task = T.new(PROJECT, "Door block fixture", "Block me.", actor="burak")
        task.update({"state": "running", "attempt": 2})
        S.save_task(PROJECT, task)
        slug = task["slug"]
        out = self._alt("task", "block", slug, "--reason", "which key?")
        self.assertIn("ALTITUDE_TASK", out.stderr)
        stale = {"ALTITUDE_TASK": slug, "ALTITUDE_ATTEMPT": "1"}
        out = self._alt("task", "block", slug, "--reason", "which key?", env=stale)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("attempt 1 is no longer current", out.stderr)
        self.assertEqual(S.load_task(PROJECT, slug)["state"], "running")
        out = self._alt("task", "block", slug, "--reason", "which key?", env={**stale, "ALTITUDE_ATTEMPT": "2"})
        self.assertEqual(out.returncode, 0, out.stderr)
        blocked = S.load_task(PROJECT, slug)
        self.assertEqual((blocked["state"], blocked["blocked_reason"]), ("blocked", "which key?"))
        self.assertEqual(S.read_events(PROJECT, slug)[-1]["by"], "l2")


class TestFreshAttempt(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(PROJECT, path=self.repo)

    def test_requeue_with_a_worker_needs_clear_worker_and_pins_the_next_engine(self):
        task = T.new(PROJECT, "Requeue fixture", "Do it.", actor="burak")
        task.update({"state": "blocked", "agent_id": "a", "session_id": "s", "l2_engine": "codex",
                     "engine_model": "m", "routing": "why", "resume_after": "x"})
        S.save_task(PROJECT, task)
        with self.assertRaisesRegex(T.TransitionError, "resume it instead"):
            T.requeue(PROJECT, task["slug"])
        out = T.requeue(PROJECT, task["slug"], engine="claude", clear_worker=True)
        self.assertEqual((out["state"], out["l2_engine"], out["agent_id"], out["session_id"], out["engine_model"],
                          out["routing"]), ("queued", "claude", None, None, None, None))
        self.assertNotIn("resume_after", out)

    def test_brief_carries_the_previous_attempts_progress(self):
        task = T.new(PROJECT, "Progress fixture", "Do it.", actor="burak")
        self.assertNotIn("stopped before finishing", dispatch.build_brief(PROJECT, task["slug"]))
        task["attempt"] = 1
        S.save_task(PROJECT, task)
        (S.task_dir(PROJECT, task["slug"]) / "progress.md").write_text("# Progress\n\n- done: half of it\n")
        brief = dispatch.build_brief(PROJECT, task["slug"])
        self.assertIn("Attempt 1 stopped before finishing", brief)
        self.assertIn("- done: half of it", brief)
        self.assertIn("Altitude resumes your thread on Codex", brief, "one conversation contract for both engines")

    def test_fresh_dispatch_does_not_turn_a_previous_selection_into_a_pin(self):
        task = T.new(PROJECT, "Switch fixture", "Do it.", actor="burak")
        task["l2_engine"] = "claude"
        S.save_task(PROJECT, task)
        fake = {"stdout": "", "stderr": "", "returncode": 0, "agent": {"id": "agent-1", "sessionId": "session-1"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=config.ROOT), \
             mock.patch.object(dispatch.route, "pick_engine",
                               side_effect=lambda role, forced=None, **kwargs: {"engine": forced or "codex", "why": "t"}) as pick, \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake) as launch:
            dispatch.run(PROJECT, task["slug"])
        self.assertIsNone(pick.call_args.kwargs["forced"])
        self.assertEqual(launch.call_args.args[0], "codex")
        self.assertEqual(launch.call_args.kwargs["persona"], config.PERSONAS / "l2.md")
        self.assertEqual(S.load_task(PROJECT, task["slug"])["attempt"], 1)


if __name__ == "__main__":
    unittest.main()
