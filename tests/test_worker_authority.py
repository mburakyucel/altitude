"""A task worker cannot act as the project's coordinator or as the operator's CLI: the coordinator socket serves only
the running coordinator turn's job, the task door admits only the task's own worker job, and no Altitude sandbox can
read the CLI's key, the pairing record or the HTTPS keys."""
import json
import os
import socket
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import access, config, engines, l3, platform, server, state as S, tasks as T, tls

TURN = "altitude-coordinator-0123.service"
WORKER = "altitude-claude-4567.service"


class TestCoordinatorSocket(AltitudeCase):
    def setUp(self):
        super().setUp()
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        self.served = self.patch(server, "l3_verb_request", return_value={"returncode": 0, "stdout": "ok\n", "stderr": ""})

    def running(self, project: str, unit: str) -> None:
        """A coordinator turn of `project` runs as the job `unit`."""
        l3._active[project] = {"id": f"turn-{project}"[:12], "trigger": "chat", "unit": unit}
        self.addCleanup(l3._active.pop, project, None)

    def ask(self) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(l3.verb_socket_path(self.project)))
            client.sendall(b'{"kind": "alt", "args": ["state"]}\n')
            client.shutdown(socket.SHUT_WR)
            return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))

    def test_the_running_turns_job_is_served_and_every_other_caller_is_refused_unread(self):
        other = "other-project"
        self.register(other)
        self.running(other, "altitude-coordinator-89ab.service")
        callers = {"a process in the turn's job": (TURN, ["user.slice", "app.slice", TURN]),
                   "a task worker": (TURN, ["user.slice", "app.slice", WORKER]),
                   "another project's turn": (TURN, ["app.slice", "altitude-coordinator-89ab.service"]),
                   "a process that cannot be told (ended, or reused)": (TURN, []),
                   "a worker while no turn runs": (None, ["app.slice", WORKER])}
        for caller, (unit, groups) in callers.items():
            with self.subTest(caller=caller):
                self.served.reset_mock()
                if unit:
                    self.running(self.project, unit)
                else:
                    l3._active.pop(self.project, None)
                with mock.patch.object(platform, "peer_groups", return_value=groups):
                    response = self.ask()
                if caller == "a process in the turn's job":
                    self.assertEqual(response["stdout"], "ok\n")
                    self.served.assert_called_once_with(self.project, {"kind": "alt", "args": ["state"]})
                else:
                    self.assertEqual(response, {"error": server.L3_VERB_REFUSED})
                    self.served.assert_not_called()

    def test_the_real_seam_refuses_a_process_outside_the_turn(self):
        """No fixture: this test process, like any worker, is not in the recorded turn's job."""
        self.running(self.project, TURN)
        self.assertEqual(self.ask(), {"error": server.L3_VERB_REFUSED})
        self.served.assert_not_called()

    def test_each_turn_records_a_fresh_job_before_it_launches_on_both_engines(self):
        self.private_ledgers()
        seen = []

        def engine(name):
            def launch(prompt, **kwargs):
                seen.append((name, kwargs["unit"], l3.turn_unit(self.project)))
                return {"text": "answered", "session_id": f"{name}-1", "reported_session_id": f"{name}-1",
                        "error": None, "usage": {}, "returncode": 0}
            return launch

        for name in ("claude", "codex"):
            with mock.patch.object(l3, "_select", return_value={"engine": name, "why": "test", "quota": {}}), \
                 mock.patch.object(engines, "claude_print", side_effect=engine("claude")), \
                 mock.patch.object(engines, "codex_exec", side_effect=engine("codex")):
                l3.turn(self.project, "coordinate")
        self.assertEqual([name for name, _, _ in seen], ["claude", "codex"])
        for _, unit, recorded in seen:
            self.assertRegex(unit, r"^altitude-coordinator-[0-9a-f]{32}\.service$")
            self.assertEqual(recorded, unit, "the socket serves the job from the turn's first request")
        self.assertNotEqual(seen[0][1], seen[1][1])
        self.assertIsNone(l3.turn_unit(self.project), "an ended turn's job is served no more")


@unittest.skipUnless(sys.platform == "linux", "Linux names the connecting process with a process handle")
class TestLinuxPeerIdentity(unittest.TestCase):
    def test_the_connecting_process_is_named_and_an_ended_one_is_not(self):
        path = Path(os.environ["XDG_RUNTIME_DIR"]) / "peer.sock"
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen()
            child = subprocess.Popen([sys.executable, "-c", "import socket, sys; s = socket.socket(socket.AF_UNIX); "
                                      f"s.connect({str(path)!r}); sys.stdin.read()"], stdin=subprocess.PIPE)
            connection, _ = listener.accept()
            with connection:
                expected = Path(f"/proc/{child.pid}/cgroup").read_text()
                self.assertEqual(platform.peer_groups(connection),
                                 [part for line in expected.splitlines() for part in line.split("/")])
                child.communicate(b"")
                self.assertEqual(platform.peer_groups(connection), [], "an ended process vouches for nothing")
        path.unlink()


class TestCoordinatorFolder(AltitudeCase):
    def test_sockets_and_turn_commands_live_where_no_task_worker_can_write(self):
        folder = platform.coordinator_directory()
        runtime = l3._l3_runtime(self.project, "claude")
        self.addCleanup(l3._remove_runtime, runtime)
        self.assertEqual((runtime.parent, l3.verb_socket_path(self.project).parent), (folder, folder))
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        worktree = self.tmp / "worktree"
        worktree.mkdir()
        settings = engines.codex_sandbox(worktree, extra_roots=[self.repo])
        roots = json.loads(next(s for s in settings if ".workspace_roots=" in s).split("=", 1)[1]
                           .replace("=true", ":true"))
        self.assertFalse([root for root in roots if folder.resolve().is_relative_to(root)])
        self.assertFalse(folder.resolve().is_relative_to(config.ROOT.resolve()))
        self.assertEqual(platform.coordinator_directory(), folder, "one folder per installation")


class TestCredentialsHidden(AltitudeCase):
    def test_every_altitude_sandbox_refuses_the_key_pairing_and_https_folders(self):
        access.prepare()
        config.TLS_DIR.mkdir(parents=True, exist_ok=True)
        secrets = {os.path.realpath(access.DIR), os.path.realpath(config.TLS_DIR)}
        self.assertEqual(set(platform.secret_places()), secrets)
        worktree = self.tmp / "worktree"
        worktree.mkdir()
        for settings in (engines.codex_sandbox(worktree), engines.codex_l3_permissions(worktree, project=self.project)):
            rules = next(s for s in settings if ".filesystem=" in s)
            for place in secrets:
                self.assertIn(f'{json.dumps(place)}="deny"', rules)
        rule = platform.private_rule()
        for place in secrets:
            self.assertIn(f'(subpath "{place}")', rule)
        self.assertTrue(rule.startswith("(deny file-read* file-write* "))
        self.assertNotEqual(tls.record().parent, access.DIR, "a sandboxed owner's alt still finds Altitude")


class TestKeyGenerations(AltitudeCase):
    gated = True

    def table(self, *units):
        return {pid: (1, ["user.slice", unit]) for pid, unit in enumerate(units, start=2)}

    def test_an_earlier_key_is_replaced_twice_and_a_current_one_kept(self):
        access.DIR.mkdir(parents=True, exist_ok=True)
        (access.DIR / "key.json").unlink(missing_ok=True)
        (access.DIR / "machine.key").write_text("readable-before-the-update\n")
        access.issue_code()
        lasting = ("altitude.service", "altitude-volumes.service", "altitude-terminal-a1.service")  # no agent
        running = self.patch(platform, "process_table", return_value=self.table(WORKER, TURN, *lasting))
        access.prepare()
        first = access.machine_key()
        self.assertNotEqual(first, "readable-before-the-update")
        self.assertFalse(access.is_machine("readable-before-the-update"))
        self.assertIsNone(json.loads((access.DIR / "devices.json").read_text())["code"], "a code in flight ends")
        self.assertEqual(json.loads((access.DIR / "key.json").read_text()),
                         {"generation": access.GENERATION, "after": sorted([WORKER, TURN])})
        access.settle()
        self.assertEqual(access.machine_key(), first, "a worker running at the update has not ended")
        running.return_value = self.table("altitude-codex-later.service", *lasting)
        access.settle()
        second = access.machine_key()
        self.assertNotEqual(second, first)
        self.assertEqual(json.loads((access.DIR / "key.json").read_text())["after"], [])
        access.prepare()
        access.settle()
        self.assertEqual(access.machine_key(), second)

    def test_a_first_key_keeps_the_installers_pairing_code(self):
        (access.DIR / "machine.key").unlink(missing_ok=True)
        (access.DIR / "key.json").unlink(missing_ok=True)
        access.issue_code(first_browser=True)
        self.patch(platform, "process_table", return_value=self.table(WORKER))
        access.prepare()
        self.assertIsNotNone(access.machine_key())
        self.assertIsNotNone(json.loads((access.DIR / "devices.json").read_text())["code"])
        self.assertEqual(json.loads((access.DIR / "key.json").read_text()), {"generation": access.GENERATION, "after": []})


class TestServiceRecord(AltitudeCase):
    def test_the_record_carries_the_public_ca_certificate_and_nothing_secret(self):
        tls_dir = self.tmp / "tls"
        tls_dir.mkdir()
        (tls_dir / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\nfictional\n-----END CERTIFICATE-----\n")
        (tls_dir / "ca.key").write_text("private")
        self.addCleanup(tls.record().unlink, missing_ok=True)
        tls.publish({"host": "127.0.0.1", "port": 8443, "tls": True, "tls_dir": tls_dir})
        self.assertEqual(tls.service()["ca"], (tls_dir / "ca.crt").read_text())
        self.assertNotIn("private", tls.record().read_text())
        (tls_dir / "ca.crt").unlink()  # an external certificate has no CA here
        tls.publish({"host": "127.0.0.1", "port": 8443, "tls": True, "tls_dir": tls_dir})
        self.assertIsNone(tls.service()["ca"])


class TestTaskDoor(AltitudeCase):
    """`alt task run`, `validate`, `review run` and `terminal` reach altd without the operator's key, from the
    task's own worker job only; every other caller, key or not, gets one refusal that says nothing about tasks."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        task = T.new(self.project, "Door", "Run the authorized command.")
        task.update(state="running", attempt=2, agent_id="worker-1")
        S.save_task(self.project, task)
        self.slug = task["slug"]
        self.owner = self.patch(server, "task_owner_connection", return_value=False)
        self.queued = self.patch(server.Handler, "_in_line", autospec=True,
                                 side_effect=lambda handler, call: handler._json({"admitted": True}))
        self.reviewed = self.patch(server.reviews, "run", return_value={"id": "review"})
        self.terminal = self.patch(server, "owner_terminal_output", return_value={"state": "none"})
        self.port = self.listen(server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler))

    def listen(self, httpd) -> int:
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_port

    def post(self, endpoint: str, body: dict, key: str | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/{endpoint}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  **({access.KEY_HEADER: key} if key else {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def bodies(self, attempt=2, slug=None, project=None) -> dict:
        common = {"project": project or self.project, "slug": slug or self.slug, "attempt": attempt}
        return {"task/run": {**common, "command": "true", "request": "a" * 32},
                "task/validate": {**common, "command": ["true"]},
                "task/terminal": common,
                "task/review/run": {**common, "review_id": "r1"}}

    def test_every_other_caller_gets_one_refusal_before_anything_reads_or_queues(self):
        access.prepare()
        self.patch(access, "is_machine", side_effect=lambda key: key == access.machine_key())
        cases = {"a local process outside the worker, holding the key": (self.bodies(), access.machine_key()),
                 "an earlier attempt": (self.bodies(attempt=1), None),
                 "a task that does not exist": (self.bodies(slug="no-such-task"), None),
                 "a project that does not exist": (self.bodies(project="no-such-project"), None)}
        for case, (bodies, key) in cases.items():
            for endpoint, body in bodies.items():
                with self.subTest(case=case, endpoint=endpoint):
                    self.assertEqual(self.post(endpoint, body, key), (403, {"error": server.TASK_DOOR_REFUSED}))
        self.queued.assert_not_called()
        self.reviewed.assert_not_called()
        self.terminal.assert_not_called()
        self.assertEqual({call.args[:2] for call in self.owner.call_args_list}, {(self.project, self.slug)},
                         "only the running attempt's own task is checked for its worker")

    def test_a_running_task_without_its_worker_record_refuses_the_same_way(self):
        self.owner.side_effect = RuntimeError("Worker ownership record is unavailable; stop is unconfirmed")
        for endpoint, body in self.bodies().items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.post(endpoint, body), (403, {"error": server.TASK_DOOR_REFUSED}))
        self.queued.assert_not_called()

    def test_the_owners_worker_needs_no_key(self):
        self.patch(access, "is_machine", return_value=False)
        self.owner.return_value = True
        for endpoint in ("task/run", "task/validate"):
            self.assertEqual(self.post(endpoint, self.bodies()[endpoint]), (200, {"admitted": True}))
        self.assertEqual(self.post("task/review/run", self.bodies()["task/review/run"]),
                         (200, {"ok": True, "review": {"id": "review"}}))
        self.assertEqual(self.post("task/terminal", self.bodies()["task/terminal"]), (200, {"state": "none"}))
        project, slug, task, peer, local = self.owner.call_args.args
        self.assertEqual((project, slug, task["agent_id"], peer[0], local[1]),
                         (self.project, self.slug, "worker-1", "127.0.0.1", self.port))

    def test_an_owners_alt_verifies_altitude_from_the_service_record_without_the_https_folder(self):
        self.patch(access, "is_machine", return_value=False)
        self.owner.return_value = True
        self.patch(config, "TLS", True)
        self.patch(config, "TLS_DIR", self.tmp / "tls")
        self.patch(server, "log")
        server.tls_init()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.socket = tls.check("127.0.0.1").wrap_socket(httpd.socket, server_side=True)
        port = self.listen(httpd)
        tls.publish({"host": "127.0.0.1", "port": port, "tls": True, "tls_dir": config.TLS_DIR})
        self.addCleanup(tls.record().unlink, missing_ok=True)
        config.TLS_DIR.rename(self.tmp / "hidden")  # as every sandbox hides it
        result = self.alt("task", "terminal", "--json", env={
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": self.project, "ALTITUDE_TASK": self.slug,
            "ALTITUDE_ATTEMPT": "2"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"state": "none"})
        self.owner.return_value = False
        result = self.alt("task", "terminal", "--json", env={
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": self.project, "ALTITUDE_TASK": self.slug,
            "ALTITUDE_ATTEMPT": "2"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(server.TASK_DOOR_REFUSED, result.stderr)


if __name__ == "__main__":
    unittest.main()
