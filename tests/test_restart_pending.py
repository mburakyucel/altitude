"""A merged Altitude change marks a restart pending; the page offers the restart only when nothing is running."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-restart-pending-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, server, state as S, tasks as T  # noqa: E402

PROJECT = "restartpending"


class TestRestartPending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects()
        projects[PROJECT] = {"name": PROJECT, "path": _TMP}
        config.save_projects(projects)

    def setUp(self):
        self.flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        self.flag.unlink(missing_ok=True)

    def test_nothing_pending_means_no_banner(self):
        self.assertIsNone(server.restart_status())

    def test_pending_flag_lists_what_the_restart_waits_for(self):
        S.write_json(self.flag, {"since": "2026-09-02T05:12:48+00:00", "head": "97e1197", "files": ["bin/alt"]})
        task = T.new(PROJECT, "keeps altd busy", "request", actor="burak")
        task["state"] = "running"; S.save_task(PROJECT, task)
        try:
            status = server.restart_status()
            self.assertEqual(status["files"], ["bin/alt"])
            self.assertIn(f"{PROJECT}/{task['slug']}", status["waiting_for"])
            task["state"] = "queued"; S.save_task(PROJECT, task)
            self.assertNotIn(f"{PROJECT}/{task['slug']}", server.restart_status()["waiting_for"])
        finally:
            T.reject(PROJECT, task["slug"], "test over", actor="burak")

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


if __name__ == "__main__":
    unittest.main()
