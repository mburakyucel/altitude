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
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.test_machine_access import SHIM
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T, terminal, validation

RUNNER_HOME = validation.home

PODMAN = r'''#!/usr/bin/env python3
"""podman stand-in: record the call; run a container's command on the host with its mounts in place."""
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


class TestValidationRunner(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "systemd-run"
        shim.write_text(SHIM)
        shim.chmod(0o755)
        self.record, built = self.tmp / "podman.jsonl", self.tmp / "built"
        podman = bin_dir / "podman"
        podman.write_text(PODMAN.replace("RECORD", repr(str(self.record)), 1).replace("BUILT", repr(str(built))))
        podman.chmod(0o755)
        self.patch(platform, "SYSTEMD_RUN", str(shim))
        self.patch(validation, "podman", return_value=[str(podman)])
        self.patch(platform, "validation_unavailable", return_value=None)
        self.patch(validation, "FREE_DISK", 0)   # the host's free disk is not the fixture's
        self.owner = self.patch(terminal, "owner_connection", return_value=True)
        self.runner = self.tmp / "runner"
        self.patch(validation, "home", return_value=self.runner)
        validation._ready.set()
        self.stops = self.patch(platform, "job_stop")
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
                      "--device=/dev/fuse", "--device=/dev/net/tun", f"--publish=127.0.0.1:{host}:8890"):
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
        self.assertEqual({k: row[k] for k in ("n", "purpose", "commit", "kvm", "exit", "ended", "image")},
                         {"n": 1, "purpose": "validation", "commit": self.head, "kvm": False, "exit": 1,
                          "ended": "exit", "image": validation.image_tag()})
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
            self.assertEqual(stages, ["clone", "job", "deliver", "record", "job"])
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
        validation._ready.clear()
        self.assertIn("has not finished removing", self.validate(["true"], status=400)["error"])
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
        self.assertIn("altd stopped during the run", saved[0]["error"])
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

    def test_a_failed_cleanup_keeps_the_runner_closed_and_an_unrecorded_outcome_keeps_its_record(self):
        with mock.patch.object(T, "finish_machine_run", side_effect=OSError("disk full")):
            self.assertIn("disk full", self.validate(["true"], status=400)["error"])
        [area] = list((validation.home() / "runs").iterdir())
        self.assertTrue((area / "run.json").exists(), "the next start still knows which row to finish")
        validation._ready.clear()
        with mock.patch.object(validation, "cleanup", return_value={"exit": 0}):
            validation.reconcile()
        self.assertFalse(validation._ready.is_set())
        self.assertIn("has not finished removing", self.validate(["true"], status=400)["error"])
        validation.reconcile()
        self.assertTrue(validation._ready.is_set())
        self.assertFalse(area.exists())
        [row] = [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                 .splitlines()]
        self.assertEqual(row["ended"], "interrupted")
        self.assertEqual(self.validate(["true"])["exit"], 0)

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
