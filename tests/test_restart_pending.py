"""Merged backend/web changes activate at the quiet point, with an early restart only when nothing runs."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, server, state as S, tasks as T


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
        task["state"] = "running"; S.save_task(self.project, task)

        status = server.restart_status()
        self.assertEqual(status["files"], ["bin/alt"])
        self.assertIn(f"{self.project}/{task['slug']}", status["waiting_for"])

        task["state"] = "queued"; S.save_task(self.project, task)
        self.assertNotIn(f"{self.project}/{task['slug']}", server.restart_status()["waiting_for"])

    def test_restart_runs_the_operator_script_as_a_transient_user_unit(self):
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            out = server.restart_service()
        cmd = run.call_args.args[0]
        self.assertTrue(out["unit"].startswith("altitude-restart-"))
        self.assertIn("--user", cmd)
        self.assertIn(f"--unit={out['unit']}", cmd)
        self.assertEqual(cmd[-1], str(config.REPO / "scripts" / "restart_altitude.py"))
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="", stderr="no bus")):
            with self.assertRaisesRegex(RuntimeError, "no bus"):
                server.restart_service()

    def test_web_only_pending_work_uses_the_quiet_point_automatic_restart(self):
        S.write_json(self.flag, {"since": "2026-09-03T08:00:00+00:00", "head": "abc",
                                 "files": ["web/src/routes/Chat.tsx"]})
        with mock.patch.object(server, "restart_service", return_value={"ok": True, "unit": "altitude-restart-x"}) as restart:
            server.auto_restart()
            server.auto_restart()  # the unit is still building and swapping the bundle: no second request
        self.assertEqual(restart.call_count, 1)
        pend = S.read_json(self.flag)
        self.assertEqual(pend["unit"], "altitude-restart-x"); self.assertIn("requested_at", pend)

    def test_a_pending_restart_waits_for_work_and_holds_new_dispatches(self):
        S.write_json(self.flag, {"since": "2026-09-03T08:00:00+00:00", "head": "abc", "files": ["bin/alt"]})
        busy = T.new(self.project, "keeps altd busy", "request", actor="burak")
        busy["state"] = "running"; S.save_task(self.project, busy)
        T.new(self.project, "waits its turn", "request", actor="burak")
        with mock.patch.object(server, "restart_service") as restart, mock.patch.object(server.dispatch, "run") as run:
            server.auto_restart(); server.dispatch_waiting(self.project)
        restart.assert_not_called(); run.assert_not_called()
        busy["state"] = "done"; S.save_task(self.project, busy)
        with mock.patch.object(server, "restart_service", return_value={"ok": True, "unit": "u"}) as restart:
            server.auto_restart()
        restart.assert_called_once()

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
