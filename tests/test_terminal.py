"""The operator's terminal: a real shell on a real pseudo-terminal, opened through the real HTTP door.

The shell is a plain `bash` without profile files so output is predictable. The service manager's part is replaced
at the platform seam: the fixture launcher starts the shell on the terminal in a session of its own, and stopping
its job hangs up, then kills, that session. The agent and owner checks read a fixture process table where a test
needs a particular process layout, and the real one for a real connection.
"""
import base64
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from tests.support import AltitudeCase, local_terminal_launch, local_terminal_stop, make_repo, terminal_session as session
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T, terminal

SHELL = ["bash", "--noprofile", "--norc"]
class TerminalCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.patch(terminal, "shell_command", return_value=SHELL)
        self.patch(terminal, "launch", side_effect=local_terminal_launch)
        self.patch(terminal, "stop", side_effect=local_terminal_stop)
        self.addCleanup(terminal._ended.clear)
        self.setenv("PS1", "$ ")
        settings = config.ROOT / "settings.json"
        saved = settings.read_text() if settings.exists() else None
        self.addCleanup(lambda: settings.write_text(saved) if saved is not None else settings.unlink(missing_ok=True))
        self.addCleanup(self._close_all)
        self.worktree = self.tmp / "worktree"
        self.worktree.mkdir()
        self.slug = T.new(self.project, "Inspect the build", "Look at the build output.")["slug"]
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.worktree), branch="work")

    def _close_all(self):
        for key in list(terminal._terminals):
            if key[0] == self.project:
                terminal.close(*key, "closed")
                terminal._terminals.pop(key, None)

    def turn(self, on: bool):
        settings = config.machine_settings()
        settings["terminal"] = on
        S.write_json(config.ROOT / "settings.json", settings)

    def wait(self, check, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not check():
            self.assertLess(time.monotonic(), deadline, "timed out")
            time.sleep(.02)

    def gone(self, term):
        """Wait for `term` to end: it leaves the status at once, and only a stream naming it reads how it ended."""
        self.wait(lambda: term.ended and terminal._terminals.get((term.project, term.slug)) is not term)
        self.assertEqual(terminal.status(term.project, term.slug)["state"], "none")
        return term

    def current(self, slug=None):
        return terminal._terminals[(self.project, slug)]

    def open(self, slug=None) -> str:
        """Open the terminal here and return its id."""
        return terminal.open_terminal(self.project, slug)["id"]

    def type(self, text, slug=None):
        terminal.write(self.project, slug, terminal.status(self.project, slug)["id"], text)

    def output(self, slug=None, until="", offset=0):
        """Everything the terminal printed from `offset`, once it contains `until`."""
        seen = b""
        deadline = time.monotonic() + 10
        term = terminal._terminals[(self.project, slug)]
        while until.encode() not in seen:
            self.assertLess(time.monotonic(), deadline, seen)
            data, offset, _, ended = terminal.read(term, offset, .2)
            seen += data
            if ended and until.encode() not in seen:
                self.fail(seen)
        return seen.decode(errors="replace")


class TestTerminalLifecycle(TerminalCase):
    def test_off_until_turned_on(self):
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.open_terminal(self.project, None)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(terminal.status(self.project, None)["state"], "none")

    def test_project_terminal_runs_in_the_project_folder_and_records_only_open_and_close(self):
        self.turn(True)
        opened = terminal.open_terminal(self.project, None)
        self.assertEqual((opened["state"], opened["folder"]), ("running", str(self.repo)))
        self.assertEqual(terminal.open_terminal(self.project, None)["folder"], str(self.repo))  # one per project
        self.type("echo secret-$((6*7)); pwd\n")
        self.assertIn(str(self.repo), self.output(until=str(self.repo) + "\r\n"))
        self.assertIn("secret-42", self.output(until="secret-42"))
        term = self.current()
        self.type("exit 3\n")
        self.gone(term)
        self.assertEqual((term.exit_code, term.reason), (3, "exited"))
        rows = [row for row in S.read_project_log(self.project) if row["kind"] == "terminal"]
        self.assertEqual([row["action"] for row in rows], ["opened", "closed"])
        self.assertEqual(rows[1]["exit_code"], 3)
        self.assertNotIn("secret", json.dumps(rows))
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.write(self.project, None, opened["id"], "ls\n")
        self.assertEqual(caught.exception.status, 410)

    def test_task_terminal_runs_in_the_worktree_reports_the_busy_command_and_closes_it(self):
        self.turn(True)
        self.assertEqual(terminal.open_terminal(self.project, self.slug)["folder"], str(self.worktree))
        self.type("sleep 300\n", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        # Recorded by the time Close returns: Altitude stopping or a test's folders going next loses nothing.
        rows = [row for row in S.read_events(self.project, self.slug) if row["kind"] == "terminal"]
        self.assertTrue(term.ended)
        self.assertEqual(self.gone(term).reason, "closed")
        self.assertEqual([(row["action"], row["folder"]) for row in rows],
                         [("opened", str(self.worktree)), ("closed", str(self.worktree))])

    def test_finished_task_closes_its_terminal_and_refuses_a_new_one(self):
        self.turn(True)
        terminal.open_terminal(self.project, self.slug)
        term = self.current(self.slug)
        task = S.load_task(self.project, self.slug)
        task.update(state="done", agent_id=None)
        S.save_task(self.project, task)
        terminal.sweep()
        self.assertEqual(self.gone(term).reason, "task-finished")
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.open_terminal(self.project, self.slug)
        self.assertEqual(str(caught.exception), "This task has no worktree to open a terminal in.")

    def test_replay_keeps_the_latest_output_and_says_what_was_missed(self):
        self.turn(True)
        self.patch(terminal, "REPLAY_BYTES", 4096)
        self.open()
        self.type("head -c 20000 /dev/zero | tr '\\0' x; echo; echo done-$((1+1))\n")
        self.output(until="done-2")
        term = terminal._terminals[(self.project, None)]
        data, offset, missed, ended = terminal.read(term, 0, 0)
        self.assertTrue(missed)
        self.assertFalse(ended)
        self.assertEqual(len(data), 4096)
        self.assertEqual(terminal.read(term, offset, 0)[:3], (b"", offset, False))

    def test_input_and_size_are_validated(self):
        self.turn(True)
        ident = self.open()
        for data in (None, 7, "x" * (terminal.INPUT_LIMIT + 1)):
            with self.assertRaises(terminal.TerminalError):
                terminal.write(self.project, None, ident, data)
        for cols, rows in ((0, 24), (80, True), ("80", 24), (80, 5000)):
            with self.assertRaises(terminal.TerminalError):
                terminal.resize(self.project, None, ident, cols, rows)
        terminal.resize(self.project, None, ident, 100, 30)
        self.type("stty size\n")
        self.output(until="30 100")

    def test_a_resize_as_the_shell_exits_reports_it_closed(self):
        # The walkthrough crashed altd's handler: a resize passed the running check just before the descriptor closed.
        self.turn(True)
        ident = self.open()
        term = self.current()
        with term.io:
            fd, term.fd = term.fd, -1
        try:
            with self.assertRaises(terminal.TerminalError) as caught:
                terminal.resize(self.project, None, ident, 80, 24)
            self.assertEqual(caught.exception.status, 410)
        finally:
            with term.io:
                term.fd = fd

    def test_close_stops_commands_that_ignore_the_hang_up(self):
        # A review found Close could leave a hang-up-ignoring command, and the terminal, running.
        run = subprocess.run
        def confined_run(command, *args, **kwargs):
            if Path(command[0]).name == "ps":
                raise PermissionError("Issue #617: confined ps is refused")
            return run(command, *args, **kwargs)
        self.patch(subprocess, "run", side_effect=confined_run)
        self.turn(True)
        self.patch(terminal, "CLOSE_GRACE_SECONDS", .3)
        self.open()
        term = self.current()
        shell = term.proc.pid
        self.type("trap '' HUP; nohup sleep 301 >/dev/null 2>&1 & nohup sleep 302 >/dev/null 2>&1\n")
        self.wait(lambda: terminal.status(self.project, None)["busy"] == "sleep" and len(session(shell)) >= 3)
        terminal.close(self.project, None)
        self.assertEqual(self.gone(term).reason, "closed")
        self.wait(lambda: not session(shell))

    def test_the_shell_exiting_ends_the_terminal_and_what_it_left_running(self):
        self.turn(True)
        self.open()
        term = self.current()
        shell = term.proc.pid
        self.type("nohup sleep 303 >/dev/null 2>&1 & sleep .2; exit 4\n")
        self.assertEqual(self.gone(term).exit_code, 4)
        self.wait(lambda: not session(shell))

    def test_a_shell_that_cannot_start_ends_the_terminal_saying_why(self):
        self.turn(True)
        failing = lambda unit, tty, path: subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('Failed to connect to bus\\n'); sys.exit(1)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.patch(terminal, "launch", side_effect=failing)
        ident = self.open()
        term = self.current()
        self.gone(term)
        stream = terminal.stream(self.project, None, ident)  # a page attaching after the end still reads it
        self.assertIs(stream, term)
        self.assertTrue(terminal.read(stream, 0, 0)[3])
        self.assertEqual(terminal.view(term) | {"id": None, "folder": None, "offset": None},
                         {"state": "exited", "id": None, "enabled": True, "folder": None, "offset": None, "exit_code": 1,
                          "reason": "failed", "error": "Failed to connect to bus", "busy": None})

    def test_close_right_after_opening_stops_the_job_once_it_exists(self):
        # A review found a Close reaching the manager before the job registered let the shell start afterwards.
        self.turn(True)
        calls = []
        stop = terminal.stop
        self.patch(terminal, "stop", side_effect=lambda unit: calls.append(unit) if len(calls) < 1 else stop(unit))
        self.open()
        term = self.current()
        terminal.close(self.project, None)
        self.assertEqual(self.gone(term).reason, "closed")
        self.assertGreaterEqual(len(calls), 1)

    def test_a_job_that_does_not_stop_is_reported(self):
        self.turn(True)
        self.patch(terminal, "STOP_SECONDS", .3)
        self.patch(terminal, "STOP_POLL_SECONDS", .1)
        stop = terminal.stop
        self.patch(terminal, "stop", side_effect=lambda unit: None)
        self.open()
        term = self.current()
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.close(self.project, None)
        self.assertEqual(caught.exception.status, 500)
        with self.assertRaises(terminal.TerminalError):
            terminal.close_all()
        self.assertFalse(term.ended)
        self.patch(terminal, "stop", side_effect=stop)
        terminal.close(self.project, None)
        self.gone(term)

    def test_input_a_program_does_not_read_gives_up_and_close_still_works(self):
        # A review found a blocked write held the terminal, so Close and turning it off stalled behind it.
        self.turn(True)
        self.patch(terminal, "WRITE_SECONDS", .3)
        ident = self.open()
        self.type("stty raw -echo; sleep 300\n")
        self.wait(lambda: terminal.status(self.project, None)["busy"] == "sleep")
        with self.assertRaises(terminal.TerminalError) as caught:
            for _ in range(20):
                terminal.write(self.project, None, ident, "x" * terminal.INPUT_LIMIT)
        self.assertEqual(str(caught.exception), "The terminal is not reading input. Press Ctrl+C or close it.")
        started = time.monotonic()
        term = self.current()
        terminal.close(self.project, None)
        self.gone(term)
        self.assertLess(time.monotonic() - started, 5)

    def test_requests_for_a_replaced_terminal_are_refused(self):
        # A review found a stale page's input, Close or stream could reach the terminal that replaced its own.
        self.turn(True)
        old = self.open()
        term = self.current()
        self.type("exit\n")
        self.gone(term)
        new = self.open()
        self.assertNotEqual(old, new)
        for call in (lambda: terminal.write(self.project, None, old, "ls\n"),
                     lambda: terminal.resize(self.project, None, old, 80, 24),
                     lambda: terminal.stream(self.project, None, old)):
            with self.assertRaises(terminal.TerminalError) as caught:
                call()
            self.assertEqual((caught.exception.status, str(caught.exception)), (410, "This terminal was replaced."))
        terminal.close(self.project, None, ident=old)
        self.assertEqual(terminal.status(self.project, None)["state"], "running")
        self.assertEqual(terminal.status(self.project, None)["id"], new)

    def test_turning_it_off_closes_open_terminals(self):
        self.turn(True)
        self.open()
        term = self.current()
        terminal.close_all()  # also what altd runs as it stops: every close is recorded before it returns
        self.assertTrue(term.ended)
        self.assertEqual([row["action"] for row in S.read_project_log(self.project) if row["kind"] == "terminal"],
                         ["opened", "closed"])
        self.assertEqual(self.gone(term).reason, "closed")

    def test_an_open_racing_the_switch_going_off_starts_no_shell(self):
        # A review found an open that had passed the switch could register its shell after turning off closed all.
        self.turn(True)
        place = terminal.folder

        def switched_off_meanwhile(project, slug):
            self.turn(False)
            terminal.close_all()
            return place(project, slug)
        self.patch(terminal, "folder", side_effect=switched_off_meanwhile)
        with self.assertRaises(terminal.TerminalError) as caught:
            self.open()
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn((self.project, None), terminal._terminals)


class TestHandedCommand(TerminalCase):
    """A command the page typed from the owner's `run` block: the owner hears once the operator has run it."""

    def setUp(self):
        super().setUp()
        self.turn(True)
        self.ident = self.open(self.slug)

    def hand(self, text):
        terminal.hand(self.project, self.slug, self.ident, text)
        self.type(text, self.slug)

    def notices(self):
        return [row["text"] for row in T.pending(self.project, self.slug) if row.get("by") == "terminal"]

    def notice(self):
        self.wait(self.notices)
        [text] = self.notices()
        return text

    def test_the_owner_hears_once_the_command_has_run_not_while_it_runs(self):
        self.hand("sleep 1.5; echo slept-$((4*4))")
        self.output(self.slug, until="slept-$((4*4))")
        time.sleep(1.5)  # typed but not run: nothing to report
        self.assertEqual(self.notices(), [])
        self.type("\r", self.slug)
        self.output(self.slug, until="slept-16")
        self.assertEqual(self.notices(), [])  # quiet while sleep holds the foreground
        text = self.notice()
        self.assertIn("the command you handed the operator looks finished in the task terminal: "
                      "`sleep 1.5; echo slept-$((4*4))`", text)
        self.assertIn("alt task terminal", text)
        self.assertIn("slept-16", terminal.owner_output(self.project, self.slug)["text"])

    def test_a_failed_command_is_reported_as_run_and_the_owner_reads_how(self):
        self.hand("ls /no-such-folder-here")
        self.type("\r", self.slug)
        self.assertIn("looks finished", self.notice())
        self.assertIn("No such file or directory", terminal.owner_output(self.project, self.slug)["text"])

    def test_a_shell_builtin_counts_once_the_shell_is_back(self):
        self.hand("cd /")
        self.type("\r", self.slug)
        self.assertIn("looks finished", self.notice())

    def test_ctrl_c_before_enter_drops_the_command_so_a_later_enter_is_not_it(self):
        self.hand("echo handed")
        self.type("\x03", self.slug)
        self.type("echo other\r", self.slug)
        self.output(self.slug, until="other\r\n")
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)
        self.assertEqual(self.notices(), [])

    def test_the_shell_exiting_is_not_the_command_finishing(self):
        self.hand("sleep 30")
        self.type("\r", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.assertIn("the task terminal ended (closed) before the command you handed the operator finished: "
                      "`sleep 30`", self.notice())

    def test_a_command_never_run_is_reported_when_the_terminal_ends(self):
        self.hand("echo never")
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.assertIn("the task terminal ended (closed) before the operator ran the command you handed them: "
                      "`echo never`", self.notice())

    def test_a_command_that_ends_the_shell_is_reported_as_the_terminal_ending(self):
        self.hand("exit 4")
        term = self.current(self.slug)
        self.type("\r", self.slug)
        self.gone(term)
        self.assertIn("the task terminal ended (exited", self.notice())

    def test_a_suspended_command_has_not_finished_until_it_ends(self):
        self.hand("sleep 2")
        self.type("\r", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        time.sleep(terminal.POLL_SECONDS * 3)  # the reader has seen sleep hold the foreground
        self.type("\x1a", self.slug)
        self.output(self.slug, until="Stopped")
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)  # the shell holds the terminal; sleep is suspended
        self.assertEqual(self.notices(), [])
        self.type("fg\r", self.slug)
        self.assertIn("looks finished", self.notice())

    def test_a_notice_goes_only_to_the_attempt_that_handed_the_command(self):
        self.hand("true")
        task = S.load_task(self.project, self.slug)
        task["attempt"] += 1
        S.save_task(self.project, task)
        self.type("\r", self.slug)
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + 1)
        self.assertEqual(self.notices(), [])
        self.assertIsNone(T.notify(self.project, self.slug, "Terminal: notice", by="terminal",
                                   attempt=task["attempt"] - 1))

    def test_the_terminal_keeps_printing_while_its_notice_waits_for_the_project(self):
        self.hand("echo first")
        with S.project_lock(self.project):
            self.type("\r", self.slug)
            time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)  # the notice is waiting for this lock
            self.type("echo second-$((1+1))\r", self.slug)
            self.output(self.slug, until="second-2")
            self.assertEqual(self.notices(), [])
        self.assertIn("`echo first`", self.notice())

    def test_a_blocked_owner_is_woken_and_one_stopped_or_faulted_is_not(self):
        T.block(self.project, self.slug, "Waiting for the operator's command in the task terminal.", actor="l2")
        self.hand("true")
        self.type("\r", self.slug)
        self.assertIn("finished", self.notice())
        with S.project_lock(self.project):  # the notice and its wake are one change
            task = S.load_task(self.project, self.slug)
        self.assertEqual(task["resume_request"], T.pending(self.project, self.slug)[-1]["id"])
        for hold in ({"stop_id": "stopped"}, {"fault": {"kind": "system_fault"}}):
            with self.subTest(hold=hold):
                task = S.load_task(self.project, self.slug)
                task.pop("resume_request", None)
                task.update(hold)
                S.save_task(self.project, task)
                row = T.notify(self.project, self.slug, "Terminal: notice", by="terminal", attempt=task["attempt"])
                task = S.load_task(self.project, self.slug)
                self.assertNotIn("resume_request", task)
                self.assertIn(row["id"], [pending["id"] for pending in T.pending(self.project, self.slug)])
                for key in hold:
                    task.pop(key)
                S.save_task(self.project, task)

    def test_a_finished_task_gets_no_notice_and_a_project_terminal_takes_no_command(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        self.assertIsNone(T.notify(self.project, self.slug, "Terminal: notice", by="terminal", attempt=task["attempt"]))
        self.open()
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.hand(self.project, None, terminal.status(self.project, None)["id"], "echo x")
        self.assertEqual(caught.exception.status, 400)


class TestOwnerOutput(TerminalCase):
    def test_the_owner_reads_its_task_terminal_as_text_until_a_new_terminal_or_the_task_ends(self):
        self.turn(True)
        self.assertEqual(terminal.owner_output(self.project, self.slug)["state"], "none")
        self.open(self.slug)
        self.type("printf '\\033[1;32mgreen\\033[0m\\n'; printf 'step 1\\rstep 2\\n'\n", self.slug)
        self.output(self.slug, until="step 2\r\n")
        running = terminal.owner_output(self.project, self.slug)
        self.assertEqual((running["state"], running["missed"]), ("running", False))
        self.assertIn("\ngreen\nstep 2\n", running["text"])
        self.assertNotIn("\x1b", running["text"])
        term = self.current(self.slug)
        self.type("exit 5\n", self.slug)
        self.gone(term)
        ended = terminal.owner_output(self.project, self.slug)
        self.assertEqual((ended["state"], ended["exit_code"], ended["reason"]), ("exited", 5, "exited"))
        self.assertIn("step 2", ended["text"])
        self.open(self.slug)  # a new terminal replaces what the owner could read
        self.assertNotIn("step 2", terminal.owner_output(self.project, self.slug)["text"])
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.assertEqual(terminal.owner_output(self.project, self.slug)["state"], "exited")
        task = S.load_task(self.project, self.slug)
        task.update(state="done", agent_id=None)
        S.save_task(self.project, task)
        terminal.sweep()
        self.assertEqual(terminal.owner_output(self.project, self.slug)["state"], "none")

    def test_the_owner_learns_when_earlier_output_was_dropped_and_never_reads_the_project_terminal(self):
        self.turn(True)
        self.patch(terminal, "REPLAY_BYTES", 4096)
        self.open(self.slug)
        self.type("head -c 20000 /dev/zero | tr '\\0' x; echo; echo done-$((1+1))\n", self.slug)
        self.output(self.slug, until="done-2")
        self.assertTrue(terminal.owner_output(self.project, self.slug)["missed"])
        self.open()
        self.type("echo project-only\n")
        self.output(until="project-only")
        self.assertNotIn("project-only", terminal.owner_output(self.project, self.slug)["text"])


class TestOwnerHttp(TerminalCase):
    def setUp(self):
        super().setUp()
        self.owner = self.patch(terminal, "owner_connection", return_value=True)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        root = dispatch.l2_job_root(self.project, self.slug)
        root.mkdir(parents=True, exist_ok=True)
        self.unit = engines._claude_unit("agent")
        S.write_json(root / "agent.json", {"id": "agent", "engine": "claude", "unit": self.unit})

    def read(self, body, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        try:
            connection.request("POST", "/api/task/terminal", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def test_only_the_running_owners_worker_reads_and_nothing_can_be_typed(self):
        self.turn(True)
        self.open(self.slug)
        self.type("echo for-the-owner\n", self.slug)
        self.output(self.slug, until="for-the-owner\r\n")
        body = {"project": self.project, "slug": self.slug, "attempt": "1"}
        self.assertIn("for-the-owner", self.read(body)["text"])
        self.assertEqual(self.owner.call_args.args[2], self.unit)  # the task's current worker job
        self.read({**body, "attempt": "2"}, status=403)
        self.read({**body, "data": "rm -rf ~\n"}, status=400)  # a read carries no input
        self.owner.return_value = False
        refused = self.read(body, status=403)
        self.assertEqual(refused["error"], "alt task terminal: only this task's owner may read its terminal")
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        S.save_task(self.project, task)
        self.owner.return_value = True
        self.read(body, status=403)


    def test_the_cli_reads_only_for_the_current_owner(self):
        self.turn(True)
        self.open(self.slug)
        self.type("echo via-cli\n", self.slug)
        self.output(self.slug, until="via-cli\r\n")
        self.serving(self.httpd.server_address[1])
        base = {"ALTITUDE_PROJECT": self.project}
        owner = {**base, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"}
        result = self.alt("task", "terminal", env=owner)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("[altitude] terminal running\n"), result.stdout)
        self.assertIn("via-cli", result.stdout)
        result = self.alt("task", "terminal", "other-task", env=owner)
        self.assertIn("only the current L2 reads its own task's terminal", result.stderr)
        result = self.alt("task", "terminal", self.slug, env={**base, "ALTITUDE_ACTOR": "l3"})
        self.assertIn("not available to an L3", result.stderr)


class TestTerminalJob(AltitudeCase):
    host = "linux"  # systemd and procfs fixtures

    def test_the_shell_runs_in_its_own_job_without_altds_hardening_and_ends_with_altitude(self):
        argv = platform.terminal_job("altitude-terminal-x.service", "/dev/pts/9", ["bash", "-l"], {"TERM": "xterm"},
                                     grace=2)
        for flag in ("--user", "--wait", "--unit=altitude-terminal-x.service", "--property=NoNewPrivileges=no",
                     "--property=TTYPath=/dev/pts/9", "--property=StandardInput=tty", "--property=KillMode=control-group",
                     "--property=KillSignal=SIGHUP", "--property=TimeoutStopSec=2", "--property=PartOf=altitude.service"):
            self.assertIn(flag, argv)
        self.assertIn("--setenv=TERM=xterm", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["bash", "-l"])
        self.assertTrue(terminal.ALTITUDE_UNIT.fullmatch("altitude-terminal-x.service"))  # its processes are Altitude's


def fake_proc(root: Path, processes: dict[int, tuple[int, str, list[int]]], connections: list[tuple[str, str, int]],
              family: str = "") -> None:
    """A /proc tree: pid -> (parent, cgroup path, socket inodes held), plus tcp rows (client, server, inode)."""
    (root / "net").mkdir(parents=True)
    rows = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"]
    rows += [f"   0: {client} {server_} 01 00000000:00000000 00:00000000 00000000  1000        0 {inode}"
             for client, server_, inode in connections]
    (root / "net" / f"tcp{family}").write_text("\n".join(rows) + "\n")
    for pid, (parent, cgroup, inodes) in processes.items():
        (root / str(pid) / "fd").mkdir(parents=True)
        (root / str(pid) / "stat").write_text(f"{pid} (some (odd) name) S {parent} 1 1 0\n")
        (root / str(pid) / "cgroup").write_text(f"0::{cgroup}\n")
        for n, inode in enumerate(inodes):
            os.symlink(f"socket:[{inode}]", root / str(pid) / "fd" / str(n + 3))


class TestAgentRefusal(AltitudeCase):
    host = "linux"  # systemd and procfs fixtures

    PEER, LOCAL = ("127.0.0.1", 51000), ("127.0.0.1", 8443)

    def setUp(self):
        super().setUp()
        self.proc = self.tmp / "proc"
        self.patch(platform, "PROC", self.proc)
        self.client = platform._hex_address(*self.PEER)[0]
        self.server = platform._hex_address(*self.LOCAL)[0]
        self.altd = os.getpid()

    def layout(self, holder_cgroup: str, holder_parent: int = 1) -> bool:
        fake_proc(self.proc, {
            self.altd: (1, "/user.slice/user-1000.slice/user@1000.service/app.slice/altitude.service", [5]),
            4000: (holder_parent, holder_cgroup, [777]),
        }, [(self.client, self.server, 777)])
        return terminal.agent_connection(self.PEER, self.LOCAL)

    def test_the_operators_browser_is_allowed(self):
        self.assertFalse(self.layout("/user.slice/user-1000.slice/user@1000.service/app.slice/app-chrome-1.scope"))

    def test_a_worker_unit_is_refused(self):
        for unit in ("altitude-codex-abc.service", "altitude-claude-1b2c.service", "altitude-review-x.service"):
            with self.subTest(unit=unit):
                shutil.rmtree(self.proc, ignore_errors=True)
                self.assertTrue(self.layout(f"/user.slice/user@1000.service/app.slice/{unit}/sub"))

    def test_a_process_altd_started_is_refused(self):  # an L3 turn runs as altd's child, in altd's own group
        self.assertTrue(self.layout("/user.slice/user@1000.service/app.slice/other.scope", holder_parent=self.altd))

    def test_an_unidentified_local_client_is_refused_and_a_remote_one_allowed(self):
        fake_proc(self.proc, {self.altd: (1, "/altitude.service", [])}, [])
        (self.proc / "net" / "tcp6").write_text("  sl  local_address rem_address\n")
        # Loopback binds for real; the offline guard forbids binding anything else, which no host here owns.
        this_host = terminal._this_host
        self.patch(terminal, "_this_host", side_effect=lambda ip: this_host(ip) if ip.is_loopback else False)
        self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))
        self.assertTrue(terminal.agent_connection(("::ffff:127.0.0.1", 51000), ("::ffff:127.0.0.1", 8443)))
        if this_host(terminal._address("127.8.9.10")):  # Linux owns all of 127/8; macOS binds 127.0.0.1 only
            self.assertTrue(terminal.agent_connection(("127.8.9.10", 51000), ("127.0.0.1", 8443)))
        self.assertFalse(terminal.agent_connection(("203.0.113.20", 51000), ("192.168.1.5", 8443)))

    def test_a_client_on_the_other_address_family_is_still_identified(self):
        # A review found an IPv6 client reaching an IPv4 address was missed in tcp and passed as remote.
        mapped = lambda address: platform._hex_address(f"::ffff:{address[0]}", address[1])[0]
        for cgroup, refused in (("/app.slice/altitude-codex-a.service", True), ("/app.slice/app-chrome-1.scope", False)):
            with self.subTest(cgroup=cgroup):
                shutil.rmtree(self.proc, ignore_errors=True)
                fake_proc(self.proc, {self.altd: (1, "/altitude.service", []), 4000: (1, cgroup, [31])},
                          [(mapped(self.PEER), mapped(self.LOCAL), 31)], family="6")
                (self.proc / "net" / "tcp").write_text("  sl  local_address rem_address\n")
                self.assertEqual(terminal.agent_connection(self.PEER, self.LOCAL), refused)

    def test_a_holder_whose_descriptors_cannot_be_read_is_not_identified(self):
        # A review found an agent process that hides its descriptors (made undumpable) passed the check.
        for cgroup in ("/app.slice/altitude-codex-a.service", "/app.slice/app-chrome-1.scope"):
            with self.subTest(cgroup=cgroup):
                shutil.rmtree(self.proc, ignore_errors=True)
                fake_proc(self.proc, {self.altd: (1, "/altitude.service", []), 4000: (1, cgroup, [777])},
                          [(self.client, self.server, 777)])
                (self.proc / "4000" / "fd").chmod(0)
                self.addCleanup((self.proc / "4000" / "fd").chmod, 0o700)
                self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))
                (self.proc / "4000" / "fd").chmod(0o700)

    def test_a_holder_whose_unit_cannot_be_read_is_not_identified(self):
        fake_proc(self.proc, {self.altd: (1, "/altitude.service", []), 4000: (1, "/app.slice/app-chrome-1.scope", [777])},
                  [(self.client, self.server, 777)])
        (self.proc / "4000" / "cgroup").unlink()
        self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))

    def test_an_unreadable_process_table_is_refused(self):
        self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))  # the fixture tree does not exist

    def test_ipv6_rows_are_matched(self):
        peer, local = ("::1", 51000), ("::1", 8443)
        fake_proc(self.proc, {4000: (1, "/app.slice/altitude-codex-a.service", [9])},
                  [(platform._hex_address(*peer)[0], platform._hex_address(*local)[0], 9)], family="6")
        self.assertTrue(terminal.agent_connection(peer, local))

    def test_a_real_connection_from_altd_itself_is_refused(self):
        self.patch(platform, "PROC", Path("/proc"))
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        client = socket.create_connection(listener.getsockname())
        self.addCleanup(client.close)
        accepted, peer = listener.accept()
        self.addCleanup(accepted.close)
        self.assertTrue(terminal.agent_connection(peer, accepted.getsockname()))


    def test_only_a_process_in_the_owners_worker_job_is_the_owner(self):
        worker = "/user.slice/user-1000.slice/user@1000.service/app.slice/altitude-claude-owner.service"
        fake_proc(self.proc, {4000: (1, worker, [777]), 4001: (1, worker.replace("owner", "other"), [778])},
                  [(self.client, self.server, 777)])
        self.assertTrue(terminal.owner_connection(self.PEER, self.LOCAL, "altitude-claude-owner.service"))
        self.assertFalse(terminal.owner_connection(self.PEER, self.LOCAL, "altitude-claude-other.service"))
        self.assertFalse(terminal.owner_connection(("127.0.0.1", 51001), self.LOCAL, "altitude-claude-owner.service"))


class TestTerminalHttp(TerminalCase):
    def setUp(self):
        super().setUp()
        self.agent = self.patch(terminal, "agent_connection", return_value=False)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = "%s:%d" % self.httpd.server_address

    def request(self, method, path, body=None, *, status=200, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        try:
            sent = {"Content-Type": "application/json", **(headers or {})}
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=sent)
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def test_the_switch_is_a_machine_setting(self):
        self.assertFalse(self.request("GET", "/api/machine")["terminal"])
        self.request("POST", f"/api/terminal/{self.project}/open", {}, status=403)
        self.assertTrue(self.request("POST", "/api/terminal-access", {"enabled": True})["terminal"])
        self.request("POST", "/api/terminal-access", {"enabled": "yes"}, status=400)
        self.request("POST", f"/api/terminal/{self.project}/open", {})
        term = self.current()
        self.assertFalse(self.request("POST", "/api/terminal-access", {"enabled": False})["terminal"])
        self.gone(term)

    def test_cross_site_pages_and_agents_are_refused(self):
        self.turn(True)
        refusals = [{"Origin": "https://elsewhere.example"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Sec-Fetch-Site": "same-site"}, {"Content-Type": "text/plain"}]
        for headers in refusals:
            with self.subTest(headers=headers):
                self.request("POST", f"/api/terminal/{self.project}/open", {}, status=403, headers=headers)
        self.request("GET", f"/api/terminal/{self.project}", status=403, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(terminal.status(self.project, None)["state"], "none")
        self.agent.return_value = True
        refused = self.request("POST", "/api/terminal-access", {"enabled": False}, status=403)
        self.assertEqual(refused["error"], "Terminal requests from Altitude's own agents are refused.")
        self.assertTrue(config.machine_settings()["terminal"])
        self.agent.return_value = False
        # Over plain HTTP a DNS-rebinding page names its own host in both Origin and Host.
        port = self.httpd.server_address[1]
        rebound = {"Host": f"rebind.example:{port}", "Origin": f"http://rebind.example:{port}"}
        refused = self.request("POST", f"/api/terminal/{self.project}/open", {}, status=403, headers=rebound)
        self.assertEqual(refused["error"], "Over plain HTTP, open Altitude at its address or localhost.")
        local = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
        self.assertEqual(self.request("GET", f"/api/terminal/{self.project}", headers=local)["state"], "none")
        headers = {"Origin": f"http://{self.host}", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.request("POST", f"/api/terminal/{self.project}/open", {}, headers=headers)["state"],
                         "running")

    def test_a_task_terminal_streams_output_and_its_end(self):
        self.turn(True)
        base = f"/api/terminal/{self.project}"
        opened = self.request("POST", f"{base}/open", {"task": self.slug})
        self.assertEqual(opened["folder"], str(self.worktree))
        at = {"task": self.slug, "id": opened["id"]}
        self.request("POST", f"{base}/resize", {**at, "cols": 90, "rows": 20})
        self.request("POST", f"{base}/resize", {**at, "cols": 0, "rows": 20}, status=400)
        self.request("POST", f"{base}/input", {"task": self.slug, "data": "ls\n"}, status=400)  # names no terminal
        self.request("POST", f"{base}/command", {**at, "text": "echo 20 90"})
        self.request("POST", f"{base}/command", {**at, "text": ""}, status=400)
        self.request("POST", f"{base}/input", {**at, "data": "stty size; exit 5\n"})
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        self.addCleanup(connection.close)
        connection.request("GET", f"{base}/stream?task={self.slug}&id={opened['id']}&offset=0")
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        events = response.read().decode().split("\n\n")
        output = b"".join(base64.b64decode(json.loads(e.split("data: ", 1)[1])["data"])
                          for e in events if e.startswith("event: output"))
        self.assertIn(b"20 90", output)
        end = json.loads(next(e for e in events if e.startswith("event: end")).split("data: ", 1)[1])
        self.assertEqual((end["state"], end["exit_code"], end["reason"]), ("exited", 5, "exited"))
        # Nothing more reaches the ended terminal; only a page naming it still reads how it ended.
        self.request("POST", f"{base}/input", {**at, "data": "ls\n"}, status=410)
        self.assertEqual(self.request("GET", f"{base}?task={self.slug}")["state"], "none")
        self.request("GET", f"{base}/stream?task={self.slug}&id=other&offset=0", status=404)
        self.request("POST", f"{base}/forget", at, status=404)

    def test_typing_reuses_one_connection_and_other_replies_close_theirs(self):
        # Every keystroke paid a new TCP and TLS handshake: about three round trips before its echo instead of one.
        self.turn(True)
        base = f"/api/terminal/{self.project}"
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        self.addCleanup(connection.close)
        post = lambda path, body: connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        post(f"{base}/open", {})
        response = connection.getresponse()
        opened = json.loads(response.read())
        self.assertEqual(response.version, 11)
        sock = connection.sock
        self.output(until="$ ")  # the shell is reading its terminal
        for key in "echo ke''pt\n":  # the echoed input never reads as the output
            post(f"{base}/input", {"id": opened["id"], "data": key})
            self.assertEqual(json.loads(connection.getresponse().read()), {"ok": True})
            self.assertIs(connection.sock, sock)
        self.assertEqual(self.agent.call_count, 1)  # asked once for the connection
        self.output(until="kept\r\n")
        connection.request("GET", "/api/machine")
        response = connection.getresponse()
        response.read()
        self.assertEqual((response.version, response.will_close), (10, True))

    def test_unknown_places_are_refused(self):
        self.turn(True)
        self.request("POST", "/api/terminal/no-such-project/open", {}, status=404)
        self.request("POST", f"/api/terminal/{self.project}/open", {"task": "no-such-task"}, status=404)
        self.request("POST", f"/api/terminal/{self.project}/open", {"task": 3}, status=400)
        self.request("POST", f"/api/terminal/{self.project}/launch", {}, status=404)
