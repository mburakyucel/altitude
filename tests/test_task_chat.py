"""Burak's messages queue on the task and reach the exact current L2 at its next checkpoint."""
import contextlib
import io
import json
import runpy
import unittest
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import dispatch, engines, incidents, server, state as S, tasks as T, l3


def cli(argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(str(ALT))["main"](argv)
    return json.loads(output.getvalue())


class ChatCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Direct conversation", "Build the focused change.")
        self.slug = task["slug"]
        self.worktree = self.repo / ".claude" / "worktrees" / self.slug
        self.worktree.mkdir(parents=True)
        task.update({"state": "running", "attempt": 1, "session_id": "session-old", "agent_id": "agent-old",
                     "worktree": str(self.worktree), "branch": f"worktree-{self.slug}"})
        S.save_task(self.project, task)

    def block(self, reason="Need one decision."):
        task = S.load_task(self.project, self.slug)
        task.update({"state": "blocked", "blocked_reason": reason})
        S.save_task(self.project, task)

    def quiet_launch(self):
        """One resume launches nothing real: base check, worktree validation and the holds are stubbed out."""
        self.patch(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40)
        self.patch(dispatch, "_validate_task_worktree")
        self.patch(dispatch, "wip_hold", return_value=None)
        self.quiet_engines()

    def resume(self, seen, worker="agent-new", session="session-new"):
        def fake(name, session_id, prompt, *, cwd, **kw):
            seen.update(name=name, session_id=session_id, prompt=prompt, cwd=str(cwd), env=kw.get("extra_env") or {})
            return {"returncode": 0, "stdout": "", "stderr": "",
                    "agent": {"id": worker, "sessionId": session, "state": "working"}}
        self.quiet_launch()
        self.patch(engines, "claude_resume_bg", side_effect=fake)
        return dispatch.resume(self.project, self.slug)


class TestTaskConversation(ChatCase):
    def test_burak_message_is_durable_and_waits_in_the_inbox(self):
        row = T.message(self.project, self.slug, "burak", "Prefer the smaller diff.")

        history = T.task_messages(self.project, self.slug)
        self.assertEqual([(m["role"], m["text"], m["by"]) for m in history], [("burak", "Prefer the smaller diff.", "burak")])
        self.assertEqual([m["id"] for m in T.pending(self.project, self.slug)], [row["id"]])
        self.assertEqual(S.read_events(self.project, self.slug)[-1]["kind"], "task-message")

    def test_l2_reply_and_task_api_show_both_sides_without_qa_log(self):
        T.message(self.project, self.slug, "burak", "Can we keep this small?")
        T.message(self.project, self.slug, "l2", "Yes. I will keep one focused PR.", by="l2", expected_attempt=1)
        (S.task_dir(self.project, self.slug) / "qa.md").write_text("legacy log dump\n")

        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            view = server.task_view(self.project, self.slug)

        self.assertEqual([m["role"] for m in view["messages"]], ["burak", "l2"])
        self.assertEqual([m["text"] for m in view["messages"]],
                         ["Can we keep this small?", "Yes. I will keep one focused PR."])
        self.assertNotIn("qa", view["files"])
        self.assertEqual([m["role"] for m in T.pending(self.project, self.slug)], ["burak"],
                         "a reply is never queued for the worker itself")

    def test_an_earlier_attempt_cannot_reply_and_a_finished_task_takes_no_message(self):
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "attempt 1 is no longer current"):
            T.message(self.project, self.slug, "l2", "stale reply", by="l2", expected_attempt=1)

        task["state"] = "done"
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "in done state"):
            T.message(self.project, self.slug, "burak", "late steering")
        self.assertEqual(T.task_messages(self.project, self.slug), [])
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_a_message_to_a_blocked_task_resumes_its_session_with_the_message(self):
        self.block()
        T.message(self.project, self.slug, "burak", "Use the existing API.")
        seen = {}

        result = self.resume(seen)

        self.assertEqual(result["agent"]["id"], "agent-new")
        self.assertTrue(seen["prompt"].startswith("Message from Burak ("), seen["prompt"])
        self.assertTrue(seen["prompt"].endswith("\nUse the existing API."), seen["prompt"])
        self.assertEqual((seen["name"], seen["session_id"], seen["cwd"]),
                         (f"{self.project}/{self.slug}-1", "session-old", str(self.worktree)))
        self.assertEqual((seen["env"]["ALTITUDE_ATTEMPT"], seen["env"]["ALTITUDE_SESSION_KEY"]),
                         ("1", f"{self.project}--{self.slug}-1"))
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["agent_id"], task["session_id"], task["attempt"]),
                         ("running", "agent-new", "session-new", 1))
        self.assertIsNone(task["blocked_reason"])
        self.assertEqual(T.pending(self.project, self.slug), [], "delivered messages leave the inbox")
        self.assertEqual([m["text"] for m in T.task_messages(self.project, self.slug)], ["Use the existing API."])

    def worker_env(self):
        return {"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}

    def l3_queue(self):
        p = l3.queue_path(self.project)
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def test_l2_block_goes_to_l3_first_and_a_flagged_one_to_burak(self):
        out = self.alt("--project", self.project, "task", "block", self.slug, "--reason", "Keep the old API?", env=self.worker_env())
        self.assertEqual(out.returncode, 0, out.stderr)
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["waiting_on"]), ("blocked", "l3"))
        self.assertEqual(T.decisions(self.project), [], "a block waiting on L3 is not a card for Burak")
        queued = self.l3_queue()
        self.assertEqual([row["trigger"] for row in queued], ["block"])
        self.assertIn(self.slug, queued[0]["text"]); self.assertIn("Keep the old API?", queued[0]["text"])
        self.assertIn("alt task escalate", queued[0]["text"])

        task.update({"state": "running"}); S.save_task(self.project, task)
        out = self.alt("--project", self.project, "task", "block", self.slug, "--reason", "Which colour?", "--for-burak",
                       env=self.worker_env())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(S.load_task(self.project, self.slug)["waiting_on"], "burak")
        cards = T.decisions(self.project)
        self.assertEqual([c["question"] for c in cards], ["Stopped mid-task: Which colour?"])
        self.assertEqual(len(self.l3_queue()), 1, "a block flagged for Burak does not wake L3")

    def test_l3_answers_a_block_or_escalates_it_as_one_dilemma(self):
        self.block("Keep the old API?")
        task = S.load_task(self.project, self.slug); task["waiting_on"] = "l3"; S.save_task(self.project, task)
        T.escalate(self.project, self.slug, "Keep the old API (recommended) or break it now?")
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["waiting_on"], task["escalated"]), ("burak", True))
        self.assertEqual([c["question"] for c in T.decisions(self.project)],
                         ["L3 asks: Keep the old API (recommended) or break it now?"])
        self.assertEqual(S.read_events(self.project, self.slug)[-1]["kind"], "escalated")
        with self.assertRaises(T.TransitionError):
            T.escalate(self.project, self.slug, "")

        T.message(self.project, self.slug, "l3", "Keep it; the brief says no breaking changes.")
        seen = {}
        self.resume(seen)
        self.assertTrue(seen["prompt"].startswith("Message from L3 ("), seen["prompt"])
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "running")
        for key in ("waiting_on", "escalated", "fault"):
            self.assertNotIn(key, task, f"{key} leaves with the block")
        out = self.alt("--project", self.project, "task", "message", self.slug, "Also keep the tests.", env={"ALTITUDE_ACTOR": "l3"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(T.task_messages(self.project, self.slug)[-1]["role"], "l3")

    def test_resume_without_a_message_continues_from_the_progress_file(self):
        self.block()
        seen = {}
        self.resume(seen)
        self.assertEqual(seen["prompt"], "Continue from your progress file.")

    def test_current_l2_cli_can_reply_but_a_human_shell_cannot_impersonate_it(self):
        self.setenv("ALTITUDE_ACTOR", "l2")
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_TASK", self.slug)
        self.setenv("ALTITUDE_ATTEMPT", "1")
        payload = cli(["task", "reply", "I can implement this directly."])
        self.assertEqual((payload["role"], payload["by"]), ("l2", "l2"))
        self.assertEqual(T.task_messages(self.project, self.slug)[0]["text"], "I can implement this directly.")

        self.setenv("ALTITUDE_ACTOR", "burak")
        for name in ("ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_ATTEMPT"):
            self.setenv(name, None)
        with self.assertRaisesRegex(SystemExit, "only the current L2"):
            cli(["--project", self.project, "task", "reply", "forged"])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 1)

    def test_cli_message_queues_for_a_running_l2_and_resumes_a_blocked_one(self):
        self.setenv("ALTITUDE_ACTOR", "burak")
        with mock.patch.object(dispatch, "resume") as resume:
            payload = cli(["--project", self.project, "task", "message", self.slug, "Prefer one PR."])
            resume.assert_not_called()
            self.block()
            cli(["--project", self.project, "task", "message", self.slug, "Go ahead."])
        self.assertEqual((payload["role"], payload["by"]), ("burak", "burak"))
        resume.assert_called_once_with(self.project, self.slug)
        self.assertEqual([m["text"] for m in T.pending(self.project, self.slug)], ["Prefer one PR.", "Go ahead."])

    def test_task_conversation_corruption_is_not_silently_dropped(self):
        (S.task_dir(self.project, self.slug) / "conversation.jsonl").write_text("{not json\n")
        with self.assertRaisesRegex(ValueError, "corrupt task conversation"):
            T.task_messages(self.project, self.slug)
        (S.task_dir(self.project, self.slug) / "inbox.jsonl").write_text('{"id": "x"}\n')
        with self.assertRaisesRegex(ValueError, "corrupt task inbox"):
            T.pending(self.project, self.slug)


class TestStop(ChatCase):
    def test_stop_blocks_the_task_before_the_worker_goes_away(self):
        with mock.patch.object(engines, "stop_l2_worker", return_value="stopped") as stop:
            task = dispatch.stop(self.project, self.slug)

        stop.assert_called_once_with("claude", "agent-old", job_root=dispatch.l2_job_root(self.project, self.slug))
        self.assertEqual((task["state"], task["blocked_reason"]), ("blocked", "stopped by burak"))
        events = S.read_events(self.project, self.slug)
        self.assertEqual([e["kind"] for e in events[-2:]], ["state", "stopped"])
        self.assertEqual(events[-1]["agent_id"], "agent-old")

    def test_stop_of_a_finished_task_is_refused(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        with mock.patch.object(engines, "stop_l2_worker") as stop:
            with self.assertRaisesRegex(T.TransitionError, "nothing to stop"):
                dispatch.stop(self.project, self.slug)
        stop.assert_not_called()


class TestResumeBinding(ChatCase):
    def test_task_change_during_resume_stops_the_replacement_worker(self):
        self.block()

        def launch(*_args, **_kwargs):
            T.reject(self.project, self.slug, "cancelled while resuming")
            return {"returncode": 0, "stdout": "", "stderr": "",
                    "agent": {"id": "agent-new", "sessionId": "session-new", "state": "working"}}

        self.quiet_launch()
        self.patch(engines, "claude_resume_bg", side_effect=launch)
        self.patch(engines, "claude_rm", return_value="removed")
        stop = self.patch(engines, "claude_stop", return_value="stopped")
        with self.assertRaises(T.TransitionError):
            dispatch.resume(self.project, self.slug)

        self.assertEqual([call.args[0] for call in stop.call_args_list], ["agent-new"])
        self.assertEqual(S.load_task(self.project, self.slug)["state"], "rejected")

    def test_bind_failure_stops_the_unowned_replacement_and_records_the_fault(self):
        self.block()
        launched = {"returncode": 0, "stdout": "", "stderr": "",
                    "agent": {"id": "agent-new", "sessionId": "session-new", "state": "working"}}
        self.quiet_launch()
        self.patch(engines, "claude_resume_bg", return_value=launched)
        stop = self.patch(engines, "claude_stop", return_value="stopped")
        fault = self.patch(incidents, "system_fault")
        with mock.patch.object(S, "save_task", side_effect=OSError("state disk unavailable")):
            with self.assertRaisesRegex(RuntimeError, "state disk unavailable"):
                dispatch.resume(self.project, self.slug)

        self.assertEqual([call.args[0] for call in stop.call_args_list], ["agent-new"])
        current = S.load_task(self.project, self.slug)
        self.assertEqual((current["state"], current["agent_id"], current["session_id"]),
                         ("blocked", "agent-old", "session-old"))
        self.assertEqual(S.read_events(self.project, self.slug)[-1]["kind"], "resume-failed")
        fault.assert_called_once()


if __name__ == "__main__":
    unittest.main()
