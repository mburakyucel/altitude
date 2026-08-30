"""The launch envelope is billed by authoritative `l1-started` events, never by attempts (decisions 31/36/45).

Every engine-failure case here drives the real launcher against a real fake binary — a missing one, a
non-executable one, a wrapper that exits 127 — rather than asserting about a hand-written event log, because the
defect these cover was precisely that the old code billed the wrapper and never looked at the engine.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "subagent_cap.py"
EDIT_HOOK = ROOT / "hooks" / "edit_count.py"

_TMP = Path(tempfile.mkdtemp(prefix="altitude-started-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
FAKE = _TMP / "bin"
FAKE.mkdir()
(FAKE / "codex").write_text('''#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
open(args[args.index("-o") + 1], "w").write("RESULT: no PR — fake codex ran")
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}}))
''')
(FAKE / "claude").write_text('''#!/usr/bin/env python3
import sys, json
print(json.dumps({"type": "result", "subtype": "success", "result": "RESULT: no PR — fake claude ran",
                  "session_id": "fake-sid", "is_error": False}))
''')
(FAKE / "exit127").write_text("#!/bin/sh\nexit 127\n")
(FAKE / "not-executable").write_text("#!/bin/sh\nexit 0\n")  # deliberately left without the executable bit
(FAKE / "invalid-format").write_text("this is executable but has no executable format\n")
(FAKE / "quota-refusal").write_text('''#!/usr/bin/env python3
import json, sys
message = "You've hit your session limit · resets 8pm (America/Los_Angeles)"
print(json.dumps({"type": "assistant", "message": {"model": "<synthetic>",
      "content": [{"type": "text", "text": message}]},
      "quotaLimits": {"status": "rejected", "resetsAt": 4102444800}}))
print(json.dumps({"type": "result", "is_error": True, "result": message}))
print("synthetic quota rejection from Claude CLI", file=sys.stderr)
''')
for f in ("codex", "claude", "exit127", "invalid-format", "quota-refusal"):
    (FAKE / f).chmod((FAKE / f).stat().st_mode | stat.S_IEXEC)
sys.path.insert(0, str(ROOT))
from hooks import launch_counter as LC  # noqa: E402  — the same module object `altitude.l1` imports
from altitude import config, l1, monitor, route, state as S, tasks as T  # noqa: E402

REPO = _TMP / "repo"
MISSING = str(FAKE / "no-such-engine")


def _restore_env(var, prior):
    """The engine binaries are process-global, and the suite shares one process with the other L1 tests."""
    if prior is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = prior


class _Reached(Exception):
    """The launcher got past the checks under test; what it would have spawned is not this test's business."""


def _only_the_wrapper(exc):
    """A `Popen` side effect that breaks the detached wrapper's spawn and leaves `_git` alone."""
    real = subprocess.Popen

    def spawn(args, *a, **kw):
        if args and str(args[0]) == sys.executable:
            raise exc
        return real(args, *a, **kw)
    return spawn


def _wait_done(slug, name, secs=30):
    for _ in range(secs * 4):
        r = l1.load("altitude", slug, name)
        if r and r.get("done"):
            return r
        time.sleep(0.25)
    raise AssertionError(f"run {name} did not finish: {l1.load('altitude', slug, name)}")


class HookTests(unittest.TestCase):
    """The PreToolUse hook inside a dispatched task: it enforces, the launcher bills."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="cap-hook-", dir=_TMP))
        self.task = self.home / "demo" / "tasks" / "task-one"
        self.task.mkdir(parents=True)
        (self.home / "monitor").mkdir()
        self.set_cap(3)

    def set_cap(self, cap):
        (self.home / "monitor" / "envelope-demo--task-one-1.json").write_text(json.dumps({"subagent_launches": cap}))

    def env(self, **over):
        e = dict(os.environ, ALTITUDE_HOME=str(self.home), ALTITUDE_PROJECT="demo", ALTITUDE_TASK="task-one",
                 ALTITUDE_SESSION_KEY="demo--task-one-1")
        e.update(over)
        return e

    def call(self, tool="Bash", command="alt l1 run --brief sub.md", call_id="c1", env=None, **tool_input):
        payload = {"session_id": "session-one", "tool_use_id": call_id, "tool_name": tool,
                   "tool_input": ({"command": command} if tool == "Bash" else tool_input or {"prompt": "go"})}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=env or self.env())

    def started(self, name, where=None, **extra):
        log = (where or self.task) / "events.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as stream:
            stream.write(json.dumps({"kind": "l1-started", "name": name, **extra}) + "\n")

    def counts_file(self):
        return self.home / "monitor" / "counts-demo--task-one-1.json"

    def counted(self):
        p = self.counts_file()
        return int(json.loads(p.read_text())["subagent_launches"]) if p.exists() else 0

    # --- what is billed ---------------------------------------------------------------------------------
    def test_alt_l1_run_is_allowed_and_billed_only_by_its_event(self):
        allowed = self.call(call_id="launch")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.counted(), 0)
        self.started("implementer-1")
        self.assertEqual(self.call(call_id="after").returncode, 0)
        self.assertEqual(self.counted(), 1)

    def test_repeated_settlement_is_idempotent(self):
        self.started("implementer-1")
        self.started("implementer-1")  # the same session recorded twice is still one launch
        for i in range(3):
            self.assertEqual(self.call(call_id=f"c{i}").returncode, 0)
        self.assertEqual(self.counted(), 1)

    def test_cap_refused_launch_is_not_billed(self):
        self.set_cap(1)
        self.started("implementer-1")
        refused = self.call(call_id="over-cap")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("envelope reached (1/1", refused.stderr)
        self.assertEqual(self.counted(), 1)

    def test_phantom_count_over_cap_is_reduced_to_the_event_count(self):
        self.set_cap(2)
        self.counts_file().write_text(json.dumps({"subagent_launches": 7, "edits": 4}))
        allowed = self.call(call_id="after-phantoms")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.counted(), 0)
        self.assertEqual(json.loads(self.counts_file().read_text())["edits"], 4, "other counters survive")

    def test_genuine_count_at_the_cap_still_blocks(self):
        self.set_cap(2)
        for n in range(3):
            self.started(f"implementer-{n + 1}")
        refused = self.call(call_id="genuinely-over")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("envelope reached (3/2", refused.stderr)
        self.assertEqual(self.counted(), 3)

    def test_a_live_reservation_counts_against_the_cap(self):
        self.set_cap(1)
        (self.task / "l1").mkdir()
        (self.task / "l1" / "reservations.json").write_text(json.dumps(
            {"reservations": {"t1": {"at": time.time(), "pid": os.getpid(), "name": "implementer-1"}}}))
        refused = self.call(call_id="while-reserved")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("envelope reached (1/1", refused.stderr)

    def test_an_aged_reservation_with_a_live_owner_still_counts(self):
        self.set_cap(1)
        (self.task / "l1").mkdir()
        (self.task / "l1" / "reservations.json").write_text(json.dumps(
            {"reservations": {"t1": {"at": time.time() - 7200, "pid": os.getpid(),
                                      "name": "implementer-1", "role": "implementer"}}}))
        refused = self.call(call_id="while-old-but-live")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("envelope reached (1/1", refused.stderr)

    def test_a_dead_reservation_does_not(self):
        self.set_cap(1)
        (self.task / "l1").mkdir()
        (self.task / "l1" / "reservations.json").write_text(json.dumps(
            {"reservations": {"t1": {"at": time.time(), "pid": 2 ** 22 - 1, "name": "implementer-1"}}}))
        self.assertEqual(self.call(call_id="after-death").returncode, 0)

    # --- direct launch forms ----------------------------------------------------------------------------
    def test_managed_agent_and_task_launches_are_refused_not_billed(self):
        for tool in ("Agent", "Task"):
            with self.subTest(tool=tool):
                refused = self.call(tool=tool, call_id=f"direct-{tool}")
                self.assertEqual(refused.returncode, 2, refused.stdout)
                self.assertIn("alt l1 run", refused.stderr)
                self.assertEqual(self.counted(), 0)

    def test_managed_raw_engine_commands_are_refused_not_billed(self):
        for command in ("codex " + "exec 'go'", "claude " + "-p 'go'", "claude " + "--print 'go'",
                        "claude " + "--bg --name x 'go'"):
            with self.subTest(command=command):
                refused = self.call(command=command, call_id="direct-" + command[:12])
                self.assertEqual(refused.returncode, 2, refused.stdout)
                self.assertIn("alt l1 run", refused.stderr)
                self.assertEqual(self.counted(), 0)

    def test_managed_path_command_and_shell_wrapped_engines_are_refused(self):
        for command in ("/usr/bin/codex exec 'go'", "command codex exec 'go'", "sh -c 'claude -p go'"):
            with self.subTest(command=command):
                refused = self.call(command=command, call_id="wrapped-" + command[:12])
                self.assertEqual(refused.returncode, 2, refused.stdout)
                self.assertIn("alt l1 run", refused.stderr)
                self.assertEqual(self.counted(), 0)

    def test_a_direct_form_anywhere_in_the_command_is_refused(self):
        refused = self.call(command="alt l1 run --brief a.md && codex " + "exec 'go'", call_id="mixed")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("alt l1 run", refused.stderr)

    def test_an_unmanaged_counts_file_that_is_not_an_object_is_rebuilt(self):
        env = self.env()
        env.pop("ALTITUDE_TASK")
        self.counts_file().write_text("[]")
        self.assertEqual(self.call(tool="Agent", call_id="free-corrupt", env=env).returncode, 0)
        self.assertEqual(self.counted(), 1)

    def test_unmanaged_sessions_keep_per_tool_call_billing(self):
        env = self.env()
        env.pop("ALTITUDE_TASK")  # no task folder to reconcile against: decision 31's original counter
        for i in range(2):
            self.assertEqual(self.call(tool="Agent", call_id=f"free-{i}", env=env).returncode, 0)
        self.assertEqual(self.counted(), 2)

    # --- the cheap path ---------------------------------------------------------------------------------
    def test_ordinary_command_in_a_managed_session_exits_early(self):
        self.started("implementer-1")
        r = self.call(command="ls -la", call_id="ordinary")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.counts_file().exists(), "an ordinary command must not rewrite the counts file")
        self.assertFalse((self.home / "monitor" / "counts-demo--task-one-1.json.lock").exists(),
                         "an ordinary command must not take the counts lock")

    def test_capability_probe_in_a_managed_session_is_not_a_launch(self):
        r = self.call(command="codex " + "exec --help", call_id="probe")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- broken data ------------------------------------------------------------------------------------
    def test_counts_file_that_is_not_an_object_is_rebuilt(self):
        self.counts_file().write_text("[]")
        self.started("implementer-1")
        r = self.call(call_id="corrupt-shape")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counted(), 1)

    def test_corrupt_counts_file_is_rebuilt(self):
        self.counts_file().write_text("{not json")
        self.started("implementer-1")
        self.assertEqual(self.call(call_id="corrupt-json").returncode, 0)
        self.assertEqual(self.counted(), 1)

    def test_malformed_non_object_and_non_utf8_event_lines_block(self):
        corruptions = (
            ("malformed", b"not json at all\n"),
            ("non-object", b"[]\n"),
            ("invalid-kind-bytes", b'{"kind": "l1-st\xffarted", "name": "implementer-2"}\n'),
        )
        for label, line in corruptions:
            with self.subTest(corruption=label):
                (self.task / "events.log").write_text(json.dumps(
                    {"kind": "l1-started", "name": "implementer-1"}) + "\n")
                with open(self.task / "events.log", "ab") as stream:
                    stream.write(line)
                refused = self.call(call_id="corrupt-" + label)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("cannot be read", refused.stderr)

    def test_unreadable_or_non_object_reservation_table_blocks(self):
        (self.task / "l1").mkdir()
        path = self.task / "l1" / "reservations.json"
        for label, data in (("malformed", b"{bad"), ("non-object", b"[]"), ("non-utf8", b"\xff")):
            with self.subTest(corruption=label):
                path.write_bytes(data)
                refused = self.call(call_id="reservations-" + label)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("reservations", refused.stderr)

    def test_missing_event_log_is_zero_not_unknown(self):
        self.assertEqual(self.call(call_id="no-log").returncode, 0)
        self.assertEqual(self.counted(), 0)

    def test_unreadable_event_log_blocks_instead_of_failing_open(self):
        (self.task / "events.log").mkdir()  # every read of it raises: the count is unknown, not zero
        refused = self.call(call_id="unknown")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("cannot be read", refused.stderr)

    # --- active and archive -----------------------------------------------------------------------------
    def test_archived_events_are_counted(self):
        archive = self.home / "demo" / "archive" / "task-one"
        self.started("implementer-1", where=archive)
        self.assertEqual(self.call(call_id="archived").returncode, 0)
        self.assertEqual(self.counted(), 1)

    def test_both_logs_are_unioned_without_double_counting(self):
        archive = self.home / "demo" / "archive" / "task-one"
        self.started("implementer-1")
        self.started("implementer-1", where=archive)  # the same run, in both homes
        self.started("reviewer-2", where=archive)
        self.assertEqual(self.call(call_id="both").returncode, 0)
        self.assertEqual(self.counted(), 2)


class CounterUnitTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="counter-unit-", dir=_TMP))

    def test_started_count_is_none_outside_a_task(self):
        self.assertIsNone(LC.started_count(self.home, "demo", "nope"))
        self.assertIsNone(LC.started_count(self.home, None, None))

    def test_settlement_leaves_the_file_alone_when_the_count_is_unknown(self):
        path = LC.counts_path(self.home, "k")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"subagent_launches": 9}))
        self.assertIsNone(LC.settle_counts(self.home, path, "demo", "nope", 2))
        self.assertEqual(json.loads(path.read_text())["subagent_launches"], 9)
        self.assertIn("unknown", (self.home / "monitor" / "hook-faults.log").read_text())

    def test_a_reservation_is_released_exactly_once(self):
        (self.home / "demo" / "tasks" / "t").mkdir(parents=True)
        with LC.launch_lock(self.home, "demo", "t"):
            token = LC.reserve(self.home, "demo", "t", name="implementer-1")
        self.assertEqual(len(LC.reservations(self.home, "demo", "t")), 1)
        LC.release(self.home, "demo", "t", token)
        LC.release(self.home, "demo", "t", token)
        self.assertEqual(LC.reservations(self.home, "demo", "t"), [])

    def test_settlement_and_edit_updates_share_the_counts_lock(self):
        task = self.home / "demo" / "tasks" / "t"
        task.mkdir(parents=True)
        (task / "events.log").write_text(json.dumps(
            {"kind": "l1-started", "name": "implementer-1"}) + "\n")
        path = LC.counts_path(self.home, "k")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"edits": 0, "files": []}))
        entered, resume = threading.Event(), threading.Event()
        real_read = LC.read_counts

        def paused_read(*args):
            entered.set()
            self.assertTrue(resume.wait(5))
            return real_read(*args)

        result = []
        with unittest.mock.patch.object(LC, "read_counts", side_effect=paused_read):
            worker = threading.Thread(
                target=lambda: result.append(LC.settle_counts(self.home, path, "demo", "t", 3)), daemon=True)
            worker.start()
            self.assertTrue(entered.wait(5))
            payload = json.dumps({"session_id": "sid", "tool_input": {"file_path": "x.py"}})
            proc = subprocess.Popen([sys.executable, str(EDIT_HOOK)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env=dict(os.environ, ALTITUDE_HOME=str(self.home), ALTITUDE_SESSION_KEY="k"))
            proc.stdin.write(payload)
            proc.stdin.close()
            time.sleep(0.1)
            self.assertIsNone(proc.poll(), "edit_count must wait while settlement owns the counts lock")
            resume.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            returncode = proc.wait(timeout=5)
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
            self.assertEqual(returncode, 0, stderr)
        self.assertEqual(result, [1])
        counts = json.loads(path.read_text())
        self.assertEqual(counts["subagent_launches"], 1)
        self.assertEqual(counts["edits"], 1)
        self.assertEqual(counts["files"], ["x.py"])


class LauncherTests(unittest.TestCase):
    """`alt l1 run` end to end: the reservation, the handshake, and the one event that is the bill."""

    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        REPO.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=REPO, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "init"], cwd=REPO, check=True)
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO), "stacks": ["python"]}})
        monitor.quota = lambda: {"known": False}
        route.quota_codex = lambda: {"known": False}

    def setUp(self):
        self.addCleanup(os.environ.pop, "ALTITUDE_SESSION_KEY", None)
        for var, fake in (("CODEX_BIN", FAKE / "codex"), ("CLAUDE_BIN", FAKE / "claude")):
            self.addCleanup(_restore_env, var, os.environ.get(var))
            os.environ[var] = str(fake)

    def task(self, slug, *, launches=3, in_flight=1):
        T.new("altitude", slug, "S", "req", actor="l3")
        with S.project_lock("altitude"):
            t = S.load_task("altitude", slug)
            t["worktree"] = str(REPO)
            t["envelope"].update({"subagent_launches": launches, "l1_in_flight": in_flight})
            S.save_task("altitude", t)
        brief = S.task_dir("altitude", slug) / "sub-1.md"
        brief.write_text("# sub-brief\n")
        return brief

    def starts(self, slug):
        return [e for e in S.read_events("altitude", slug) if e.get("kind") == "l1-started"]

    def counts(self, key):
        p = LC.counts_path(config.ROOT, key)
        return json.loads(p.read_text()) if p.exists() else {}

    # --- a launch that runs -----------------------------------------------------------------------------
    def test_a_started_engine_is_billed_once(self):
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                slug = f"started-{engine}"
                brief = self.task(slug)
                os.environ["ALTITUDE_SESSION_KEY"] = f"altitude--{slug}-1"
                rec = l1.start("altitude", slug, brief, role="reviewer", engine=engine)
                self.assertTrue(rec["reservation"], "the launch holds a slot until the handshake settles it")
                self.assertEqual(self.starts(slug), [], "nothing is billed before the engine answers")
                done = _wait_done(slug, rec["name"])
                self.assertIsNone(done["result"]["error"])
                self.assertEqual([e["name"] for e in self.starts(slug)], [rec["name"]])
                self.assertEqual(self.counts(f"altitude--{slug}-1")["subagent_launches"], 1)
                self.assertEqual(LC.reservations(config.ROOT, "altitude", slug), [], "the slot is settled, not held")

    # --- launches that never reach an engine ------------------------------------------------------------
    def _no_bill(self, slug, engine, binary):
        brief = self.task(slug)
        os.environ["CODEX_BIN" if engine == "codex" else "CLAUDE_BIN"] = binary
        os.environ["ALTITUDE_SESSION_KEY"] = f"altitude--{slug}-1"
        rec = l1.start("altitude", slug, brief, role="reviewer", engine=engine)
        self.assertTrue(rec["reservation"], "a slot was taken, so the release below is a real release")
        done = _wait_done(slug, rec["name"])
        self.assertIsNotNone(done["result"]["error"], "the record still closes with the reason")
        self.assertEqual(self.starts(slug), [], "an engine that never ran is never billed")
        self.assertEqual(self.counts(f"altitude--{slug}-1").get("subagent_launches", 0), 0)
        self.assertEqual(LC.reservations(config.ROOT, "altitude", slug), [], "the slot is given back")

    def test_wrapper_starts_but_engine_exits_127_is_not_billed(self):
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                self._no_bill(f"exit127-{engine}", engine, str(FAKE / "exit127"))

    def test_missing_engine_binary_is_not_billed(self):
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                self._no_bill(f"missing-{engine}", engine, MISSING)

    def test_non_executable_engine_binary_is_not_billed(self):
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                self._no_bill(f"noexec-{engine}", engine, str(FAKE / "not-executable"))

    def test_codex_executable_with_invalid_format_is_not_billed(self):
        self._no_bill("enoexec-codex", "codex", str(FAKE / "invalid-format"))

    def test_a_streamed_claude_quota_refusal_is_not_billed(self):
        from altitude import engines
        self.addCleanup(engines.usage_limit_path().unlink, True)
        self._no_bill("streamed-quota-refusal", "claude", str(FAKE / "quota-refusal"))

    def test_a_closed_subscription_window_is_not_billed(self):
        slug = "usage-refusal"
        brief = self.task(slug)
        os.environ["ALTITUDE_SESSION_KEY"] = f"altitude--{slug}-1"
        from datetime import datetime, timedelta, timezone
        from altitude import engines
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
        engines.usage_limit_path().parent.mkdir(parents=True, exist_ok=True)
        engines.usage_limit_path().write_text(json.dumps({"until": until, "seen": until, "detail": "test"}))
        self.addCleanup(engines.usage_limit_path().unlink, True)
        rec = l1.start("altitude", slug, brief, role="reviewer", engine="claude")
        done = _wait_done(slug, rec["name"])
        self.assertIn("usage limit", done["result"]["error"])
        self.assertEqual(self.starts(slug), [], "a refused call never started an engine")
        self.assertEqual(LC.reservations(config.ROOT, "altitude", slug), [])

    # --- the pre-launch cap -----------------------------------------------------------------------------
    def test_the_cap_is_enforced_before_routing_or_spawning(self):
        slug = "cap-before-launch"
        brief = self.task(slug, launches=2)
        for n in range(2):
            S.append_event("altitude", slug, "l1-started", name=f"implementer-{n + 1}")
        with unittest.mock.patch.object(l1.route, "pick_engine", side_effect=AssertionError("routed anyway")):
            with unittest.mock.patch.object(l1.subprocess, "Popen", side_effect=AssertionError("spawned anyway")):
                with self.assertRaisesRegex(T.TransitionError, r"2 run\(s\) already started \(cap 2\)"):
                    l1.start("altitude", slug, brief, role="reviewer")
        self.assertEqual(LC.reservations(config.ROOT, "altitude", slug), [], "a refused launch takes no slot")

    def test_an_unknown_count_refuses_the_launch(self):
        slug = "unknown-count"
        brief = self.task(slug)
        with unittest.mock.patch.object(LC, "launch_ledger", return_value=None):
            with self.assertRaisesRegex(T.TransitionError, "cannot be read"):
                l1.start("altitude", slug, brief, role="reviewer")

    def test_a_spawn_that_fails_leaves_no_reservation_event_or_bill(self):
        slug = "spawn-failure"
        brief = self.task(slug)
        os.environ["ALTITUDE_SESSION_KEY"] = f"altitude--{slug}-1"
        with unittest.mock.patch.object(l1.subprocess, "Popen", side_effect=_only_the_wrapper(PermissionError("denied"))):
            with self.assertRaisesRegex(T.TransitionError, "failed to start"):
                l1.start("altitude", slug, brief, role="reviewer")
        self.assertEqual(self.starts(slug), [])
        self.assertEqual(LC.reservations(config.ROOT, "altitude", slug), [])
        self.assertEqual(self.counts(f"altitude--{slug}-1").get("subagent_launches", 0), 0)

    # --- run records without a start event --------------------------------------------------------------
    def test_a_run_without_a_start_event_holds_no_slot_and_is_not_the_author(self):
        slug = "unstarted-implementer"
        brief = self.task(slug, in_flight=1)
        l1.save("altitude", slug, {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
                                   "why": "phantom", "model": None, "worktree": str(REPO), "branch": "main",
                                   "brief": str(brief), "started": S.now(), "pid": None, "done": None,
                                   "result": None, "reservation": None})
        with unittest.mock.patch.object(l1, "_spawn", side_effect=_Reached):
            with self.assertRaises(_Reached):
                l1.start("altitude", slug, brief, role="implementer")  # the in-flight cap did not fire
        rec = l1.start("altitude", slug, brief, role="reviewer", engine="claude")
        self.assertEqual(rec["engine"], "claude", "an unstarted implementer is not an author to route away from")

    def test_an_implementer_reservation_occupies_the_in_flight_slot(self):
        slug = "reserved-implementer"
        brief = self.task(slug, launches=3, in_flight=1)
        with LC.launch_lock(config.ROOT, "altitude", slug):
            token = LC.reserve(config.ROOT, "altitude", slug, name="implementer-1", role="implementer")
        self.addCleanup(LC.release, config.ROOT, "altitude", slug, token)
        with self.assertRaisesRegex(T.TransitionError, "1 implementer.*in flight"):
            l1.start("altitude", slug, brief, role="implementer")

    def test_concurrent_starts_reserve_distinct_sequence_names(self):
        slug = "concurrent-reviewers"
        brief = self.task(slug, launches=3, in_flight=1)
        barrier = threading.Barrier(2)
        results, errors = [], []

        def fake_spawn(*_args, **kwargs):
            barrier.wait(timeout=5)
            return kwargs

        def launch():
            try:
                results.append(l1.start("altitude", slug, brief, role="reviewer", engine="codex"))
            except BaseException as exc:
                errors.append(exc)

        with unittest.mock.patch.object(l1, "_spawn", side_effect=fake_spawn):
            workers = [threading.Thread(target=launch) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(6)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(result["name"] for result in results), ["reviewer-1", "reviewer-2"])
        self.assertEqual(sorted(result["n"] for result in results), [1, 2])
        for result in results:
            LC.release(config.ROOT, "altitude", slug, result["token"])

    def test_settlement_is_idempotent(self):
        slug = "settle-twice"
        brief = self.task(slug)
        os.environ["ALTITUDE_SESSION_KEY"] = f"altitude--{slug}-1"
        rec = l1.start("altitude", slug, brief, role="reviewer", engine="codex")
        _wait_done(slug, rec["name"])
        l1._settle_launch("altitude", slug, l1.load("altitude", slug, rec["name"]), True)
        self.assertEqual(len(self.starts(slug)), 1)
        self.assertEqual(self.counts(f"altitude--{slug}-1")["subagent_launches"], 1)


class HandshakeTests(unittest.TestCase):
    """`engine_started` is the whole billing decision; these are its edges."""

    def check(self, engine, res, exc=None, pid=None):
        return l1.engine_started({"engine": engine}, res, exc, pid)

    def test_claude_needs_its_child_pid(self):
        self.assertFalse(self.check("claude", {}, pid=None))
        self.assertTrue(self.check("claude", {"error": None}, pid=4242))

    def test_exit_127_is_never_a_launch(self):
        self.assertFalse(self.check("claude", {"error": "claude exit 127: not found"}, pid=4242))
        self.assertFalse(self.check("codex", {"returncode": 127}))

    def test_an_exec_that_never_happened_is_never_a_launch(self):
        for exc in (FileNotFoundError("no such file"), PermissionError("denied"), NotADirectoryError("nope"),
                    OSError(8, "exec format error")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(self.check("codex", {}, exc=exc))
                self.assertFalse(self.check("claude", {}, exc=exc, pid=None))

    def test_a_closed_window_is_never_a_launch(self):
        self.assertFalse(self.check("codex", {"limited": "2026-08-30T12:00:00+00:00", "returncode": 0}))

    def test_an_explicit_preflight_failure_is_never_a_launch(self):
        self.assertFalse(self.check("codex", {"returncode": 1, "engine_started": False,
                                               "error": "Codex sandbox preflight failed"}))

    def test_an_engine_that_ran_and_failed_is_still_a_launch(self):
        self.assertTrue(self.check("codex", {"returncode": 1, "error": "engine said no"}))
        self.assertTrue(self.check("codex", {}, exc=subprocess.TimeoutExpired("codex", 1)))
        self.assertTrue(self.check("claude", {"error": "claude exit 1: boom"}, pid=4242))


if __name__ == "__main__":
    unittest.main()
