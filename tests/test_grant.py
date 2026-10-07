"""An operator's purpose grant lets one task run commands as the operator; every command under it is recorded.

Only the transient-unit launcher is a fixture: a `systemd-run` shim inside the test directory runs the
exact command line altd composes, appends output where the unit would, and enforces the runtime limit.
Authority fences, records, the HTTP door and the CLI door are real.
"""
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from datetime import datetime, timedelta, timezone

from tests.support import REPO, AltitudeCase, add_worktree, git, make_repo
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


# The altd that a restart replaces: a real process that launches the unit, then is killed while it runs.
EARLIER_ALTD = r'''
import sys
from altitude import access, platform, server
platform.SYSTEMD_RUN, platform.sys.platform, access.is_machine = sys.argv[1], "linux", lambda presented: True
httpd = server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), server.Handler)
print("ready", flush=True)
httpd.serve_forever()
'''


def wait_for(condition, what):
    deadline = time.monotonic() + 30
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(.02)


class TestOperatorGrant(AltitudeCase):
    host = "linux"  # systemd fixtures

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        shim = self.tmp / "bin" / "systemd-run"
        shim.parent.mkdir(exist_ok=True)
        shim.write_text(SHIM)
        shim.chmod(0o755)
        self.patch(platform, "SYSTEMD_RUN", str(shim))
        self.active = set()  # units the fixture service manager still runs
        self.patch(platform, "job_active", side_effect=lambda name, env: name in self.active)
        self.patch(server, "MACHINE_POLL_SECONDS", .01)
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

    def run_command(self, command, *, status=200, attempt="1", request=None):
        return self.request("/api/task/run", {"project": self.project, "slug": self.slug, "attempt": attempt,
                                              "command": command, "request": request or uuid.uuid4().hex},
                            status=status)

    def ask(self, text="May I edit the service unit, reload and restart it to keep TLS?", waiting_on="burak"):
        self.tick()
        T.block(self.project, self.slug, text, actor="l2", expected_state="running", expected_attempt=1,
                updates={"waiting_on": waiting_on})
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
        return T.record_grant(self.project, self.slug, row["id"], question=question["id"],
                              revision=question["revision"], reason="Purpose matches the answer.",
                              actor="l3"), question, row

    def unit(self, sequence):
        return f"altitude-machine-{self.project}-{self.slug}-{sequence}.service"

    def events(self, kind):
        return [e for e in S.read_events(self.project, self.slug) if e["kind"] == kind]

    def test_grant_needs_the_operator_answer_to_the_current_question(self):
        question = self.ask()
        with self.assertRaisesRegex(T.TransitionError, "answered the current operator question"):
            T.record_grant(self.project, self.slug, "0" * 32, question=question["id"],
                           revision=question["revision"], reason="r", actor="l3")
        row = self.answer(question)
        self.tick()
        other = T.message(self.project, self.slug, "burak", "Also, use the blue theme.", by="burak")
        self.resolve(question, row)
        with self.assertRaisesRegex(T.TransitionError, "answered the current operator question"):
            T.record_grant(self.project, self.slug, other["id"], question=question["id"],
                           revision=question["revision"], reason="r", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "owner, the coordinator or the operator"):
            T.record_grant(self.project, self.slug, row["id"], question=question["id"],
                           revision=question["revision"], reason="r", actor="altd")
        with self.assertRaisesRegex(T.TransitionError, "current attempt"):  # the owner's fence
            T.record_grant(self.project, self.slug, row["id"], question=question["id"],
                           revision=question["revision"], reason="r", actor="l2")
        self.assertEqual(len(self.events("grant-refused")), 3)
        self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))
        grant = T.record_grant(self.project, self.slug, row["id"], question=question["id"],
                               revision=question["revision"], reason="Purpose matches.", actor="l3")
        self.assertEqual((grant["purpose"], grant["answer"], grant["approval"], grant["actor"], grant["attempt"]),
                         (question["detail"], "Yes, go ahead.", row["id"], "l3", 1))
        self.assertEqual(S.load_task(self.project, self.slug)["grant"], grant)
        self.assertEqual(len(self.events("grant")), 1)
        brief = self.alt("task", "status", self.slug, "--brief", env={"ALTITUDE_PROJECT": self.project})
        self.assertIn(f"operator grant: {question['detail']}", brief.stdout)

    def test_the_owner_records_its_grant_from_the_operators_task_chat_answer(self):
        question = self.ask()
        row = self.answer(question)
        grant = dict(question=question["id"], revision=question["revision"], reason="Burak said yes to this purpose.")
        with self.assertRaisesRegex(T.TransitionError, "answered the current operator question"):
            T.record_grant(self.project, self.slug, row["id"], actor="l2", expected_attempt=1, **grant)
        self.resolve(question, row)
        for attempt, source in ((2, "task"), (1, "project")):
            with self.assertRaisesRegex(T.TransitionError, "current attempt, from the operator's task-chat answer"):
                T.record_grant(self.project, self.slug, row["id"], actor="l2", expected_attempt=attempt,
                               source=source, **grant)
        self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))
        recorded = T.record_grant(self.project, self.slug, row["id"], actor="l2", expected_attempt=1, **grant)
        self.assertEqual((recorded["purpose"], recorded["approval"], recorded["actor"], recorded["attempt"]),
                         (question["detail"], row["id"], "l2", 1))
        self.assertEqual(self.run_command("echo granted")["exit"], 0)

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
            T.record_grant(self.project, self.slug, row["id"], question=revised["id"],
                           revision=revised["revision"], reason="r", actor="l3")
        self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))

    def standing(self):
        """An original project decision committed as policy before the owner requests its scope."""
        approval = {"turn_id": "abcdef123456", "role": "user", "by": "burak", "trigger": "chat",
                    "at": self.at, "text": "Container operations are approved until I revoke them."}
        self.standing_chat = config.project_dir(self.project) / "chat.jsonl"
        self.standing_chat.write_text(json.dumps(approval) + "\n")
        self.standing_heading = "Container operations on this machine"
        self.standing_text = ("The operator's standing approval (`abcdef123456`) covers this project's Linux\n"
                              "launcher, rootless containers and their created volumes and images until revoked.\n"
                              "It excludes native services, host trust stores, network configuration, credential\n"
                              "directories and every host resource the launcher did not create.")
        self.standing_document = f"# Rules\n\n### {self.standing_heading}\n\n{self.standing_text}\n"
        self.commit_standing(self.standing_document)
        return approval

    def commit_standing(self, document):
        (self.repo / "AGENTS.md").write_text(document)
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "Record fixture policy", cwd=self.repo)
        return git("rev-parse", "main", cwd=self.repo).strip()

    def standing_grant(self, question, **overrides):
        arguments = dict(question=question["id"], revision=question["revision"], reason="Container work only.",
                         actor="l3", source="project", expected_attempt=1, standing_policy=self.standing_heading)
        arguments.update(overrides)
        return T.record_grant(self.project, self.slug, "abcdef123456", **arguments)

    def test_standing_cli_grant_resume_run_and_revoke_preserve_original_authority(self):
        approval = self.standing()
        commit = git("rev-parse", "main", cwd=self.repo).strip()
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "worktree": str(add_worktree(self.repo, self.slug))})
        self.patch(engines, "worker_live", return_value=False)
        question = self.ask(" ".join(self.standing_text.split()), "l3")
        self.assertLess(approval["at"], question["asked"])
        arguments = ("task", "grant", self.slug, "--approval", approval["turn_id"],
                     "--question", question["id"], "--revision", str(question["revision"]), "--source", "project",
                     "--standing-policy", self.standing_heading, "--reason", "Container work only.")
        env = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l3"}
        missing = self.alt(*arguments, env=env)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("current task attempt", missing.stderr)
        result = self.alt(*arguments, "--attempt", "1", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        grant = json.loads(result.stdout)
        self.assertEqual(grant["policy"], {"file": "AGENTS.md", "heading": self.standing_heading,
                                         "commit": commit, "text": self.standing_text})
        self.assertEqual((grant["approval"], grant["answer"], grant["approved_at"], grant["question"],
                          grant["revision"], grant["source"], grant["actor"], grant["attempt"]),
                         (approval["turn_id"], approval["text"], approval["at"], question["id"],
                          question["revision"], "project", "l3", 1))
        stored = S.load_task(self.project, self.slug)
        self.assertEqual(stored["state"], "blocked")
        self.assertEqual(stored["questions"][-1]["status"], "open")
        self.assertEqual(stored["grant"], grant)
        self.assertEqual(self.events("grant")[-1]["policy"], grant["policy"])
        self.assertIn("only the running owner's", self.run_command("true", status=403)["error"])
        message = self.resumed_by("l3", "Standing grant recorded; continue within its scope.")
        T.resolve_question(self.project, self.slug, question["id"], question["revision"], message,
                           disposition="answered", reason="Coordinator recorded the standing grant.", expected_attempt=1)
        result = self.run_command("echo standing-grant-fixture")
        self.assertEqual(result["exit"], 0)
        self.assertIn("standing-grant-fixture\n", result["output"])
        self.assertEqual(self.events("machine-run")[-1]["purpose"], grant["purpose"])
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        S.save_task(self.project, task)
        self.assertIn("earlier attempt", self.run_command("true", status=403, attempt="2")["error"])
        revoked = self.alt("task", "grant", self.slug, "--revoke", "--reason", "Purpose complete.", env=env)
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertIsNone(S.load_task(self.project, self.slug)["grant"])
        self.assertIn("no operator grant", self.run_command("true", status=403, attempt="2")["error"])

    def test_standing_grant_refuses_outside_and_mixed_purposes_without_mutation(self):
        self.standing()
        for purpose in ("Restart the native Altitude service.", "Update the host trust store.",
                        "Change host network configuration.", "Read host credential directories.",
                        "Delete a host directory the launcher did not create.",
                        self.standing_text + " Also restart the native service."):
            with self.subTest(purpose=purpose):
                question = self.ask(purpose, "l3")
                before = S.load_task(self.project, self.slug)
                with self.assertRaisesRegex(T.TransitionError, "exactly match the standing policy scope"):
                    self.standing_grant(question)
                self.assertEqual(S.load_task(self.project, self.slug), before)
                T.resume(self.project, self.slug)
        self.assertEqual(len(self.events("grant-refused")), 6)
        self.assertFalse(self.events("grant"))

    def test_standing_grant_reads_main_not_branch_dirty_checkout_or_owner_worktree(self):
        self.standing()
        expanded = self.standing_text + " Also change native services."
        git("switch", "-q", "-c", "unapproved-policy", cwd=self.repo)
        self.commit_standing(self.standing_document.replace(self.standing_text, expanded))
        (self.repo / "AGENTS.md").write_text(self.standing_document.replace(self.standing_text, expanded + " Dirty edit."))
        (self.worktree / "AGENTS.md").write_text(self.standing_document.replace(self.standing_text, expanded + " Owner edit."))
        for purpose in (expanded, expanded + " Dirty edit.", expanded + " Owner edit."):
            with self.subTest(purpose=purpose):
                question = self.ask(purpose, "l3")
                with self.assertRaisesRegex(T.TransitionError, "exactly match"):
                    self.standing_grant(question)
                T.resume(self.project, self.slug)
        question = self.ask(self.standing_text, "l3")
        self.assertEqual(self.standing_grant(question)["policy"]["text"], self.standing_text)

    def test_standing_grant_refuses_missing_or_ambiguous_policy(self):
        self.standing()
        question = self.ask(self.standing_text, "l3")
        documents = ("# Rules\n", self.standing_document + self.standing_document,
                     self.standing_document + "\nAnother policy paragraph.\n",
                     self.standing_document.replace("`abcdef123456`", "`different123`"))
        for document in documents:
            with self.subTest(document=document):
                self.commit_standing(document)
                with self.assertRaisesRegex(T.TransitionError, "standing policy"):
                    self.standing_grant(question)
                self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))
        git("rm", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "Remove standing policy", cwd=self.repo)
        (self.worktree / "AGENTS.md").write_text(self.standing_document)
        with self.assertRaisesRegex(T.TransitionError, "committed on the registered project's main"):
            self.standing_grant(question)

    def test_standing_grant_requires_original_operator_source_in_this_project(self):
        approval = self.standing()
        question = self.ask(self.standing_text, "l3")
        other = self.project + "-other"
        self.register(other)
        config.project_dir(other).mkdir(parents=True, exist_ok=True)
        (config.project_dir(other) / "chat.jsonl").write_text(json.dumps(approval) + "\n")
        rows = ([], [{**approval, "role": "assistant"}], [{**approval, "by": "l3"}],
                [{**approval, "trigger": "timer"}], [{**approval, "removed_at": self.at}],
                [{**approval, "turn_id": "other1234567"}])
        for messages in rows:
            with self.subTest(messages=messages):
                self.standing_chat.write_text("".join(json.dumps(row) + "\n" for row in messages))
                with self.assertRaisesRegex(T.TransitionError, "original operator message in this project's chat"):
                    self.standing_grant(question)
                self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))
        self.standing_chat.write_text((json.dumps(approval) + "\n") * 2)
        with self.assertRaisesRegex(T.TransitionError, "duplicate project operator message identity"):
            self.standing_grant(question)

    def test_standing_grant_preserves_role_source_attempt_and_question_fences(self):
        self.standing()
        question = self.ask(self.standing_text, "l3")
        for overrides in ({"actor": "l2"}, {"actor": "burak"}, {"actor": "altd"}, {"source": "task"},
                          {"expected_attempt": None}, {"expected_attempt": 2}, {"revision": 99}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(T.TransitionError):
                    self.standing_grant(question, **overrides)
                self.assertIsNone(S.load_task(self.project, self.slug).get("grant"))
        T.resume(self.project, self.slug)
        # Even a running owner cannot turn a project standing decision into a self-recorded grant.
        for source in ("task", "project"):
            with self.assertRaises(T.TransitionError):
                self.standing_grant(question, actor="l2", source=source)
        T.block(self.project, self.slug, self.standing_text, actor="l2", expected_state="running", expected_attempt=1,
                updates={"waiting_on": "l3"}, questions={"questions": [
                    {"id": question["id"], "question": self.standing_text + " Revised."}]})
        with self.assertRaisesRegex(T.TransitionError, "current open L3 purpose question"):
            self.standing_grant(question)
        T.resume(self.project, self.slug)
        operator_question = self.ask(self.standing_text, "burak")
        with self.assertRaisesRegex(T.TransitionError, "current open L3 purpose question"):
            self.standing_grant(operator_question)

    def test_switching_to_or_from_standing_grant_requires_explicit_revocation(self):
        direct, _, _ = self.granted()
        self.standing()
        question = self.ask(self.standing_text, "l3")
        with self.assertRaisesRegex(T.TransitionError, "revoke the existing operator grant"):
            self.standing_grant(question)
        self.assertEqual(S.load_task(self.project, self.slug)["grant"], direct)
        T.revoke_grant(self.project, self.slug, "Replace completed purpose.", actor="l3")
        standing = self.standing_grant(question)
        T.resume(self.project, self.slug)
        direct_question = self.ask()
        answer = self.answer(direct_question)
        self.resolve(direct_question, answer)
        arguments = dict(question=direct_question["id"], revision=direct_question["revision"], reason="New purpose.", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "revoke the existing operator grant"):
            T.record_grant(self.project, self.slug, answer["id"], **arguments)
        self.assertEqual(S.load_task(self.project, self.slug)["grant"], standing)
        T.revoke_grant(self.project, self.slug, "Container work complete.", actor="l3")
        self.assertNotIn("policy", T.record_grant(self.project, self.slug, answer["id"], **arguments))

    def resumed_by(self, actor, reason):
        """altd performs the requested resume; the reason reaches the owner as the requester's message."""
        self.tick()
        request = dispatch.request_task_operation(self.project, self.slug, "resume", reason, actor=actor)["request"]
        launched = {"returncode": 0, "agent": {"id": "agent", "sessionId": "session", "input_delivered": True}}
        with mock.patch.object(engines, "resume_l2", return_value=launched):
            self.assertEqual(dispatch.run_task_operation(self.project, self.slug)["request"]["status"], "done")
        self.tick()
        return request["id"]

    def test_l3_resume_after_recording_the_grant_settles_the_owners_grant_dependency(self):
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "worktree": str(add_worktree(self.repo, self.slug))})
        self.patch(engines, "worker_live", return_value=False)
        grant, _question, _row = self.granted()
        waiting = self.ask("L3: record the operator grant for the approved purpose, then resume me.", "l3")
        self.assertEqual(waiting["audience"], "l3")
        # An operator's resume reason authorizes that resume only; it answers no question.
        operator_resume = self.resumed_by("burak", "Go on.")
        with self.assertRaisesRegex(T.TransitionError, "original message with authority"):
            T.resolve_question(self.project, self.slug, waiting["id"], waiting["revision"], operator_resume,
                               disposition="answered", reason="Resumed.", expected_attempt=1)
        waiting = self.ask("L3: record the operator grant for the approved purpose, then resume me.", "l3")
        l3_resume = self.resumed_by("l3", "Grant recorded for the approved purpose.")
        view = T.resolve_question(self.project, self.slug, waiting["id"], waiting["revision"], l3_resume,
                                  disposition="answered", reason="L3 recorded the grant and resumed me.",
                                  expected_attempt=1)
        self.assertEqual((view["status"], view["resolution"]["by"]), ("resolved", "l3"))
        self.assertEqual(S.load_task(self.project, self.slug)["grant"], grant)

    def test_commands_run_only_under_a_grant_and_are_recorded(self):
        refused = self.run_command("echo hello", status=403)
        self.assertIn("no operator grant", refused["error"])
        self.assertIn("alt task grant", refused["error"])
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
        self.assertIn("hello\n", (folder / f"{self.unit(1)}.log").read_text())
        self.assertEqual((folder / f"{self.unit(2)}.log").read_text(), "second\n")
        self.assertEqual((again["log"], (folder / f"{self.unit(2)}.exit").read_text()),
                         (str(folder / f"{self.unit(2)}.log"), "0"))
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
        self.granted()
        self.patch(platform, "SYSTEMD_RUN", str(self.tmp / "bin" / "missing"))
        result = self.run_command("true")
        self.assertEqual((result["exit"], result["timed_out"], result["output"]), (None, False, ""))
        self.assertIn("no exit status recorded", result["error"])
        self.assertIn("missing", result["error"])
        self.assertEqual([(e["exit"], e["error"]) for e in self.events("machine-run")], [(None, result["error"])])

    def test_a_command_past_its_limit_is_reported_as_a_timeout(self):
        self.granted()
        self.patch(config, "MACHINE_COMMAND_TIMEOUT", 1)
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
            T.revoke_grant(self.project, self.slug, "done", actor="l2", expected_attempt=2)
        task = T.revoke_grant(self.project, self.slug, "Purpose complete.", actor="l3")
        self.assertIsNone(task["grant"])
        self.assertEqual(len(self.events("grant-revoke")), 1)
        self.assertIn("no operator grant", self.run_command("true", status=403)["error"])
        with self.assertRaisesRegex(T.TransitionError, "no operator grant"):
            T.revoke_grant(self.project, self.slug, "again", actor="l3")
        self.granted()
        task = T.revoke_grant(self.project, self.slug, "Finished early.", actor="l2", expected_attempt=1)
        self.assertIsNone(task["grant"])
        self.granted()
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        S.save_task(self.project, task)
        self.assertIn("earlier attempt", self.run_command("true", attempt="2", status=403)["error"])

    def test_cli_door_and_exit_status(self):
        grant, question, row = self.granted()
        self.serving(self.httpd.server_address[1])
        base = {"ALTITUDE_PROJECT": self.project}
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
        result = self.alt("task", "grant", self.slug, "--revoke", "--reason", "Purpose complete.", env=owner)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["grant"])
        grant_args = ("--approval", row["id"], "--question", question["id"],
                      "--revision", str(question["revision"]), "--reason", "Same answer.")
        for elsewhere in (("task", "grant", "other-task"), ("--project", "other", "task", "grant", self.slug)):
            result = self.alt(*elsewhere, *grant_args, env=owner)
            self.assertEqual(result.returncode, 1)
            self.assertIn("only on its own task", result.stderr)
        result = self.alt("task", "grant", self.slug, *grant_args, env=owner)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((json.loads(result.stdout)["purpose"], json.loads(result.stdout)["actor"]),
                         (question["detail"], "l2"))
        self.alt("task", "grant", self.slug, "--revoke", "--reason", "Done.", env=owner)
        result = self.alt("task", "grant", self.slug, *grant_args, env={**base, "ALTITUDE_ACTOR": "l3"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["actor"], "l3")

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

    def rows(self):
        return [json.loads(line) for line in (S.task_dir(self.project, self.slug) / "machine.jsonl").read_text()
                .splitlines()]

    def test_a_command_interrupted_by_a_restart_keeps_its_result_for_the_ledger_and_the_owner(self):
        self.granted()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        earlier = subprocess.Popen([sys.executable, "-c", EARLIER_ALTD, platform.SYSTEMD_RUN, str(port)], cwd=REPO,
                                   stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": str(REPO)})
        self.addCleanup(earlier.stdout.close)
        self.addCleanup(earlier.kill)
        self.assertEqual(earlier.stdout.readline(), "ready\n")
        self.serving(port)
        started, release = self.tmp / "started", self.tmp / "release"
        owner = subprocess.Popen(
            [sys.executable, str(REPO / "bin" / "alt"), "task", "run", self.slug,
             f"echo before; touch {started}; while [ ! -e {release} ]; do sleep .02; done; echo after; exit 5"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": self.project,
                 "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"})
        self.addCleanup(owner.kill)
        wait_for(started.exists, "the command to start")
        self.active.add(self.unit(1))
        earlier.send_signal(signal.SIGKILL)  # Altitude restarts; the unit runs on
        earlier.wait()
        [row] = self.rows()
        self.assertEqual((row["exit"], row["finished"]), (None, None))
        self.assertIn("interrupted", row["error"])
        self.assertIn("one command at a time", self.run_command("echo meanwhile", status=400)["error"])
        # The replacement listens elsewhere; the waiting owner follows the record it writes.
        replacement = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        replacement.daemon_threads = True
        self.serving(replacement.server_port)
        threading.Thread(target=replacement.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(replacement.server_close)
        self.addCleanup(replacement.shutdown)
        settling = server.settle_interrupted_machine_commands()
        self.assertEqual(len(settling), 1)
        release.touch()
        exit_file = S.task_dir(self.project, self.slug) / f"{self.unit(1)}.exit"
        wait_for(exit_file.exists, "the unit's exit status")
        self.active.discard(self.unit(1))
        for thread in settling:
            thread.join(30)
        stdout, stderr = owner.communicate(timeout=60)
        self.assertEqual(owner.returncode, 5, stderr)
        self.assertIn("waiting for this command's result", stderr)
        self.assertTrue(stdout.startswith("before\nafter\n"), stdout)
        self.assertIn(f"[altitude] {self.unit(1)}: exit 5; log ", stdout)
        [row] = self.rows()
        finished = datetime.fromtimestamp(exit_file.stat().st_mtime, timezone.utc).isoformat()
        self.assertEqual((row["exit"], row["timed_out"], row["error"], row["finished"]), (5, False, None, finished))
        self.assertEqual([(e["n"], e["exit"], e["finished"]) for e in self.events("machine-run")], [(1, 5, finished)])
        self.assertEqual([(e["unit"], e["exit"]) for e in S.read_project_log(self.project)
                          if e["kind"] == "machine-run"], [(self.unit(1), 5)])
        self.assertEqual((self.run_command("echo next")["n"], len(self.events("machine-run"))), (2, 2))

    def test_claude_sessions_may_call_only_their_own_task_runner_natively(self):
        settings = S.read_json(dispatch.session_settings(self.project, self.slug, "fixture-key"))
        self.assertEqual(settings["permissions"], {"allow": [f"Bash(alt task run {self.slug} *)"]})
        # The rule admits the call; altd still refuses it without the task's current grant.
        self.assertIn("no operator grant", self.run_command("true", status=403)["error"])

    def test_revoking_the_grant_stops_the_command_it_is_running(self):
        self.granted()
        started, release = self.tmp / "started", self.tmp / "release"
        stops = []
        def job_stop(name, env, **kwargs):  # the fixture service manager ends the job when asked
            stops.append(name)
            release.touch()
        self.patch(platform, "job_stop", side_effect=job_stop)
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(self.run_command,
                                  f"touch {started}; while [ ! -e {release} ]; do sleep .02; done; exit 3")
            wait_for(started.exists, "the command to start")
            self.tick()
            T.revoke_grant(self.project, self.slug, "Operator withdrew permission.", actor="l3")
            result = running.result(timeout=30)
        self.assertEqual(stops, [self.unit(1)])
        self.assertEqual(result["exit"], 3)
        self.assertIn("grant was revoked", result["error"])
        [row] = self.rows()
        self.assertIn("grant was revoked", row["error"])
        self.assertIn("no operator grant", self.run_command("true", status=403)["error"])

    def test_a_command_interrupted_by_a_restart_is_stopped_when_its_grant_was_revoked_meanwhile(self):
        grant = self.granted()[0]
        folder = S.task_dir(self.project, self.slug)
        (folder / "machine.jsonl").write_text(json.dumps({
            "n": 1, "purpose": "p", "granted": grant["at"], "command": "make deploy", "unit": self.unit(1),
            "exit": None, "timed_out": False, "started": datetime.now(timezone.utc).isoformat(), "finished": None,
            "error": "still running or interrupted with altd"}) + "\n")
        self.active.add(self.unit(1))
        self.patch(platform, "job_stop", side_effect=lambda name, env, **kwargs: self.active.discard(name))
        self.tick()
        T.revoke_grant(self.project, self.slug, "Operator withdrew permission.", actor="l3")
        for thread in server.settle_interrupted_machine_commands():
            thread.join(30)
        platform.job_stop.assert_called_once()
        [row] = self.rows()
        self.assertEqual(row["exit"], None)
        self.assertIn("grant was revoked", row["error"])

    def test_an_interrupted_command_is_settled_from_its_exit_status_or_stays_uncertain(self):
        self.granted()
        folder = S.task_dir(self.project, self.slug)
        runs = folder / "machine.jsonl"
        started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        interrupted = {"purpose": "p", "granted": S.load_task(self.project, self.slug)["grant"]["at"], "exit": None,
                       "timed_out": False, "started": started, "finished": None,
                       "error": "still running or interrupted with altd"}
        runs.write_text(json.dumps({**interrupted, "n": 1, "command": "make gate", "unit": self.unit(1)}) + "\n")
        (folder / f"{self.unit(1)}.exit").write_text("0")
        os.utime(folder / f"{self.unit(1)}.exit", (1790000000, 1790000000))
        (folder / f"{self.unit(1)}.log").write_text("gate passed\n")
        for thread in server.settle_interrupted_machine_commands():
            thread.join(30)
        with runs.open("a") as ledger:
            ledger.write(json.dumps({**interrupted, "n": 2, "command": "make vm", "unit": self.unit(2)}) + "\n")
        other = T.new(self.project, "Another task", "With an unreadable ledger.")["slug"]
        (S.task_dir(self.project, other) / "machine.jsonl").write_text("{not json\n")
        S.append_event(self.project, self.slug, "machine-run", actor="l2", unit=self.unit(2), exit=None)
        answers = iter([OSError("bus timeout"), True, False])  # a status query that fails keeps waiting
        def job_active(name, env):
            answer = next(answers, False)
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.patch(platform, "job_active", side_effect=job_active)
        for thread in server.settle_interrupted_machine_commands():
            thread.join(30)
        first, second = self.rows()
        self.assertEqual((first["exit"], first["error"], first["finished"]),
                         (0, None, datetime.fromtimestamp(1790000000, timezone.utc).isoformat()))
        self.assertEqual((second["exit"], second["timed_out"]), (None, False))
        self.assertIn("the unit ended without writing one", second["error"])  # watched: it was seen running
        self.assertIsNotNone(second["finished"])
        self.assertEqual(next(answers, "drained"), "drained")
        # the second row's event was recorded before a restart stopped its row write; it is not repeated
        self.assertEqual([(e.get("n"), e["exit"]) for e in self.events("machine-run")], [(1, 0), (None, None)])
        self.assertEqual(server.settle_interrupted_machine_commands(), [])
        self.assertEqual(self.run_command("true")["n"], 3)

    def test_asking_again_with_the_same_request_returns_its_result_without_running_it_again(self):
        self.granted()
        request = uuid.uuid4().hex
        counter = self.tmp / "runs"
        first = self.run_command(f"echo once >> {counter}; echo done", request=request)
        again = self.run_command(f"echo once >> {counter}; echo done", request=request)
        self.assertEqual((again["n"], again["exit"], again["output"]), (first["n"], 0, "done\n"))
        self.assertEqual(counter.read_text(), "once\n")
        self.assertIn("different command", self.run_command("echo other", request=request, status=400)["error"])
        self.assertIn("current attempt", self.run_command(f"echo once >> {counter}; echo done", request=request,
                                                          attempt="2", status=403)["error"])
        self.assertIn("hexadecimal", self.run_command("true", request="not-a-request", status=400)["error"])
        self.assertEqual((len(self.rows()), len(self.events("machine-run"))), (1, 1))

    def test_a_request_from_an_earlier_attempt_is_refused(self):
        self.granted()
        request = uuid.uuid4().hex
        self.run_command("echo first", request=request)
        task = S.load_task(self.project, self.slug)
        task["attempt"] = 2
        task["grant"]["attempt"] = 2
        S.save_task(self.project, task)
        self.assertIn("earlier attempt", self.run_command("echo first", request=request, attempt="2",
                                                          status=403)["error"])
        self.assertEqual(self.run_command("echo fresh", attempt="2")["n"], 2)

    def test_the_cli_fails_at_once_when_altd_never_received_the_command(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        started = time.monotonic()
        for host in ("127.0.0.1", "unresolvable.invalid"):
            self.serving(port, host)
            result = self.alt("task", "run", self.slug, "true", env={
                "ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                "ALTITUDE_ATTEMPT": "1"})
            self.assertEqual(result.returncode, 1)
            self.assertIn("altd unavailable", result.stderr)
            self.assertNotIn("waiting for this command's result", result.stderr)
        self.assertLess(time.monotonic() - started, 30)
