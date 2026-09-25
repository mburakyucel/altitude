"""An operator's purpose grant opens the machine to one task; every command under it is recorded.

Only the transient-unit launcher is a fixture: a `systemd-run` shim inside the test directory runs the
exact command line altd composes, appends output where the unit would, and enforces the runtime limit.
Authority fences, records, the HTTP door and the CLI door are real.
"""
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from tests.support import AltitudeCase, make_repo
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T

SHIM = r'''#!/usr/bin/env python3
"""systemd-run stand-in: honour the output properties and runtime limit, run the command after `--`."""
import subprocess, sys
args = sys.argv[1:]
command = args[args.index("--") + 1:]
log = next(a.split("append:", 1)[1] for a in args if a.startswith("--property=StandardOutput=append:"))
limit = int(next(a.split("=", 2)[2] for a in args if a.startswith("--property=RuntimeMaxSec=")))
with open(log, "ab") as out:
    try:
        sys.exit(subprocess.run(command, stdout=out, stderr=out, timeout=limit).returncode)
    except subprocess.TimeoutExpired:
        sys.exit(1)
'''


class TestMachineAccess(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        shim = self.tmp / "bin" / "systemd-run"
        shim.parent.mkdir(exist_ok=True)
        shim.write_text(SHIM)
        shim.chmod(0o755)
        self.patch(platform, "SYSTEMD_RUN", str(shim))
        self.at = "2026-09-16T06:00:00+00:00"
        self.patch(S, "now", side_effect=lambda: self.at)
        self.patch(T, "_conversation_time", side_effect=lambda: self.at)
        self.worktree = self.tmp / "worktree"
        self.worktree.mkdir()
        self.slug = T.new(self.project, "Preserve the service configuration", "Keep TLS across the install.")["slug"]
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.worktree), branch="work")
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def tick(self):
        self.at = (datetime.fromisoformat(self.at) + timedelta(seconds=1)).isoformat()

    def request(self, path, body, *, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        try:
            connection.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def run_command(self, command, *, status=200, attempt="1"):
        return self.request("/api/task/run", {"project": self.project, "slug": self.slug, "attempt": attempt,
                                              "command": command}, status=status)

    def ask(self, text="May I edit the service unit, reload and restart it to keep TLS?"):
        self.tick()
        T.block(self.project, self.slug, text, actor="l2", expected_state="running", expected_attempt=1,
                updates={"waiting_on": "burak"})
        return S.load_task(self.project, self.slug)["questions"][-1]

    def answer(self, question, text="Yes, go ahead."):
        self.tick()
        row = T.message(self.project, self.slug, "burak", text, by="burak",
                        question_id=question["id"], revision=question["revision"])
        self.tick()
        T.resume(self.project, self.slug)
        return row

    def resolve(self, question, row):
        self.tick()
        T.resolve_question(self.project, self.slug, question["id"], question["revision"], row["id"],
                           disposition="answered", reason="Operator agreed.", expected_attempt=1)

    def granted(self):
        question = self.ask()
        row = self.answer(question)
        self.resolve(question, row)
        self.tick()
        return T.grant_machine_access(self.project, self.slug, row["id"], question=question["id"],
                                      revision=question["revision"], reason="Purpose matches the answer.",
                                      actor="l3"), question, row

    def unit(self, sequence):
        return f"altitude-machine-{self.project}-{self.slug}-{sequence}.service"

    def events(self, kind):
        return [e for e in S.read_events(self.project, self.slug) if e["kind"] == kind]

    def test_grant_needs_the_operator_answer_to_the_current_question(self):
        question = self.ask()
        with self.assertRaisesRegex(T.TransitionError, "answered the current operator question"):
            T.grant_machine_access(self.project, self.slug, "0" * 32, question=question["id"],
                                   revision=question["revision"], reason="r", actor="l3")
        row = self.answer(question)
        self.tick()
        other = T.message(self.project, self.slug, "burak", "Also, use the blue theme.", by="burak")
        self.resolve(question, row)
        with self.assertRaisesRegex(T.TransitionError, "answered the current operator question"):
            T.grant_machine_access(self.project, self.slug, other["id"], question=question["id"],
                                   revision=question["revision"], reason="r", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "coordinator or the operator"):
            T.grant_machine_access(self.project, self.slug, row["id"], question=question["id"],
                                   revision=question["revision"], reason="r", actor="l2")
        self.assertEqual(len(self.events("machine-grant-refused")), 2)
        self.assertIsNone(S.load_task(self.project, self.slug).get("machine_access"))
        grant = T.grant_machine_access(self.project, self.slug, row["id"], question=question["id"],
                                       revision=question["revision"], reason="Purpose matches.", actor="l3")
        self.assertEqual((grant["purpose"], grant["answer"], grant["approval"], grant["actor"], grant["attempt"]),
                         (question["detail"], "Yes, go ahead.", row["id"], "l3", 1))
        self.assertEqual(S.load_task(self.project, self.slug)["machine_access"], grant)
        self.assertEqual(len(self.events("machine-grant")), 1)
        brief = self.alt("task", "status", self.slug, "--brief", env={"ALTITUDE_PROJECT": self.project})
        self.assertIn(f"machine access: {question['detail']}", brief.stdout)

    def test_grant_binds_the_purpose_the_operator_read_not_a_later_revision(self):
        question = self.ask("May I install ffmpeg under my user?")
        row = self.answer(question)
        self.tick()
        T.block(self.project, self.slug, "Revised purpose", actor="l2", expected_state="running", expected_attempt=1,
                updates={"waiting_on": "burak"}, questions={"questions": [
                    {"id": question["id"], "question": "May I restart the service instead?"}]})
        revised = S.load_task(self.project, self.slug)["questions"][-1]
        self.assertEqual(revised["revision"], question["revision"] + 1)
        self.resolve(revised, row)  # the owner may judge the earlier answer still applies ...
        self.tick()
        with self.assertRaises(T.TransitionError):  # ... but a grant needs the operator's answer to this purpose
            T.grant_machine_access(self.project, self.slug, row["id"], question=revised["id"],
                                   revision=revised["revision"], reason="r", actor="l3")
        self.assertIsNone(S.load_task(self.project, self.slug).get("machine_access"))

    def test_commands_run_only_under_a_grant_and_are_recorded(self):
        refused = self.run_command("echo hello", status=403)
        self.assertIn("no machine grant", refused["error"])
        self.assertIn("alt task machine --grant", refused["error"])
        grant, question, row = self.granted()
        result = self.run_command("echo hello; echo trouble >&2; pwd; exit 3")
        self.assertEqual((result["exit"], result["timed_out"], result["n"], result["error"]), (3, False, 1, None))
        self.assertEqual(result["unit"], self.unit(1))
        self.assertIn("hello\n", result["output"])
        self.assertIn("trouble\n", result["output"])
        self.assertIn(str(self.worktree), result["output"])
        again = self.run_command("echo second")
        self.assertEqual((again["exit"], again["n"], again["unit"]), (0, 2, self.unit(2)))
        self.assertEqual(again["output"], "second\n")
        folder = S.task_dir(self.project, self.slug)
        log = (folder / "machine.log").read_text()
        self.assertIn("hello\n", log)
        self.assertIn("second\n", log)
        rows = [json.loads(line) for line in (folder / "machine.jsonl").read_text().splitlines()]
        self.assertEqual([(r["n"], r["command"], r["exit"], r["purpose"]) for r in rows],
                         [(1, "echo hello; echo trouble >&2; pwd; exit 3", 3, question["detail"]),
                          (2, "echo second", 0, question["detail"])])
        runs = self.events("machine-run")
        self.assertEqual([(e["n"], e["exit"], e["actor"]) for e in runs], [(1, 3, "l2"), (2, 0, "l2")])
        logged = [e for e in S.read_project_log(self.project) if e["kind"] == "machine-run"]
        self.assertEqual([(e["slug"], e["command"], e["exit"]) for e in logged],
                         [(self.slug, "echo hello; echo trouble >&2; pwd; exit 3", 3), (self.slug, "echo second", 0)])

    def test_the_record_exists_before_the_unit_starts_and_one_command_runs_at_a_time(self):
        self.granted()
        runs = S.task_dir(self.project, self.slug) / "machine.jsonl"
        seen = self.run_command(f"cat {runs}")
        pending = json.loads(seen["output"])
        self.assertEqual((pending["n"], pending["unit"], pending["exit"], pending["finished"]),
                         (1, self.unit(1), None, None))
        self.assertIn("interrupted", pending["error"])
        marker = self.tmp / "started"
        with ThreadPoolExecutor(1) as pool:
            first = pool.submit(self.run_command, f"touch {marker}; sleep 1; echo first")
            while not marker.exists():
                pass
            refused = self.run_command("echo second", status=400)
            self.assertIn("one command at a time", refused["error"])
            self.assertEqual((first.result()["n"], first.result()["exit"], first.result()["output"]),
                             (2, 0, "first\n"))
        self.assertEqual(self.run_command("echo third")["n"], 3)
        rows = [json.loads(line) for line in runs.read_text().splitlines()]
        self.assertEqual([(r["n"], r["exit"], r["error"]) for r in rows], [(n, 0, None) for n in (1, 2, 3)])

    def test_a_unit_without_an_exit_status_is_never_a_success(self):
        self.patch(platform, "SYSTEMD_RUN", str(self.tmp / "bin" / "missing"))
        result = engines.machine_command("true", cwd=self.worktree, log=self.tmp / "machine.log", unit=self.unit(1),
                                         identity=dispatch.l2_env(self.project, self.slug, 1))
        self.assertEqual((result["exit"], result["timed_out"]), (None, False))
        self.assertIn("no exit status recorded", result["error"])
        self.assertFalse((self.tmp / f"{self.unit(1)}.exit").exists())

    def test_a_command_past_its_limit_is_reported_as_a_timeout(self):
        self.granted()
        original = engines.machine_command
        self.patch(engines, "machine_command", side_effect=lambda command, **kw: original(command, timeout=1, **kw))
        result = self.run_command("echo started; sleep 5; echo never")
        self.assertEqual((result["exit"], result["timed_out"]), (None, True))
        self.assertIn("limit", result["error"])
        self.assertEqual(result["output"], "started\n")
        self.assertEqual([(e["exit"], e["timed_out"]) for e in self.events("machine-run")], [(None, True)])

    def test_stale_attempts_blocked_tasks_and_revocation_refuse(self):
        self.granted()
        self.assertIn("current attempt", self.run_command("true", attempt="2", status=403)["error"])
        self.assertIn("unsupported fields", self.request("/api/task/run", {"project": self.project, "slug": self.slug,
                                                                            "attempt": "1", "command": "true",
                                                                            "extra": 1}, status=400)["error"])
        self.assertIn("non-empty command", self.run_command("   ", status=400)["error"])
        self.tick()
        T.block(self.project, self.slug, "Waiting on review", actor="l2", expected_state="running", expected_attempt=1)
        self.assertIn("running owner", self.run_command("true", status=403)["error"])
        self.tick()
        T.resume(self.project, self.slug)
        with self.assertRaisesRegex(T.TransitionError, "current attempt"):
            T.revoke_machine_access(self.project, self.slug, "done", actor="l2", expected_attempt=2)
        task = T.revoke_machine_access(self.project, self.slug, "Purpose complete.", actor="l3")
        self.assertIsNone(task["machine_access"])
        self.assertEqual(len(self.events("machine-revoke")), 1)
        self.assertIn("no machine grant", self.run_command("true", status=403)["error"])
        with self.assertRaisesRegex(T.TransitionError, "no machine grant"):
            T.revoke_machine_access(self.project, self.slug, "again", actor="l3")
        self.granted()
        task = T.revoke_machine_access(self.project, self.slug, "Finished early.", actor="l2", expected_attempt=1)
        self.assertIsNone(task["machine_access"])
        self.granted()
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        S.save_task(self.project, task)
        self.assertIn("earlier attempt", self.run_command("true", attempt="2", status=403)["error"])

    def test_cli_door_and_exit_status(self):
        grant, question, row = self.granted()
        base = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_HOST": "127.0.0.1",
                "ALTITUDE_PORT": str(self.httpd.server_address[1]), "ALTITUDE_TLS": "0"}
        owner = {**base, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}
        result = self.alt("task", "run", self.slug, "true", env={**base, "ALTITUDE_ACTOR": "l3"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("not available to an L3", result.stderr)
        result = self.alt("task", "run", "other-task", "true", env=owner)
        self.assertEqual(result.returncode, 1)
        self.assertIn("only for its own task", result.stderr)
        result = self.alt("task", "run", self.slug, "echo via-cli", env=owner)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertIn("via-cli\n", result.stdout)
        self.assertIn(f"[altitude] {self.unit(1)}: exit 0; log ", result.stdout)
        result = self.alt("task", "run", self.slug, "echo failing >&2; exit 7", env=owner)
        self.assertEqual(result.returncode, 7)
        self.assertIn("failing\n", result.stdout)
        self.assertIn("exit 7", result.stdout)
        result = self.alt("task", "machine", self.slug, "--revoke", "--reason", "Purpose complete.", env=owner)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["machine_access"])
        result = self.alt("task", "machine", self.slug, "--grant", "--approval", row["id"], "--question",
                          question["id"], "--revision", str(question["revision"]), "--reason", "Same answer.",
                          env=owner)
        self.assertEqual(result.returncode, 1)
        self.assertIn("L3 or the operator records one", result.stderr)
        result = self.alt("task", "machine", self.slug, "--grant", "--approval", row["id"], "--question",
                          question["id"], "--revision", str(question["revision"]), "--reason", "Same answer.",
                          env={**base, "ALTITUDE_ACTOR": "l3"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["purpose"], question["detail"])

    def test_transient_unit_keeps_the_bus_the_limit_the_owner_identity_and_its_own_record(self):
        env = engines.codex_env(dispatch.l2_env(self.project, self.slug, 1), retain_user_bus=True)
        argv = platform.logged_job_command("altitude-machine-x-1.service", "systemctl --user daemon-reload",
                                                log=self.tmp / "machine.log", status=self.tmp / "unit.exit",
                                                env=env, timeout=config.MACHINE_COMMAND_TIMEOUT)
        self.assertEqual(argv[:3], [str(self.tmp / "bin" / "systemd-run"), "--user", "--wait"])
        for expected in ("--collect", "--unit=altitude-machine-x-1.service", "--property=KillMode=control-group",
                         f"--property=RuntimeMaxSec={config.MACHINE_COMMAND_TIMEOUT}",
                         f"--property=StandardOutput=append:{self.tmp / 'machine.log'}",
                         f"--property=StandardError=append:{self.tmp / 'machine.log'}"):
            self.assertIn(expected, argv)
        scrub = argv[argv.index("--") + 1:]
        self.assertEqual(scrub[:2], [platform.ENV_BIN, "-i"])
        self.assertIn(f"DBUS_SESSION_BUS_ADDRESS={env['DBUS_SESSION_BUS_ADDRESS']}", scrub)
        self.assertIn(f"XDG_RUNTIME_DIR={env['XDG_RUNTIME_DIR']}", scrub)
        self.assertIn("ALTITUDE_ACTOR=l2", scrub)
        self.assertIn(f"ALTITUDE_TASK={self.slug}", scrub)
        self.assertIn("ALTITUDE_ATTEMPT=1", scrub)
        self.assertEqual(scrub[-3:], ["altitude-machine", "systemctl --user daemon-reload", str(self.tmp / "unit.exit")])
        self.assertIn('bash -lc "$1"', scrub[-4])
