"""Decision 36: Altitude's own faults are raised, not papered over. Runs against a throwaway ALTITUDE_HOME."""
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-faults-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, improve, verify, engines, dispatch, server, tasks as T  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "permission_prompt_fault.py"
_CAP = re.search(r"^PENDING_CAP = (\d+)", HOOK.read_text(), re.M)
PENDING_CAP = int(_CAP.group(1)) if _CAP else None


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

    def post(self, command, tool_use_id="toolu_1", sid="s1", env=None):
        return self.run_hook({"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                              "tool_input": {"command": command}, "tool_use_id": tool_use_id, "tool_response": {}}, env)

    def prompt(self, sid="s1", kind="permission_prompt", tool="Bash", env=None):
        # the 2.1.251 payload: message "Claude needs your permission to use <Tool>", notification_type, title
        return self.run_hook({"session_id": sid, "hook_event_name": "Notification", "title": "Claude Code",
                              "message": f"Claude needs your permission to use {tool}", "notification_type": kind}, env)

    def counts(self, key=None):
        p = self.mon / f"counts-{key or self.KEY}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def pending(self, key=None):
        p = self.mon / f"pending-bash-{key or self.KEY}.json"
        return json.loads(p.read_text())["entries"] if p.exists() else []

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
        """A second call parking on an identical payload is a new prompt — but opened *after* the first billing it is
        indistinguishable from that prompt re-delivered, and C3 resolves the tie toward no false fault: its first
        delivery is absorbed, its next delivery bills it. (Two calls parked side by side keep billing on their own
        deliveries — see test_two_parked_calls_are_two_faults — and the drain collapses same-command lines into one
        system fault anyway, so nothing real is lost by the absorbed delivery.)"""
        self.pre("gh pr merge 56", "toolu_1"); self.prompt()
        self.pre("gh pr merge 56", "toolu_2"); self.prompt()          # could be toolu_1's prompt delivered again (C3)
        self.assertEqual(self.counts()["permission_denials"], 1)
        self.prompt()
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual(len(self.fault_lines()), 2)
        self.assertEqual([r["tool_use_id"] for r in self.records()], ["toolu_1", "toolu_2"])

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

    def test_two_parked_calls_are_two_faults(self):
        """F6: parallel Bash calls used to overwrite one capture, so the second prompt borrowed the first's tool_use_id and
        was dropped as a duplicate — a genuine fault suppressed (decision 36). Captures are pending per tool_use_id and
        each is consumed at most once."""
        self.assert_passive(self.pre("gh pr merge 56 --squash", "toolu_a"))
        self.assert_passive(self.pre("git reset --hard origin/main", "toolu_b"))
        self.assertEqual([e["tool_use_id"] for e in self.pending()], ["toolu_a", "toolu_b"])
        self.assert_passive(self.prompt())
        self.assert_passive(self.prompt())
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual({(r["tool_use_id"], r["command"]) for r in self.records()},
                         {("toolu_a", "gh pr merge 56 --squash"), ("toolu_b", "git reset --hard origin/main")})
        self.assertTrue(all(e["consumed"] for e in self.pending()))
        self.assert_passive(self.prompt())                         # nothing left to consume: the last prompt again, billed once
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual(len(self.fault_lines()), 2)
        c = self.counts()
        self.assertNotIn("subagent_launches", c)
        self.assertNotIn("cap", c)

    def test_a_redelivered_notification_does_not_consume_another_open_capture(self):
        """C3 (the second review of PR #89): prompt A billed, capture B opens, A's notification delivered again — the
        duplicate used to consume B and bill it as a distinct fault for a command that may then run fine. Now the
        duplicate is absorbed (no consume, no count, B stays open) and B's own notification bills B."""
        self.assert_passive(self.pre("gh pr merge 56 --squash", "toolu_a"))
        self.assert_passive(self.prompt())                            # A's prompt: billed with A's command
        self.assertEqual(self.counts()["permission_denials"], 1)
        self.assert_passive(self.pre("git reset --hard origin/main", "toolu_b"))
        self.assert_passive(self.prompt())                            # A's prompt delivered again, while B is open
        self.assertEqual(self.counts()["permission_denials"], 1)      # not billed again
        self.assertEqual(len(self.fault_lines()), 1)
        self.assertEqual([e["tool_use_id"] for e in self.pending() if not e["consumed"]], ["toolu_b"])   # B left open
        self.assert_passive(self.prompt())                            # B's own prompt: a genuinely new fault
        self.assertEqual(self.counts()["permission_denials"], 2)
        self.assertEqual([(r["tool_use_id"], r["command"]) for r in self.records()],
                         [("toolu_a", "gh pr merge 56 --squash"), ("toolu_b", "git reset --hard origin/main")])
        self.assertTrue(all(e["consumed"] for e in self.pending()))

    def test_a_redelivered_notification_then_a_release_leaves_no_false_fault(self):
        """C3, the run-fine tail: the duplicate absorbed, B's call then runs — the release removes B and no fault for
        B ever exists; one more duplicate after that is keyed to the last consumed capture and billed once."""
        self.pre("gh pr merge 56", "toolu_a"); self.prompt()
        self.pre("ls", "toolu_b")
        self.assert_passive(self.prompt())                            # duplicate: absorbed, B untouched
        self.assert_passive(self.post("ls", "toolu_b"))               # B ran fine
        self.assert_passive(self.prompt())                            # still the same prompt delivered again
        self.assertEqual(self.counts()["permission_denials"], 1)
        self.assertEqual([r["tool_use_id"] for r in self.records()], ["toolu_a"])

    def test_fault_line_append_waits_for_the_log_lock(self):
        """C4: the hook appends its fault line under monitor/hook-faults.log.lock — the lock the drain's rotate holds —
        so a line can never land in an inode the drain has already read. With the lock held elsewhere the hook blocks
        at the append (counts already billed, all other locks released) instead of writing unlocked."""
        self.pre("gh pr merge 56")
        self.mon.mkdir(parents=True, exist_ok=True)
        lock_f = open(self.mon / "hook-faults.log.lock", "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            e = dict(os.environ, ALTITUDE_HOME=self.home, **self.env)
            p = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=e)
            p.stdin.write(json.dumps({"session_id": "s1", "hook_event_name": "Notification", "title": "Claude Code",
                                      "message": "Claude needs your permission to use Bash",
                                      "notification_type": "permission_prompt"}))
            p.stdin.close()
            try:
                p.wait(timeout=1.0)
                self.fail("the hook must wait for hook-faults.log.lock before appending its fault line")
            except subprocess.TimeoutExpired:
                pass
            self.assertEqual(self.fault_lines(), [])                  # nothing appended while the lock is held
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
            lock_f.close()
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertEqual((p.stdout.read(), p.stderr.read()), ("", ""))
        self.assertEqual(len(self.fault_lines()), 1)                  # the line lands once the lock is free
        self.assertEqual(self.records()[0]["command"], "gh pr merge 56")

    def test_a_call_that_ran_is_released_and_never_billed(self):
        self.assert_passive(self.pre("ls", "toolu_ok"))
        self.assert_passive(self.post("ls", "toolu_ok"))            # PostToolUse: the call ran, so it was never parked
        self.assertEqual(self.pending(), [])
        self.pre("gh pr merge 56", "toolu_p"); self.prompt(); self.prompt()
        self.assertEqual(self.counts()["permission_denials"], 1)
        self.assertEqual([r["tool_use_id"] for r in self.records()], ["toolu_p"])
        self.assert_passive(self.post("never captured", "toolu_x"))   # a release with no matching capture is a no-op
        self.assertEqual([e["tool_use_id"] for e in self.pending()], ["toolu_p"])
        self.assertEqual(self.fault_lines()[1:], [])                # and nothing the release did was a fault

    def test_pending_captures_are_bounded_oldest_first(self):
        self.assertIsNotNone(PENDING_CAP, "the hook declares its bound as PENDING_CAP")
        for i in range(PENDING_CAP + 3):
            self.pre(f"echo {i}", f"toolu_{i}")
        ids = [e["tool_use_id"] for e in self.pending()]
        self.assertEqual(len(ids), PENDING_CAP)
        self.assertEqual(ids, [f"toolu_{i}" for i in range(3, PENDING_CAP + 3)])
        self.prompt()
        self.assertEqual(self.records()[0]["tool_use_id"], f"toolu_{PENDING_CAP + 2}")   # the newest is the one that parked


class TestPromptThroughBothSettingsPaths(unittest.TestCase):
    """F2: the hook rides on every Claude launch — the per-repository file every launch without a per-dispatch file gets
    (the L3, where I-064 was observed; L1s, proposals, critic, sizer) and the L2's per-dispatch file. A residual prompt
    through either path ends as a `permission-prompt` system fault; the first path carries no task, actor or session key,
    and both the hook and the drain cope with the absent fields."""

    def setUp(self):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})
        self.log = config.MONITOR_DIR / "hook-faults.log"
        if self.log.exists():
            self.log.unlink()
        for d in config.MONITOR_DIR.glob("hook-faults.log.*.drain"):
            d.unlink()
        self.scratch_home = tempfile.mkdtemp(prefix="altitude-home-", dir=_TMP)   # a hook without ALTITUDE_HOME would write here

    def run_wired(self, settings, event, payload):
        """Run the prompt-fault command the settings file wires for `event`, with the file's own env and nothing else Altitude-specific."""
        entries = [e for e in settings["hooks"].get(event, []) if any(h["command"].endswith("permission_prompt_fault.py") for h in e["hooks"])]
        self.assertEqual(len(entries), 1, f"{event}: exactly one prompt-fault entry")
        subject = payload.get("tool_name") or payload.get("notification_type")
        self.assertTrue(re.fullmatch(entries[0]["matcher"], subject), (event, entries[0]["matcher"], subject))
        env = {k: v for k, v in os.environ.items() if not k.startswith("ALTITUDE")}
        env["HOME"] = self.scratch_home
        env.update(settings.get("env") or {})
        for h in entries[0]["hooks"]:
            r = subprocess.run(shlex.split(h["command"]), input=json.dumps(payload), text=True, capture_output=True, env=env)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_a_residual_prompt_is_a_fault_through_both_paths(self):
        t = T.new("altitude", "both paths", "S", "req")
        slug = t["slug"]
        key = f"altitude--{slug}-1"
        global_st = json.loads(engines.claude_settings().read_text())
        l2_st = json.loads(dispatch.session_settings("altitude", slug, key).read_text())
        for st, sid in ((global_st, "sid-global"), (l2_st, "sid-l2")):
            self.run_wired(st, "PreToolUse", {"session_id": sid, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                              "tool_input": {"command": "gh pr merge 56 --squash"}, "tool_use_id": f"toolu-{sid}"})
            self.run_wired(st, "Notification", {"session_id": sid, "hook_event_name": "Notification", "title": "Claude Code",
                                                "message": "Claude needs your permission to use Bash", "notification_type": "permission_prompt"})
        self.assertEqual(os.listdir(self.scratch_home), [])                       # every line landed under config.ROOT
        self.assertEqual(json.loads((config.MONITOR_DIR / "counts-sid-global.json").read_text())["permission_denials"], 1)
        self.assertEqual(json.loads((config.MONITOR_DIR / f"counts-{key}.json").read_text())["permission_denials"], 1)
        recs = [json.loads(ln.split(" ", 1)[1]) for ln in self.log.read_text().splitlines()]
        self.assertEqual([(r["project"], r["task"], r["actor"], r["key"], r["command"]) for r in recs],
                         [(None, None, None, "sid-global", "gh pr merge 56 --squash"), ("altitude", slug, "l2", key, "gh pr merge 56 --squash")])
        calls = []
        with mock.patch.object(improve, "system_fault", side_effect=lambda k, d, **kw: calls.append((k, d, kw)) or {"incident": "I-1", "count": 1}):
            server.drain_hook_faults()
        self.assertEqual([c[0] for c in calls], ["permission-prompt", "permission-prompt"])
        self.assertEqual(calls[0][2], {"project": None, "task": None})           # the global path: no task, no actor
        self.assertIn("a claude session of ? parked on a permission prompt for `gh pr merge 56 --squash`", calls[0][1])
        self.assertEqual(calls[1][2], {"project": "altitude", "task": slug})
        self.assertIn(f"a l2 session of altitude/{slug}", calls[1][1])
        events = [e for e in S.read_events("altitude", slug) if e["kind"] == "permission-prompt"]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["actor"], events[0]["incident"]), ("l2", "I-1"))
        self.assertFalse(self.log.exists())


class TestDrainPermissionPrompts(unittest.TestCase):
    """B2: the tick raises permission-prompt lines once per distinct project/task/actor/command, with a task event; the
    plain lines from the other hooks keep their path."""

    def setUp(self):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})
        self.log = config.MONITOR_DIR / "hook-faults.log"
        if self.log.exists():
            self.log.unlink()
        for d in config.MONITOR_DIR.glob("hook-faults.log.*.drain"):
            d.unlink()

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

    def test_an_append_racing_the_drain_is_kept_for_the_next_tick(self):
        """F7: read-then-unlink lost a line a hook appended in between. The log is rotated with os.replace() before it is
        read, so the append lands in a fresh log the next tick raises."""
        self.log.write_text(self.line(command="first") + "\n")
        raced = self.line(command="second")
        real = Path.read_text
        state = {"raced": False}

        def read_text(p, *a, **kw):
            out = real(p, *a, **kw)
            if not state["raced"] and p.name.startswith("hook-faults"):
                state["raced"] = True
                with open(self.log, "a") as f:                         # a hook appends while the tick is reading
                    f.write(raced + "\n")
            return out

        calls = []
        fake = lambda k, d, **kw: calls.append(d) or None   # noqa: E731
        with mock.patch.object(Path, "read_text", read_text), mock.patch.object(improve, "system_fault", side_effect=fake):
            server.drain_hook_faults()
        self.assertTrue(state["raced"])
        self.assertEqual(len(calls), 1)
        self.assertIn("`first`", calls[0])
        self.assertTrue(self.log.exists(), "the raced append must survive in a fresh log")
        with mock.patch.object(improve, "system_fault", side_effect=fake):
            server.drain_hook_faults()
        self.assertEqual(len(calls), 2)
        self.assertIn("`second`", calls[1])
        self.assertFalse(self.log.exists())
        self.assertEqual(list(config.MONITOR_DIR.glob("hook-faults.log.*.drain")), [])

    def test_a_locked_appender_serializes_with_the_rotate_and_loses_no_line(self):
        """C4: os.replace() alone still lost the line of an appender that opened the log before the rename and wrote
        after the drain had read — the write landed in the renamed inode post-read. The rotate now holds
        monitor/hook-faults.log.lock, the lock hooks/permission_prompt_fault.py holds across its appends: the drain
        waits, the line lands before the rename, and both lines are raised. (edit_count.py and subagent_cap.py do not
        take this lock yet — lease boundary — so only permission-prompt lines get this guarantee.)"""
        self.log.write_text(self.line(command="first") + "\n")
        lock_f = open(config.MONITOR_DIR / "hook-faults.log.lock", "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX)                        # the appender's lock, held across open+write
        appender = open(self.log, "a")                            # opened before the rotate — the racing inode
        calls = []
        with mock.patch.object(improve, "system_fault", side_effect=lambda k, d, **kw: calls.append(d) or None):
            th = threading.Thread(target=server.drain_hook_faults)
            th.start()
            th.join(0.5)
            self.assertTrue(th.is_alive(), "the rotate must wait for the appender's hook-faults.log.lock")
            self.assertEqual(calls, [])                           # and nothing was read around the lock
            appender.write(self.line(command="second") + "\n")
            appender.close()
            fcntl.flock(lock_f, fcntl.LOCK_UN)
            lock_f.close()
            th.join(10)
            self.assertFalse(th.is_alive())
        self.assertEqual(len(calls), 2)                           # both lines raised: the appended one was not lost
        self.assertTrue(any("`first`" in c for c in calls))
        self.assertTrue(any("`second`" in c for c in calls))
        self.assertFalse(self.log.exists())
        self.assertEqual(list(config.MONITOR_DIR.glob("hook-faults.log.*.drain")), [])

    def test_a_drain_left_by_a_dead_tick_is_raised_next_time(self):
        left = config.MONITOR_DIR / "hook-faults.log.123-456.drain"
        left.write_text(self.line(command="orphaned") + "\n")
        calls = []
        with mock.patch.object(improve, "system_fault", side_effect=lambda k, d, **kw: calls.append(d) or None):
            server.drain_hook_faults()
        self.assertEqual(len(calls), 1)
        self.assertIn("`orphaned`", calls[0])
        self.assertFalse(left.exists())


if __name__ == "__main__":
    unittest.main()
