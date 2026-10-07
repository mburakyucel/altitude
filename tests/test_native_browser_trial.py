"""Protected trial transaction/failure integration; external native jobs and services stay fictional."""
import importlib.util
from contextlib import nullcontext
import json
import os
import plistlib
import stat
import subprocess
import time
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, REPO
from altitude import config, engines, platform
from scripts import restart_altitude


def script():
    spec = importlib.util.spec_from_file_location("native_browser_trial", REPO / "scripts/native_browser_trial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNativeBrowserTrial(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.native = script()
        self.area = self.tmp / "protected-trial"
        self.candidate = self.tmp / "candidate"
        self.candidate.mkdir()
        (self.candidate / "bin").mkdir()
        (self.candidate / "bin/runtime").write_bytes(b"fictional-runtime")
        (self.candidate / "metadata.json").write_bytes(b"fictional-resources")
        (self.candidate / "bin/runtime").chmod(0o755)
        self.package = {"entrypoint": "bin/runtime", "executables": ("bin/runtime",), "files": {
            name: self.native.digest((self.candidate / name).read_bytes())
            for name in ("bin/runtime", "metadata.json")}}
        self.stock = self.tmp / "stock"
        self.stock.write_bytes(b"fictional-stock")
        self.stock.chmod(0o755)
        self.service = self.tmp / "service.plist"
        self.original = plistlib.dumps({"Label": "fictional-altitude", "WorkingDirectory": str(self.repo),
            "KeepAlive": {"SuccessfulExit": False}, "EnvironmentVariables": {
                "PATH": str(self.tmp), "CODEX_BIN": str(self.stock), "ALTITUDE_HOST": "127.0.0.1"}})
        self.service.write_bytes(self.original)
        self.service.chmod(0o640)
        self.patch(self.native, "home", return_value=self.area)
        # Separately tested platform/root-exclusion seam; the real transaction uses actual fixture files.
        self.patch(self.native, "protected")
        self.patch(self.native, "owner", return_value={"project": "fictional", "slug": "trial", "attempt": 1})
        self.interval = self.native.native_interval
        self.patch(self.native, "native_interval")
        self.phase_deadline = platform.native_browser_trial_deadline
        self.patch(platform, "native_browser_trial_deadline", side_effect=lambda seconds: nullcontext())
        self.patch(platform, "require_native_browser_trial")
        self.patch(platform, "service_path", return_value=self.service)
        self.patch(engines, "browser_trial_jobs", return_value=[])
        self.patch(engines, "browser_trial_package", return_value=self.package)
        self.patch(engines, "browser_trial_stock_root", return_value=self.candidate)
        self.patch(restart_altitude, "require_deployed_checkout")

    def row(self):
        return json.loads((self.area / "receipt.json").read_text())

    def test_successful_interval_restores_exact_stock_definition_and_final_sweep(self):
        order = []
        def activate(row):
            order.append("candidate" if self.service.read_bytes() != self.original else "stock")
        with mock.patch.object(self.native, "activate", side_effect=activate), \
             mock.patch.object(self.native, "native_interval", side_effect=lambda row, seconds: order.append("native-window")), \
             mock.patch.object(self.native, "stop_jobs", side_effect=lambda row: order.append("stop")):
            self.native.trial(self.candidate, 10)
        self.assertEqual(order, ["candidate", "native-window", "stop", "stock", "stop"])
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.service.stat().st_mode), 0o640)
        self.assertEqual(self.stock.read_bytes(), b"fictional-stock")
        self.assertEqual(self.row()["phase"], "restored")
        for file in self.package["files"]:
            self.assertEqual((self.area / "package" / file).read_bytes(), (self.candidate / file).read_bytes())
            self.assertFalse((self.area / "package" / file).stat().st_mode & 0o222)
        selected = plistlib.loads((self.area / "selected.plist").read_bytes())
        original = plistlib.loads(self.original)
        self.assertEqual(selected.keys(), original.keys())
        for key in original:
            if key != "EnvironmentVariables":
                self.assertEqual(selected[key], original[key])
        self.assertEqual({k:v for k,v in selected["EnvironmentVariables"].items() if k != "CODEX_BIN"},
                         {k:v for k,v in original["EnvironmentVariables"].items() if k != "CODEX_BIN"})

    def test_activation_failure_rolls_back_selection_and_retains_original_failure(self):
        with mock.patch.object(self.native, "activate", side_effect=[self.native.TrialError("bootstrap refused"), None]) as activate:
            with self.assertRaisesRegex(self.native.TrialError, "bootstrap refused"):
                self.native.trial(self.candidate, 5)
        self.assertEqual(activate.call_count, 2)
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual(self.row()["trial_failure"], "bootstrap refused")
        self.assertEqual(self.row()["stock_health"], "verified")

    def test_internal_deadline_or_handled_interruption_restores_selection(self):
        for error in (TimeoutError("native timeout"), self.native.TrialError("interrupted by signal 15")):
            with self.subTest(error=error):
                if self.area.exists():
                    for directory in self.area.rglob("*"):
                        if directory.is_dir(): directory.chmod(0o700)
                    import shutil
                    shutil.rmtree(self.area)
                with mock.patch.object(self.native, "activate"), mock.patch.object(self.native, "native_interval", side_effect=error):
                    with self.assertRaises(type(error)):
                        self.native.trial(self.candidate, 1)
                self.assertEqual(self.service.read_bytes(), self.original)
                self.assertEqual(self.row()["phase"], "restored")

    def test_stock_health_refusal_is_recovery_pending_even_after_bytes_restore(self):
        with mock.patch.object(self.native, "activate", side_effect=[None, self.native.TrialError("quiet point unknown")]), \
             mock.patch.object(self.native.time, "sleep"):
            with self.assertRaisesRegex(self.native.TrialError, "quiet point unknown"):
                self.native.trial(self.candidate, 1)
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual(self.row()["phase"], "recovery-pending")
        self.assertEqual(self.row()["stock_health"], "unverified")
        with self.assertRaisesRegex(self.native.TrialError, "Prior trial area"):
            self.native.prepare(self.candidate)

    def test_restore_is_independent_of_live_owner_or_daemon_and_ends_new_candidate_jobs(self):
        row = self.native.prepare(self.candidate)
        self.service.write_bytes((self.area / "selected.plist").read_bytes())
        with mock.patch.object(self.native, "activate"), \
             mock.patch.object(engines, "browser_trial_jobs", side_effect=[["first.service"], ["late.service"], []]), \
             mock.patch.object(platform, "job_stop") as stop, mock.patch.object(platform, "job_active", return_value=False):
            self.native.restore()
        self.assertEqual([call.args[0] for call in stop.call_args_list], ["first.service", "late.service"])
        self.assertEqual(self.row()["terminated_jobs"], ["first.service", "late.service"])
        self.assertEqual(self.service.read_bytes(), self.original)

    def test_drift_refusal_preserves_external_change_and_records_pending(self):
        self.native.prepare(self.candidate)
        drift = b"unrelated operator edit"
        self.service.write_bytes(drift)
        with mock.patch.object(self.native, "activate") as activate:
            with self.assertRaisesRegex(self.native.TrialError, "drift"):
                self.native.restore()
        activate.assert_not_called()
        self.assertEqual(self.service.read_bytes(), drift)
        self.assertEqual(self.row()["phase"], "recovery-pending")

    def test_changed_stock_or_backup_refuses_restore(self):
        self.native.prepare(self.candidate)
        self.stock.write_bytes(b"changed stock")
        with mock.patch.object(self.native, "activate") as activate:
            with self.assertRaisesRegex(self.native.TrialError, "Stock executable identity"):
                self.native.restore()
        activate.assert_not_called()
        (self.area / "original.plist").chmod(0o600)
        (self.area / "original.plist").write_bytes(b"changed backup")
        with self.assertRaisesRegex(self.native.TrialError, "backup digest"):
            self.native.restore()

    def test_extra_missing_changed_or_linked_package_never_creates_trial_area(self):
        extra = self.candidate / "extra"
        extra.write_bytes(b"unreviewed")
        with self.assertRaisesRegex(self.native.TrialError, "file set"):
            self.native.prepare(self.candidate)
        extra.unlink()
        metadata = self.candidate / "metadata.json"
        metadata.write_bytes(b"wrong digest")
        with self.assertRaisesRegex(self.native.TrialError, "digest"):
            self.native.prepare(self.candidate)
        metadata.unlink()
        with self.assertRaisesRegex(self.native.TrialError, "file set"):
            self.native.prepare(self.candidate)
        metadata.symlink_to(self.stock)
        with self.assertRaisesRegex(self.native.TrialError, "Linked package"):
            self.native.prepare(self.candidate)
        self.assertFalse(self.area.exists())
        self.assertEqual(self.service.read_bytes(), self.original)

    def test_unsafe_interval_never_installs_or_selects(self):
        for seconds in (0, 91, 600):
            with self.assertRaisesRegex(self.native.TrialError, "reserving rollback"):
                self.native.trial(self.candidate, seconds)
        self.assertFalse(self.area.exists())

    def test_guarded_activation_uses_protected_source_and_bounded_native_job(self):
        row = self.native.prepare(self.candidate)
        with mock.patch.object(platform, "job_command", return_value=["fictional-native-job"]) as job, \
             mock.patch.object(self.native.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             mock.patch.object(platform, "job_stop") as stop:
            self.native.activate(row)
        self.assertEqual(job.call_args.kwargs["runtime_max"], 90)
        self.assertIn(str(Path(row["source"]) / "scripts/restart_altitude.py"), job.call_args.args[1])
        stop.assert_called_once()

    def test_standalone_restore_refuses_live_transaction_before_selection_or_health(self):
        self.native.prepare(self.candidate)
        with self.native.transaction(), mock.patch.object(self.native, "activate") as activate:
            with self.assertRaisesRegex(self.native.TrialError, "active; do not race"):
                self.native.restore()
        activate.assert_not_called()
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual(self.row()["phase"], "prepared")

    def test_explicit_fault_changed_attempt_or_unreadable_identity_ends_interval_and_restores(self):
        for task in ({"attempt": 1, "fault": {"reason": "isolation denied"}, "entrypoint": "/untrusted"},
                     {"attempt": 2}, {"attempt": 1, "resume_failed": True},
                     {"attempt": 1, "agent_id": "candidate", "state": "blocked", "stop_id": "explicit-stop"},
                     OSError("owner state unavailable")):
            with self.subTest(task=task):
                # Exercise the real bounded wait; task data only requests earlier fixed restoration.
                with mock.patch.object(self.native, "activate"), \
                     mock.patch.object(self.native.S, "load_task", side_effect=[task] if not isinstance(task, Exception) else task), \
                     mock.patch.object(self.native, "native_interval", side_effect=self.interval):
                    with self.assertRaises((self.native.TrialError, OSError)):
                        self.native.trial(self.candidate, 1)
                self.assertEqual(self.service.read_bytes(), self.original)
                self.assertEqual(self.row()["phase"], "restored")
                import shutil
                for directory in self.area.rglob("*"):
                    if directory.is_dir():
                        directory.chmod(0o700)
                shutil.rmtree(self.area)

    def test_actual_trial_deadline_enters_independently_bounded_stock_recovery(self):
        phases = []
        def deadline(seconds):
            phases.append(seconds)
            return self.phase_deadline(1)
        def acceptance(row, seconds):
            platform.signals.setitimer(platform.signals.ITIMER_REAL, 0.02)
            time.sleep(1)
        with mock.patch.object(platform, "native_browser_trial_deadline", side_effect=deadline), \
             mock.patch.object(self.native, "activate") as activate, \
             mock.patch.object(self.native, "native_interval", side_effect=acceptance):
            with self.assertRaisesRegex(platform.NativeBrowserTrialDeadline, "phase deadline"):
                self.native.trial(self.candidate, 1)
        self.assertEqual(phases, [60, 210, 210])
        self.assertEqual(activate.call_count, 2)
        self.assertEqual(self.service.read_bytes(), self.original)
        self.assertEqual(self.row()["stock_health"], "verified")

    def test_resource_drift_or_missing_execute_bits_refuses_candidate_or_fallback(self):
        binary = self.candidate / "bin/runtime"
        binary.chmod(0o644)
        with self.assertRaisesRegex(self.native.TrialError, "executable mode"):
            self.native.prepare(self.candidate)
        self.assertFalse(self.area.exists())
        binary.chmod(0o755)
        self.native.prepare(self.candidate)
        (self.candidate / "metadata.json").write_bytes(b"delegated resource changed")
        with mock.patch.object(self.native, "activate") as activate:
            with self.assertRaisesRegex(self.native.TrialError, "Complete stock package"):
                self.native.restore()
        activate.assert_not_called()
        self.assertEqual(self.row()["stock_health"], "unverified")


class TestTrialProtection(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.job_root_check = platform._native_trial_job_root
        self.worker_specs = platform.native_trial_worker_specs
        self.patch(platform, "native_trial_worker_specs", return_value=[])
        self.patch(platform, "_native_trial_job_root", return_value=self.tmp / "fictional-protected-jobs")

    def test_real_phase_deadline_interrupts_fictional_work_and_restores_signal_handler(self):
        handler = platform.signals.getsignal(platform.signals.SIGALRM)
        with mock.patch.object(platform, "require_native_browser_trial"):
            with self.assertRaisesRegex(platform.NativeBrowserTrialDeadline, "phase deadline"):
                with platform.native_browser_trial_deadline(0.02):
                    time.sleep(1)
        self.assertEqual(platform.signals.getsignal(platform.signals.SIGALRM), handler)
        self.assertEqual(platform.signals.getitimer(platform.signals.ITIMER_REAL), (0.0, 0.0))

    def test_phase_deadline_is_not_swallowed_by_native_status_error_handler(self):
        with mock.patch.object(platform, "require_native_browser_trial"), \
             mock.patch.object(platform, "_print", side_effect=lambda label: time.sleep(1)), \
             mock.patch.object(platform.subprocess, "run") as control:
            with self.assertRaisesRegex(platform.NativeBrowserTrialDeadline, "phase deadline"):
                with platform.native_browser_trial_deadline(0.02):
                    platform._launchd_job_stop("fictional.service", 1)
        control.assert_not_called()

    def test_other_engine_state_temporary_and_job_roots_are_refused(self):
        native = script()
        private = self.tmp / "private-engine-state"
        private.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(private)}), \
             mock.patch.object(config, "load_projects", return_value={}):
            with self.assertRaisesRegex(native.TrialError, "write roots"):
                native.protected(private / "recovery-input")
        with self.assertRaisesRegex(native.TrialError, "write roots"):
            native.protected(self.tmp / "temporary-input")
        with mock.patch.object(platform, "_jobs", return_value=private):
            with self.assertRaisesRegex(RuntimeError, "overlap worker"):
                self.job_root_check((self.tmp,))
            with self.assertRaisesRegex(RuntimeError, "ancestry"):
                self.job_root_check(())
        temp = self.tmp / "user/T"
        cache = self.tmp / "user/C"
        with mock.patch.object(platform, "_user_temp", return_value=str(temp)):
            self.assertTrue(any(cache.is_relative_to(root) for root in platform.native_temporary_roots()))
            with self.assertRaisesRegex(native.TrialError, "write roots"):
                native.protected(cache / "recovery-input")

    def test_stock_resolution_matches_actual_prefixed_launch_path(self):
        commands = self.tmp / "source/bin"
        commands.mkdir(parents=True)
        preferred = commands / "codex"
        preferred.write_text("fictional native executable")
        preferred.chmod(0o755)
        alternative = self.tmp / "codex"
        alternative.write_text("different raw-PATH executable")
        alternative.chmod(0o755)
        environment = {"PATH": str(self.tmp)}
        with mock.patch.object(config, "SOURCE", commands.parent), \
             mock.patch.object(config, "RELEASE", None), \
             mock.patch.object(config, "subprocess_env", return_value=environment):
            self.assertEqual(engines.browser_trial_stock(environment), preferred.resolve())
        with self.assertRaises((FileNotFoundError, RuntimeError)):
            engines.browser_trial_stock_root(alternative)

    def test_worker_root_and_links_are_rejected(self):
        native = script()
        with mock.patch.object(config, "ROOT", self.tmp), mock.patch.object(config, "load_projects", return_value={}):
            with self.assertRaisesRegex(native.TrialError, "write roots"):
                native.protected(self.tmp / "not-protected")
        link = self.tmp / "link"
        link.symlink_to(self.repo)
        with mock.patch.object(config, "ROOT", self.tmp / "state"), mock.patch.object(config, "load_projects", return_value={}):
            with self.assertRaisesRegex(native.TrialError, "Linked|write roots"):
                native.protected(link)

    def test_native_job_inventory_uses_exact_protected_command_and_actual_lifetime(self):
        jobs = self.tmp / "native-jobs"
        jobs.mkdir()
        for name, command in (("one", ["/protected/runtime", "exec"]), ("two", ["/other/runtime"]),
                              ("three", ["/protected/runtime-unrelated"])):
            folder = jobs / f"dev.altitude.job.{name}"
            folder.mkdir(mode=0o700)
            (folder / "spec.json").write_text(json.dumps({"label":folder.name,"command":command}))
            (folder / "spec.json").chmod(0o600)
        with mock.patch.object(platform, "_darwin", return_value=True), \
             mock.patch.object(platform, "containerized", return_value=False), \
             mock.patch.object(platform, "_native_trial_job_root", return_value=jobs), \
             mock.patch.object(platform, "native_trial_worker_specs", wraps=self.worker_specs), \
             mock.patch.object(platform, "job_active", return_value=True) as active:
            self.assertEqual(platform.native_runtime_jobs(Path("/protected/runtime"), unit_prefixes=("one",), excluded_roots=()), ["one.service"])
            active.assert_called_once_with("one.service", {})
            (jobs / "dev.altitude.job.one/spec.json").chmod(0o666)
            with self.assertRaisesRegex(RuntimeError, "protection"):
                platform.native_runtime_jobs(Path("/protected/runtime"), unit_prefixes=("one",), excluded_roots=())

    def test_every_candidate_role_and_late_reviewer_is_included_without_unrelated_jobs(self):
        jobs = self.tmp / "role-jobs"
        jobs.mkdir()
        for name, executable in (("altitude-codex-l2", "/candidate"), ("altitude-codex-sync-l3", "/candidate"),
                                 ("altitude-review-abc", "/candidate"), ("altitude-review-other", "/stock")):
            folder = jobs / f"dev.altitude.job.{name}"
            folder.mkdir(mode=0o700)
            (folder / "spec.json").write_text(json.dumps({"label": folder.name, "command": [executable, "exec"]}))
            (folder / "spec.json").chmod(0o600)
        with mock.patch.object(platform, "_darwin", return_value=True), \
             mock.patch.object(platform, "containerized", return_value=False), \
             mock.patch.object(platform, "_native_trial_job_root", return_value=jobs), \
             mock.patch.object(platform, "native_trial_worker_specs", wraps=self.worker_specs), \
             mock.patch.object(engines, "browser_trial_write_roots", return_value=()), \
             mock.patch.object(platform, "job_active", return_value=True):
            self.assertEqual(engines.browser_trial_jobs(Path("/candidate")), [
                "altitude-codex-l2.service", "altitude-codex-sync-l3.service", "altitude-review-abc.service"])

    def test_live_launch_roots_survive_task_record_removal_and_include_extra_permissions(self):
        cwd = self.tmp / "actual-live-cwd"
        extra = self.tmp / "actual-extra-write"
        settings = engines.codex_sandbox(cwd, extra_roots=[extra])
        command = ["/fictional/runtime", "exec", *[arg for value in settings for arg in ("-c", value)]]
        spec = {"cwd": str(cwd), "command": command, "writable": None}
        with mock.patch.object(config, "load_projects", return_value={}), \
             mock.patch.object(platform, "native_trial_worker_specs", return_value=[spec]):
            roots = engines.browser_trial_write_roots()
        self.assertIn(cwd, roots)
        self.assertIn(extra, roots)
        with self.assertRaises((KeyError, RuntimeError)):
            engines._browser_trial_launch_roots({"cwd": str(cwd), "command": ["/unknown"], "writable": None})
        self.assertEqual(engines._browser_trial_launch_roots({"cwd": str(cwd), "writable": [str(extra)]}), [cwd, extra])
