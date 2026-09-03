"""The inbox hook hands Burak's queued messages to a running Claude L2 at its next checkpoint."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-inbox-hook-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "inbox.py"


class InboxHook(unittest.TestCase):
    _number = 0

    @classmethod
    def setUpClass(cls):
        config.ensure_root()

    def setUp(self):
        InboxHook._number += 1
        self.project = f"inbox-hook-{self._number}"
        repo = Path(config.ROOT) / "repos" / self.project
        repo.mkdir(parents=True)
        projects = config.load_projects()
        projects[self.project] = {"name": self.project, "path": str(repo)}
        config.save_projects(projects)
        task = T.new(self.project, "Hooked task", "request")
        self.slug = task["slug"]
        task.update({"state": "running", "attempt": 1, "session_id": "sid", "agent_id": "aid"})
        S.save_task(self.project, task)

    def run_hook(self, event, project=None, slug=None):
        env = dict(os.environ, ALTITUDE_HOME=str(config.ROOT), ALTITUDE_PROJECT=project or self.project,
                   ALTITUDE_TASK=slug or self.slug)
        payload = {"hook_event_name": event, "session_id": "sid",
                   **({"tool_name": "Bash"} if event == "PostToolUse" else {"stop_hook_active": False})}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env, timeout=60)

    def test_after_a_tool_call_the_messages_arrive_as_context_and_leave_the_inbox(self):
        T.message(self.project, self.slug, "burak", "Prefer the smaller diff.")
        T.message(self.project, self.slug, "burak", "And add a test.")

        done = self.run_hook("PostToolUse")

        self.assertEqual(done.returncode, 0, done.stderr)
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("Message from Burak ("), context)
        self.assertLess(context.index("Prefer the smaller diff."), context.index("And add a test."))
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(len(T.task_messages(self.project, self.slug)), 2, "the conversation keeps them")

    def test_when_the_worker_is_about_to_stop_the_messages_keep_it_going(self):
        T.message(self.project, self.slug, "burak", "One more thing before you finish.")

        done = self.run_hook("Stop")

        out = json.loads(done.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("One more thing before you finish.", out["reason"])
        self.assertEqual(T.pending(self.project, self.slug), [])

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
