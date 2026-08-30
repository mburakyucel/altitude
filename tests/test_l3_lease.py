"""The project L3 lease and report retry record survive altd restarts without duplicate turns."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l3-lease-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, server, state as S  # noqa: E402

PROJECT = "l3-lease"


def future(seconds=300):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def fake_claude_tree() -> Path:
    """Executable fake Claude whose SIGTERM-ignoring tool inherits its output pipes."""
    path = _TMP / "fake-claude-tree.py"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
        "time.sleep(0.2)\n"
        "print(json.dumps({'type':'stream_event','event':{'delta':"
        "{'type':'text_delta','text':'chunk'}}}), flush=True)\n"
        "time.sleep(30)\n")
    path.chmod(0o755)
    return path


def fake_success_tree(engine: str) -> Path:
    """Successful CLI leader leaves a detached-from-pipes, SIGTERM-ignoring tool behind."""
    path = _TMP / f"fake-{engine}-success-tree.py"
    prelude = (
        "#!/usr/bin/env python3\n"
        "import json, pathlib, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "time.sleep(0.2)\n")
    if engine == "claude":
        body = (
            "print(json.dumps({'type':'result','session_id':'sid-success',"
            "'result':'done','is_error':False}), flush=True)\n")
    else:
        body = (
            "args=sys.argv[1:]\n"
            "out=args[args.index('-o')+1]\n"
            "pathlib.Path(out).write_text('done')\n"
            "print(json.dumps({'type':'thread.started','thread_id':'codex-success'}), flush=True)\n"
            "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)\n")
    path.write_text(prelude + body)
    path.chmod(0o755)
    return path


class TestDurableL3Lease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects()
        projects[PROJECT] = {"name": PROJECT, "path": str(config.ROOT), "stacks": ["python"]}
        config.save_projects(projects)

    def setUp(self):
        S.write_json(l3.lease_path(PROJECT), {})

    def record(self, **changes):
        rec = {"version": 1, "generation": "old-generation", "trigger": "audit",
               "owner_pid": 99999991, "owner_pid_start": "dead", "engine_pid": None,
               "engine_pid_start": None, "started": S.now(), "deadline": future(),
               "evidence": {"kind": "test"}}
        rec.update(changes)
        S.write_json(l3.lease_path(PROJECT), rec)
        return rec

    def test_restart_adopts_exact_live_orphan_and_suppresses_every_trigger(self):
        self.record(engine_pid=os.getpid(), engine_pid_start=l3._proc_start_time(os.getpid()))
        triggers = ("proposal-ready", "report-landed", "reconcile", "chat",
                    "idea", "backlog", "start", "audit")
        for trigger in triggers:
            result = l3.turn(PROJECT, "must not launch", trigger=trigger)
            self.assertTrue(result["busy"], trigger)
            self.assertTrue(result["skipped"], trigger)
            self.assertEqual(result["lease_generation"], "old-generation")
        rec = S.read_json(l3.lease_path(PROJECT), {})
        self.assertEqual(rec["adopted_by_pid"], os.getpid())
        self.assertTrue(l3.busy(PROJECT))
        public = l3.public_info(PROJECT)
        self.assertTrue(public["busy"])
        self.assertEqual(public["active_turn"]["generation"], "old-generation")

    def test_dead_claim_is_recovered_and_only_current_generation_clears(self):
        self.record()
        self.assertEqual(l3.lease_info(PROJECT), {})
        self.assertEqual(S.read_json(l3.lease_path(PROJECT), {}), {})
        self.record(generation="new")
        l3._clear_lease(PROJECT, "old")
        self.assertEqual(S.read_json(l3.lease_path(PROJECT), {})["generation"], "new")
        l3._clear_lease(PROJECT, "new")
        self.assertEqual(S.read_json(l3.lease_path(PROJECT), {}), {})

    def test_expired_exact_engine_is_reaped_but_pid_reuse_mismatch_is_not_signalled(self):
        child = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.record(engine_pid=child.pid, engine_pid_start=l3._proc_start_time(child.pid),
                    deadline="2000-01-01T00:00:00+00:00")
        self.assertEqual(l3.lease_info(PROJECT), {})
        child.wait(timeout=3)
        self.assertIsNotNone(child.returncode)

        self.record(engine_pid=os.getpid(), engine_pid_start="recycled-start",
                    deadline="2000-01-01T00:00:00+00:00")
        with mock.patch.object(l3.os, "kill", side_effect=AssertionError("recycled PID must not be signalled")):
            self.assertEqual(l3.lease_info(PROJECT), {})

    def test_orphaned_codex_is_immediately_fenced_with_its_descendants(self):
        child = subprocess.Popen(
            [sys.executable, "-c",
             ("import subprocess,sys,time; "
              "subprocess.Popen([sys.executable,'-c',"
              "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
              "time.sleep(30)")],
            start_new_session=True,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        for _ in range(100):
            if len(engines.process_group_members(child.pid)) >= 2:
                break
            time.sleep(0.01)
        self.assertGreaterEqual(len(engines.process_group_members(child.pid)), 2)
        time.sleep(0.1)  # the descendant has installed SIG_IGN before the leader is terminated
        self.record(engine="codex", engine_pid=child.pid,
                    engine_pid_start=l3._proc_start_time(child.pid), engine_pgid=child.pid,
                    evidence={"proposal_slug": "requeue-me"})
        self.assertEqual(l3.lease_info(PROJECT), {})
        child.wait(timeout=3)
        self.assertEqual(engines.process_group_members(child.pid), {})

    def test_expired_claude_private_group_reaps_sigterm_ignoring_tool(self):
        child = subprocess.Popen(
            [sys.executable, "-c",
             ("import subprocess,sys,time; "
              "subprocess.Popen([sys.executable,'-c',"
              "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
              "time.sleep(30)")],
            start_new_session=True,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        for _ in range(100):
            if len(engines.process_group_members(child.pid)) >= 2:
                break
            time.sleep(0.01)
        self.assertGreaterEqual(len(engines.process_group_members(child.pid)), 2)
        time.sleep(0.1)
        leader_start = l3._proc_start_time(child.pid)
        os.kill(child.pid, signal.SIGTERM)
        child.wait(timeout=3)
        self.assertTrue(engines.process_group_members(child.pid),
                        "the SIGTERM-ignoring tool must outlive its Claude leader for this regression")
        self.record(engine="claude", engine_pid=child.pid,
                    engine_pid_start=leader_start, engine_pgid=child.pid,
                    deadline="2000-01-01T00:00:00+00:00",
                    evidence={"proposal_slug": "requeue-claude"})
        self.assertEqual(l3.lease_info(PROJECT), {})
        child.wait(timeout=3)
        self.assertEqual(engines.process_group_members(child.pid), {})

    def test_successful_claude_reaps_lingering_tool_before_lease_clear(self):
        starts = []
        with mock.patch.object(config, "CLAUDE_BIN", str(fake_success_tree("claude"))), \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}):
            result = l3.turn(PROJECT, "success", trigger="chat", on_start=starts.append)
        self.assertEqual((result["text"], result["error"]), ("done", None))
        self.assertEqual(len(starts), 1)
        self.assertEqual(engines.process_group_members(starts[0]), {})
        self.assertFalse(l3.busy(PROJECT))

    def test_successful_codex_reaps_lingering_tool_before_return(self):
        starts = []
        with mock.patch.object(config, "CODEX_BIN", str(fake_success_tree("codex"))):
            result = engines.codex_exec("success", cwd=config.ROOT, on_start=starts.append)
        self.assertEqual((result["text"], result["error"]), ("done", None))
        self.assertEqual(len(starts), 1)
        self.assertEqual(engines.process_group_members(starts[0]), {})

    def test_claude_timeout_reaps_sigterm_ignoring_tool_before_lease_clear(self):
        starts = []
        real_claude = engines.claude_print

        def short_turn(prompt, **kwargs):
            kwargs["timeout"] = 0.4
            return real_claude(prompt, **kwargs)

        with mock.patch.object(config, "CLAUDE_BIN", str(fake_claude_tree())), \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}), \
             mock.patch.object(l3.engines, "claude_print", side_effect=short_turn):
            result = l3.turn(PROJECT, "timeout", trigger="audit", on_start=starts.append)
        self.assertEqual(len(starts), 1)
        self.assertIn("timed out", result["error"])
        self.assertEqual(engines.process_group_members(starts[0]), {})
        self.assertFalse(l3.busy(PROJECT), "lease may clear only after the private group is empty")

    def test_claude_on_text_failure_reaps_sigterm_ignoring_tool_before_raise(self):
        starts = []

        def reject_text(_text):
            raise RuntimeError("consumer rejected streamed text")

        with mock.patch.object(config, "CLAUDE_BIN", str(fake_claude_tree())), \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}):
            with self.assertRaisesRegex(RuntimeError, "consumer rejected"):
                l3.turn(PROJECT, "callback", trigger="report-landed",
                        on_start=starts.append, on_text=reject_text)
        self.assertEqual(len(starts), 1)
        self.assertEqual(engines.process_group_members(starts[0]), {})
        self.assertFalse(l3.busy(PROJECT), "exception unwind must not clear ahead of group reaping")

    def test_claude_on_start_failure_reaps_already_spawned_tool(self):
        starts = []

        def reject_start(pid):
            starts.append(pid)
            for _ in range(200):
                if len(engines.process_group_members(pid)) >= 2:
                    break
                time.sleep(0.01)
            self.assertGreaterEqual(len(engines.process_group_members(pid)), 2)
            raise RuntimeError("consumer rejected engine start")

        with mock.patch.object(config, "CLAUDE_BIN", str(fake_claude_tree())), \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}):
            with self.assertRaisesRegex(RuntimeError, "consumer rejected engine start"):
                l3.turn(PROJECT, "startup callback", trigger="chat", on_start=reject_start)
        self.assertEqual(len(starts), 1)
        self.assertEqual(engines.process_group_members(starts[0]), {})
        self.assertFalse(l3.busy(PROJECT))

    def test_claim_race_does_not_replace_dead_leader_with_live_private_group(self):
        leader = subprocess.Popen(
            [sys.executable, "-c",
             ("import subprocess,sys,time; "
              "subprocess.Popen([sys.executable,'-c',"
              "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
              "time.sleep(30)")],
            start_new_session=True,
        )
        for _ in range(100):
            if len(engines.process_group_members(leader.pid)) >= 2:
                break
            time.sleep(0.01)
        self.assertGreaterEqual(len(engines.process_group_members(leader.pid)), 2)
        time.sleep(0.1)
        leader_start = l3._proc_start_time(leader.pid)
        os.kill(leader.pid, signal.SIGTERM)
        leader.wait(timeout=3)
        self.assertTrue(engines.process_group_members(leader.pid))

        def interleaved_stale_claim(_project):
            self.record(engine="claude", engine_pid=leader.pid,
                        engine_pid_start=leader_start, engine_pgid=leader.pid,
                        deadline="2000-01-01T00:00:00+00:00")
            return {}

        with mock.patch.object(l3, "lease_info", side_effect=interleaved_stale_claim):
            claim = l3._claim_lease(PROJECT, "chat", "new generation", None)
        self.assertIsNone(claim)
        self.assertEqual(S.read_json(l3.lease_path(PROJECT), {})["generation"], "old-generation")
        self.assertEqual(l3.lease_info(PROJECT), {})
        self.assertEqual(engines.process_group_members(leader.pid), {})

    def test_expired_engine_keeps_exact_live_owner_during_unwind(self):
        engine = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: engine.poll() is None and engine.kill())
        self.record(engine="codex", owner_pid=os.getpid(),
                    owner_pid_start=l3._proc_start_time(os.getpid()), engine_pid=engine.pid,
                    engine_pid_start=l3._proc_start_time(engine.pid), engine_pgid=engine.pid,
                    deadline="2000-01-01T00:00:00+00:00")
        rec = l3.lease_info(PROJECT)
        engine.wait(timeout=3)
        self.assertEqual(rec["generation"], "old-generation")
        self.assertIsNotNone(rec["timed_out_at"])
        self.assertTrue(l3.busy(PROJECT))
        l3._clear_lease(PROJECT, "old-generation")
        self.assertFalse(l3.busy(PROJECT))

    def test_combined_on_start_persists_engine_before_calling_consumer(self):
        observed = []

        def fake_engine(prompt, **kwargs):
            self.assertTrue(kwargs["start_new_session"])
            child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
            kwargs["on_start"](child.pid)
            child.terminate(); child.wait(timeout=3)
            return {"text": "ok", "session_id": "s", "usage": {}, "context_tokens": 10,
                    "cost": 0.0, "turns": 1, "structured": None, "error": None, "tools": [],
                    "limited": None}

        def consumer(pid):
            rec = S.read_json(l3.lease_path(PROJECT), {})
            observed.append((pid, rec.get("engine_pid"), rec.get("engine_pid_start"), rec.get("engine_pgid")))

        with mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}), \
             mock.patch.object(l3.engines, "claude_print", side_effect=fake_engine):
            result = l3.turn(PROJECT, "run", trigger="chat", on_start=consumer)
        self.assertEqual(result["text"], "ok")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], observed[0][1])
        self.assertEqual(observed[0][0], observed[0][3])
        self.assertIsNotNone(observed[0][2])
        self.assertFalse(l3.busy(PROJECT))

    def test_mid_turn_limit_records_codex_before_fallback_engine_can_orphan(self):
        observed = []

        def limited(*args, **kwargs):
            self.assertTrue(kwargs["start_new_session"])
            return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0,
                    "cost": 0.0, "turns": 0, "structured": None, "error": "usage limit",
                    "tools": [], "limited": future()}

        def crashing_codex(prompt, **kwargs):
            child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
            kwargs["on_start"](child.pid)
            rec = S.read_json(l3.lease_path(PROJECT), {})
            observed.append((rec.get("engine"), rec.get("engine_pid"), rec.get("engine_pid_start"),
                             rec.get("engine_pgid")))
            child.terminate(); child.wait(timeout=3)
            raise RuntimeError("daemon crashed while collecting Codex output")

        with mock.patch.object(l3.route, "pick_l3_engine", return_value={"engine": "claude", "why": "test"}), \
             mock.patch.object(l3.engines, "claude_print", side_effect=limited), \
             mock.patch.object(l3.engines, "codex_exec", side_effect=crashing_codex):
            with self.assertRaisesRegex(RuntimeError, "daemon crashed"):
                l3.turn(PROJECT, "fallback", trigger="chat")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], "codex")
        self.assertEqual(observed[0][1], observed[0][3])
        self.assertIsNotNone(observed[0][2])
        self.assertFalse(l3.busy(PROJECT))

    def test_precheck_can_take_project_lock_and_false_check_clears_claim(self):
        checks = []

        def precheck():
            with S.project_lock(PROJECT):
                checks.append(True)
            return False

        result = l3.turn(PROJECT, "skip", trigger="proposal-ready", precheck=precheck)
        self.assertTrue(result["skipped"])
        self.assertFalse(result.get("busy"))
        self.assertFalse(l3.busy(PROJECT))
        self.assertEqual(len(checks), 1)  # the cheap first check avoids claiming at all

    def test_busy_weekly_audit_is_not_stamped_complete(self):
        old = "2000-01-01T00:00:00+00:00"
        l3.save_info(PROJECT, {"session_id": "audit-session", "last_audit": old})
        with mock.patch.object(server.l3, "turn", return_value={"busy": True, "skipped": True}):
            server._weekly_audit_turn(PROJECT, "audit", "input-a")
        inf = l3.info(PROJECT)
        self.assertEqual(inf["last_audit"], old)
        self.assertGreater(inf["audit_retry_after"], S.now())
        with mock.patch.object(server.l3, "turn", return_value={"text": "done", "error": None}):
            server._weekly_audit_turn(PROJECT, "audit", "input-a")
        self.assertGreater(l3.info(PROJECT)["last_audit"], old)


class TestDurableReportRetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TestDurableL3Lease.setUpClass()

    def setUp(self):
        S.write_json(l3.lease_path(PROJECT), {})

    @staticmethod
    def verdict():
        return {"verdict": "contradicted", "problems": ["needs judgement"], "signals": [],
                "spend": {}, "prs": [], "report": {}}

    def task(self, slug):
        directory = S.task_dir(PROJECT, slug)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report.json").write_text(json.dumps({"landed": {}, "revision": 1}))
        task = {"slug": slug, "title": slug, "class": "L", "state": "reported",
                "created": S.now(), "updated": S.now(), "verified": self.verdict(),
                "l3_handled": None, "prs": []}
        S.save_task(PROJECT, task)
        return task

    def make_due(self, slug):
        with S.project_lock(PROJECT):
            task = S.load_task(PROJECT, slug)
            task["report_recovery"]["retry_after"] = "2000-01-01T00:00:00+00:00"
            S.save_task(PROJECT, task)

    def test_unchanged_noop_is_bounded_faulted_and_evidence_change_rearms(self):
        task = self.task("bounded-report")
        turns = []
        faults = []
        with mock.patch.object(server.improve, "index", return_value=[]), \
             mock.patch.object(server.improve, "system_fault", side_effect=lambda *a, **k: faults.append((a, k))), \
             mock.patch.object(server.l3, "turn", side_effect=lambda *a, **k: turns.append(k["evidence"]) or {"text": "noop"}):
            server.report_turn(PROJECT, task, self.verdict())
            self.make_due(task["slug"])
            server.report_turn(PROJECT, task, self.verdict())
            self.make_due(task["slug"])
            server.report_turn(PROJECT, task, self.verdict())
            self.assertEqual(len(turns), 2)
            self.assertEqual(len(faults), 1)
            rec = S.load_task(PROJECT, task["slug"])["report_recovery"]
            self.assertEqual(rec["attempts"], config.REPORT_MAX_FAILURES)

            report = S.task_dir(PROJECT, task["slug"]) / "report.json"
            report.write_text(json.dumps({"landed": {}, "revision": 2}))
            server.report_turn(PROJECT, task, self.verdict())
        self.assertEqual(len(turns), 3, "changed report bytes must re-arm a faulted evidence generation")
        rec = S.load_task(PROJECT, task["slug"])["report_recovery"]
        self.assertEqual(rec["attempts"], 1)

    def test_restart_claim_waits_for_matching_generic_lease_then_backoffs_when_dead(self):
        task = self.task("restart-report")
        claim = server._claim_report_turn(PROJECT, task["slug"], self.verdict())
        self.assertIsNotNone(claim)
        generation, fingerprint = claim
        with S.project_lock(PROJECT):
            live = S.load_task(PROJECT, task["slug"])
            live["report_recovery"]["claim"].update({"owner_pid": 99999991, "owner_pid_start": "dead"})
            S.save_task(PROJECT, live)
        S.write_json(l3.lease_path(PROJECT), {
            "version": 1, "generation": "generic", "trigger": "report-landed",
            "owner_pid": 99999991, "owner_pid_start": "dead", "engine_pid": os.getpid(),
            "engine_pid_start": l3._proc_start_time(os.getpid()), "started": S.now(), "deadline": future(),
            "evidence": {"report_generation": generation, "report_fingerprint": fingerprint},
        })
        self.assertFalse(server._report_ready(PROJECT, S.load_task(PROJECT, task["slug"]), self.verdict()))
        S.write_json(l3.lease_path(PROJECT), {})
        self.assertIsNone(server._claim_report_turn(PROJECT, task["slug"], self.verdict()))
        rec = S.load_task(PROJECT, task["slug"])["report_recovery"]
        self.assertEqual(rec["attempts"], 1)
        self.assertIsNone(rec["claim"])
        self.assertGreater(rec["retry_after"], S.now())

    def test_orphaned_codex_report_is_fenced_then_retried_once_after_backoff(self):
        task = self.task("codex-replay-report")
        generation, fingerprint = server._claim_report_turn(PROJECT, task["slug"], self.verdict())
        with S.project_lock(PROJECT):
            live = S.load_task(PROJECT, task["slug"])
            live["report_recovery"]["claim"].update({"owner_pid": 99999991, "owner_pid_start": "dead"})
            S.save_task(PROJECT, live)
        engine = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: engine.poll() is None and engine.kill())
        S.write_json(l3.lease_path(PROJECT), {
            "version": 1, "generation": "orphan-codex", "trigger": "report-landed", "engine": "codex",
            "owner_pid": 99999991, "owner_pid_start": "dead", "engine_pid": engine.pid,
            "engine_pid_start": l3._proc_start_time(engine.pid), "engine_pgid": engine.pid,
            "started": S.now(), "deadline": future(),
            "evidence": {"report_slug": task["slug"], "report_generation": generation,
                         "report_fingerprint": fingerprint},
        })
        self.assertTrue(server._report_ready(PROJECT, S.load_task(PROJECT, task["slug"]), self.verdict()))
        engine.wait(timeout=3)
        self.assertIsNone(server._claim_report_turn(PROJECT, task["slug"], self.verdict()))
        self.make_due(task["slug"])
        turns = []
        with mock.patch.object(server.improve, "index", return_value=[]), \
             mock.patch.object(server.improve, "system_fault"), \
             mock.patch.object(server.l3, "turn", side_effect=lambda *a, **k: turns.append(k["evidence"]) or {"text": "noop"}):
            server.report_turn(PROJECT, task, self.verdict())
        self.assertEqual(len(turns), 1)

    def test_wrong_generation_cannot_settle_current_report_claim(self):
        task = self.task("generation-report")
        generation, fingerprint = server._claim_report_turn(PROJECT, task["slug"], self.verdict())
        self.assertFalse(server._settle_report_turn(PROJECT, task["slug"], "wrong", fingerprint,
                                                    "reported", {"text": "noop"}))
        rec = S.load_task(PROJECT, task["slug"])["report_recovery"]
        self.assertEqual(rec["claim"]["generation"], generation)


if __name__ == "__main__":
    unittest.main()
