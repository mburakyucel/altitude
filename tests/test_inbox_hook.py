"""The inbox hook hands Burak's queued messages to a running Claude L2 at its next checkpoint."""
import json
import os
import subprocess
import sys
import unittest

from tests.support import REPO, AltitudeCase
from altitude import config, dispatch, state as S, tasks as T

HOOK = REPO / "hooks" / "inbox.py"


class InboxHook(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Hooked task", "request")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "session_id": "sid", "agent_id": "aid"})
        S.save_task(self.project, task)

    def run_hook(self, event, project=None, slug=None, **inputs):
        env = dict(os.environ, ALTITUDE_HOME=str(config.ROOT), ALTITUDE_PROJECT=project or self.project,
                   ALTITUDE_TASK=slug or self.slug)
        payload = {"hook_event_name": event, "session_id": "sid",
                   **({"tool_name": "Bash"} if event == "PostToolUse" else {"stop_hook_active": False}),
                   **inputs}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env, timeout=60)

    def test_after_a_tool_call_the_messages_arrive_as_context_and_leave_the_inbox(self):
        T.message(self.project, self.slug, "burak", "Prefer the smaller diff.")
        T.message(self.project, self.slug, "burak", "And add a test.")

        done = self.run_hook("PostToolUse")

        self.assertEqual(done.returncode, 0, done.stderr)
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("Message from Operator ("), context)
        self.assertLess(context.index("Prefer the smaller diff."), context.index("And add a test."))
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 2, "the conversation keeps them")

    def test_a_helper_subagent_tool_call_leaves_messages_for_the_owner(self):
        T.message(self.project, self.slug, "l3", "Only the owner should read this.")

        helper = self.run_hook("PostToolUse", agent_id="helper-1", agent_type="Explore")
        owner = self.run_hook("PostToolUse")

        self.assertEqual((helper.returncode, helper.stdout), (0, ""))
        context = json.loads(owner.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context.split("\n", 1)[1], "Only the owner should read this.")
        self.assertTrue(context.startswith("Message from L3 (message id "), context)

    def test_when_the_worker_is_about_to_stop_the_messages_keep_it_going(self):
        T.message(self.project, self.slug, "burak", "One more thing before you finish.")

        done = self.run_hook("Stop")

        out = json.loads(done.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("One more thing before you finish.", out["reason"])
        self.assertEqual(T.pending(self.project, self.slug), [])

    def test_required_background_validation_survives_premature_clean_completion(self):
        # The provider boundary is scripted; the hook, task storage and validation process are real.
        # Without the Stop guard the scripted provider cleans up its still-running child.
        settings = S.read_json(dispatch.session_settings(self.project, self.slug, "fixture-key"))
        self.assertIn(str(HOOK), settings["hooks"]["Stop"][0]["hooks"][0]["command"])
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                child = subprocess.Popen([sys.executable, "-u", "-c",
                    "import sys; print('validation started'); sys.stdin.readline(); "
                    f"print('validation result'); sys.exit({exit_code})"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), "validation started")
                    done = self.run_hook("Stop", background_tasks=[
                        {"id": "required-check", "type": "shell", "status": "running"}])
                    decision = json.loads(done.stdout) if done.stdout else {}
                    if decision.get("decision") != "block":
                        child.kill()  # Native print-session cleanup on premature final response.
                    self.assertEqual(decision.get("decision"), "block", done.stderr)
                    self.assertIn("required-check", decision["reason"])
                    self.assertIn("wait/result", decision["reason"])
                    self.assertIsNone(child.poll())
                    # The owner consumes the result and exit status, including a failed check.
                    stdout, stderr = child.communicate("finish\n", timeout=5)
                    self.assertEqual((stdout.strip(), stderr, child.returncode),
                                     ("validation result", "", exit_code))
                    done = self.run_hook("Stop", stop_hook_active=True, background_tasks=[])
                    self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
                    self.assertEqual(S.load_task(self.project, self.slug)["state"], "running")
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)

    def test_background_guard_keeps_steering_and_repeated_waits_without_accepting_a_report(self):
        T.message(self.project, self.slug, "burak", "Keep the merge held.")
        (S.task_dir(self.project, self.slug) / "report.json").write_text('{"blocked":""}')
        tasks = [{"id": "check", "type": "shell", "status": "running"},
                 {"id": "review", "type": "subagent", "status": "pending"},
                 {"id": "finished", "type": "shell", "status": "completed"}]
        for active in (False, True):
            with self.subTest(stop_hook_active=active):
                out = json.loads(self.run_hook("Stop", stop_hook_active=active, background_tasks=tasks).stdout)
                self.assertEqual(out["decision"], "block")
                self.assertIn("check, review", out["reason"])
                self.assertNotIn("finished", out["reason"].split(". Use native")[0])
                self.assertEqual("Keep the merge held." in out["reason"], not active)
                self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(self.run_hook("PostToolUse", background_tasks=tasks).stdout, "")
        self.assertEqual(self.run_hook("Stop", background_tasks=tasks[-1:]).stdout, "")

    def test_explicit_question_fault_and_stop_allow_exit_with_background_work(self):
        tasks = [{"id": "check", "type": "shell", "status": "running"}]
        for reason, actor, updates in (("Which scope?", "l2", {"waiting_on": "l3"}),
                                       ("Validation unavailable", "l2", {"fault": "worker-fault"}),
                                       ("Operator stop", "burak", {})):
            with self.subTest(reason=reason):
                T.block(self.project, self.slug, reason, actor=actor, updates=updates)
                done = self.run_hook("Stop", background_tasks=tasks)
                self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
                self.assertEqual(S.load_task(self.project, self.slug)["blocked_reason"], reason)
                T.resume(self.project, self.slug)
        # A supported resume restores the same protection without rotating the session.
        self.assertEqual(json.loads(self.run_hook("Stop", background_tasks=tasks).stdout)["decision"], "block")

    def test_quick_acceptance_reaches_the_running_owner_once_through_the_existing_hook(self):
        question = T.block(self.project, self.slug, "How long?", actor="l2",
                           updates={"waiting_on": "burak"}, recommendation="Keep fourteen days.")["questions"][-1]
        T.resume(self.project, self.slug)
        receipt = T.accept_question(self.project, self.slug, question["id"], question["revision"])
        done = self.run_hook("PostToolUse")
        self.assertEqual(done.returncode, 0, done.stderr)
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Keep fourteen days.", context)
        self.assertIn(receipt["response"]["message_id"], context)
        T.accept_question(self.project, self.slug, question["id"], question["revision"])
        self.assertEqual(self.run_hook("Stop").stdout, "")

    def test_silent_when_nothing_waits_or_the_task_is_not_running(self):
        self.assertEqual((self.run_hook("PostToolUse").returncode, self.run_hook("Stop").stdout), (0, ""))

        task = S.load_task(self.project, self.slug)
        task.update({"state": "blocked", "blocked_reason": "Which colour?"})
        S.save_task(self.project, task)
        T.message(self.project, self.slug, "burak", "Blue.")
        done = self.run_hook("Stop")

        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual([m["text"] for m in T.pending(self.project, self.slug)], ["Blue."],
                         "a blocked task keeps its messages for the resume that answers them")
        self.assertEqual(self.run_hook("PostToolUse", project="nope", slug="none").returncode, 0)

    def test_a_corrupt_inbox_is_a_fault_line_not_a_crash(self):
        (S.task_dir(self.project, self.slug) / "inbox.jsonl").write_text("{not json\n")

        done = self.run_hook("PostToolUse")

        self.assertEqual((done.returncode, done.stdout), (0, ""))
        faults = (config.MONITOR_DIR / "hook-faults.log").read_text()
        self.assertIn(f"inbox.py {self.project}/{self.slug}: corrupt task inbox", faults)


if __name__ == "__main__":
    unittest.main()
