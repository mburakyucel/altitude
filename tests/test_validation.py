"""A running owner runs a command in the validation runner's disposable container, with no grant, and every run is
recorded like a machine command.

The transient-unit launcher and Podman are fixtures: a `systemd-run` shim runs the exact script altd composes, and a
`podman` stand-in records every invocation and runs a container's command on the host with the run's clone and
results folder in place of their mount points. The authority fences, the fixed container options, the clone of the
committed HEAD, the results copy, cleanup, records, the switch and the HTTP and CLI doors are real.
"""
import http.client
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock

from tests.support import AltitudeCase, make_repo
from tests.test_grant import SHIM
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T, terminal, validation

RUNNER_HOME = validation.home


class TestValidationImage(TestCase):
    def test_browser_pin_matches_the_walkthroughs_lockfile(self):
        # #667: browser downloads alone cannot prepare a runner's Linux libraries.
        # Keep image preparation tied to the version the application actually uses.
        source = Path(__file__).resolve().parents[1]
        image = (source / "scripts/validation.Containerfile").read_text()
        lock = (source / "web/pnpm-lock.yaml").read_text()
        pin = re.search(r"^ARG PLAYWRIGHT=(\S+)$", image, re.M).group(1)
        versions = set(re.findall(r"^  '@playwright/test@([^']+)':", lock, re.M))
        self.assertEqual(versions, {pin})

# Fixture scripts run on the suite's interpreter, not macOS's /usr/bin/python3, whose xcrun shim reports
# the run's read-only lookup cache in the logs these cases compare.
PODMAN = f"#!{sys.executable}\n" + r'''"""podman stand-in: record the call; run a container's command on the host with its mounts in place."""
import json, os, subprocess, sys
args = sys.argv[1:]
with open(RECORD, "a") as out:
    out.write(json.dumps(args) + "\n")
if args[:2] == ["image", "exists"]:
    sys.exit(0 if os.path.exists(BUILT) else 1)
if args[:1] == ["build"]:
    open(BUILT, "w").close()
if args[:1] == ["images"]:
    print("localhost/altitude-validation:earlier")
if args[:1] == ["unshare"]:
    sys.exit(subprocess.run(args[1:]).returncode)
if args[:1] == ["run"] and any(a.endswith(":/runner:ro") for a in args):
    sys.exit(0)  # the deployed runner's cloud-image refresh
if args[:1] == ["run"]:
    image = next(i for i, a in enumerate(args) if a.startswith("localhost/altitude-validation:"))
    mounts = [a.split("=", 1)[1].split(":")[:2] for a in args[:image] if a.startswith("--volume=")]
    def host(word):
        for source, target in mounts:
            word = word.replace(target, source)
        return word
    work = next(source for source, target in mounts if target == "/work")
    sys.exit(subprocess.run([host(a) for a in args[image + 1:]], cwd=work).returncode)
'''


class RunnerCase(AltitudeCase):
    """A running task whose worker owns the request, and altd's HTTP door; each host's fixtures come from a subclass."""

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir(exist_ok=True)
        self.patch(platform, "validation_unavailable", return_value=None)
        self.patch(validation, "FREE_DISK", 0)   # the host's free disk is not the fixture's
        self.owner = self.patch(terminal, "owner_connection", return_value=True)
        self.runner = self.tmp / "runner"
        self.patch(validation, "home", return_value=self.runner)
        validation._ready.set()
        self.stops = self.patch(platform, "job_stop")
        self.active = self.patch(platform, "job_active", return_value=False)
        self.slug = T.new(self.project, "Check the installation", "Run the VM lifecycle.")["slug"]
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.repo), branch="work")
        root = dispatch.l2_job_root(self.project, self.slug)
        root.mkdir(parents=True, exist_ok=True)
        S.write_json(root / "agent.json", {"id": "agent", "engine": "claude", "unit": engines._claude_unit("agent")})
        self.head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body, *, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=60)
        try:
            connection.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def validate(self, command, *, status=200, attempt="1", **options):
        return self.request("/api/task/validate", {"project": self.project, "slug": self.slug, "attempt": attempt,
                                                   "command": command, **options}, status=status)

    def rows(self):
        return [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                .splitlines()]


class TestValidationRunner(RunnerCase):
    host = "linux"  # the fixtures are systemd-run and Podman

    def setUp(self):
        super().setUp()
        shim = self.bin_dir / "systemd-run"
        shim.write_text(SHIM)
        shim.chmod(0o755)
        self.record, built = self.tmp / "podman.jsonl", self.tmp / "built"
        podman = self.bin_dir / "podman"
        podman.write_text(PODMAN.replace("RECORD", repr(str(self.record)), 1).replace("BUILT", repr(str(built))))
        podman.chmod(0o755)
        self.patch(platform, "SYSTEMD_RUN", str(shim))
        self.patch(validation, "podman", return_value=[str(podman)])

    def calls(self, verb):
        return [a for a in map(json.loads, self.record.read_text().splitlines()) if a[:1] == [verb]]

    def test_a_run_uses_the_committed_head_the_fixed_container_and_leaves_a_record(self):
        (self.repo / "uncommitted.txt").write_text("not tested\n")
        result = self.validate(["sh", "-c", "git rev-parse HEAD > /results/head; mkdir /results/logs; "
                                            "echo evidence > /results/logs/a; ln -s /etc/passwd /results/link; "
                                            "echo done; test -e uncommitted.txt"],
                               publish=8890)
        self.assertEqual((result["exit"], result["commit"]), (1, self.head))
        self.assertIn("done\n", result["output"])
        results = S.task_dir(self.project, self.slug) / "validation" / "1"
        self.assertEqual(result["results"], str(results))
        self.assertEqual((results / "head").read_text().strip(), self.head)
        self.assertEqual((results / "logs" / "a").read_text(), "evidence\n")
        self.assertFalse((results / "link").exists())
        self.assertEqual(result["results_skipped"], ["link: not a regular file or folder"])
        self.assertEqual(list((validation.home() / "runs").iterdir()), [])
        self.assertIn("done\n", (results.parent / "1.log").read_text())
        self.assertEqual(result["log"], str(results.parent / "1.log"))
        self.assertEqual(sorted(p.name for p in S.task_dir(self.project, self.slug).glob("*.exit")), [])
        self.assertEqual(self.owner.call_args.args[2],
                         engines.worker_unit("agent", job_root=dispatch.l2_job_root(self.project, self.slug)))

        [run] = self.calls("run")
        image = next(i for i, a in enumerate(run) if a.startswith("localhost/altitude-validation:"))
        options = run[:image]
        host = result["publish"]["host"]
        self.assertEqual(result["publish"], {"container": 8890, "host": host})
        self.assertNotEqual(host, config.PORT)
        for fixed in ("--rm", "--pull=never", "--cgroups=disabled", "--userns=keep-id:uid=1000,gid=1000",
                      "--user=1000:1000", "--network=slirp4netns:allow_host_loopback=false",
                      "--device=/dev/fuse", "--device=/dev/net/tun", f"--publish=127.0.0.1:{host}:8890",
                      "--env=VALIDATION_RESULTS=/results"):
            self.assertIn(fixed, options)
        self.assertNotIn("--device=/dev/kvm", options)
        self.assertEqual(sorted(a.split(":")[1] for a in options if a.startswith("--volume=")),
                         ["/home/ubuntu/.cache/altitude-installation-vm", "/results", "/work"])
        self.assertFalse(any(a.startswith(("--privileged", "--cap-add", "--security-opt", "--env-host"))
                             for a in options))
        self.assertEqual(len(self.calls("build")), 1)
        self.assertIn(["rmi", "--force", "localhost/altitude-validation:earlier"], self.calls("rmi"))

        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                 .splitlines()]
        tree = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertEqual({k: row[k] for k in ("n", "purpose", "commit", "tree", "kvm", "exit", "ended", "isolation",
                                              "host", "cleanup")},
                         {"n": 1, "purpose": "validation", "commit": self.head, "tree": tree, "kvm": False, "exit": 1,
                          "ended": "exit", "isolation": validation.image_tag(), "host": platform.host_identity(),
                          "cleanup": None})
        self.assertEqual({k: result[k] for k in ("tree", "host", "isolation", "ended")},
                         {k: row[k] for k in ("tree", "host", "isolation", "ended")})
        self.assertTrue(row["unit"].startswith("altitude-validation-"))
        [event] = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "machine-run"]
        self.assertEqual((event["purpose"], event["exit"]), ("validation", 1))

        self.assertEqual(self.validate(["true"])["n"], 2)
        self.assertEqual(len(self.calls("build")), 1, "a built image is reused")

    def test_the_image_comes_from_the_source_the_service_activated_after_import(self):
        activated = self.tmp / "activated"
        (activated / "scripts").mkdir(parents=True)
        (activated / "scripts" / "validation.Containerfile").write_text("FROM scratch\n")
        self.patch(config, "SOURCE", activated)   # git_policy.activate_source() at service startup
        self.assertEqual(self.validate(["true"])["exit"], 0)
        [build] = self.calls("build")
        self.assertIn(f"--file={activated / 'scripts' / 'validation.Containerfile'}", build)
        self.assertIn(hashlib.sha256(b"FROM scratch\n").hexdigest()[:16], validation.image_tag())

    def test_kvm_refreshes_the_cloud_image_with_the_deployed_runner_first(self):
        kvm = self.tmp / "kvm"
        kvm.write_text("")
        self.patch(platform, "KVM", kvm)
        self.assertEqual(self.validate(["true"], kvm=True)["exit"], 0)
        prefetch, run = self.calls("run")
        self.assertIn(f"--volume={config.SOURCE / 'scripts'}:/runner:ro", prefetch)
        self.assertIn(f"--volume={validation.home() / 'cache'}:/cache", prefetch)
        self.assertIn("--device=/dev/kvm", run)
        self.assertIn(f"--volume={validation.home() / 'cache'}:/home/ubuntu/.cache/altitude-installation-vm:O", run)
        self.patch(platform, "KVM", self.tmp / "missing")
        self.assertIn("no KVM access", self.validate(["true"], kvm=True, status=400)["error"])

    def test_refusals_leave_no_run(self):
        self.assertIn("current attempt", self.validate(["true"], attempt="2", status=403)["error"])
        self.assertIn("unsupported fields", self.validate(["true"], extra=1, status=400)["error"])
        self.assertIn("command", self.validate("true", status=400)["error"])
        self.assertIn("command", self.validate([], status=400)["error"])
        self.assertIn("one container port", self.validate(["true"], publish=0, status=400)["error"])
        self.assertIn("one container port", self.validate(["true"], kvm="yes", status=400)["error"])
        with mock.patch.object(platform, "validation_unavailable", return_value="validation runs need podman"):
            self.assertIn("need podman", self.validate(["true"], status=400)["error"])
        with mock.patch.object(validation, "FREE_DISK", 1 << 62):
            self.assertIn("GiB free", self.validate(["true"], status=400)["error"])
        with validation._lock:
            self.assertIn("another validation run", self.validate(["true"], status=400)["error"])
        T.block(self.project, self.slug, "Waiting", actor="l2", expected_state="running", expected_attempt=1)
        self.assertIn("running owner", self.validate(["true"], status=403)["error"])
        self.assertFalse(self.record.exists())
        self.assertFalse((S.task_dir(self.project, self.slug) / "machine.jsonl").exists())

    def test_the_switch_is_the_operators_and_off_stops_the_running_run(self):
        self.assertTrue(server.machine_view()["validation"])
        with mock.patch.object(terminal, "agent_connection", return_value=True):
            self.assertIn("agents are refused", self.request("/api/validation-access", {"enabled": False},
                                                             status=403)["error"])
        with mock.patch.object(terminal, "agent_connection", return_value=False):
            validation._active.update(unit="altitude-validation-x.service", area=self.tmp, stopped=False)
            self.addCleanup(validation._active.clear)
            self.assertFalse(self.request("/api/validation-access", {"enabled": False})["validation"])
            self.assertEqual(self.stops.call_args.args[0], "altitude-validation-x.service")
            self.assertTrue(validation._active["stopped"])
            self.assertTrue((self.tmp / "stopped").exists())
            self.assertTrue((validation.home() / "off").exists(), "the switch lives in the runner's own storage")
            self.assertNotIn("validation", config.machine_settings())
            validation._active.clear()
            self.assertIn("turned validation runs off", self.validate(["true"], status=403)["error"])
            self.assertTrue(self.request("/api/validation-access", {"enabled": True})["validation"])
        self.assertEqual(self.validate(["true"])["exit"], 0)

    def test_results_never_follow_links_or_exceed_the_limit(self):
        source = self.tmp / "results"
        source.mkdir()
        (source / "big").write_bytes(b"x" * 10)
        (source / "small").write_bytes(b"y")
        os.mkfifo(source / "pipe")
        parent = os.open(self.tmp, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        with mock.patch.object(validation, "RESULTS_LIMIT", 5):
            skipped = validation.copy_results(source, parent, "copy")
        self.assertEqual(sorted(p.name for p in (self.tmp / "copy").iterdir()), ["small"])
        self.assertEqual(skipped, ["big: over the 0 MiB results limit", "pipe: not a regular file or folder"])
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.tmp / "planted").symlink_to(elsewhere)
        with self.assertRaises(FileExistsError):
            validation.copy_results(source, parent, "planted")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_the_runner_and_what_a_run_writes_stay_out_of_worker_writable_paths(self):
        self.assertFalse(RUNNER_HOME().is_relative_to(config.ROOT))
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (S.task_dir(self.project, self.slug) / "validation").symlink_to(elsewhere)
        self.assertIn("error", self.validate(["sh", "-c", "echo x > /results/x"], status=400))
        self.assertEqual(list(elsewhere.iterdir()), [])
        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                 .splitlines()]
        self.assertEqual((row["ended"], row["exit"]), ("failed", 0))
        self.assertTrue(row["error"])
        [area] = list((validation.home() / "runs").iterdir())
        self.assertEqual(Path(row["results"]), area / "results")
        self.assertEqual((area / "results" / "x").read_text(), "x\n")
        validation.reconcile()
        self.assertTrue(area.exists(), "failed delivery never deletes the original evidence")
        self.assertFalse(validation._ready.is_set())

    def test_activation_waits_for_validation_and_its_evidence_then_restarts(self):
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        stages = []

        def during(name, action):
            def checked(*args, **kwargs):
                stages.append(name)
                # A merge arrives while this validation is admitted, including before its unit starts.
                S.write_json(flag, {"since": S.now(), "head": "candidate", "files": ["altitude/validation.py"]})
                self.assertTrue(server.restart_status()["waiting_for"])
                server.auto_restart()
                with self.assertRaises(server.RestartBusy):
                    server.restart_service()
                return action(*args, **kwargs)
            return checked

        with mock.patch.object(server, "_request_restart_unit", return_value={"ok": True, "unit": "restart"}) as restart, \
                mock.patch.object(validation, "_clone", side_effect=during("clone", validation._clone)), \
                mock.patch.object(validation, "_job", side_effect=during("job", validation._job)), \
                mock.patch.object(validation, "_deliver", side_effect=during("deliver", validation._deliver)), \
                mock.patch.object(T, "finish_machine_run", side_effect=during("record", T.finish_machine_run)):
            result = self.validate(["sh", "-c", "echo retained; echo evidence > /results/check"])
            self.assertEqual(result["exit"], 0)
            self.assertEqual(Path(result["log"]).read_text(), "retained\n")
            self.assertEqual((Path(result["results"]) / "check").read_text(), "evidence\n")
            [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl")
                     .read_text().splitlines()]
            self.assertEqual(row["ended"], "exit")
            self.assertEqual(stages, ["clone", "job", "deliver", "job", "record"], "cleanup precedes the record")
            restart.assert_not_called()
            server.auto_restart()
            restart.assert_called_once()
            self.assertEqual(server.restart_status()["waiting_for"], [])

    def test_restart_request_fences_validation_admission_without_leaving_a_run(self):
        with config.restart_lock(exclusive=True):
            self.assertIn("restarting", self.validate(["true"], status=400)["error"])
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        S.write_json(flag, {"requested_at": S.now(), "unit": "restart"})
        self.assertIn("restarting", self.validate(["true"], status=400)["error"])
        self.assertFalse(self.runner.exists())
        flag.unlink()
        self.assertEqual(self.validate(["true"])["exit"], 0)

    def test_only_the_tasks_own_worker_may_run_its_validation(self):
        self.owner.return_value = False
        self.assertIn("only this task's owner", self.validate(["true"], status=403)["error"])
        self.assertFalse(self.record.exists())

    def test_turning_the_runner_off_while_a_run_is_admitted_stops_it_before_or_as_it_starts(self):
        clone = validation._clone
        with mock.patch.object(validation, "_clone", side_effect=lambda *a: (validation.stop_all(), clone(*a))[1]):
            before = self.validate(["true"])
        self.assertEqual((before["exit"], before["error"]), (None, "turned off before it started"))
        self.assertFalse(self.calls("run"))
        script = validation.run_script
        with mock.patch.object(validation, "run_script", side_effect=lambda *a, **k: (validation.stop_all(), script(*a, **k))[1]):
            racing = self.validate(["true"])
        self.assertEqual(racing["exit"], 125, "the unit found the marker and exited before any container ran")
        self.assertFalse(self.calls("run"))
        rows = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                .splitlines()]
        self.assertEqual([r["ended"] for r in rows], ["turned off", "turned off"])

    def test_restart_stops_an_expired_run_and_retains_its_interrupted_record_and_evidence(self):
        areas = {}
        for name in ("old", "finished", "unrecorded"):
            areas[name] = validation.home() / "runs" / name
            (areas[name] / "results").mkdir(parents=True)
            (areas[name] / "run.json").write_text(json.dumps({"project": self.project, "slug": self.slug,
                                                              "unit": f"altitude-validation-{name}.service"}))
            if name != "unrecorded":
                expired = (datetime.now(timezone.utc) - timedelta(seconds=validation.TIMEOUT + 60)).isoformat()
                with mock.patch.object(S, "now", return_value=expired):
                    row = T.start_machine_run(self.project, self.slug, lambda n: {
                        "purpose": "validation", "command": "true", "unit": f"altitude-validation-{name}.service"})
            (areas[name] / "results" / "check").write_text("partial evidence\n")
            engines.machine_files(areas[name], f"altitude-validation-{name}.service")[0].write_text("before restart\n")
        T.finish_machine_run(self.project, self.slug, {**row, "exit": 0, "finished": S.now(), "ended": "exit"})
        cleanup = validation.cleanup
        held = []
        with mock.patch.object(validation, "cleanup", side_effect=lambda runs: (held.append(validation._lock.locked()),
                                                                               cleanup(runs))[1]):
            validation.reconcile()
        self.assertTrue(validation._ready.is_set())
        self.assertEqual(held, [True])
        self.assertEqual(self.stops.call_args.args[0], "altitude-validation-*.service")
        self.assertFalse(any(area.exists() for area in areas.values()))
        saved = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                 .splitlines()]
        self.assertEqual([(r["ended"], r["exit"]) for r in saved], [("interrupted", None), ("exit", 0)],
                         "a row finished before its area was removed stays as it ended")
        self.assertIn("altd did not record the run's end", saved[0]["error"])
        self.assertGreater((datetime.now(timezone.utc) - datetime.fromisoformat(saved[0]["started"]))
                           .total_seconds(), validation.TIMEOUT)
        self.assertIn("retained", saved[0]["error"])
        self.assertEqual(Path(saved[0]["log"]).read_text(), "before restart\n")
        self.assertEqual((Path(saved[0]["results"]) / "check").read_text(), "partial evidence\n")
        self.assertTrue(any(e["kind"] == "machine-run" for e in S.read_events(self.project, self.slug)))

    def test_startup_keeps_originals_when_interrupted_delivery_cannot_finish(self):
        area = validation.home() / "runs" / "interrupted"
        (area / "results").mkdir(parents=True)
        unit = "altitude-validation-interrupted.service"
        S.write_json(area / "run.json", {"project": self.project, "slug": self.slug, "unit": unit})
        (area / "results" / "check").write_text("partial evidence")
        engines.machine_files(area, unit)[0].write_text("partial log")
        T.start_machine_run(self.project, self.slug, lambda n: {"purpose": "validation", "unit": unit, "command": "check"})
        validation._ready.clear()
        with mock.patch.object(validation, "_deliver", side_effect=OSError("disk full")):
            validation.reconcile()
        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl")
                 .read_text().splitlines()]
        self.assertEqual(row["ended"], "interrupted")
        self.assertIn("disk full", row["error"])
        self.assertEqual(Path(row["log"]).read_text(), "partial log")
        self.assertEqual((Path(row["results"]) / "check").read_text(), "partial evidence")
        validation.reconcile()
        self.assertTrue(area.exists())
        self.assertFalse(validation._ready.is_set())

    def test_cli_door(self):
        self.serving(self.httpd.server_address[1])
        base = {"ALTITUDE_PROJECT": self.project}
        owner = {**base, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}
        result = self.alt("task", "validate", "--", "true", env={**base, "ALTITUDE_ACTOR": "l3"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("not available to an L3", result.stderr)
        result = self.alt("task", "validate", "--", "sh", "-c", "echo via-cli; exit 3", env=owner)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("via-cli\n", result.stdout)
        self.assertIn("exit 3", result.stdout)
        self.assertIn(f"results {S.task_dir(self.project, self.slug) / 'validation' / '1'}", result.stdout)

    def test_a_failed_cleanup_keeps_the_area_until_a_request_removes_it_and_an_unrecorded_outcome_keeps_its_record(self):
        with mock.patch.object(T, "finish_machine_run", side_effect=OSError("disk full")):
            self.assertIn("disk full", self.validate(["true"], status=400)["error"])
        [area] = list((validation.home() / "runs").iterdir())
        self.assertTrue((area / "run.json").exists(), "the runner still knows which row to finish")
        failure = "the cleanup unit ended without success: 3"
        with mock.patch.object(validation, "cleanup", return_value=failure):
            validation.reconcile()
            self.assertFalse(validation._ready.is_set())
            self.assertIn(f"has not finished removing what earlier runs left: {failure}",
                          self.validate(["true"], status=400)["error"])
        self.assertEqual(self.validate(["true"])["exit"], 0, "the next request removes the area, without a restart")
        self.assertTrue(validation._ready.is_set())
        self.assertFalse(area.exists())
        self.assertEqual([row["ended"] for row in self.rows()], ["interrupted", "exit"])

    def test_startup_leaves_validation_rows_to_the_runner(self):
        T.start_machine_run(self.project, self.slug, lambda n: {
            "purpose": "validation", "command": "true", "unit": "altitude-validation-old.service"})
        self.assertEqual(server.settle_interrupted_machine_commands(), [])

    def test_recovery_keeps_original_evidence_when_a_prior_terminal_event_names_it(self):
        write = S.atomic_write

        def interrupted_write(path, *args, **kwargs):
            if path.name == "machine.jsonl" and any(e["kind"] == "machine-run"
                                                   for e in S.read_events(self.project, self.slug)):
                raise OSError("interrupted ledger replacement")
            return write(path, *args, **kwargs)

        with mock.patch.object(validation, "_deliver", side_effect=OSError("temporary copy failure")), \
                mock.patch.object(S, "atomic_write", side_effect=interrupted_write):
            result = self.validate(["sh", "-c", "echo original-log; echo original-result > /results/check"], status=400)
        self.assertIn("interrupted ledger", result["error"])
        [area] = list((validation.home() / "runs").iterdir())
        validation.reconcile()
        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl")
                 .read_text().splitlines()]
        self.assertEqual(row["ended"], "failed", "the first terminal event remains authoritative")
        self.assertEqual(Path(row["results"]), area / "results")
        self.assertEqual(Path(row["log"]).read_text(), "original-log\n")
        self.assertEqual((Path(row["results"]) / "check").read_text(), "original-result\n")
        self.assertFalse(validation._ready.is_set())
        validation.reconcile()
        self.assertTrue(area.exists(), "repeated startup never deletes the ledger's evidence")

    def test_an_ordinary_grant_named_validation_is_still_settled_at_startup(self):
        T.start_machine_run(self.project, self.slug, lambda n: {
            "purpose": "validation", "command": "true", "unit": "altitude-machine-1.service"})
        (S.task_dir(self.project, self.slug) / "altitude-machine-1.service.exit").write_text("0")
        [settling] = server.settle_interrupted_machine_commands()
        settling.join(30)
        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                 .splitlines()]
        self.assertEqual((row["exit"], row["purpose"]), (0, "validation"))

    def test_finishing_again_after_an_interrupted_finish_keeps_the_first_outcome_once(self):
        row = T.start_machine_run(self.project, self.slug, lambda n: {
            "purpose": "validation", "command": "true", "unit": "altitude-validation-x.service"})
        with mock.patch.object(S, "atomic_write", side_effect=OSError("altd stopped")):
            with self.assertRaises(OSError):
                T.finish_machine_run(self.project, self.slug, {**row, "exit": 0, "finished": S.now(), "ended": "exit"})
        T.finish_machine_run(self.project, self.slug, {**row, "finished": S.now(), "ended": "interrupted"})
        [saved] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                   .splitlines()]
        self.assertEqual((saved["ended"], saved["exit"]), ("exit", 0))
        self.assertEqual(len([e for e in S.read_events(self.project, self.slug) if e["kind"] == "machine-run"]), 1)

    def test_a_run_that_never_started_leaves_nothing_and_keeps_the_runner_open(self):
        with mock.patch.object(validation, "_clone", side_effect=OSError("worktree unreadable")):
            self.assertIn("worktree unreadable", self.validate(["true"], status=400)["error"])
        self.assertEqual(list((validation.home() / "runs").iterdir()), [])
        self.assertFalse((S.task_dir(self.project, self.slug) / "machine.jsonl").exists())
        self.assertEqual(self.validate(["true"])["ended"], "exit")

    def test_a_run_whose_cleanup_fails_is_never_a_success(self):
        with mock.patch.object(validation, "cleanup_script", return_value="exit 3"):
            result = self.validate(["true"])
        self.assertEqual((result["exit"], result["ended"]), (0, "cleanup failed"))
        self.assertIn("cleanup unit ended without success", result["cleanup"])
        [row] = self.rows()
        self.assertEqual((row["ended"], row["cleanup"]), ("cleanup failed", result["cleanup"]))
        self.assertFalse(validation._ready.is_set())
        self.assertEqual(self.validate(["true"])["ended"], "exit", "the next request removes the area first")
        self.assertEqual(list((validation.home() / "runs").iterdir()), [])
        self.assertEqual(self.rows()[0]["ended"], "cleanup failed", "removing the area keeps the recorded failure")
        self.serving(self.httpd.server_address[1])
        owner = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                 "ALTITUDE_ATTEMPT": "1"}
        with mock.patch.object(validation, "cleanup_script", return_value="exit 3"):
            cli = self.alt("task", "validate", "--", "true", env=owner)
        self.assertEqual(cli.returncode, 1, "exit 0 with a failed cleanup is a failure")
        self.assertIn("exit 0; cleanup failed", cli.stdout)


# sandbox-exec stand-in: record the profile, then run the command it confines.
SANDBOX = r'''#!/bin/sh
printf '%s' "$2" > PROFILE
shift 2
exec "$@"
'''

# A launchd job stand-in for logged_job_command: the command's output and exit status as the job writes them.
LOGGED = 'bash -c "$1" >> "$3" 2>&1; status=$?; printf %s "$status" > "$2.tmp" && mv "$2.tmp" "$2"; exit "$status"'


class MacRunnerCase(RunnerCase):
    """macOS: the job launcher and sandbox-exec are fixtures; the script, profile, environment, records and cleanup
    are real. scripts/platform_probe.py's validation-confinement row checks the profile itself on a Mac."""
    host = "darwin"

    def setUp(self):
        super().setUp()
        self.profile = self.tmp / "profile.sb"
        sandbox = self.bin_dir / "sandbox-exec"
        sandbox.write_text(SANDBOX.replace("PROFILE", shlex.quote(str(self.profile))))
        sandbox.chmod(0o755)
        self.patch(platform, "SANDBOX_EXEC", str(sandbox))
        self.patch(platform, "logged_job_command", side_effect=lambda name, command, *, log, status, **kwargs:
                   ["/bin/bash", "-c", LOGGED, "job", command, str(status), str(log)])
        self.patch(platform, "validation_temp", side_effect=lambda run: self.tmp / f"av-{run}")
        hidden = Path.home() / "bin"
        self.setenv("PATH", f"{hidden}:{os.environ['PATH']}")
        self.developer = self.tmp / "Developer"
        (self.developer / "usr/bin").mkdir(parents=True)
        select = self.bin_dir / "xcode-select"
        select.write_text(f"#!/bin/sh\n[ \"$1\" = -p ] && echo {shlex.quote(str(self.developer))}\n")
        select.chmod(0o755)
        self.patch(platform, "XCODE_SELECT", str(select))


class TestMacValidationRunner(MacRunnerCase):
    def test_a_run_uses_the_committed_head_under_the_validation_profile_with_its_own_environment(self):
        (self.repo / "uncommitted.txt").write_text("not tested\n")
        result = self.validate(["sh", "-c", 'git rev-parse HEAD > "$VALIDATION_RESULTS/head"; '
                                            'env > "$VALIDATION_RESULTS/env"; echo done; test ! -e uncommitted.txt'])
        self.assertEqual((result["exit"], result["ended"], result["commit"]), (0, "exit", self.head))
        self.assertIn("done\n", result["output"])
        results = Path(result["results"])
        self.assertEqual((results / "head").read_text().strip(), self.head)
        env = dict(line.split("=", 1) for line in (results / "env").read_text().splitlines())
        area = Path(env["HOME"]).parent
        self.assertEqual(area.parent, validation.home() / "runs")
        self.assertEqual((env["TMPDIR"], env["VALIDATION_RESULTS"], env["ALTITUDE_VALIDATION"]),
                         (str(self.tmp / f"av-{area.name}"), str(area / "results"), "1"))
        self.assertFalse({key for key in env if key.startswith(("ALTITUDE_", "GIT_", "CLAUDE", "CODEX"))}
                         - {"ALTITUDE_VALIDATION"}, "nothing of altd's environment crosses")
        self.assertNotIn(str(Path.home() / "bin"), env["PATH"].split(":"), "folders the profile hides are dropped")
        altd = self.altd_path()
        at = altd.index("/usr/bin") if "/usr/bin" in altd else len(altd)
        self.assertEqual(env["PATH"].split(":"), [*altd[:at], str(self.developer / "usr/bin"), *altd[at:]],
                         "git and python3 skip /usr/bin's xcrun shims; altd's earlier entries still come first")
        self.assertEqual(self.profile.read_text(), platform.validation_profile(
            (area / "work", area / "results", area / "home", self.tmp / f"av-{area.name}"),
            area / f"{result['unit']}.log",
            config.PORT),
            "the candidate's folders only: the runner's own files beside them stay out of its reach")
        self.assertFalse(area.exists() or (self.tmp / f"av-{area.name}").exists())
        self.assertEqual(self.stops.call_args.args[0], result["unit"])
        [row] = self.rows()
        self.assertEqual({k: row[k] for k in ("commit", "ended", "cleanup", "kvm", "publish")},
                         {"commit": self.head, "ended": "exit", "cleanup": None, "kvm": False, "publish": None})
        self.assertTrue(row["isolation"].startswith("seatbelt:"))
        self.assertEqual(row["isolation"], "seatbelt:" + hashlib.sha256(self.profile.read_bytes()).hexdigest()[:16],
                         "the receipt identifies the actual admitted profile, including run paths")
        self.assertTrue(row["host"].startswith("macOS "))

    def altd_path(self):
        home = os.path.realpath(Path.home())
        return [entry for entry in os.environ["PATH"].split(":")
                if entry and not Path(os.path.realpath(entry)).is_relative_to(home)]

    def test_a_host_without_developer_tools_keeps_altds_path(self):
        self.patch(platform, "XCODE_SELECT", str(self.tmp / "absent"))
        result = self.validate(["sh", "-c", 'echo "$PATH" > "$VALIDATION_RESULTS/path"'])
        self.assertEqual((Path(result["results"]) / "path").read_text().strip().split(":"), self.altd_path())

    def test_container_options_are_refused(self):
        self.assertIn("Linux container runner", self.validate(["true"], kvm=True, status=400)["error"])
        self.assertIn("Linux container runner", self.validate(["true"], publish=8000, status=400)["error"])
        self.assertFalse((S.task_dir(self.project, self.slug) / "machine.jsonl").exists())

    def test_processes_left_running_fail_the_run_and_close_the_runner(self):
        self.active.return_value = True
        result = self.validate(["true"])
        self.assertEqual((result["exit"], result["ended"]), (0, "cleanup failed"))
        self.assertIn("still running", result["cleanup"])
        [area] = list((validation.home() / "runs").iterdir())
        self.assertTrue((area / "work").exists(), "the clone stays while the run's processes may still use it")
        refusal = self.validate(["true"], status=400)["error"]
        self.assertIn(f"processes of {result['unit']} are still running", refusal)
        self.assertTrue((area / "work").exists(), "a request does not remove what running processes may use")
        self.active.return_value = False
        validation.reconcile()
        self.assertEqual(self.stops.call_args_list[-1].args[0], "altitude-validation-*.service")
        self.assertFalse(area.exists() or (self.tmp / f"av-{area.name}").exists())
        self.assertEqual(self.validate(["true"])["ended"], "exit")


    def test_a_link_left_in_place_of_the_temporary_folder_is_removed_without_following_it(self):
        target = self.tmp / "operator-files"
        target.mkdir()
        (target / "keep").write_text("operator\n")
        temp = lambda area: self.tmp / f"av-{area.name}"  # noqa: E731

        def replace(*args, **kwargs):
            [area] = list((validation.home() / "runs").iterdir())
            shutil.rmtree(temp(area))
            temp(area).symlink_to(target)
            return original(*args, **kwargs)
        original = validation.engines.machine_command
        with mock.patch.object(validation.engines, "machine_command", side_effect=replace):
            result = self.validate(["true"])
        self.assertEqual((result["ended"], result["cleanup"]), ("exit", None))
        self.assertEqual((target / "keep").read_text(), "operator\n", "the link's target is untouched")
        self.assertFalse(list((validation.home() / "runs").iterdir()))

    def protected(self, folder):
        """Read-only package fixtures (#700): a file in a folder without write permission."""
        return (f'mkdir -p "{folder}/package/bin" && touch "{folder}/package/bin/runtime" && '
                f'chmod 0555 "{folder}/package/bin" "{folder}/package"')

    def test_folders_left_without_write_permission_are_removed_and_the_runner_stays_open(self):
        result = self.validate(["sh", "-c", f'{self.protected("$TMPDIR")} && {self.protected("$HOME")} && '
                                            f'mkdir "$HOME/sealed" && chmod 0 "$HOME/sealed"'])
        self.assertEqual((result["exit"], result["ended"], result["cleanup"]), (0, "exit", None))
        self.assertEqual(self.rows()[0]["cleanup"], None)
        self.assertTrue(validation._ready.is_set())
        self.assertEqual(list((validation.home() / "runs").iterdir()), [])
        self.assertEqual(list(self.tmp.glob("av-*")), [])
        self.assertEqual(self.validate(["true"])["ended"], "exit")

    def test_a_retained_area_with_a_folder_without_write_permission_is_removed_at_start(self):
        ident = "8baeb2fb7460"
        area, unit = validation.home() / "runs" / ident, f"altitude-validation-{ident}.service"
        area.mkdir(parents=True)
        S.write_json(area / "run.json", {"project": self.project, "slug": self.slug, "unit": unit})
        row = T.start_machine_run(self.project, self.slug, lambda n: {"purpose": "validation", "command": "true",
                                                                       "unit": unit})
        T.finish_machine_run(self.project, self.slug, {
            **row, "exit": 0, "finished": S.now(), "ended": "cleanup failed", "cleanup": "could not remove",
            "results": str(S.task_dir(self.project, self.slug) / "validation" / "1")})
        temp = self.tmp / f"av-{ident}"
        temp.mkdir()
        subprocess.run(["sh", "-c", self.protected(temp)], check=True)
        validation._ready.clear()
        validation.reconcile()
        self.assertTrue(validation._ready.is_set())
        self.assertFalse(area.exists() or temp.exists())
        self.assertEqual(self.rows()[0]["ended"], "cleanup failed", "the recorded outcome stays")

    def test_what_cannot_be_removed_is_named_and_a_later_request_removes_it_without_a_restart(self):
        with mock.patch.object(validation.os, "chmod"):  # permissions cannot be restored
            result = self.validate(["sh", "-c", self.protected("$TMPDIR")])
            [area] = list((validation.home() / "runs").iterdir())
            stuck = f"{self.tmp / f'av-{area.name}'}/package/bin/runtime: Permission denied"
            self.assertEqual((result["exit"], result["ended"]), (0, "cleanup failed"))
            self.assertEqual(result["cleanup"], f"could not remove {stuck}")
            self.assertEqual(self.rows()[0]["cleanup"], result["cleanup"])
            self.assertFalse(validation._ready.is_set())
            self.assertIn(stuck, self.validate(["true"], status=400)["error"])
            validation.reconcile()
            self.assertTrue(area.exists(), "the area stays as the marker while its temporary folder remains")
        self.assertEqual(self.validate(["true"])["ended"], "exit")
        self.assertFalse(area.exists() or (self.tmp / f"av-{area.name}").exists())


    def test_restoring_permissions_never_follows_a_link_swapped_in_for_a_folder(self):
        outside = self.tmp / "operator-files"
        outside.mkdir(mode=0o500)
        self.addCleanup(outside.chmod, 0o700)
        temp = self.tmp / "av-swap"
        (temp / "package").mkdir(parents=True)
        chmod = os.chmod

        def swapped(name, mode, *, dir_fd=None, follow_symlinks=True):
            if name == "package":  # after the folder was seen, before its permissions change
                os.rmdir("package", dir_fd=dir_fd)
                os.symlink(outside, "package", dir_fd=dir_fd)
            return chmod(name, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        with mock.patch.object(validation.os, "chmod", side_effect=swapped):
            self.assertIsNone(validation._remove(temp))
        self.assertFalse(os.path.lexists(temp))
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o500, "the link's target is untouched")


class TestValidationProfile(TestCase):
    def test_a_socket_nested_in_the_temporary_folder_accepts_connections(self):
        """In a macOS validation run the suite's folder is in the run's TMPDIR, a later root of its profile, where
        the coordinator's verb broker binds (#695)."""
        with tempfile.TemporaryDirectory() as folder, socket.socket(socket.AF_UNIX) as server_socket, \
                socket.socket(socket.AF_UNIX) as client:
            path = Path(folder) / "at-x" / "case-y" / "altitude" / "l3-verbs" / "a.sock"
            path.parent.mkdir(parents=True)
            server_socket.bind(str(path))
            server_socket.listen()
            client.connect(str(path))

    def test_a_root_replaced_by_a_link_admits_the_link_never_its_target(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(platform.sys, "platform", "darwin"):
            link = Path(folder) / "av-run"
            link.symlink_to(Path.home())
            profile = platform.validation_profile((link,), Path(folder) / "unit.log", 8890)
        own = os.path.join(os.path.realpath(folder), "av-run")
        self.assertIn(f'(allow file-read* (subpath "{own}"))', profile)
        self.assertNotIn(f'(allow file-read* (subpath "{os.path.realpath(Path.home())}"))', profile)


    def test_the_profile_confines_a_run_to_its_own_folders(self):
        with mock.patch.object(platform.sys, "platform", "darwin"), \
                mock.patch.object(platform, "_user_temp", return_value="/private/var/folders/ab/cd/T/"):
            area = Path.home() / '.altitude-validation/runs/a"b'
            profile = platform.validation_profile((area / "work", area / "results"), area / "unit.log", 8890)
        home = os.path.realpath(Path.home())
        own = str(Path(home) / '.altitude-validation/runs/a\\"b')
        roots = f'(subpath "{own}/work") (subpath "{own}/results")'
        shared = '(subpath "/private/var/folders/ab/cd") (subpath "/private/tmp") (subpath "/private/var/tmp")'
        for clause in (f'(deny file-write*)(allow file-write* {roots} (subpath "/dev"))',
                       f'(deny file-read* (subpath "{home}") {shared})(allow file-read* {roots})',
                       f'(allow file-write-data file-read-metadata (literal "{own}/unit.log"))', f'(literal "{own}")', f'(literal "{home}")', '(literal "/private/tmp")',
                       '(deny network-bind (local ip "*:8890"))', '(deny network-outbound (remote ip "*:8890"))',
                       '(deny network-outbound (remote unix-socket))(allow network-outbound '
                       f'(remote unix-socket (subpath "{own}/work")) (remote unix-socket (subpath "{own}/results")) '
                       '(remote unix-socket (path-literal "/private/var/run/mDNSResponder"))',
                       '(allow file-read* (literal "/private/var/folders/ab/cd/T/xcrun_db"))',
                       '(global-name "com.apple.SecurityServer")', "(allow signal (target same-sandbox))",
                       '(global-name-prefix "com.apple.CoreSimulator.")', "(deny lsopen)"):
            self.assertIn(clause, profile)
        self.assertNotIn(f'(subpath "{own}")', profile, "the runner's files beside the candidate's folders stay out")
        self.assertNotRegex(profile, r'\(remote unix-socket \([^()]*\) \(',
                            "Seatbelt honors only a unix-socket's first path, so none has a second (#695)")
