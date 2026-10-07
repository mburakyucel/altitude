"""Candidate browser stages preserve shared configuration, failures and disposable evidence."""
import json
import os
import subprocess
import unittest
import sys
import time
from unittest import mock

from scripts import validate_ui as ui
from tests.support import AltitudeCase


class TestValidateUI(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.web = self.tmp / "web"
        (self.web / "e2e").mkdir(parents=True)
        (self.web / "package.json").write_text(json.dumps({"packageManager": "pnpm@10.34.5"}))
        (self.web / "e2e" / "project-menu.pw.ts").touch()
        self.results = self.tmp / "results"
        self.results.mkdir()
        self.patch(ui, "__file__", str(self.tmp / "scripts" / "validate_ui.py"))
        self.patch(ui.platform, "validation_in_container", lambda: True)
        self.patch(ui.platform, "host_identity", lambda: "fixture host")
        self.patch(ui.shutil, "which", lambda name: name)
        self.env = mock.patch.dict(os.environ, {"ALTITUDE_VALIDATION": "1", "VALIDATION_RESULTS": str(self.results),
                                               "HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp)})
        self.env.start(); self.addCleanup(self.env.stop)

    def launch(self, statuses):
        def start(command, **kwargs):
            if "ALTITUDE_UI_RESULTS" in kwargs["env"]:
                target = self.results / ("preflight" if "browser-preflight.pw.ts" in command else "journeys")
                target.mkdir(exist_ok=True)
                (target / "receipt").write_text("fictional")
            proc = mock.Mock(pid=54321)
            proc.wait.side_effect = [next(statuses)]
            return proc
        return mock.patch.object(ui.subprocess, "Popen", side_effect=start)

    def evidence(self):
        return json.loads((self.results / "ui-validation.json").read_text())

    def test_only_existing_spec_files_can_select_journeys(self):
        for args in (["--list"], ["--config", "playwright.config.ts"], ["../project-menu.pw.ts"], ["missing.pw.ts"]):
            with self.subTest(args=args), mock.patch.object(ui.subprocess, "Popen") as launch:
                with self.assertRaises(ValueError):
                    ui.validate(args)
                launch.assert_not_called()

    def test_preflight_failure_stops_before_journeys_and_retains_configuration_and_report(self):
        with self.launch(iter([0, 0, 0, 7])) as launch:
            self.assertEqual(ui.validate(["project-menu.pw.ts"]), 7)
        self.assertEqual(launch.call_count, 4)
        row = self.evidence()
        self.assertFalse(row["passed"])
        self.assertEqual(row["stages"][-1]["exit"], 7)
        self.assertEqual(row["configuration"], "playwright.validation.config.ts")
        self.assertEqual((self.results / "preflight" / "receipt").read_text(), "fictional")

    def test_stage_timeout_terminates_owned_group_before_reaping_and_marks_failure(self):
        proc = mock.Mock(pid=54321)
        proc.wait.side_effect = [subprocess.TimeoutExpired("fixture", 600), -9]
        with mock.patch.object(ui.subprocess, "Popen", return_value=proc), mock.patch.object(ui.os, "killpg") as kill:
            self.assertEqual(ui.validate([]), 124)
        kill.assert_called_once_with(proc.pid, ui.signal.SIGKILL)
        self.assertEqual(proc.wait.call_args.kwargs, {"timeout": 10})
        self.assertFalse(self.evidence()["passed"])

    def test_mac_probe_failure_stops_before_install_or_browser(self):
        self.patch(ui.platform, "validation_in_container", lambda: False)
        self.patch(ui.platform, "validation_browser_probe", lambda work: {"passed": False, "checks": []})
        with mock.patch.object(ui.subprocess, "Popen") as launch:
            self.assertEqual(ui.validate([]), 1)
        launch.assert_not_called()
        self.assertFalse(self.evidence()["passed"])

    def test_success_keeps_preflight_and_journeys_separate_and_forces_full_browser(self):
        os.environ["ALTITUDE_UI_HEADLESS_SHELL"] = "1"
        with self.launch(iter([0, 0, 0, 0, 0])) as launch:
            self.assertEqual(ui.validate(["project-menu.pw.ts"]), 0)
        self.assertTrue(self.evidence()["passed"])
        self.assertTrue((self.results / "journeys" / "receipt").is_file())
        self.assertEqual(launch.call_args.kwargs["env"]["ALTITUDE_UI_HEADLESS_SHELL"], "0")
        self.assertTrue((self.results / "preflight" / "receipt").is_file())
        args = launch.call_args.args[0]
        self.assertEqual(args[-3:], ["--config", "playwright.validation.config.ts", "project-menu.pw.ts"])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_actual_timeout_prevents_owned_grandchild_from_writing_later(self):
        marker = self.tmp / 'late-child'
        child = f"import time; from pathlib import Path; time.sleep(0.8); Path({str(marker)!r}).touch()"
        leader = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
        popen = subprocess.Popen
        def start(command, **kwargs):
            proc = popen([sys.executable, '-c', leader], **kwargs)
            wait = proc.wait
            proc.wait = lambda timeout=None: wait(timeout=min(timeout, 0.2))
            return proc
        with mock.patch.object(ui.subprocess, "Popen", side_effect=start):
            self.assertEqual(ui.validate([]), 124)
        time.sleep(1)
        self.assertFalse(marker.exists(), "the timeout kills descendants, not just the package-manager wrapper")



if __name__ == "__main__":
    unittest.main()
