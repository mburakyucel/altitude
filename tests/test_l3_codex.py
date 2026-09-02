"""Codex L3 turns have one durable physical owner and idempotent delivery."""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l3-owner-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, l3_actions, recovery, server, state as S  # noqa: E402

INSTANCE = "altd:111:test-invocation"


def result(session="cx-1", *, message="answer", actions=None):
    defaults = {"slug": None, "title": None, "text": None, "reason": None, "request": None,
                "digest": None, "answer": None, "source": None, "engine": None, "model": None,
                "paths": [], "hold_merge": None, "merge_hold": None, "incident": None, "labels": []}
    actions = [{**defaults, **action} for action in (actions or [])]
    return {
        "text": message, "session_id": session, "reported_session_id": session,
        "error": None, "usage": {"input_tokens": 1200, "cached_input_tokens": 900},
        "structured": {"message": message, "actions": actions}, "returncode": 0,
        "containment_empty": True,
    }


class TestL3Ownership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root(); (_TMP / "repo").mkdir(exist_ok=True)
        config.save_projects({"k": {"name": "k", "path": str(_TMP / "repo")}})

    def setUp(self):
        shutil.rmtree(config.project_dir("k"), ignore_errors=True)
        config.project_dir("k").mkdir(parents=True)
        recovery.hold_path().unlink(missing_ok=True)
        recovery.clearance_history_path().unlink(missing_ok=True)
        server._bg.clear()  # noqa: SLF001 - isolate the process-local timer optimization
        self.transitions = []
        self.empty = True
        self.run_child = True
        self.observe = mock.patch.object(engines, "observe_managed_unit", side_effect=self._observe)
        self.spawn = mock.patch.object(engines, "spawn_managed_unit", side_effect=self._spawn)
        self.observe.start(); self.spawn.start()

    def tearDown(self):
        self.spawn.stop(); self.observe.stop()

    @staticmethod
    def choice(engine="codex"):
        return {"engine": engine, "why": f"test chose {engine}", "quota": {}}

    def _observe(self, unit):
        return {
            "process_unit_id": unit, "load_state": "not-found" if self.empty else "loaded",
            "active_state": "inactive" if self.empty else "active",
            "sub_state": "dead" if self.empty else "running", "control_group": "",
            "population": "empty" if self.empty else "populated", "empty": self.empty,
        }

    def _spawn(self, transition, command, **_kwargs):
        self.transitions.append(transition)
        if self.run_child:
            l3._managed_codex(command[-3], command[-2], command[-1])  # noqa: SLF001 - real adapter seam
        return mock.Mock(pid=123, wait=mock.Mock(return_value=0))

    @staticmethod
    def spooled_provider(response):
        """Model the real adapter contract: process status plus durable answer/event spools."""
        def provider(*args, **kwargs):
            raw = response(*args, **kwargs) if callable(response) else response
            if isinstance(raw, dict) and raw.get("returncode") == 0:
                S.atomic_write(kwargs["answer_path"], json.dumps(raw["structured"]))
                with kwargs["event_spool"].open("a") as stream:
                    stream.write(json.dumps({"type": "thread.started",
                                             "thread_id": raw["reported_session_id"]}) + "\n")
                    stream.write(json.dumps({"type": "turn.completed",
                                             "usage": raw.get("usage", {})}) + "\n")
            return raw
        return provider

    def run_turn(self, prompt="hello", *, codex=None, **kwargs):
        kwargs.setdefault("instance_id", INSTANCE)
        adapter = codex or (lambda *_a, **_k: result())
        with mock.patch.object(l3, "_select", return_value=self.choice()), \
             mock.patch.object(engines, "codex_exec",
                               side_effect=self.spooled_provider(adapter) if callable(adapter) else adapter):
            return l3.turn("k", prompt, **kwargs)

    def prior_stopped(self, prompt="crash boundary"):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(  # noqa: SLF001
            "k", prompt, "chat", self.choice(), inf, None, None, INSTANCE)
        inf["current_turn"] = op
        l3.save_info("k", inf)
        return l3._prior_stopped("k", op, session)  # noqa: SLF001

    def recovery_identity(self):
        held = recovery.hold("test recovery", kind="test", actor="altd")
        recovery.request_l3_attention("k", kind="test")
        claimed = recovery.claim_l3_attention("k")
        self.assertIsNotNone(claimed)
        return {"episode_id": held["episode"], "permit_revision": claimed["revision"],
                "claim": claimed["claim"]}

    def test_fresh_and_resume_use_new_generations_but_one_provider_thread(self):
        calls = []

        def codex(prompt, **kwargs):
            calls.append((prompt, kwargs.get("resume")))
            return result(kwargs.get("resume") or "cx-1")

        first = self.run_turn("first", codex=codex)
        first_unit = l3.info("k")["current_turn"]["physical"]["process_unit_id"]
        second = self.run_turn("second", codex=codex)
        current = l3.info("k")["current_turn"]

        self.assertEqual((first["session_id"], second["session_id"]), ("cx-1", "cx-1"))
        self.assertEqual([call[1] for call in calls], [None, "cx-1"])
        self.assertNotEqual(first_unit, current["physical"]["process_unit_id"])
        self.assertEqual(current["physical"]["receipts"]["prior_stopped"]["previous_process_unit_id"],
                         first_unit)
        self.assertEqual(len({row["id"] for row in l3.chat_history("k")}), 4)

    def test_model_scratch_is_below_the_host_written_result_marker(self):
        seen = {}
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        l3.chat_log("k", "user", f"remember token={secret}", message_id="prior-secret")
        (config.project_dir("k") / "private.pem").write_text(secret)

        def codex(_prompt, **kwargs):
            seen.update(kwargs)
            return result("cx-state")

        self.run_turn("coordinate", codex=codex)
        self.assertEqual(Path(seen["cwd"]).parent, config.project_dir("k") / "l3-codex-runtime" / "scratch")
        self.assertEqual(l3._result_path("k").parent.parent, Path(seen["cwd"]).parent.parent)  # noqa: SLF001
        roots = [Path(path) for path in seen["readable_roots"]]
        self.assertEqual(roots[1], config.project_path("k"))
        self.assertEqual(roots[0].parent, config.project_dir("k") / "l3-codex-runtime" / "context")
        projection = json.loads(roots[0].read_text())
        self.assertEqual(set(projection), {"version", "project", "generated_at", "recovery", "tasks", "recent_chat"})
        self.assertNotIn("claim", projection["recovery"])
        self.assertNotIn(secret, roots[0].read_text())
        self.assertIn("[REDACTED]", roots[0].read_text())
        self.assertNotIn(config.ROOT, roots)

    def test_context_projection_rejects_symlinked_parent_escape(self):
        outside = config.ROOT / "outside-context"; outside.mkdir()
        runtime = config.project_dir("k") / "l3-codex-runtime"
        runtime.symlink_to(outside, target_is_directory=True)
        with mock.patch.object(engines, "codex_exec") as codex:
            out = self.run_turn("do not escape")
        self.assertIn("runtime path escapes", out["error"])
        codex.assert_not_called()

    def test_all_runtime_families_reject_symlinked_parents_and_targets(self):
        runtime = config.project_dir("k") / "l3-codex-runtime"; runtime.mkdir()
        outside = config.ROOT / "outside-runtime"; outside.mkdir(exist_ok=True)
        for family in ("context", "spool", "results", "scratch"):
            link = runtime / family; link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(l3.L3OwnershipError, "runtime path escapes"):
                l3._runtime_path("k", family, "generation")  # noqa: SLF001
            link.unlink()
        scratch = runtime / "scratch"; scratch.mkdir(); (scratch / "generation").symlink_to(outside)
        with self.assertRaisesRegex(l3.L3OwnershipError, "runtime path escapes"):
            l3._runtime_path("k", "scratch", "generation")  # noqa: SLF001

    def test_codex_adapter_uses_and_preserves_predeclared_spools(self):
        answer, events = config.project_dir("k") / "answer", config.project_dir("k") / "events"
        S.atomic_write(answer, ""); S.atomic_write(events, '{"type":"host-header"}\n')

        def run(cmd, **kwargs):
            kwargs["answer_path"].write_text('{"message":"adapter","actions":[]}')
            with kwargs["event_spool"].open("a") as stream:
                stream.write('{"type":"thread.started","thread_id":"cx-adapter"}\n')
                stream.write('{"type":"turn.completed","usage":{"input_tokens":3}}\n')
            return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr="")

        with mock.patch.object(engines, "_run_codex_to_bounded_spools", side_effect=run), \
             mock.patch.object(engines, "read_bounded_codex_output",
                               wraps=engines.read_bounded_codex_output) as bounded:
            out = engines.codex_exec("prompt", cwd=config.project_dir("k"),
                                     answer_path=answer, event_spool=events)

        self.assertEqual((out["reported_session_id"], out["structured"]["message"]),
                         ("cx-adapter", "adapter"))
        self.assertEqual([(call.args[0], call.args[1]) for call in bounded.call_args_list],
                         [(events, engines.CODEX_EVENT_CAP), (answer, engines.CODEX_ANSWER_CAP)])
        self.assertTrue(answer.exists()); self.assertIn("host-header", events.read_text())

    def test_codex_adapter_never_stringifies_invalid_thread_ids(self):
        invalid = [7, {"id": "cx"}, ["cx"], "", "é" * 513]
        for index, thread_id in enumerate(invalid):
            answer = config.project_dir("k") / f"bad-answer-{index}"
            events = config.project_dir("k") / f"bad-events-{index}"
            S.atomic_write(answer, ""); S.atomic_write(events, '{"type":"host-header"}\n')

            def run(cmd, **kwargs):
                kwargs["answer_path"].write_text('{"message":"adapter","actions":[]}')
                with kwargs["event_spool"].open("a") as stream:
                    stream.write(json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n")
                    stream.write('{"type":"turn.completed","usage":{}}\n')
                return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr="")

            with mock.patch.object(engines, "_run_codex_to_bounded_spools", side_effect=run):
                out = engines.codex_exec("prompt", cwd=config.project_dir("k"),
                                         answer_path=answer, event_spool=events)
            self.assertIsNone(out["reported_session_id"])

    def test_codex_spool_caps_stop_the_producer_while_it_is_running(self):
        root = config.project_dir("k") / "cap-test"; root.mkdir()
        answer, events = root / "answer", root / "events"
        S.atomic_write(answer, ""); S.atomic_write(events, "header\n")
        with mock.patch.object(engines, "CODEX_EVENT_CAP", 128), \
             self.assertRaisesRegex(RuntimeError, "event spool exceeds 128"):
            engines._run_codex_to_bounded_spools(  # noqa: SLF001 - exact producer boundary
                [sys.executable, "-c", "import os,time; os.write(1,b'x'*8192); time.sleep(10)"],
                cwd=root, env=dict(os.environ), timeout=10, event_spool=events, answer_path=answer)
        self.assertLessEqual(events.stat().st_size, 128)

        S.atomic_write(events, "header\n"); S.atomic_write(answer, "")
        script = "from pathlib import Path; import sys,time; Path(sys.argv[1]).write_bytes(b'x'*8192); time.sleep(10)"
        with mock.patch.object(engines, "CODEX_ANSWER_CAP", 128), \
             self.assertRaisesRegex(RuntimeError, "answer exceeds 128"):
            engines._run_codex_to_bounded_spools(  # noqa: SLF001
                [sys.executable, "-c", script, str(answer)], cwd=root, env=dict(os.environ), timeout=10,
                event_spool=events, answer_path=answer)

    def test_bounded_adapter_failure_stops_and_proves_the_exact_inner_unit_empty(self):
        root = config.project_dir("k") / "inner-stop"; root.mkdir()
        answer, events = root / "answer", root / "events"
        with mock.patch.object(engines, "_run_codex_to_bounded_spools",
                               side_effect=RuntimeError("event spool exceeds cap")), \
             mock.patch.object(engines, "_stop_codex_unit") as stop, \
             mock.patch.object(engines, "_codex_unit_empty", return_value=True) as empty, \
             mock.patch.object(engines, "read_bounded_codex_output") as read, \
             self.assertRaisesRegex(RuntimeError, "event spool exceeds cap"):
            engines.codex_exec("prompt", cwd=root, contain=True,
                               answer_path=answer, event_spool=events)
        self.assertTrue(stop.call_args.args[0].startswith(engines.CODEX_SYSTEMD_PREFIX))
        empty.assert_called_with(stop.call_args.args[0]); read.assert_not_called()

    def test_managed_adapter_error_stops_the_outer_generation_before_failure_settlement(self):
        with mock.patch.object(engines, "stop_managed_unit", side_effect=self._observe) as stop:
            out = self.run_turn(codex=RuntimeError("bounded producer failed"))
        self.assertFalse(out["completed"]); self.assertIn("bounded producer failed", out["error"])
        self.assertEqual(stop.call_args.args[0],
                         l3.info("k")["current_turn"]["physical"]["process_unit_id"])

    def test_one_inert_action_is_applied_after_exact_result_and_empty_unit(self):
        action = {"type": "github_issue", "title": "sidecar", "text": "save sidecar", "labels": []}
        pending = [{"type": "github_issue", "id": "1234567890abcdef12345678", "pending_review": True}]
        with mock.patch("altitude.l3_actions.apply", return_value=pending) as apply:
            out = self.run_turn("save sidecar", codex=lambda *_a, **_k: result(actions=[action]))
        self.assertTrue(out["completed"])
        self.assertIn("Publication remains disabled until Phase 1C.4", out["text"])
        self.assertEqual(apply.call_args.kwargs["github_issue_source"], "save sidecar")
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "complete")

    def test_populated_unit_keeps_result_and_action_semantically_inert(self):
        applied = mock.Mock(return_value=[])
        def running_after_spawn(*args, **kwargs):
            process = self._spawn(*args, **kwargs)
            self.empty = False
            return process
        with mock.patch.object(engines, "spawn_managed_unit", side_effect=running_after_spawn), \
             mock.patch("altitude.l3_actions.apply", applied):
            pending = self.run_turn()
            self.assertFalse(pending["completed"]); applied.assert_not_called()
            self.empty = True
            completed = l3.reconcile("k", instance_id=INSTANCE)
        self.assertTrue(completed["completed"]); applied.assert_called_once()

    def test_crash_after_spawn_before_receipt_never_relaunches(self):
        calls = []

        def crash(_pid):
            calls.append("after-spawn")
            raise RuntimeError("daemon died after spawn")

        with self.assertRaisesRegex(RuntimeError, "daemon died"):
            self.run_turn(on_start=crash)
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "prior_stopped")
        completed = l3.reconcile("k", instance_id=INSTANCE)
        self.assertTrue(completed["completed"])
        self.assertEqual(calls, ["after-spawn"])
        self.assertEqual(len(self.transitions), 1)

    def test_planned_crash_continues_the_exact_turn_once(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(
            "k", "planned crash", "chat", self.choice(), inf, None, None, INSTANCE)  # noqa: SLF001
        inf["current_turn"] = op
        l3.save_info("k", inf)

        with mock.patch.object(engines, "codex_exec",
                               side_effect=self.spooled_provider(result())) as codex:
            out = l3.reconcile("k", instance_id=INSTANCE)

        self.assertTrue(out["completed"]); codex.assert_called_once()
        current = l3.info("k")["current_turn"]
        self.assertEqual(current["physical"]["stage"], "complete")
        history = l3.chat_history("k")
        self.assertEqual([(row["role"], row["text"]) for row in history],
                         [("user", "planned crash"), ("assistant", "answer")])
        self.assertEqual(len(self.transitions), 1)

    def test_replacement_instance_persists_handoff_before_crossing_launch(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(  # noqa: SLF001
            "k", "replacement", "chat", self.choice(), inf, None, None, "dead-instance")
        inf["current_turn"] = op; l3.save_info("k", inf)
        seen = {}

        def inspect_spawn(transition, command, **kwargs):
            seen["claim"] = l3.info("k")["current_turn"]["instance_claim"]
            return self._spawn(transition, command, **kwargs)

        with mock.patch.object(engines, "spawn_managed_unit", side_effect=inspect_spawn), \
             mock.patch.object(engines, "codex_exec", side_effect=self.spooled_provider(result())):
            out = l3.reconcile("k", instance_id="replacement-instance")

        self.assertTrue(out["completed"])
        self.assertEqual(seen["claim"]["instance_id"], "replacement-instance")
        self.assertEqual(seen["claim"]["revision"], 1)
        self.assertEqual(seen["claim"]["replacement"]["from_instance_id"], "dead-instance")

    def test_missing_instance_and_wrong_managed_child_claim_fail_before_provider(self):
        with mock.patch.object(l3, "_select") as select:
            with self.assertRaisesRegex(l3.L3OwnershipError, "service instance"):
                l3.turn("k", "offline")
        select.assert_not_called(); self.assertFalse(l3.info_path("k").exists())

        op = self.prior_stopped("wrong child")
        with mock.patch.object(engines, "codex_exec") as codex, \
             self.assertRaisesRegex(l3.L3OwnershipError, "instance claim"):
            l3._managed_codex("k", op["physical"]["transition_id"], "other-instance")  # noqa: SLF001
        codex.assert_not_called()

    def test_planned_to_prior_stopped_cas_elects_exactly_one_launcher(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(  # noqa: SLF001
            "k", "planned", "chat", self.choice(), inf, None, None, INSTANCE)
        inf["current_turn"] = op; l3.save_info("k", inf)
        barrier, launches, failures = threading.Barrier(2), [], []
        def run():
            try:
                barrier.wait()
                _prepared, winner = l3._prepare_launch("k", op, INSTANCE)  # noqa: SLF001
                launches.append(winner)
            except l3.L3OwnershipError as exc:
                failures.append(str(exc))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(launches.count(True), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "prior_stopped")

    def test_two_processes_cas_the_same_planned_launch_once(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(  # noqa: SLF001
            "k", "process race", "chat", self.choice(), inf, None, None, INSTANCE)
        inf["current_turn"] = op; l3.save_info("k", inf)
        gate = config.ROOT / "cas-go"; gate.unlink(missing_ok=True)
        code = (
            "import json,sys,time; from pathlib import Path; from unittest import mock; "
            "from altitude import engines,l3,state as S; ready,out,gate=map(Path,sys.argv[1:]); "
            "op=l3._owned_info('k')['current_turn']; ready.write_text('1'); "
            "\nwhile not gate.exists(): time.sleep(.005)\n"
            "obs={'process_unit_id':op['physical']['process_unit_id'],'load_state':'not-found',"
            "'active_state':'inactive','sub_state':'dead','control_group':'','population':'empty','empty':True}; "
            "\ntry:\n"
            f" with mock.patch.object(engines,'observe_managed_unit',return_value=obs): row=l3._prepare_launch('k',op,{INSTANCE!r})\n"
            " result={'winner':row[1]}\n"
            "except Exception as exc: result={'error':str(exc)}\n"
            "S.atomic_write(out,json.dumps(result))"
        )
        children, outputs = [], []
        for index in range(2):
            ready, out = config.ROOT / f"cas-ready-{index}", config.ROOT / f"cas-out-{index}"
            ready.unlink(missing_ok=True); out.unlink(missing_ok=True); outputs.append((ready, out))
            children.append(subprocess.Popen([sys.executable, "-c", code, str(ready), str(out), str(gate)],
                cwd=Path(__file__).parents[1], env={**os.environ, "ALTITUDE_HOME": str(config.ROOT)}))
        try:
            for _ in range(400):
                if all(ready.exists() for ready, _out in outputs): break
                time.sleep(.005)
            self.assertTrue(all(ready.exists() for ready, _out in outputs)); gate.write_text("go")
            for child in children: child.wait(timeout=5)
            rows = [json.loads(out.read_text()) for _ready, out in outputs]
            self.assertEqual(sum(row.get("winner") is True for row in rows), 1)
            self.assertEqual(sum("error" in row for row in rows), 1)
            self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "prior_stopped")
        finally:
            gate.write_text("go")
            for child in children:
                if child.poll() is None: child.kill(); child.wait()

    def test_periodic_reconcile_skips_when_process_local_reconcile_is_busy(self):
        guard = l3.lock("k"); guard.acquire()
        try:
            started = time.monotonic()
            self.assertIsNone(l3.reconcile("k", instance_id=INSTANCE, nonblocking=True))
            self.assertLess(time.monotonic() - started, .5)
        finally:
            guard.release()

    def test_tick_schedules_one_planned_turn_and_slow_delivery_without_blocking(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(  # noqa: SLF001
            "k", "timer-planned", "chat", self.choice(), inf, None, None, server._L3_INSTANCE_ID)  # noqa: SLF001
        inf["current_turn"] = op; l3.save_info("k", inf)
        entered, release, effects = threading.Event(), threading.Event(), []

        def slow_apply(*_args, **_kwargs):
            effects.append("apply"); entered.set(); release.wait(5)
            return []

        patches = (
            mock.patch.object(server.quota_codex, "refresh_if_due"),
            mock.patch.object(server, "drain_hook_faults"),
            mock.patch.object(server, "wake_recovery_l3"),
            mock.patch.object(server, "resume_pending_actions"),
            mock.patch.object(server.dispatch, "poll", return_value=[]),
            mock.patch.object(server, "resume_stranded_reports"),
            mock.patch.object(server.dispatch, "resume_due", return_value=[]),
            mock.patch.object(server, "dispatch_waiting"),
            mock.patch.object(server, "morning_digest"),
            mock.patch.object(S, "list_tasks", return_value=[]),
            mock.patch.object(engines, "codex_exec", side_effect=self.spooled_provider(result())),
            mock.patch.object(l3_actions, "apply", side_effect=slow_apply),
        )
        for patch in patches: patch.start()
        try:
            started = time.monotonic(); server.tick()
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(entered.wait(2)); self.assertEqual(len(self.transitions), 1)
            started = time.monotonic(); server.tick()
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(len(self.transitions), 1)
        finally:
            release.set()
            thread = server._bg.get("l3-reconcile:k")  # noqa: SLF001
            if thread: thread.join(5)
            for patch in reversed(patches): patch.stop()
        self.assertEqual(len(self.transitions), 1)
        self.assertEqual(effects, ["apply"])
        self.assertEqual(l3.info("k")["current_turn"]["delivery"], "complete")

    def test_bound_missing_marker_records_a_durable_failure(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(
            "k", "lost marker", "chat", self.choice(), inf, None, None, INSTANCE)  # noqa: SLF001
        inf["current_turn"] = op
        l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001
        op = l3._step("k", op, "prior_stopped", "spawned", {  # noqa: SLF001
            "process_unit_id": op["physical"]["process_unit_id"], "launched": True,
        })
        l3._step("k", op, "spawned", "bound", {  # noqa: SLF001
            "bound": True, "physical_worker_id": op["physical"]["process_unit_id"],
            "provider_session_id": "cx-lost",
        })

        out = l3.reconcile("k", instance_id=INSTANCE)

        self.assertFalse(out["completed"])
        self.assertIn("without a durable result marker", out["error"])
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "failed")

    def test_marker_written_during_empty_observation_is_not_overwritten(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(
            "k", "marker race", "chat", self.choice(), inf, None, None, INSTANCE)  # noqa: SLF001
        inf["current_turn"] = op
        l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001
        op = l3._step("k", op, "prior_stopped", "spawned", {  # noqa: SLF001
            "process_unit_id": op["physical"]["process_unit_id"], "launched": True,
        })
        marker = {"version": 1, "transition_id": op["physical"]["transition_id"],
                  "process_unit_id": op["physical"]["process_unit_id"],
                  "intent_digest": op["physical"]["intent_digest"],
                  "result": {"text": "won race", "structured": {"message": "won race", "actions": []}, "returncode": 0,
                             "usage": {}, "provider_session_id": "cx-race", "error": None}}

        def marker_then_empty(unit):
            S.write_json(l3._result_path("k", op), marker)  # noqa: SLF001
            return self._observe(unit)

        engines.observe_managed_unit.side_effect = marker_then_empty
        out = l3.reconcile("k", instance_id=INSTANCE)

        self.assertTrue(out["completed"])
        self.assertEqual(out["text"], "won race")
        self.assertEqual(S.read_json(l3._result_path("k"), None), marker)  # noqa: SLF001

    def test_fast_collected_process_without_marker_is_ownership_uncertain(self):
        self.run_child = False
        with self.assertRaisesRegex(RuntimeError, "crash"):
            self.run_turn(on_start=lambda _pid: (_ for _ in ()).throw(RuntimeError("crash")))
        replay = l3.reconcile("k", instance_id=INSTANCE)
        self.assertIn("ownership_uncertain", replay["error"])
        self.assertEqual(len(self.transitions), 1)

    def test_killed_managed_child_restarts_as_failed_without_relaunch(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(
            "k", "crash", "chat", self.choice(), inf, None, None, INSTANCE)  # noqa: SLF001
        inf["current_turn"] = op; l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001
        op = l3._step("k", op, "prior_stopped", "spawned", {  # noqa: SLF001
            "process_unit_id": op["physical"]["process_unit_id"], "launched": True,
        })
        code = (
            "import os,signal; from altitude import engines,l3; "
            "engines.codex_exec=lambda *a,**k: os.kill(os.getpid(),signal.SIGKILL); "
            f"l3._managed_codex('k','{op['physical']['transition_id']}','{INSTANCE}')"
        )
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1],
                               env={**os.environ, "ALTITUDE_HOME": str(config.ROOT)})
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertFalse(l3._result_path("k").exists())  # noqa: SLF001
        out = l3.reconcile("k", instance_id=INSTANCE)
        self.assertFalse(out["completed"]); self.assertIn("preterminal spool is invalid", out["error"])
        self.assertEqual(self.transitions, [], "crash reconciliation must not call the launch adapter")

    def test_kill_after_durable_provider_output_recovers_the_exact_spool(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(  # noqa: SLF001
            "k", "durable provider boundary", "chat", self.choice(), inf, None, None, INSTANCE)
        inf["current_turn"] = op; l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001
        op = l3._step("k", op, "prior_stopped", "spawned", {  # noqa: SLF001
            "process_unit_id": op["physical"]["process_unit_id"], "launched": True,
        })
        code = (
            "import json,os,signal; from altitude import l3,state as S; "
            "\ndef killed(*_a,**kw):\n"
            " S.atomic_write(kw['answer_path'],json.dumps({'message':'spooled answer','actions':[]}))\n"
            " with open(kw['event_spool'],'a') as f:\n"
            "  f.write(json.dumps({'type':'thread.started','thread_id':'cx-spooled'})+'\\n')\n"
            "  f.write(json.dumps({'type':'turn.completed','usage':{'input_tokens':321}})+'\\n')\n"
            "  f.flush(); os.fsync(f.fileno())\n"
            " S._fsync_directory(kw['event_spool'].parent)\n"
            " os.kill(os.getpid(),signal.SIGKILL)\n"
            "\nl3.engines.codex_exec=killed\n"
            f"l3._managed_codex('k','{op['physical']['transition_id']}','{INSTANCE}')\n"
        )
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1],
                               env={**os.environ, "ALTITUDE_HOME": str(config.ROOT)})
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertFalse(l3._result_path("k", op).exists())  # noqa: SLF001

        recovered = l3.reconcile("k", instance_id=INSTANCE)

        self.assertTrue(recovered["completed"])
        self.assertEqual((recovered["text"], recovered["session_id"]),
                         ("spooled answer", "cx-spooled"))
        self.assertEqual([row["role"] for row in l3.chat_history("k")], ["user", "assistant"])
        old_spool = tuple(l3._spool_paths("k", op).values())  # noqa: SLF001
        self.assertTrue(all(path.exists() for path in old_spool))
        self.run_turn("next")
        self.assertTrue(all(not path.exists() for path in old_spool))

    def test_parent_sigkill_before_spawn_receipt_recovers_completed_child_spool_once(self):
        op = self.prior_stopped("parent dies before spawned receipt")
        tag = op["physical"]["generation"]
        pid_path, release = config.ROOT / f"orphan-{tag}.pid", config.ROOT / f"orphan-{tag}.release"
        called, done = config.ROOT / f"orphan-{tag}.called", config.ROOT / f"orphan-{tag}.spooled"
        child_code = (
            "import json,os,signal,sys,time; from pathlib import Path; "
            "from altitude import l3,state as S; release,called,done=map(Path,sys.argv[1:4]); "
            "\ndef provider(*_a,**kw):\n"
            " called.write_text('1')\n"
            " while not release.exists(): time.sleep(.01)\n"
            " S.atomic_write(kw['answer_path'],json.dumps({'message':'orphan answer','actions':[]}))\n"
            " with open(kw['event_spool'],'a') as stream:\n"
            "  stream.write(json.dumps({'type':'thread.started','thread_id':'cx-orphan'})+'\\n')\n"
            "  stream.write(json.dumps({'type':'turn.completed','usage':{'input_tokens':7}})+'\\n')\n"
            "  stream.flush(); os.fsync(stream.fileno())\n"
            " S._fsync_directory(kw['event_spool'].parent)\n"
            " done.write_text('durable')\n"
            " os.kill(os.getpid(),signal.SIGKILL)\n"
            "\nl3.engines.codex_exec=provider\n"
            "l3._managed_codex('k',sys.argv[4],sys.argv[5])\n"
        )
        parent_code = (
            "import os,signal,subprocess,sys; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[4:]],"
            "cwd=sys.argv[2],env=os.environ.copy(),start_new_session=True); "
            "Path(sys.argv[3]).write_text(str(child.pid)); os.kill(os.getpid(),signal.SIGKILL)"
        )
        repo = str(Path(__file__).parents[1])
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code, child_code, repo, str(pid_path),
             str(release), str(called), str(done), op["physical"]["transition_id"], INSTANCE],
            cwd=repo, env={**os.environ, "ALTITUDE_HOME": str(config.ROOT)},
        )
        self.assertEqual(parent.wait(timeout=10), -signal.SIGKILL)
        for _ in range(500):
            if pid_path.exists() and called.exists():
                break
            threading.Event().wait(.01)
        self.assertTrue(pid_path.exists() and called.exists())
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "prior_stopped")
        release.write_text("finish")
        for _ in range(500):
            if done.exists():
                break
            threading.Event().wait(.01)
        self.assertTrue(done.exists())
        child_pid = int(pid_path.read_text())
        for _ in range(500):
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
            except FileNotFoundError:
                state = None
            if state in (None, "Z"):
                break
            threading.Event().wait(.01)
        self.assertIn(state, (None, "Z"), "managed child did not exit")
        self.assertFalse(l3._result_path("k", op).exists())  # noqa: SLF001
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "prior_stopped")

        recovered = l3.reconcile("k", instance_id=INSTANCE)

        self.assertTrue(recovered["completed"])
        self.assertEqual((recovered["text"], recovered["session_id"]),
                         ("orphan answer", "cx-orphan"))
        self.assertEqual(called.read_text(), "1")
        self.assertEqual(self.transitions, [], "spool recovery must not call the launch adapter")

    def test_prior_stopped_incomplete_spool_becomes_an_exact_inert_failure(self):
        op = self.prior_stopped()
        paths = l3._prepare_spool("k", op)  # noqa: SLF001
        S.atomic_write(paths["answer"], '{"message":"partial","actions":[]}')

        out = l3.reconcile("k", instance_id=INSTANCE)

        self.assertFalse(out["completed"])
        self.assertIn("no single successful turn", out["error"])
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["stage"], "failed")
        self.assertEqual(self.transitions, [])

    def test_prior_stopped_mismatched_spool_refuses_without_launch(self):
        op = self.prior_stopped()
        paths = l3._prepare_spool("k", op)  # noqa: SLF001
        S.atomic_write(paths["answer"], '{"message":"wrong","actions":[]}')
        rows = [
            {"type": "altitude.l3.spool", "version": 1, "transition_id": "wrong",
             "process_unit_id": op["physical"]["process_unit_id"],
             "intent_digest": op["physical"]["intent_digest"],
             "answer_id": f"l3-codex-runtime/spool/{op['physical']['generation']}.answer"},
            {"type": "thread.started", "thread_id": "cx-wrong"},
            {"type": "turn.completed", "usage": {}},
        ]
        S.atomic_write(paths["events"], "".join(json.dumps(row) + "\n" for row in rows))

        out = l3.reconcile("k", instance_id=INSTANCE)

        self.assertFalse(out["completed"])
        self.assertIn("no single successful turn", out["error"])
        self.assertEqual(self.transitions, [])

    def test_answer_and_event_spool_reads_are_bounded_before_parsing(self):
        op = self.prior_stopped()
        paths = l3._prepare_spool("k", op)  # noqa: SLF001
        S.atomic_write(paths["answer"], "x" * 17)
        with mock.patch.object(engines, "CODEX_ANSWER_CAP", 16), \
             self.assertRaisesRegex(l3.L3OwnershipError, "answer spool exceeds 16 bytes"):
            l3._spooled_result("k", op)  # noqa: SLF001

    def test_spool_rejects_duplicate_nonfinite_and_nonterminal_event_sequences(self):
        op = self.prior_stopped(); paths = l3._prepare_spool("k", op)  # noqa: SLF001
        header = paths["events"].read_text()
        cases = [
            ('{"message":"a","message":"b","actions":[]}', [
                '{"type":"thread.started","thread_id":"cx"}',
                '{"type":"turn.completed","usage":{}}']),
            ('{"message":"a","actions":[]}', [
                '{"type":"thread.started","thread_id":"cx"}',
                '{"type":"turn.completed","usage":{"input_tokens":NaN}}']),
            ('{"message":"a","actions":[]}', [
                '{"type":"thread.started","thread_id":"cx"}',
                '{"type":"thread.started","thread_id":"cx2"}',
                '{"type":"turn.completed","usage":{}}']),
            ('{"message":"a","actions":[]}', [
                '{"type":"thread.started","thread_id":"cx"}',
                '{"type":"turn.completed","usage":{}}',
                '{"type":"item.completed","item":{}}']),
        ]
        for answer, rows in cases:
            S.atomic_write(paths["answer"], answer)
            S.atomic_write(paths["events"], header + "\n".join(rows) + "\n")
            with self.assertRaises(l3.L3OwnershipError):
                l3._spooled_result("k", op)  # noqa: SLF001
        S.atomic_write(paths["answer"], "{}")
        with mock.patch.object(engines, "CODEX_EVENT_CAP", 16), \
             self.assertRaisesRegex(l3.L3OwnershipError, "event spool exceeds 16 bytes"):
            l3._spooled_result("k", op)  # noqa: SLF001

    def test_spool_rejects_non_string_empty_and_oversized_thread_ids(self):
        op = self.prior_stopped(); paths = l3._prepare_spool("k", op)  # noqa: SLF001
        header = paths["events"].read_text()
        for thread_id in (7, {"id": "cx"}, ["cx"], "", "é" * 513):
            S.atomic_write(paths["answer"], '{"message":"a","actions":[]}')
            rows = [{"type": "thread.started", "thread_id": thread_id},
                    {"type": "turn.completed", "usage": {}}]
            S.atomic_write(paths["events"], header + "".join(json.dumps(row) + "\n" for row in rows))
            with self.assertRaisesRegex(l3.L3OwnershipError, "no single successful turn"):
                l3._spooled_result("k", op)  # noqa: SLF001

    def test_result_marker_is_bounded_and_rejects_symlinks(self):
        op = self.prior_stopped()
        marker = l3._result_path("k", op)  # noqa: SLF001
        S.atomic_write(marker, "x" * 70000)
        with mock.patch.object(engines, "CODEX_ANSWER_CAP", 16), \
             self.assertRaisesRegex(l3.L3OwnershipError, "result marker exceeds"):
            l3._marker("k", op)  # noqa: SLF001
        marker.unlink(); target = config.project_dir("k") / "outside-marker"
        target.write_text("{}")
        marker.symlink_to(target)
        with self.assertRaisesRegex(l3.L3OwnershipError, "runtime path escapes"):
            l3._marker("k", op)  # noqa: SLF001

    def test_marker_writer_matches_reader_for_escaped_answer_and_bounded_session(self):
        op = self.prior_stopped()
        structured = {"message": "\\" * 1000, "actions": []}
        l3._write_marker("k", op, {"text": json.dumps(structured), "structured": structured,  # noqa: SLF001
            "returncode": 0, "usage": {}, "provider_session_id": "cx-ok", "error": None})
        marker, _ = l3._marker("k", op, required=True)  # noqa: SLF001
        self.assertEqual(marker["result"]["text"], "")
        self.assertEqual(marker["result"]["structured"], structured)

        l3._write_marker("k", op, {"text": "", "structured": structured, "returncode": 0,  # noqa: SLF001
            "usage": {}, "provider_session_id": "x" * (l3._PROVIDER_SESSION_CAP + 1), "error": None})  # noqa: SLF001
        marker, _ = l3._marker("k", op, required=True)  # noqa: SLF001
        self.assertIsNone(marker["result"]["provider_session_id"])
        self.assertIn("durable marker boundary", marker["result"]["error"])

    def test_invalid_provider_usage_becomes_one_inert_valid_marker(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        op, session = l3._new_operation(  # noqa: SLF001
            "k", "bad usage", "chat", self.choice(), inf, None, None, INSTANCE)
        inf["current_turn"] = op; l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001
        bad = result(); bad["usage"] = {"input_tokens": -1}

        def provider(*_args, **kwargs):
            S.atomic_write(kwargs["answer_path"], json.dumps(bad["structured"]))
            with kwargs["event_spool"].open("a") as stream:
                stream.write('{"type":"thread.started","thread_id":"cx-1"}\n')
                stream.write('{"type":"turn.completed","usage":{"input_tokens":-1}}\n')
            return bad

        with mock.patch.object(engines, "codex_exec", side_effect=provider):
            l3._managed_codex("k", op["physical"]["transition_id"], INSTANCE)  # noqa: SLF001
        marker, _observed = l3._marker("k", op, required=True)  # noqa: SLF001
        self.assertEqual(marker["result"]["usage"], {})
        self.assertIn("usage", marker["result"]["error"])

    def test_live_success_uses_the_same_strict_spool_sequence_as_recovery(self):
        cases = {
            "missing-thread": [{"type": "turn.completed", "usage": {}}],
            "duplicate-thread": [{"type": "thread.started", "thread_id": "cx-1"},
                                 {"type": "thread.started", "thread_id": "cx-2"},
                                 {"type": "turn.completed", "usage": {}}],
            "terminal-before-thread": [{"type": "turn.completed", "usage": {}},
                                       {"type": "thread.started", "thread_id": "cx-1"}],
            "post-terminal": [{"type": "thread.started", "thread_id": "cx-1"},
                              {"type": "turn.completed", "usage": {}},
                              {"type": "item.completed", "item": {}}],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                shutil.rmtree(config.project_dir("k")); config.project_dir("k").mkdir(parents=True)
                inf = l3._owned_info("k", new=True)  # noqa: SLF001
                op, session = l3._new_operation(  # noqa: SLF001
                    "k", name, "chat", self.choice(), inf, None, None, INSTANCE)
                inf["current_turn"] = op; l3.save_info("k", inf)
                op = l3._prior_stopped("k", op, session)  # noqa: SLF001

                def provider(*_args, **kwargs):
                    S.atomic_write(kwargs["answer_path"], '{"message":"a","actions":[]}')
                    with kwargs["event_spool"].open("a") as stream:
                        stream.write("".join(json.dumps(row) + "\n" for row in rows))
                    return result()

                with mock.patch.object(engines, "codex_exec", side_effect=provider):
                    l3._managed_codex("k", op["physical"]["transition_id"], INSTANCE)  # noqa: SLF001
                marker, _observed = l3._marker("k", op, required=True)  # noqa: SLF001
                self.assertEqual(marker["result"]["returncode"], 1)
                self.assertIn("no single successful turn", marker["result"]["error"])

    def test_interrupted_delivery_replays_only_keyed_effects(self):
        crash = mock.Mock(side_effect=[KeyboardInterrupt("crash"), []])
        with mock.patch("altitude.l3_actions.apply", crash), self.assertRaises(KeyboardInterrupt):
            self.run_turn()
        self.assertEqual(l3.info("k")["current_turn"]["delivery"], "applying")
        with mock.patch("altitude.l3_actions.apply", crash):
            out = l3.reconcile("k", instance_id=INSTANCE)
        self.assertTrue(out["completed"])
        history = l3.chat_history("k")
        self.assertEqual([row["role"] for row in history], ["user", "assistant"])
        self.assertEqual(len({row["id"] for row in history}), 2)

    def test_ambiguous_action_blocks_fresh_turn_without_a_b2_escape(self):
        action = {"type": "task_fyi", "slug": "missing", "text": "possibly appended"}
        with mock.patch.object(l3_actions, "_execute", side_effect=KeyboardInterrupt("killed")):
            with self.assertRaises(KeyboardInterrupt):
                self.run_turn("ambiguous", codex=lambda *_a, **_k: result(actions=[action]))
        current = l3.info("k")["current_turn"]
        self.assertEqual(current["delivery"], "applying")
        action_id = hashlib.sha256(
            f"{current['physical']['intent_digest']}\n{l3._marker('k', current, required=True)[1]['sha256']}".encode()
        ).hexdigest()  # noqa: SLF001

        with mock.patch.object(l3_actions, "_execute") as execute:
            held = self.run_turn("must not become a new action")
        execute.assert_not_called()
        self.assertIn("unrelated durable turn pending", held["error"])
        self.assertEqual(l3.info("k")["current_turn"]["physical"]["transition_id"],
                         current["physical"]["transition_id"])
        self.assertEqual(S.read_json(l3_actions._journal_path("k", action_id))["status"], "applying")  # noqa: SLF001

        self.assertFalse(hasattr(l3_actions, "resolve_reconciliation"))
        self.assertEqual(l3.info("k")["current_turn"]["delivery"], "applying")

    def test_stale_recovery_result_cannot_apply_actions(self):
        identity = self.recovery_identity()
        checks = iter([True, True, True, False, False])
        precheck = lambda: next(checks, False)
        with mock.patch("altitude.l3_actions.apply") as apply:
            out = self.run_turn(
                recovery_identity=identity,
                precheck=precheck,
            )
        self.assertFalse(out["completed"])
        self.assertIn("recovery episode", out["error"])
        apply.assert_not_called()
        physical = l3.info("k")["current_turn"]["physical"]
        self.assertEqual((physical["recovery_episode_id"], physical["recovery_permit_revision"]),
                         (identity["episode_id"], identity["permit_revision"]))

    def test_managed_recovery_child_rechecks_before_provider_message(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        identity = self.recovery_identity()
        op, session = l3._new_operation(  # noqa: SLF001
            "k", "recovery message", "recovery", self.choice(), inf, None, identity, INSTANCE)
        inf["current_turn"] = op
        l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001

        with mock.patch.object(recovery, "l3_attention_is_current", return_value=False), \
             mock.patch.object(engines, "codex_exec") as codex:
            l3._managed_codex("k", op["physical"]["transition_id"], INSTANCE)  # noqa: SLF001

        codex.assert_not_called()
        marker = S.read_json(l3._result_path("k"), {})  # noqa: SLF001
        self.assertIn("recovery episode", marker["result"]["error"])

    def test_managed_recovery_child_rechecks_after_provider_before_result(self):
        inf = l3._owned_info("k", new=True)  # noqa: SLF001
        identity = self.recovery_identity()
        op, session = l3._new_operation(  # noqa: SLF001
            "k", "recovery result", "recovery", self.choice(), inf, None, identity, INSTANCE)
        inf["current_turn"] = op; l3.save_info("k", inf)
        op = l3._prior_stopped("k", op, session)  # noqa: SLF001

        with mock.patch.object(recovery, "l3_attention_is_current", side_effect=[True, False]), \
             mock.patch.object(engines, "codex_exec", return_value=result()) as codex:
            l3._managed_codex("k", op["physical"]["transition_id"], INSTANCE)  # noqa: SLF001

        codex.assert_called_once()
        marker = S.read_json(l3._result_path("k"), {})  # noqa: SLF001
        self.assertEqual(marker["result"]["text"], "")
        self.assertIn("recovery episode", marker["result"]["error"])

    def test_delivery_failure_still_binds_every_later_output_to_the_receipt(self):
        with mock.patch("altitude.l3_actions.apply", side_effect=l3_actions.L3ActionError("refused")):
            out = self.run_turn()
        self.assertFalse(out["completed"])
        current = l3.info("k")["current_turn"]
        self.assertEqual(current["delivery"], "failed")
        marker = S.read_json(l3._result_path("k"), {})  # noqa: SLF001
        marker["result"]["text"] = "changed after settlement"
        S.write_json(l3._result_path("k"), marker)  # noqa: SLF001
        with self.assertRaisesRegex(l3.L3OwnershipError, "settled L3 result changed"):
            l3._output("k", current)  # noqa: SLF001

    def test_corrupt_delivery_replacement_receipt_fails_closed(self):
        self.run_turn()
        valid = l3.info("k")["current_turn"]
        manager = {"unit": "altitude.service", "invocation_id": "new", "main_pid": 222,
                   "active_state": "active", "sub_state": "running", "control_group": "/service",
                   "population": "populated"}
        for change in ("self", "blank"):
            row = json.loads(json.dumps(valid)); row["delivery_claim"] = {
                "instance_id": "altd:222:new", "revision": 1, "claimed_at": S.now(),
                "replacement": {"from_instance_id": "altd:222:new" if change == "self" else INSTANCE,
                                "to_instance_id": "altd:222:new", "prior_revision": 0, "at": S.now(),
                                "manager": {**manager, **({"control_group": ""} if change == "blank" else {})}}}
            S.write_json(l3.info_path("k"), {**l3.info("k"), "current_turn": row})
            with self.assertRaisesRegex(l3.L3OwnershipError, "replacement receipt"):
                l3._owned_info("k")  # noqa: SLF001
            S.write_json(l3.info_path("k"), {**l3.info("k"), "current_turn": valid})

    def test_failed_unit_is_the_next_turns_exact_prior_owner(self):
        with mock.patch.object(l3_actions, "apply", side_effect=l3_actions.L3ActionError("refused")):
            failed = self.run_turn(codex=lambda *_a, **_k: result("cx-failed"))
        failed_marker = l3._result_path("k")  # noqa: SLF001
        failed_unit = l3.info("k")["current_turn"]["physical"]["process_unit_id"]
        self.assertFalse(failed["completed"])

        completed = self.run_turn("retry", codex=lambda *_a, **_k: result("cx-failed"))
        current = l3.info("k")["current_turn"]

        self.assertTrue(completed["completed"])
        self.assertEqual(current["physical"]["receipts"]["prior_stopped"]["previous_process_unit_id"],
                         failed_unit)
        self.assertNotEqual(failed_marker, l3._result_path("k"))  # noqa: SLF001

    def test_local_delivery_refusal_preserves_the_exact_provider_thread(self):
        with mock.patch.object(l3_actions, "apply", side_effect=l3_actions.L3ActionError("refused")):
            refused = self.run_turn(codex=lambda *_a, **_k: result("cx-refused"))
        self.assertFalse(refused["completed"])
        calls = []
        def resumed(_prompt, **kwargs):
            calls.append(kwargs.get("resume"))
            return result("cx-refused")
        followup = self.run_turn("follow up", codex=resumed)
        self.assertTrue(followup["completed"])
        self.assertEqual(calls, ["cx-refused"])

    def test_public_projection_never_exposes_turn_authority(self):
        l3.save_info("k", {
            "l3_ownership_version": 1, "session_id": "cx-public", "turns": 3,
            "context_percent": 25.0, "current_turn": {"request": {"body": "secret prompt"}},
            "recovery_claim": "secret claim", "sessions": {"codex": {"private": "authority"}},
        })

        self.assertEqual(l3.public_info("k"), {
            "session_id": "cx-public", "turns": 3, "context_percent": 25.0,
        })
        server_source = Path(l3.__file__).with_name("server.py").read_text()
        self.assertNotIn("l3.info(", server_source)

    def test_foreign_project_physical_record_is_rejected(self):
        projects = config.load_projects()
        projects["other"] = {"name": "other", "path": str(_TMP / "repo")}
        config.save_projects(projects)
        config.project_dir("other").mkdir(parents=True, exist_ok=True)
        inf = l3._owned_info("other", new=True)  # noqa: SLF001
        op, _session = l3._new_operation(
            "other", "foreign", "chat", self.choice(), inf, None, None, INSTANCE)  # noqa: SLF001
        l3.save_info("k", {"l3_ownership_version": 1, "current_turn": op})

        with self.assertRaisesRegex(l3.L3OwnershipError, "different project"):
            l3._owned_info("k")  # noqa: SLF001

    def test_non_codex_and_unknown_legacy_ownership_fail_before_transport(self):
        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "codex_exec") as codex:
            held = l3.turn("k", "no", instance_id=INSTANCE)
        self.assertIn("engine hold", held["error"]); codex.assert_not_called()
        self.assertFalse(l3.info_path("k").exists())

        l3.save_info("k", {"pid": 123, "sessions": {}})
        with mock.patch.object(engines, "codex_exec") as codex:
            blocked = l3.turn("k", "no", instance_id=INSTANCE)
        self.assertIn("unknown legacy L3 ownership", blocked["error"]); codex.assert_not_called()

    def test_cli_cannot_create_or_reset_an_offline_l3_owner(self):
        alt = Path(__file__).parents[1] / "bin" / "alt"
        env = {**os.environ, "ALTITUDE_HOME": str(config.ROOT)}
        for command in (("chat", "offline"), ("l3-reset",)):
            proc = subprocess.run([str(alt), "-p", "k", *command], capture_output=True, text=True, env=env)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("running service", proc.stderr)
        self.assertFalse(l3.info_path("k").exists())

    def test_resume_thread_and_marker_tampering_fail_closed(self):
        prior = engines.deterministic_process_unit("l3", "k", "prior")
        l3.save_info("k", {"l3_ownership_version": 1, "sessions": {"codex": {
                           "session_id": "cx-1", "process_unit_id": prior}},
                           "engine_last": "codex"})
        out = self.run_turn(codex=lambda *_a, **_k: result("cx-2"))
        self.assertFalse(out["completed"]); self.assertIn("different thread", out["error"])
        marker = S.read_json(l3._result_path("k"), {})  # noqa: SLF001
        marker["intent_digest"] = "0" * 64
        S.write_json(l3._result_path("k"), marker)  # noqa: SLF001
        replay = l3.reconcile("k", instance_id=INSTANCE)
        self.assertIn("does not match", replay["error"])


if __name__ == "__main__":
    unittest.main()
