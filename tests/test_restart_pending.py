"""Decision 11: running workers do not hold activation; only short daemon work does."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, l3, server, state as S, tasks as T


class TestRestartPending(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.flag = config.MONITOR_DIR / dispatch.RESTART_PENDING

    def test_nothing_pending_means_no_banner(self):
        self.assertIsNone(server.restart_status())

    def test_pending_flag_lists_what_the_restart_waits_for(self):
        S.write_json(self.flag, {"since": "2026-09-02T05:12:48+00:00", "head": "97e1197", "files": ["bin/alt"]})
        task = T.new(self.project, "keeps altd busy", "request", actor="burak")
        task["state"] = "running"; task["dispatching"] = S.now(); S.save_task(self.project, task)

        status = server.restart_status()
        self.assertEqual(status["files"], ["bin/alt"])
        self.assertIn(f"{self.project}/{task['slug']}", status["waiting_for"])

        task["dispatching"] = None; S.save_task(self.project, task)
        self.assertNotIn(f"{self.project}/{task['slug']}", server.restart_status()["waiting_for"])
        task["resume_claim"] = {"id": "launching"}; S.save_task(self.project, task)
        self.assertIn(f"{self.project}/{task['slug']}", server.restart_status()["waiting_for"])

    def test_restart_runs_the_operator_script_as_a_transient_user_unit(self):
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            out = server.restart_service()
        cmd = run.call_args.args[0]
        self.assertTrue(out["unit"].startswith("altitude-restart-"))
        self.assertIn("--user", cmd)
        self.assertIn(f"--unit={out['unit']}", cmd)
        self.assertEqual(cmd[-1], str(config.REPO / "scripts" / "restart_altitude.py"))
        self.flag.unlink()
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="", stderr="no bus")):
            with self.assertRaisesRegex(RuntimeError, "no bus"):
                server.restart_service()

    def test_web_only_pending_work_uses_the_quiet_point_automatic_restart(self):
        S.write_json(self.flag, {"since": "2026-09-03T08:00:00+00:00", "head": "abc",
                                 "files": ["web/src/routes/Chat.tsx"]})
        with mock.patch.object(server, "_request_restart_unit", return_value={"ok": True, "unit": "altitude-restart-x"}) as restart:
            server.auto_restart()
            server.auto_restart()  # the unit is still building and swapping the bundle: no second request
        self.assertEqual(restart.call_count, 1)
        pend = S.read_json(self.flag)
        self.assertEqual(pend["unit"], "altitude-restart-x"); self.assertIn("requested_at", pend)

    def test_decision_11_running_workers_allow_activation_and_pending_activation_allows_dispatch(self):
        S.write_json(self.flag, {"since": "2026-09-03T08:00:00+00:00", "head": "abc", "files": ["bin/alt"]})
        busy = T.new(self.project, "keeps altd busy", "request", actor="burak")
        busy["state"] = "running"; S.save_task(self.project, busy)
        T.new(self.project, "waits its turn", "request", actor="burak")
        self.register(self.project, wip=8)
        with mock.patch.object(server.dispatch, "run", return_value={"attempt": 1}) as run:
            server.dispatch_waiting(self.project)
        run.assert_called_once()
        self.assertEqual(server.restart_status()["waiting_for"], [])  # banner offers Restart while L2 runs
        with mock.patch.object(server, "_request_restart_unit", return_value={"ok": True, "unit": "u"}) as restart:
            server.auto_restart()
        restart.assert_called_once()
        with mock.patch.object(server.dispatch, "run") as run:
            server.dispatch_waiting(self.project)
        run.assert_not_called()
        self.flag.unlink()  # replacement daemon is ready
        with mock.patch.object(server.dispatch, "run", return_value={"attempt": 1}) as run:
            server.dispatch_waiting(self.project)
        run.assert_called_once()

    def test_restart_window_holds_direct_dispatch_resume_and_l3_without_consuming_queue(self):
        S.write_json(self.flag, {"requested_at": S.now(), "unit": "u"})
        task = T.new(self.project, "queued", "request", actor="burak")
        with self.assertRaisesRegex(T.TransitionError, "restarting; retry"):
            dispatch.run(self.project, task["slug"])
        self.assertIn("held", dispatch.resume(self.project, task["slug"]))
        result = l3.turn(self.project, "continue after activation")
        self.assertIn("queued", result)
        self.assertIsNone(l3.deliver_queued(self.project))
        self.assertEqual(len(l3.queued(self.project)), 1)
        self.assertTrue(l3.drop_queued(self.project, result["queued"]["id"]))

    def test_dispatch_l3_and_report_windows_fence_restart_even_before_markers_are_saved(self):
        S.write_json(self.flag, {"since": S.now(), "files": ["altitude/server.py"]})
        with config.restart_lock() as entered:
            self.assertTrue(entered)
            self.assertTrue(server.restart_status()["waiting_for"])
            with self.assertRaises(server.RestartBusy):
                server.restart_service()
        self.assertEqual(server.restart_status()["waiting_for"], [])

    def test_report_verification_holds_restart_but_waiting_report_does_not(self):
        S.write_json(self.flag, {"since": S.now(), "files": ["altitude/server.py"]})
        task = T.new(self.project, "reported", "request", actor="burak")
        task["state"] = "reported"; S.save_task(self.project, task)
        self.assertEqual(server.restart_status()["waiting_for"], [])
        with mock.patch.object(server, "_on_l2_finished", side_effect=lambda *_: self.assertTrue(
                server.restart_status()["waiting_for"])) as verify:
            server.on_l2_finished(self.project, {"task": task})
        verify.assert_called_once()

    def test_manual_request_closes_gate_before_unit_launch_and_is_idempotent(self):
        S.write_json(self.flag, {"since": S.now(), "files": ["altitude/server.py"]})
        def launch():
            with config.restart_lock() as ready:
                self.assertFalse(ready)
            return {"ok": True, "unit": "manual"}
        with mock.patch.object(server, "_request_restart_unit", side_effect=launch) as unit:
            server.restart_service()
            server.restart_service()
        unit.assert_called_once()
        self.assertTrue(config.restart_in_progress())

    def test_stranded_reports_wait_without_duplicating_l3_turns_during_restart(self):
        S.write_json(self.flag, {"requested_at": S.now(), "unit": "u"})
        task = T.new(self.project, "stranded", "request", actor="burak")
        with mock.patch.object(server, "_report_turn") as report:
            server.report_turn(self.project, task, {})
            server.report_turn(self.project, task, {})
            report.assert_not_called()
            self.assertEqual(l3.queued(self.project), [])
            self.flag.unlink()
            server.report_turn(self.project, task, {})
        report.assert_called_once()

    def test_finishing_worker_cannot_reopen_restart_window_with_another_fast_forward(self):
        pending = {"requested_at": S.now(), "unit": "u", "files": ["bin/alt"]}
        S.write_json(self.flag, pending)
        with mock.patch.object(dispatch, "_self_deploy_fast_forward", return_value=[]) as pull:
            self.assertTrue(dispatch.self_deploy_fast_forward(self.project)[0].startswith("deferred "))
            pull.assert_not_called()
            self.assertEqual(S.read_json(self.flag), pending)
            self.flag.unlink()
            self.assertEqual(dispatch.self_deploy_fast_forward(self.project), [])
        pull.assert_called_once()

    def test_a_restart_that_never_happened_is_a_fault_and_lifts_the_hold(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        S.write_json(self.flag, {"since": "2026-09-03T08:00:00+00:00", "head": "abc", "files": ["bin/alt"],
                                 "requested_at": old, "unit": "altitude-restart-old"})
        T.new(self.project, "waits its turn", "request", actor="burak")
        with mock.patch.object(server.incidents, "system_fault") as fault, mock.patch.object(server, "restart_service") as restart:
            server.auto_restart()
        restart.assert_not_called(); fault.assert_called_once()
        self.assertIn("altitude-restart-old", fault.call_args.args[1])
        self.assertTrue(S.read_json(self.flag)["failed"])
        with mock.patch.object(server.dispatch, "run", return_value={"attempt": 1, "agent": None}) as run:
            server.dispatch_waiting(self.project)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
