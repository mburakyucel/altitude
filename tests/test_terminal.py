"""The operator's terminal: a real shell on a real pseudo-terminal, opened through the real HTTP door.

The shell is a plain `bash` without profile files so output is predictable. The service manager's part is replaced
at the platform seam: the fixture launcher starts the shell on the terminal in a session of its own, and stopping
its job hangs up, then kills, that session. The agent and owner checks read a fixture process table where a test
needs a particular process layout, and the real one for a real connection.
"""
import base64
import errno
import http.client
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import NATIVE_PLATFORM, AltitudeCase, local_terminal_launch, local_terminal_stop, make_repo, terminal_session as session
from altitude import config, dispatch, engines, installation, l3, platform, server, state as S, tasks as T, terminal

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
        self.slug = T.new(self.project, "Inspect the build", "Look at the build output.")["slug"]
        self.worktree = self.task_worktree(self.slug)
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.worktree), branch="work", l2_engine=config.ENGINES[0])

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
    def test_terminal_registration_holds_the_restart_gate(self):
        self.turn(True)
        launch = terminal.launch

        def during_launch(*args, **kwargs):
            with config.restart_lock(exclusive=True) as quiet:
                self.assertFalse(quiet, "restart cannot enter before this terminal is registered")
            return launch(*args, **kwargs)

        self.patch(terminal, "launch", side_effect=during_launch)
        self.open()
        self.assertTrue(terminal.any_open())
        with config.restart_lock(exclusive=True) as quiet:
            self.assertTrue(quiet)
            self.assertTrue(terminal.any_open(), "the updater sees the registered terminal inside its gate")

    def test_update_reservation_refuses_new_terminals_without_launching(self):
        self.turn(True)
        self.patch(config, "RELEASE", {"version": "v0.1.0"})
        self.patch(config, "INSTALL_PREFIX", self.tmp / "installation")
        S.write_json(config.ROOT / "update.json", {"attempt": {"id": "fixture", "version": "v0.2.0",
                                                               "state": "running", "started": time.time()}})
        with self.assertRaises(terminal.TerminalError) as caught:
            self.open()
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(terminal.any_open())
        installation._finish_attempt("fixture", "failed")
        self.open()
        self.assertTrue(terminal.any_open())

    def test_existing_terminal_reattaches_during_update_admission(self):
        self.turn(True)
        first = self.open()
        self.patch(config, "RELEASE", {"version": "v0.1.0"})
        self.patch(config, "restart_in_progress", return_value=True)
        with config.restart_lock(exclusive=True):
            self.assertEqual(self.open(), first)

    def test_source_terminal_keeps_its_existing_restart_behavior(self):
        self.turn(True)
        self.patch(config, "RELEASE", None)
        self.patch(config, "restart_in_progress", return_value=True)
        with config.restart_lock(exclusive=True):
            self.open()
        self.assertTrue(terminal.any_open())

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

    def test_replay_is_all_but_the_sequences_after_the_last_text(self):
        # A program's queries replayed to a page that opened later were answered as typed input at the shell's prompt.
        queries = b"\x1b]11;?\x1b\\\x1b[6n"
        for data, replay in [(b"$ gh repo edit\r\n" + queries + b"Edited\r\n$ ", None),
                             (b"\x1b]0;a title\x07$ ", None), (b"$ read\r\n" + queries, len(b"$ read\r\n")),
                             (b"\x1b[?2004h" + queries, 0), (b"", 0), (b"$ \x1b", None)]:
            with self.subTest(data=data):
                self.assertEqual(terminal.replayed(data), len(data) if replay is None else replay)

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
    """A command altd typed from the owner's `run` block: the owner hears once the operator has run it."""

    def setUp(self):
        super().setUp()
        self.turn(True)
        self.ident = self.open(self.slug)

    def hand(self, text):
        terminal.hand(self.project, self.slug, self.ident, text)

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
        self.assertIn("slept-16", terminal.output(self.project, self.slug)["text"])

    def test_a_failed_command_is_reported_as_run_and_the_owner_reads_how(self):
        self.hand("ls /no-such-folder-here")
        self.type("\r", self.slug)
        self.assertIn("looks finished", self.notice())
        self.assertIn("No such file or directory", terminal.output(self.project, self.slug)["text"])

    def test_a_shell_builtin_counts_once_the_shell_is_back(self):
        self.hand("cd /")
        self.type("\r", self.slug)
        self.assertIn("looks finished", self.notice())

    def test_a_command_is_typed_once_while_it_waits_for_enter(self):
        # The operator opened a `run` block in the terminal again before running it: the second copy joined the first
        # on the line (`alt doctoralt doctor`), and one Enter ran both.
        self.output(self.slug, until="$ ")  # at the prompt, so the line is echoed once
        self.hand("echo once-$((1+1))")
        for text, refusal in (("echo once-$((1+1))", "This command is already typed at the prompt."),
                              ("echo other", "Another command is already typed at the prompt, so this one wasn't typed.")):
            with self.subTest(text=text), self.assertRaises(terminal.TerminalError) as caught:
                self.hand(text)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(str(caught.exception), f"{refusal} Press Enter to run it, or Ctrl+C to clear the line.")
        self.type("\r", self.slug)
        self.output(self.slug, until="once-2\r\n")
        self.assertEqual(self.current(self.slug).command["text"], "echo once-$((1+1))")
        self.assertIn("looks finished", self.notice())
        self.hand("echo again")  # once it has run, the next one is typed
        self.type("\r", self.slug)
        self.output(self.slug, until="again\r\n")
        self.assertEqual(terminal.output(self.project, self.slug)["text"].count("echo once-$((1+1))"), 1)

    def test_ctrl_c_clears_the_way_for_the_command_to_be_typed_again(self):
        self.hand("echo handed")
        self.type("\x03", self.slug)
        self.hand("echo handed")
        self.assertEqual(self.current(self.slug).command, {"text": "echo handed", "attempt": 1})

    def test_nothing_is_typed_or_followed_while_a_program_holds_the_foreground(self):
        self.type("sleep 30\r", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        with self.assertRaises(terminal.TerminalError) as caught:
            self.hand("echo handed")
        self.assertEqual((caught.exception.status, str(caught.exception)), (409, "sleep is running, so the command wasn't typed."))
        self.assertIsNone(self.current(self.slug).command)

    def test_a_command_is_framed_as_a_paste_only_when_the_shell_asked(self):
        with mock.patch.object(terminal, "_send") as send:
            terminal.hand(self.project, self.slug, self.ident, "echo framed", bracketed=True)
            self.type("\x03", self.slug)
            terminal.hand(self.project, self.slug, self.ident, "echo plain")
        self.assertEqual([call.args[1] for call in send.call_args_list], ["\x1b[200~echo framed\x1b[201~", "\x03", "echo plain"])
        with self.assertRaises(terminal.TerminalError) as caught:
            terminal.hand(self.project, self.slug, self.ident, "echo framed", bracketed="yes")
        self.assertEqual(caught.exception.status, 400)

    def test_a_command_typed_in_part_stays_guarded_until_ctrl_c(self):
        # Review finding: a write that failed part way dropped the guard, so opening it again joined the prefix.
        write = os.write
        sizes = iter([4])

        def stalls_after_a_prefix(fd, data):
            size = next(sizes, None)
            if size is None:
                raise BlockingIOError
            return write(fd, data[:size])
        with mock.patch.object(terminal, "WRITE_SECONDS", .3), \
                mock.patch.object(terminal.os, "write", side_effect=stalls_after_a_prefix), \
                self.assertRaises(terminal.TerminalError) as caught:
            self.hand("echo partial")
        self.assertEqual(str(caught.exception), "The terminal is not reading input. Press Ctrl+C or close it.")
        with self.assertRaises(terminal.TerminalError) as caught:
            self.hand("echo partial")
        self.assertIn("already typed at the prompt", str(caught.exception))
        self.type("\x03", self.slug)
        self.hand("echo partial")

    def test_input_waits_for_the_command_being_typed(self):
        # Review finding: a Ctrl+C or Enter could land between the check and the command's own text.
        typing, release = threading.Event(), threading.Event()
        interrupted_after_release = []
        errors = []
        send = terminal._send

        def held(term, data):
            if data == "echo handed":
                typing.set()
                release.wait(5)
            elif data == "\x03":
                interrupted_after_release.append(release.is_set())
            send(term, data)

        def run(call, *args):
            try:
                call(*args)
            except Exception as exc:  # noqa: BLE001 - surfaced to the test below
                errors.append(exc)
        with mock.patch.object(terminal, "_send", side_effect=held):
            handing = threading.Thread(target=run, args=(self.hand, "echo handed"))
            handing.start()
            self.assertTrue(typing.wait(5))
            interrupt = threading.Thread(target=run, args=(terminal.write, self.project, self.slug, self.ident, "\x03"))
            interrupt.start()
            time.sleep(.3)
            release.set()
            handing.join(5)
            interrupt.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(interrupted_after_release, [True])  # Ctrl+C reached the shell only after the command
        self.assertIsNone(self.current(self.slug).command)  # and dropped it
        self.hand("echo handed")

    def test_an_entered_command_is_followed_to_its_end_before_another_is_typed(self):
        # Review finding: a new command replaced one still settling, and its notice was lost.
        self.hand("sleep 30")
        self.type("\r", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        time.sleep(terminal.POLL_SECONDS * 3)  # the reader has seen sleep hold the foreground
        self.type("\x1a", self.slug)
        self.output(self.slug, until="Stopped")
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] is None)
        with self.assertRaises(terminal.TerminalError) as caught:
            self.hand("echo next")
        self.assertEqual(str(caught.exception), "The last command Altitude typed here hasn't finished, so this one wasn't typed.")
        self.assertEqual(self.current(self.slug).command["text"], "sleep 30")

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

    def settled(self):
        """Wait out the notice's change: its inbox row is written before the question's mark, under one lock."""
        with S.project_lock(self.project):
            pass

    def ask(self, *questions):
        """Park the owner on operator questions; returns them by text."""
        T.block(self.project, self.slug, questions[0], actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                questions={"questions": [{"question": text} for text in questions]})
        return {q["detail"]: q for q in S.load_task(self.project, self.slug)["questions"] if q["status"] == "open"}

    def test_the_question_that_handed_the_command_shows_it_ran_and_stays_open(self):
        step = "Sign in from the task terminal.\n\n```run\necho signed-$((5+5))\n```"
        asked = self.ask(step, "Which region should the installer use?")
        self.hand("echo signed-$((5+5))")
        self.type("\r", self.slug)
        self.notice()
        self.settled()
        task = S.load_task(self.project, self.slug)
        ran = {q["detail"]: q for q in task["questions"] if q["status"] == "open"}
        self.assertEqual((ran[step]["id"], ran[step]["revision"]), (asked[step]["id"], asked[step]["revision"]))
        self.assertEqual(ran[step]["ran"], {"at": T.pending(self.project, self.slug)[-1]["at"],
                                            "command": "echo signed-$((5+5))", "finished": True})
        self.assertNotIn("ran", ran["Which region should the installer use?"])
        # The card shows it wherever the question is read, and it stays the operator's question until the owner acts.
        [card] = [q for q in T.decisions(self.project) if q.get("id") == asked[step]["id"]]
        self.assertEqual(card["ran"]["finished"], True)
        self.assertEqual([(row["question_id"], row["finished"]) for row in S.read_events(self.project, self.slug)
                          if row["kind"] == "question-ran"], [(asked[step]["id"], True)])
        # Asking again publishes a new revision, which replaces the note with the ask.
        task = S.load_task(self.project, self.slug)
        task["state"] = "running"  # the woken owner, back in its session
        S.save_task(self.project, task)
        T.block(self.project, self.slug, step, actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                questions={"questions": [{"id": asked[step]["id"], "question": step}]})
        again = next(q for q in S.load_task(self.project, self.slug)["questions"]
                     if q["id"] == asked[step]["id"] and q["status"] == "open")
        self.assertEqual(again["revision"], asked[step]["revision"] + 1)
        self.assertNotIn("ran", again)

    def test_a_terminal_that_ended_first_marks_the_question_for_running_again(self):
        step = "Sign in from the task terminal.\n\n```run\nsleep 30\n```"
        self.ask(step)
        self.hand("sleep 30")
        self.type("\r", self.slug)
        self.wait(lambda: terminal.status(self.project, self.slug)["busy"] == "sleep")
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.notice()
        self.settled()
        [question] = [q for q in S.load_task(self.project, self.slug)["questions"] if q["status"] == "open"]
        self.assertFalse(question["ran"]["finished"])

    def test_reopening_the_terminal_only_to_look_keeps_the_finished_mark(self):
        # Review finding: closing a terminal reopened from the card, without Enter, turned "ran" into "closed first".
        self.ask("Sign in from the task terminal.\n\n```run\necho once\n```")
        self.hand("echo once")
        self.type("\r", self.slug)
        self.wait(lambda: len(self.notices()) == 1)
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        terminal.hand(self.project, self.slug, self.open(self.slug), "echo once")
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.wait(lambda: len(self.notices()) == 2)
        self.settled()
        self.assertIn("before the operator ran the command", self.notices()[-1])
        [question] = S.load_task(self.project, self.slug)["questions"]
        self.assertTrue(question["ran"]["finished"])

    def test_commands_the_question_did_not_hand_leave_it_unmarked(self):
        self.ask("Sign in from the task terminal.\n\n```run\necho handed\n```")
        self.type("echo handed\r", self.slug)  # typed by the operator, not handed from the card
        self.hand("echo other")  # handed from another `run` block
        self.type("\r", self.slug)
        self.notice()
        self.settled()
        self.hand("echo handed")  # dropped by Ctrl+C before Enter
        self.type("\x03", self.slug)
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)
        task = S.load_task(self.project, self.slug)
        # A later attempt than the one that handed it hears nothing and marks nothing.
        self.assertIsNone(T.notify(self.project, self.slug, "Terminal: notice", by="terminal",
                                   attempt=task["attempt"] - 1, ran="echo handed", finished=True))
        self.assertEqual([q.get("ran") for q in S.load_task(self.project, self.slug)["questions"]], [None])

    def test_a_finished_task_gets_no_notice(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        self.assertIsNone(T.notify(self.project, self.slug, "Terminal: notice", by="terminal", attempt=task["attempt"]))


class TestCoordinatorCommand(TerminalCase):
    """A command altd typed from the coordinator's `run` block in project chat: the coordinator hears once it has
    run, as a queued turn."""

    def setUp(self):
        super().setUp()
        self.turn(True)
        self.ident = self.open()

    def notices(self):
        return [row for row in l3.queued(self.project) if row["trigger"] == "terminal"]

    def notice(self):
        self.wait(self.notices)
        [row] = self.notices()
        self.assertEqual(row["role"], "server")
        return row["text"]

    def test_the_coordinator_hears_once_its_command_has_run(self):
        terminal.hand(self.project, None, self.ident, "echo handed-$((3*3))")
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)  # typed but not run: nothing to report
        self.assertEqual(self.notices(), [])
        self.type("\r")
        text = self.notice()
        self.assertIn("the command you handed the operator looks finished in the project terminal: "
                      "`echo handed-$((3*3))`", text)
        self.assertIn("`alt project terminal`", text)
        self.assertNotIn("alt task terminal", text)
        self.assertIn("handed-9", terminal.output(self.project, None)["text"])
        self.assertEqual([row for row in T.pending(self.project, self.slug) if row.get("by") == "terminal"], [])

    def test_the_notice_for_an_operator_step_clears_it_from_needs_you(self):
        step = T.publish_step(self.project, "Restore the checkout.\n\n```run\necho restored-$((2+2))\n```", [self.slug])
        terminal.hand(self.project, None, self.ident, "echo restored-$((2+2))")
        self.type("\r")
        text = self.notice()
        self.assertIn(f"It cleared that operator step from Needs you; once verified, tell the tasks it named ({self.slug})", text)
        self.wait(lambda: T.operator_steps(self.project) == [])  # cleared just after the notice is queued
        self.assertEqual([(row["action"], row["id"]) for row in S.read_project_log(self.project) if row["kind"] == "step"],
                         [("asked", step["id"]), ("ran", step["id"])])

    def test_an_operator_step_stays_when_its_notice_cannot_be_queued(self):
        # Review finding: clearing before queueing let one failure empty Needs you with L3 never told.
        step = T.publish_step(self.project, "Restore the checkout.\n\n```run\necho kept\n```", [self.slug])
        failed = threading.Event()

        def refuse(*_args, **_kwargs):
            failed.set()
            raise OSError("fixture queue failure")
        self.patch(l3, "queue_message", side_effect=refuse)
        terminal.hand(self.project, None, self.ident, "echo kept")
        self.type("\r")
        self.assertTrue(failed.wait(10))
        time.sleep(.2)
        self.assertEqual([row["id"] for row in T.operator_steps(self.project)], [step["id"]])

    def test_an_operator_step_stays_when_the_terminal_ends_before_its_command_finished(self):
        # Review finding: clearing on the ended-first notice emptied Needs you while the work was still blocked.
        step = T.publish_step(self.project, "Restore the checkout.\n\n```run\nsleep 30\n```", [self.slug])
        terminal.hand(self.project, None, self.ident, "sleep 30")
        self.type("\r")
        time.sleep(.5)
        term = self.current()
        terminal.close(self.project, None)
        self.gone(term)
        text = self.notice()
        self.assertIn("the project terminal ended (closed) before the command you handed the operator finished", text)
        self.assertNotIn("cleared", text)
        self.assertEqual([row["id"] for row in T.operator_steps(self.project)], [step["id"]])

    def test_a_command_the_operator_typed_themselves_sends_no_notice(self):
        self.type("echo mine\r")
        self.output(until="mine\r\n")
        time.sleep(terminal.COMMAND_SETTLE_SECONDS + .5)
        term = self.current()
        terminal.close(self.project, None)
        self.gone(term)
        time.sleep(.2)
        self.assertEqual(self.notices(), [])

    def test_the_terminal_ending_first_is_reported_unless_the_project_was_removed(self):
        terminal.hand(self.project, None, self.ident, "echo never")
        term = self.current()
        terminal.close(self.project, None)
        self.gone(term)
        self.assertIn("the project terminal ended (closed) before the operator ran the command you handed them: "
                      "`echo never`", self.notice())
        l3.queue_path(self.project).unlink()
        terminal.hand(self.project, None, self.open(), "echo gone")
        term = self.current()
        self.patch(config, "is_managed", return_value=False)
        terminal.sweep()
        self.gone(term)
        self.assertEqual(term.reason, "project-removed")
        time.sleep(.2)
        self.assertEqual(self.notices(), [])

    def test_a_project_removed_while_the_notice_waits_gets_none(self):
        # Review finding: removal finishing between the notice's first check and its enqueue left a stale turn.
        terminal.hand(self.project, None, self.ident, "echo raced")
        term = self.current()
        managed = iter([True, False])  # the notice's first check, then removal finished before its enqueue
        self.patch(config, "is_managed", side_effect=lambda _name: next(managed, False))
        terminal.close(self.project, None)
        self.gone(term)
        time.sleep(.2)
        self.assertEqual(self.notices(), [])


class TestOwnerOutput(TerminalCase):
    def test_the_owner_reads_its_task_terminal_as_text_until_a_new_terminal_or_the_task_ends(self):
        self.turn(True)
        self.assertEqual(terminal.output(self.project, self.slug)["state"], "none")
        self.open(self.slug)
        self.type("printf '\\033[1;32mgreen\\033[0m\\n'; printf 'step 1\\rstep 2\\n'\n", self.slug)
        self.output(self.slug, until="step 2\r\n")
        running = terminal.output(self.project, self.slug)
        self.assertEqual((running["state"], running["missed"]), ("running", False))
        self.assertIn("\ngreen\nstep 2\n", running["text"])
        self.assertNotIn("\x1b", running["text"])
        term = self.current(self.slug)
        self.type("exit 5\n", self.slug)
        self.gone(term)
        ended = terminal.output(self.project, self.slug)
        self.assertEqual((ended["state"], ended["exit_code"], ended["reason"]), ("exited", 5, "exited"))
        self.assertIn("step 2", ended["text"])
        self.open(self.slug)  # a new terminal replaces what the owner could read
        self.assertNotIn("step 2", terminal.output(self.project, self.slug)["text"])
        term = self.current(self.slug)
        terminal.close(self.project, self.slug)
        self.gone(term)
        self.assertEqual(terminal.output(self.project, self.slug)["state"], "exited")
        task = S.load_task(self.project, self.slug)
        task.update(state="done", agent_id=None)
        S.save_task(self.project, task)
        terminal.sweep()
        self.assertEqual(terminal.output(self.project, self.slug)["state"], "none")

    def test_the_owner_learns_when_earlier_output_was_dropped_and_never_reads_the_project_terminal(self):
        self.turn(True)
        self.patch(terminal, "REPLAY_BYTES", 4096)
        self.open(self.slug)
        self.type("head -c 20000 /dev/zero | tr '\\0' x; echo; echo done-$((1+1))\n", self.slug)
        self.output(self.slug, until="done-2")
        self.assertTrue(terminal.output(self.project, self.slug)["missed"])
        self.open()
        self.type("echo project-only\n")
        self.output(until="project-only")
        self.assertNotIn("project-only", terminal.output(self.project, self.slug)["text"])


class TestOwnerHttp(TerminalCase):
    door = False  # these cases decide who the task's owner is

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
        self.unit = engines.claude._claude_unit("agent")
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
        self.assertEqual(refused["error"], server.TASK_DOOR_REFUSED)
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


class TestCoordinatorReader(TerminalCase):
    """The project's coordinator reads its project terminal through its project-bound socket, and nothing else does."""

    def setUp(self):
        super().setUp()
        self.turn(True)
        self.broker = server.start_l3_verb_broker(self.project)
        self.coordinator_turn()
        self.addCleanup(server.stop_l3_verb_broker, self.broker)

    def coordinator(self, project, *args):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(l3.verb_socket_path(project)))
            client.sendall((json.dumps({"kind": "alt", "args": ["project", "terminal", *args], "stdin": ""}) + "\n").encode())
            return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))

    def test_the_coordinator_reads_its_project_terminal_as_text_until_altitude_restarts(self):
        self.assertEqual(self.coordinator(self.project)["stdout"], "[altitude] no terminal output: this project's "
                         "terminal has not been opened since Altitude last started\n")
        self.open()
        self.type("printf '\\033[1;32mgreen\\033[0m\\n'; echo for-the-$((1+1))\n")
        self.output(until="for-the-2\r\n")
        self.open(self.slug)
        self.type("echo task-$((2*2))\n", self.slug)
        self.output(self.slug, until="task-4\r\n")
        result = self.coordinator(self.project)
        self.assertEqual(result["returncode"], 0, result)
        self.assertTrue(result["stdout"].startswith("[altitude] terminal running\n"), result["stdout"])
        self.assertIn("\ngreen\nfor-the-2\n", result["stdout"])
        self.assertNotIn("task-", result["stdout"])
        self.assertNotIn("\x1b", result["stdout"])
        term = self.current()
        self.type("exit 6\n")
        self.gone(term)
        record = json.loads(self.coordinator(self.project, "--json")["stdout"])
        self.assertEqual((record["state"], record["exit_code"], record["reason"]), ("exited", 6, "exited"))
        self.assertIn("for-the-2", record["text"])
        self.assertEqual(self.coordinator(self.project, "--last", "5"), {"error": "alt project terminal: the only option is --json"})
        terminal._ended.clear()  # altd's restart: the output lived only in its memory
        self.assertIn("has not been opened since Altitude last started", self.coordinator(self.project)["stdout"])

    def test_the_coordinator_reads_only_the_last_bounded_output(self):
        self.patch(terminal, "REPLAY_BYTES", 4096)
        self.open()
        self.type("head -c 20000 /dev/zero | tr '\\0' x; echo; echo done-$((2+2))\n")
        self.output(until="done-4")
        stdout = self.coordinator(self.project)["stdout"]
        self.assertTrue(stdout.startswith("[altitude] terminal running; earlier output was dropped\n"), stdout[:80])
        self.assertIn("done-4", stdout)
        self.assertLessEqual(len(stdout), 4096 + 100)

    def test_another_projects_coordinator_and_task_workers_cannot_read_it(self):
        self.open()
        self.type("echo project-$((7*6))\n")
        self.output(until="project-42\r\n")
        other = f"{self.project}-other"
        other_repo = self.tmp / "other-repo"
        other_repo.mkdir()
        self.register(other, path=other_repo)
        broker = server.start_l3_verb_broker(other)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        self.coordinator_turn(self.project, other)
        self.assertNotIn("project-42", self.coordinator(other)["stdout"])
        forged = self.coordinator(other, "--project", self.project)
        self.assertNotIn("project-42", json.dumps(forged))
        # The owner's HTTP reader names a task; it has no project-terminal form.
        self.patch(terminal, "owner_connection", return_value=True)
        with self.assertRaises(ValueError):
            server.owner_terminal_output(self.project, None, "1", ("127.0.0.1", 1), ("127.0.0.1", 2))
        # The ordinary CLI, for a task owner, the coordinator's environment or the operator, has no such verb.
        for env in ({"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"},
                    {"ALTITUDE_ACTOR": "l3"}, {}):
            result = self.alt("project", "terminal", env={"ALTITUDE_PROJECT": self.project, **env})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("project-42", result.stdout)
            self.assertIn("only through the project's coordinator socket", result.stderr)


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


class TestAgentRefusal(AltitudeCase):
    host = "linux"  # the kernel's socket and control-group records, as fixtures

    PEER, LOCAL = ("127.0.0.1", 51000), ("127.0.0.1", 8443)
    APPS = "user.slice/user-1000.slice/user@1000.service/app.slice"
    ALTD = f"{APPS}/altitude.service"

    def setUp(self):
        super().setUp()
        self.cgroups = self.tmp / "cgroup"
        self.cgroups.mkdir()
        (self.cgroups / "cgroup.controllers").write_text("cpu memory pids\n")
        self.patch(platform, "CGROUPS", self.cgroups)
        self.proc = self.tmp / "proc"
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "self/cgroup").write_text(f"0::/{self.ALTD}\n")
        self.patch(platform, "PROC", self.proc)
        self.sockets: dict[tuple, int] = {}
        self.patch(platform, "_socket_group", side_effect=lambda *end: self.sockets.get(end))

    def group(self, path: str) -> int:
        """A control group in the fixture tree, by its id: its folder's inode, as the kernel's id is."""
        folder = self.cgroups / path
        folder.mkdir(parents=True, exist_ok=True)
        return folder.stat().st_ino

    def connect(self, group: int, peer=PEER, local=LOCAL) -> None:
        self.sockets[(peer[0], peer[1], local[0], local[1])] = group

    def from_group(self, path: str) -> bool:
        self.sockets.clear()
        self.connect(self.group(path))
        return terminal.agent_connection(self.PEER, self.LOCAL)

    def test_the_operators_browser_is_allowed(self):
        self.assertFalse(self.from_group(f"{self.APPS}/app-chrome-1.scope"))
        self.assertFalse(self.from_group(f"{self.APPS}/snap.firefox.firefox-2.scope"))

    def test_a_worker_unit_and_any_group_below_it_is_refused(self):
        for unit in ("altitude-codex-abc.service", "altitude-claude-1b2c.service", "altitude-review-x.service"):
            for path in (f"{self.APPS}/{unit}", f"{self.APPS}/{unit}/sub/deeper"):
                with self.subTest(path=path):
                    self.assertTrue(self.from_group(path))

    def test_altd_and_anything_it_started_is_refused(self):  # an L3 turn runs as altd's child, in altd's own group
        self.patch(terminal, "ALTITUDE_UNIT", re.compile("no-unit-matches"))
        for path in (self.ALTD, f"{self.ALTD}/turn"):
            with self.subTest(path=path):
                self.assertTrue(self.from_group(path))
        self.assertFalse(self.from_group(f"{self.APPS}/app-chrome-1.scope"))

    def test_a_socket_whose_group_cannot_be_named_is_refused(self):
        vanished = self.group(f"{self.APPS}/app-gone.scope")
        (self.cgroups / self.APPS / "app-gone.scope").rmdir()
        for name, group in (("missing record", 0), ("root group", self.cgroups.stat().st_ino), ("vanished", vanished)):
            with self.subTest(name):
                self.sockets.clear()
                self.connect(group)
                self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))
        browser = f"{self.APPS}/app-chrome-1.scope"
        (self.cgroups / "cgroup.controllers").unlink()  # no cgroup2 hierarchy at its place
        self.assertTrue(self.from_group(browser))

    def test_without_its_own_unified_group_every_group_counts_as_altds(self):
        (self.proc / "self/cgroup").write_text("1:name=systemd:/legacy\n")
        self.assertTrue(self.from_group(f"{self.APPS}/app-chrome-1.scope"))

    def test_an_unidentified_local_client_is_refused_and_a_remote_one_allowed(self):
        # Loopback binds for real; the offline guard forbids binding anything else, which no host here owns.
        this_host = terminal._this_host
        self.patch(terminal, "_this_host", side_effect=lambda ip: this_host(ip) if ip.is_loopback else False)
        self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))
        self.assertTrue(terminal.agent_connection(("::ffff:127.0.0.1", 51000), ("::ffff:127.0.0.1", 8443)))
        if this_host(terminal._address("127.8.9.10")):  # Linux owns all of 127/8; macOS binds 127.0.0.1 only
            self.assertTrue(terminal.agent_connection(("127.8.9.10", 51000), ("127.0.0.1", 8443)))
        self.assertFalse(terminal.agent_connection(("203.0.113.20", 51000), ("192.168.1.5", 8443)))

    def test_a_client_on_the_other_address_family_is_still_identified(self):
        # A review found an IPv6 client reaching an IPv4 address was missed in one family and passed as remote.
        mapped = lambda address: (f"::ffff:{address[0]}", address[1])
        for path, refused in ((f"{self.APPS}/altitude-codex-a.service", True), (f"{self.APPS}/app-chrome-1.scope", False)):
            with self.subTest(path=path):
                self.sockets.clear()
                self.connect(self.group(path), mapped(self.PEER), mapped(self.LOCAL))
                self.assertEqual(terminal.agent_connection(self.PEER, self.LOCAL), refused)
                self.assertEqual(terminal.agent_connection(mapped(self.PEER), mapped(self.LOCAL)), refused)

    def test_ipv6_clients_are_identified(self):
        peer, local = ("::1", 51000), ("::1", 8443)
        self.connect(self.group(f"{self.APPS}/altitude-codex-a.service"), peer, local)
        self.assertTrue(terminal.agent_connection(peer, local))
        self.sockets.clear()
        self.connect(self.group(f"{self.APPS}/app-chrome-1.scope"), peer, local)
        self.assertFalse(terminal.agent_connection(peer, local))

    def test_a_failed_query_is_refused_and_no_owners(self):
        self.patch(platform, "_socket_group", side_effect=OSError(errno.EPERM, "fixture"))
        self.assertTrue(terminal.agent_connection(self.PEER, self.LOCAL))
        self.assertFalse(terminal.owner_connection(self.PEER, self.LOCAL, "altitude-claude-owner.service"))

    def test_only_a_socket_from_the_owners_worker_job_is_the_owners(self):
        self.connect(self.group(f"{self.APPS}/altitude-claude-owner.service/engine"))
        self.group(f"{self.APPS}/altitude-claude-other.service")
        self.assertTrue(terminal.owner_connection(self.PEER, self.LOCAL, "altitude-claude-owner.service"))
        self.assertFalse(terminal.owner_connection(self.PEER, self.LOCAL, "altitude-claude-other.service"))
        self.assertFalse(terminal.owner_connection(("127.0.0.1", 51001), self.LOCAL, "altitude-claude-owner.service"))


class TestSocketDiagReplies(AltitudeCase):
    """The kernel's sock_diag replies, as bytes: whatever arrives, a reply that names no group fails closed."""
    host = "linux"

    def reply(self, *, kind=20, attributes=b"", length=None, error=None) -> bytes:
        if error is not None:
            body = struct.pack("=i", -error) + bytes(16)
        else:
            body = bytes(72) + attributes  # inet_diag_msg, then its attributes
        return struct.pack("=IHHII", length or 16 + len(body), 2 if error is not None else kind, 0, 1, 0) + body

    @staticmethod
    def attribute(kind: int, payload: bytes) -> bytes:
        size = 4 + len(payload)
        return struct.pack("=HH", size, kind) + payload + bytes(-size % 4)

    def kernel(self, reply: bytes) -> list[bytes]:
        """The netlink socket answers `reply` to whatever is sent; returns what was sent."""
        sent = []

        class Diag:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def settimeout(self, _seconds): pass
            def send(self, data): sent.append(data)
            def recv(self, _size): return reply

        self.patch(platform.socket, "socket", Diag)
        return sent

    def test_the_request_names_the_exact_connection(self):
        sent = self.kernel(self.reply(error=errno.ENOENT))
        self.assertIsNone(platform._socket_group("127.0.0.1", 51000, "127.0.0.1", 8443))
        header, request = sent[0][:16], sent[0][16:]
        self.assertEqual(struct.unpack("=IHHII", header)[:3], (16 + 56, 20, 1))
        self.assertEqual(request[0], socket.AF_INET)
        self.assertEqual(request[8:12], struct.pack("!HH", 51000, 8443))
        self.assertEqual(request[12:16] + request[28:32], socket.inet_aton("127.0.0.1") * 2)

    def test_a_recorded_group_is_read_past_other_attributes(self):
        self.kernel(self.reply(attributes=self.attribute(1, b"x" * 5) + self.attribute(21, struct.pack("=Q", 4242))))
        self.assertEqual(platform._socket_group("127.0.0.1", 51000, "127.0.0.1", 8443), 4242)

    def test_every_reply_that_names_no_group_is_refused(self):
        peer, local = ("127.0.0.1", 51000), ("127.0.0.1", 8443)
        for name, reply in (("no group recorded", self.reply(attributes=self.attribute(1, b"abcd"))),
                            ("an empty attribute", self.reply(attributes=struct.pack("=HH", 0, 21))),
                            ("a refused query", self.reply(error=errno.EPERM)),
                            ("a reply cut short", self.reply()[:10]), ("a header cut short", b"\0" * 4),
                            ("a length past the reply", self.reply(length=400, attributes=self.attribute(1, b"abcd")))):
            with self.subTest(name):
                self.kernel(reply)
                self.assertTrue(terminal.agent_connection(peer, local))
                self.assertFalse(terminal.owner_connection(peer, local, "altitude-a.service"))


@unittest.skipUnless(NATIVE_PLATFORM == "linux", "the Linux kernel's own socket records")
class TestKernelAttribution(unittest.TestCase):
    def test_a_real_connection_is_attributed_to_the_process_that_opened_it(self):
        for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with self.subTest(address=address):
                try:
                    listener = socket.create_server((address, 0), family=family)
                except OSError:  # a host without IPv6 loopback
                    continue
                self.addCleanup(listener.close)
                client = socket.create_connection(listener.getsockname()[:2])
                self.addCleanup(client.close)
                accepted, peer = listener.accept()
                self.addCleanup(accepted.close)
                own = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
                found = platform.client_groups([peer[0]], peer[1], [address], accepted.getsockname()[1])
                self.assertEqual(found, [([part for part in own.split("/") if part], True)])
                self.assertTrue(terminal.agent_connection(peer, accepted.getsockname()))
                self.assertIsNone(platform.client_groups([peer[0]], peer[1] ^ 1, [address], accepted.getsockname()[1]))


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
        self.request("POST", f"{base}/command", {**at, "text": ""}, status=400)
        self.request("POST", f"{base}/command", {**at, "text": "stty size"}, status=400)  # says how to type it
        self.request("POST", f"{base}/command", {**at, "text": "stty size; exit 5", "bracketed": False})
        self.request("POST", f"{base}/input", {**at, "data": "\n"})
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

    def test_a_page_catching_up_answers_only_what_a_program_may_still_wait_on(self):
        # Queries replayed to a page that opened later were answered as typed input at the shell's prompt.
        self.turn(True)
        base = f"/api/terminal/{self.project}"
        ident = self.request("POST", f"{base}/open", {})["id"]
        self.output(until="$ ")
        self.type("printf '\\033[6n'; echo done-$((1+1))\n")
        self.output(until="done-2")
        self.type("read -rs -d R -p $'\\033[6n' at; echo got-${#at}\n")
        self.wait(lambda: bytes(self.current().buffer).endswith(b"\r\x1b[6n"))  # read waits for its answer
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        self.addCleanup(connection.close)
        connection.request("GET", f"{base}/stream?id={ident}&offset=0")
        events = self.events(connection.getresponse())
        first = next(events)
        data = base64.b64decode(first["data"])
        self.assertEqual(first["replay"], len(data) - len(b"\x1b[6n"))
        self.assertIn(b"done-2", data[:first["replay"]])
        self.type("\x1b[5;9R")  # the page's answer to the query the program waits on
        live = b""
        while b"got-5" not in live:
            event = next(events)
            self.assertEqual(event["replay"], 0)
            live += base64.b64decode(event["data"])

    @staticmethod
    def events(response):
        """The stream's output events, as they arrive."""
        while True:
            lines = []
            while (line := response.readline().decode()) not in ("\n", ""):
                lines.append(line.rstrip("\n"))
            if lines[:1] == ["event: output"]:
                yield json.loads(lines[1].split("data: ", 1)[1])

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
