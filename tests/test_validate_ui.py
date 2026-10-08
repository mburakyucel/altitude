"""Candidate browser stages preserve shared configuration, failures and disposable evidence."""
import json
import os
import subprocess
import unittest
import sys
import time
import errno
from types import SimpleNamespace
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

    def test_capture_records_only_the_selected_journeys_stage(self):
        with mock.patch.object(ui.subprocess, "Popen") as launch, self.assertRaisesRegex(ValueError, "UI_ARGS"):
            ui.validate([], capture=True)
        launch.assert_not_called()
        for capture in (False, True):
            seen = {}
            with self.subTest(capture=capture), self.launch(iter([0] * 5)) as launch:
                start = launch.side_effect
                launch.side_effect = lambda command, **kwargs: (
                    seen.__setitem__(command[-1], kwargs["env"].get("ALTITUDE_UI_CAPTURE")), start(command, **kwargs))[1]
                self.assertEqual(ui.validate(["project-menu.pw.ts"], capture=capture), 0)
                self.assertEqual(seen["project-menu.pw.ts"], str(self.results / "captures") if capture else None)
                self.assertEqual([name for name, folder in seen.items() if folder], ["project-menu.pw.ts"] if capture else [])

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

    def test_timeout_exit_race_preserves_stage_record(self):
        proc = mock.Mock(pid=54321)
        proc.wait.side_effect = [subprocess.TimeoutExpired("fixture", 600), 0]
        with mock.patch.object(ui.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ui.os, "killpg", side_effect=ProcessLookupError()):
            self.assertEqual(ui.validate([]), 124)
        self.assertEqual(self.evidence()["stages"][-1]["exit"], 124)

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

    def test_native_temp_override_uses_only_the_owned_folder_and_is_absent_on_linux(self):
        with mock.patch.object(ui.platform, '_darwin', return_value=True):
            self.assertEqual(ui.platform.validation_browser_environment(self.tmp), {'MAC_CHROMIUM_TMPDIR': str(self.tmp)})
        with mock.patch.object(ui.platform, '_darwin', return_value=False):
            self.assertEqual(ui.platform.validation_browser_environment(self.tmp), {})

    def test_create_probe_distinguishes_denied_unavailable_and_allowed_even_when_cleanup_fails(self):
        real_open, real_kill, real_unlink = os.open, os.kill, ui.Path.unlink
        ancestor = 999999
        info = SimpleNamespace(ppid=ancestor, uid=os.getuid(), start_sec=1, start_usec=0)
        def kill(pid, sig):
            if pid == ancestor:
                raise PermissionError(errno.EPERM, "fictional outside-sandbox ancestor")
            return real_kill(pid, sig)
        for outcome in ("denied", "unavailable", "allowed-cleanup-failure"):
            def opened(path, flags, *args, **kwargs):
                if str(path) in ('/fictional-operator-home', '/private/tmp'):
                    raise PermissionError(errno.EPERM, 'fictional read denial')
                if ui.Path(path).name.startswith('browser-probe-') and ui.Path(path).parent == self.web.parent and flags & os.O_CREAT:
                    if outcome == 'denied':
                        raise PermissionError(errno.EPERM, 'fictional write denial')
                    if outcome == 'unavailable':
                        raise FileNotFoundError(errno.ENOENT, 'fictional unavailable target')
                return real_open(path, flags, *args, **kwargs)
            def unlink(path, **kwargs):
                if path.name.startswith('browser-probe-') and path.parent == self.web.parent:
                    raise PermissionError(errno.EPERM, 'fictional cleanup denial after allowed create')
                return real_unlink(path, **kwargs)
            with self.subTest(outcome=outcome), mock.patch('pwd.getpwuid', return_value=SimpleNamespace(pw_dir='/fictional-operator-home')), \
                 mock.patch.object(ui.platform, '_bsd', return_value=info), \
                 mock.patch.object(ui.platform, '_coalition_of', return_value=123), \
                 mock.patch.object(ui.platform.os, 'open', side_effect=opened), \
                 mock.patch.object(ui.platform.os, 'kill', side_effect=kill), \
                 mock.patch.object(ui.Path, 'unlink', unlink):
                result = ui.platform.validation_browser_probe(self.web)
            row = next(x for x in result['checks'] if x['name'] == 'outside-candidate-root-create')
            self.assertEqual(row['passed'], outcome == 'denied')
            if outcome == 'allowed-cleanup-failure':
                self.assertTrue(row['unexpected_allow'])
                self.assertIn('cleanup_error', row)
            elif outcome == 'unavailable':
                self.assertEqual(row['unavailable_errno'], errno.ENOENT)



if __name__ == "__main__":
    unittest.main()
