"""Decision 36: Altitude's own faults are raised, not papered over. Runs against a throwaway ALTITUDE_HOME."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-faults-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, improve, verify, engines, dispatch, server, tasks as T  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "permission_prompt_fault.py"


class TestSystemFault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})
        os.makedirs(os.path.join(_TMP, "docs"), exist_ok=True)

    def test_fault_files_incident_and_inbox_once_per_kind(self):
        first = improve.system_fault("test-kind", "something broke", project="altitude", task="t1")
        self.assertIsNotNone(first)
        self.assertTrue(first["incident"].startswith("I-"))
        again = improve.system_fault("test-kind", "something broke again", project="altitude")
        self.assertIsNone(again, "same kind within 24h must not file a second incident")
        faults = S.read_json(improve.FAULTS)
        self.assertEqual(faults["test-kind"]["count"], 2)
        self.assertEqual(faults["test-kind"]["incident"], first["incident"])
        inbox = [json.loads(l) for l in (config.project_dir("altitude") / "inbox.jsonl").read_text().splitlines()]
        self.assertEqual(sum("SYSTEM FAULT [test-kind]" in i["text"] for i in inbox), 1)
        other = improve.system_fault("other-kind", "different mechanism")
        self.assertIsNotNone(other)
        self.assertNotEqual(other["incident"], first["incident"])

    def test_corrupt_json_raises_missing_defaults(self):
        p = Path(_TMP) / "corrupt.json"
        p.write_text("{not json")
        with self.assertRaises(ValueError):
            S.read_json(p, {})
        self.assertEqual(S.read_json(Path(_TMP) / "absent.json", {"d": 1}), {"d": 1})

    def test_claude_agents_failure_raises_instead_of_empty_list(self):
        old = config.CLAUDE_BIN
        config.CLAUDE_BIN = "/nonexistent/claude"
        try:
            with self.assertRaises(RuntimeError):
                engines.claude_agents()
            with self.assertRaises(RuntimeError):
                dispatch.poll("altitude")  # must propagate, never report "all L2s gone"
        finally:
            config.CLAUDE_BIN = old

    def test_verifier_tooling_failure_is_a_fault_verdict(self):
        old = verify.gh
        verify.gh = lambda *a, **k: (_ for _ in ()).throw(verify.VerifierFault("gh: network down"))
        try:
            task = T.new("altitude", "verifier fault test", "S", "request", actor="burak")
            task["state"] = "running"; S.save_task("altitude", task)
            d = S.task_dir("altitude", task["slug"])
            S.write_json(d / "report.json", {"landed": {"prs": [{"number": 1, "merged": True}], "main_runs": [], "deploy": "not-applicable"},
                                             "review": [], "deviations": [], "decisions": [], "fyi": [], "blocked": "", "follow_ups": [],
                                             "spend": {"turns": 1, "subagent_launches": 0, "retries": 0, "reverts": 0}})
            v = verify.verify("altitude", task["slug"])
            self.assertEqual(v["verdict"], "fault")
            self.assertIn("verifier fault", v["problems"][0])
            self.assertIn("verifier", S.read_json(improve.FAULTS))
        finally:
            verify.gh = old


class TestPermissionPromptHook(unittest.TestCase):
    """I-064: the passive hook. Exit 0 always, never a permission response, never a retry; one `permission_denials`
    per prompt (the PreToolUse capture and the Notification record share one dedupe key), one bounded fault line."""
    KEY = "demo--task-1"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="altitude-prompt-", dir=_TMP)
        self.mon = Path(self.home) / "monitor"
        self.env = {"ALTITUDE_SESSION_KEY": self.KEY, "ALTITUDE_PROJECT": "demo", "ALTITUDE_TASK": "task-1", "ALTITUDE_ACTOR": "l2"}

    def run_hook(self, payload, env=None):
        e = dict(os.environ, ALTITUDE_HOME=self.home)
        for k in ("ALTITUDE_SESSION_KEY", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_ACTOR"):
            e.pop(k, None)
        e.update(self.env if env is None else env)
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(HOOK)], input=raw, text=True, capture_output=True, env=e)

    def pre(self, command, tool_use_id="toolu_1", sid="s1", env=None):
        return self.run_hook({"session_id": sid, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                              "tool_input": {"command": command}, "tool_use_id": tool_use_id}, env)

    def prompt(self, sid="s1", kind="permission_prompt", tool="Bash", env=None):
        # the 2.1.251 payload: message "Claude needs your permission to use <Tool>", notification_type, title
        return self.run_hook({"session_id": sid, "hook_event_name": "Notification", "title": "Claude Code",
                              "message": f"Claude needs your permission to use {tool}", "notification_type": kind}, env)

    def counts(self, key=None):
        p = self.mon / f"counts-{key or self.KEY}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def fault_lines(self):
        p = self.mon / "hook-faults.log"
        return p.read_text().splitlines() if p.exists() else []

    def records(self):
        return [json.loads(ln.split(" ", 1)[1]) for ln in self.fault_lines()]

    def assert_passive(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")          # no permission decision, no JSON — nothing the harness could read as one
        self.assertEqual(r.stderr, "")

    def test_paired_capture_and_prompt_bill_once(self):
        self.assert_passive(self.pre("gh pr merge 56 --squash --delete-branch"))
        self.assertEqual(self.counts(), {})                        # capture only: nothing counted, nothing raised
        self.assertEqual(self.fault_lines(), [])
        self.assert_passive(self.prompt())
        c = self.counts()
        self.assertEqual(c["permission_denials"], 1)
        self.assertNotIn("subagent_launches", c)
        self.assertNotIn("cap", c)
        self.assert_passive(self.prompt())                         # the same prompt re-delivered: billed once, no retry
        self.assertEqual(self.counts()["permission_denials"], 1)
        lines = self.fault_lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("permission_prompt_fault.py {"))
        rec = self.records()[0]
        self.assertEqual((rec["project"], rec["task"], rec["actor"]), ("demo", "task-1", "l2"))
        self.assertEqual(rec["command"], "gh pr merge 56 --squash --delete-branch")
        self.assertEqual((rec["notification_type"], rec["tool"], rec["tool_use_id"]), ("permission_prompt", "Bash", "toolu_1"))

    def test_a_new_tool_call_is_a_new_prompt(self):
        self.pre("gh pr merge 56", "toolu_1"); self.prompt()
        self.pre("gh pr merge 56", "toolu_2"); self.prompt()
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual(len(self.fault_lines()), 2)

    def test_other_notifications_are_ignored(self):
        self.pre("ls")
        for kind in ("idle_prompt", "auth_success", "elicitation_dialog", "agent_completed", "push_notification"):
            self.assert_passive(self.prompt(kind=kind))
        self.assert_passive(self.run_hook({"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                                           "tool_input": {"command": "ls"}, "tool_response": {}}))
        self.assertEqual(self.counts(), {})
        self.assertEqual(self.fault_lines(), [])

    def test_worker_prompt_counts_too(self):
        self.pre("gh pr merge 56"); self.assert_passive(self.prompt(kind="worker_permission_prompt"))
        self.assertEqual(self.counts()["permission_denials"], 1)
        self.assertEqual(self.records()[0]["notification_type"], "worker_permission_prompt")

    def test_prompt_without_a_capture_still_counts(self):
        self.assert_passive(self.prompt(tool="WebFetch"))
        self.assertEqual(self.counts()["permission_denials"], 1)
        rec = self.records()[0]
        self.assertIsNone(rec["command"])
        self.assertEqual(rec["tool"], "WebFetch")

    def test_prompt_for_another_tool_does_not_borrow_the_bash_capture(self):
        self.pre("gh pr merge 56"); self.prompt()
        self.assert_passive(self.prompt(tool="Edit"))
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual([r["command"] for r in self.records()], ["gh pr merge 56", None])

    def test_existing_counts_survive(self):
        self.mon.mkdir(parents=True, exist_ok=True)
        (self.mon / f"counts-{self.KEY}.json").write_text(json.dumps(
            {"subagent_launches": 2, "cap": 3, "seen": ["t1"], "edits": 4, "files": ["a.py"]}))
        self.pre("gh pr merge 56"); self.prompt()
        c = self.counts()
        self.assertEqual((c["subagent_launches"], c["cap"], c["seen"], c["edits"], c["files"], c["permission_denials"]),
                         (2, 3, ["t1"], 4, ["a.py"], 1))

    def test_resume_continues_the_keyed_count(self):
        self.pre("gh pr merge 56", "t1", sid="old"); self.prompt(sid="old")
        self.pre("gh pr merge 56", "t2", sid="new"); self.prompt(sid="new")
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertFalse((self.mon / "counts-old.json").exists())

    def test_without_a_key_counts_are_per_session(self):
        env = {"ALTITUDE_PROJECT": "demo", "ALTITUDE_ACTOR": "l1"}
        self.pre("gh pr merge 56", sid="sa", env=env); self.prompt(sid="sa", env=env)
        self.assertEqual(self.counts("sa")["permission_denials"], 1)
        rec = self.records()[0]
        self.assertEqual((rec["project"], rec["task"], rec["actor"]), ("demo", None, "l1"))

    def test_fault_line_is_one_bounded_line(self):
        self.pre("echo " + "x" * 5000 + "\nsecond line"); self.prompt()
        lines = self.fault_lines()
        self.assertEqual(len(lines), 1)
        self.assertLess(len(lines[0]), 1200)
        self.assertLessEqual(len(self.records()[0]["command"]), 300)

    def test_broken_input_is_still_exit_zero_and_silent(self):
        for raw in ("not json", "", "[1,2]", "null", "{}"):
            r = self.run_hook(raw)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)
        self.assertEqual(self.counts(), {})
        self.assertEqual(self.fault_lines(), [])

    def test_unwritable_home_is_still_exit_zero(self):
        r = self.prompt(env={**self.env, "ALTITUDE_HOME": "/proc/altitude-cannot-exist"})
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class TestDrainPermissionPrompts(unittest.TestCase):
    """B2: the tick raises permission-prompt lines once per distinct project/task/actor/command, with a task event; the
    plain lines from the other hooks keep their path."""

    def setUp(self):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})
        self.log = config.MONITOR_DIR / "hook-faults.log"

    @staticmethod
    def line(**rec):
        base = {"project": "altitude", "task": None, "actor": "l2", "command": "gh pr merge 56", "tool": "Bash",
                "message": "Claude needs your permission to use Bash", "session": "s1", "key": "k",
                "notification_type": "permission_prompt", "tool_use_id": "t1"}
        return "permission_prompt_fault.py " + json.dumps({**base, **rec}, sort_keys=True)

    def test_repeated_tuple_is_one_fault_and_one_task_event(self):
        t = T.new("altitude", "prompted task", "S", "req")
        plain = "subagent_cap.py session=s1 key=k: counts file lock unavailable, proceeding unlocked: boom"
        lines = [self.line(task=t["slug"])] * 3 + [self.line(task=t["slug"], command="git reset --hard origin/main"), plain]
        self.log.write_text("\n".join(lines) + "\n")
        calls = []

        def fake_fault(kind, detail, project=None, task=None):
            calls.append((kind, detail, project, task))
            return {"kind": kind, "incident": "I-999", "count": 1}

        with mock.patch.object(improve, "system_fault", side_effect=fake_fault):
            server.drain_hook_faults()
        self.assertFalse(self.log.exists())
        self.assertEqual([c[0] for c in calls], ["hook", "permission-prompt", "permission-prompt"])
        self.assertEqual(calls[0], ("hook", plain, None, None))                # the other hooks' lines: exactly as before
        merge = next(c for c in calls if "gh pr merge 56" in c[1])
        self.assertEqual((merge[2], merge[3]), ("altitude", t["slug"]))
        self.assertIn("3×", merge[1])
        self.assertIn(t["slug"], merge[1])
        events = [e for e in S.read_events("altitude", t["slug"]) if e["kind"] == "permission-prompt"]
        self.assertEqual(len(events), 2)
        by_cmd = {e["command"]: e for e in events}
        self.assertEqual(set(by_cmd), {"gh pr merge 56", "git reset --hard origin/main"})
        self.assertEqual(by_cmd["gh pr merge 56"]["occurrences"], 3)
        self.assertEqual(by_cmd["gh pr merge 56"]["incident"], "I-999")
        self.assertEqual(by_cmd["gh pr merge 56"]["actor"], "l2")

    def test_a_line_naming_no_known_task_raises_the_fault_without_an_event(self):
        self.log.write_text(self.line(task="no-such-task", actor="l1") + "\n")
        calls = []
        with mock.patch.object(improve, "system_fault", side_effect=lambda k, d, **kw: calls.append((k, d, kw)) or None):
            server.drain_hook_faults()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "permission-prompt")
        self.assertEqual(calls[0][2], {"project": "altitude", "task": "no-such-task"})
        self.assertFalse(S.task_dir("altitude", "no-such-task").exists())

    def test_the_prompt_hooks_own_failure_lines_keep_the_old_path(self):
        own = "permission_prompt_fault.py session=s1 key=k: counts file lock unavailable, proceeding unlocked: boom"
        self.log.write_text(own + "\n")
        calls = []
        with mock.patch.object(improve, "system_fault", side_effect=lambda k, d, **kw: calls.append((k, d)) or None):
            server.drain_hook_faults()
        self.assertEqual(calls, [("hook", own)])

    def test_real_system_fault_files_one_incident_and_the_event_cites_it(self):
        t = T.new("altitude", "really prompted task", "S", "req")
        self.log.write_text("\n".join([self.line(task=t["slug"])] * 2) + "\n")
        server.drain_hook_faults()
        rec = S.read_json(improve.FAULTS)["permission-prompt"]
        self.assertEqual(rec["count"], 1)                                      # two lines, one distinct prompt, one count
        self.assertTrue(rec["incident"].startswith("I-"))
        events = [e for e in S.read_events("altitude", t["slug"]) if e["kind"] == "permission-prompt"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["incident"], rec["incident"])
        self.assertEqual(events[0]["occurrences"], 2)


if __name__ == "__main__":
    unittest.main()
