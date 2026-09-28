"""Resume a delivered owner only after the previous worker's termination is known."""
import subprocess
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo
from altitude import config, dispatch, engines, platform, state as S, tasks as T


class TestWorkerStatusResume(AltitudeCase):
    host = "linux"  # systemd fixtures

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.patch(engines, "_codex_processes", {})

    def owner(self, engine, title):
        task = T.new(self.project, title, "Finish the delivery report", paths=["README.md"])
        slug = task["slug"]
        task.update(state="blocked", attempt=3, l2_engine=engine, agent_id=slug,
                    session_id="original-session", worktree=str(add_worktree(self.repo, slug)),
                    hold_merge="Recorded review hold", prs=[101], fault="l2-resume")
        S.save_task(self.project, task)
        root = dispatch.l2_job_root(self.project, slug)
        paths = engines._codex_paths(root, slug)
        S.write_json(paths["record"], {"id": slug, "engine": engine, "session_id": task["session_id"],
                     "engine_model": "fixture", "started_at": "2026-01-01T00:00:00+00:00",
                     "unit": engines._codex_unit(slug) if engine == "codex" else engines._claude_unit(slug)})
        paths["stdout"].write_text('{"type":"turn.completed"}\n' if engine == "codex" else
                                  '{"type":"result","is_error":false}\n')
        report = S.task_dir(self.project, slug) / "report.json"
        S.write_json(report, {"landed": {"prs": [{"number": 101, "merged": True}]}})
        history = S.append_event(self.project, slug, "report-superseded", report=S.read_json(report))
        message = T.message(self.project, slug, "l3", "Deployment verified; finish the report.", by="l3")
        dispatch.request_task_operation(self.project, slug, "resume", "Cause verified gone", actor="l3")
        return task, paths, report, history, message

    def resume_case(self, engine, states, success):
        task, paths, report, history, message = self.owner(engine, f"Resume {engine} {len(S.list_tasks(self.project))}")
        slug = task["slug"]
        report_before = report.read_bytes()
        real_run = subprocess.run
        observations = iter(states)
        stops = []
        late = []

        def systemctl(cmd, **kwargs):
            if cmd[0] != platform.SYSTEMCTL:
                return real_run(cmd, **kwargs)
            if cmd[2] == "stop":
                stops.append(cmd[-1])
                return subprocess.CompletedProcess(cmd, 5, "", "Unit not loaded")
            self.assertEqual(cmd[2], "is-active")
            state = next(observations)
            if isinstance(state, Exception):
                raise state
            return subprocess.CompletedProcess(cmd, *state)

        def launch(_engine, _name, session, prompt, **kwargs):
            self.assertEqual((_engine, session, kwargs["extra_env"]["ALTITUDE_ATTEMPT"]),
                             (engine, task["session_id"], "3"))
            self.assertIn(message["text"], prompt)
            # The resumed turn carries the message alone; the persona already requires a fresh report.
            self.assertEqual(prompt, T.render_inbox([message]))
            self.assertIn("Every resumed code-owner turn", (config.PERSONAS / "l2.md").read_text())
            late.append(T.message(self.project, slug, "l3", "Keep the evidence.", by="l3"))
            return {"returncode": 0, "agent": {"id": f"new-{slug}", "sessionId": session,
                                               "input_delivered": True}}

        with mock.patch.object(engines.subprocess, "run", side_effect=systemctl), \
             mock.patch.object(engines, "resume_l2", side_effect=launch) as resumed:
            if success:
                dispatch.run_task_operation(self.project, slug)
            else:
                with self.assertRaises(dispatch.ResumeFailure):
                    dispatch.run_task_operation(self.project, slug)
            current = S.load_task(self.project, slug)
            self.assertEqual(current["state"], "running" if success else "blocked")
            self.assertEqual(current["daemon_request"]["status"], "done" if success else "failed")
            self.assertEqual(resumed.call_count, int(success))
            if success:
                self.assertEqual(dispatch.resume(self.project, slug), {"already_running": True})
                self.assertEqual(resumed.call_count, 1)
        for key in ("session_id", "attempt", "l2_engine", "worktree", "paths", "prs", "hold_merge"):
            self.assertEqual(current[key], task[key], key)
        self.assertEqual(report.read_bytes(), report_before)
        self.assertIn(history, S.read_events(self.project, slug))
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)],
                         [late[0]["id"]] if success else [message["id"]])
        self.assertFalse(current.get("resume_claim"))
        if not success:
            self.assertEqual(current["agent_id"], task["agent_id"])
            self.assertNotIn("stopped", S.read_json(paths["record"]))
        return stops

    def test_exited_collected_and_stopped_workers_resume_same_owner_once(self):
        for engine in config.ENGINES:
            for terminal in ((3, "inactive", ""), (3, "failed", ""), (4, "inactive\n", "")):
                with self.subTest(engine=engine, terminal=terminal):
                    self.assertEqual(self.resume_case(engine, [terminal], True), [])
            with self.subTest(engine=engine, stopped=True):
                self.assertEqual(len(self.resume_case(engine, [(0, "active", ""),
                                      (4, "inactive", ""), (4, "inactive", "")], True)), 1)

    def test_live_or_unavailable_status_never_starts_a_replacement(self):
        for engine in config.ENGINES:
            for status in ((0, "active", ""), (1, "", "Failed to connect to bus"),
                           (4, "unknown", ""), (0, "unrecognized", ""),
                           subprocess.TimeoutExpired("systemctl", 30)):
                with self.subTest(engine=engine, status=status):
                    self.assertEqual(len(self.resume_case(engine, [(0, "active", ""), status], False)), 1)
            with self.subTest(engine=engine, initial_unavailable=True):
                self.assertEqual(self.resume_case(engine, [(1, "", "Failed to connect to bus")], False), [])
