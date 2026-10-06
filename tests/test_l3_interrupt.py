"""Chat interruption owns one job and keeps its partial transcript and session identity."""
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from unittest import mock

from tests.support import AltitudeCase
from altitude import engines, platform


class TestChatInterruption(AltitudeCase):
    @contextmanager
    def turn(self, engine, *, stop_error=False, delayed_start=False, descendants=False, unknown=False,
             callback_error=False):
        execute = getattr(engines, engine)
        ready, release, start = (self.tmp / name for name in ("ready", "release", "start"))
        for path in (ready, release, start):
            path.unlink(missing_ok=True)
        if not delayed_start:
            start.touch()
        events = ([{"type": "system", "session_id": "saved-session"},
                   {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "Partial answer"}}},
                   {"type": "assistant", "message": {"model": "observed", "usage": {"input_tokens": 7}}}]
                  if engine == "claude_print" else
                  [{"type": "thread.started", "thread_id": "saved-session"},
                   {"type": "item.completed", "item": {"type": "agent_message", "text": "Partial answer"}}])
        script = ("import sys,time\nfrom pathlib import Path\nsys.stdin.read()\n"
                  f"while not Path({str(start)!r}).exists(): time.sleep(.01)\n"
                  + "\n".join(f"print({json.dumps(event)!r}, flush=True)" for event in events)
                  + f"\nPath({str(ready)!r}).touch()\n"
                  + f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n")
        interrupt, done, absent, stopped, status_failed = (threading.Event() for _ in range(5))
        launched = threading.Event()
        error_notified = threading.Event()
        error_notifications = []
        children_alive, unavailable = threading.Event(), threading.Event()
        if descendants:
            children_alive.set()
        if unknown:
            unavailable.set()
        processes, result, failures, unit, identities = [], {}, [], [], []
        real_popen = subprocess.Popen

        def command(name, cmd, env, **kwargs):
            unit.append(name)
            self.assertEqual(kwargs["runtime_max"], 10)
            if engine == "claude_print":
                self.assertEqual(kwargs["writable"], engines._claude_writable(self.repo, engines.config.ROOT))
            return [sys.executable, "-c", script]

        def popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            processes.append(proc)
            launched.set()
            return proc

        def active(name, env):
            self.assertEqual(name, unit[0])
            if unavailable.is_set():
                status_failed.set()
                raise RuntimeError("fixture status unavailable")
            if not ready.exists():
                absent.set()
                return False
            return children_alive.is_set() or processes[0].poll() is None

        def stop(name, env):
            self.assertEqual(name, unit[0])
            stopped.set()
            if stop_error:
                raise RuntimeError("fixture stop failed")
            processes[0].terminate()

        def run():
            try:
                result.update(execute("hello", cwd=self.repo, resume="earlier-session", timeout=10,
                                      interrupt=interrupt, on_interrupt_error=on_interrupt_error,
                                      on_start=lambda pid: identities.append(platform.process_identity(pid))))
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        def on_interrupt_error(reason):
            error_notifications.append(reason)
            error_notified.set()
            if callback_error:
                raise RuntimeError("fixture callback failed")

        with mock.patch.object(platform, "job_command", side_effect=command), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(platform, "job_active", side_effect=active), \
             mock.patch.object(platform, "job_stop", side_effect=stop) as stop_call, \
             mock.patch.object(engines, "_codex_session_model", return_value={"engine_model": "observed"}):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            try:
                self.assertTrue(launched.wait(5), "fixture launcher did not start")
                yield dict(interrupt=interrupt, done=done, absent=absent, stopped=stopped, ready=ready,
                           release=release, start=start, children_alive=children_alive, unavailable=unavailable,
                           status_failed=status_failed, result=result, stop_call=stop_call,
                           error_notified=error_notified, error_notifications=error_notifications,
                           identities=identities, processes=processes)
            finally:
                start.touch()
                release.touch()
                unavailable.clear()
                children_alive.clear()
                for proc in processes:
                    if proc.poll() is None:
                        proc.terminate()
                thread.join(5)
                self.assertFalse(thread.is_alive(), "chat invocation did not finish")
                self.assertFalse(failures, failures)

    def wait_ready(self, turn):
        for _ in range(500):
            if turn["ready"].exists():
                return
            self.assertFalse(turn["done"].wait(.01), "fixture ended before output")
        self.fail("fixture output did not arrive")

    def assert_interrupted(self, result):
        self.assertEqual(result["session_id"], "saved-session")
        self.assertEqual(result["text"], "Partial answer")
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["error"], "Interrupted for a queued message")
        self.assertFalse(result["safe_to_retry"])
        self.assertIn("saved-session", result["raw_stdout"])

    def test_preserves_partial_answer_and_native_session_on_both_engines(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine) as turn:
                self.wait_ready(turn)
                turn["interrupt"].set()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])
                turn["stop_call"].assert_called_once()
                self.assertEqual(turn["result"]["engine_model"], "observed")
                if engine == "claude_print":
                    self.assertEqual(turn["result"]["context_tokens"], 7)
                    self.assertEqual(turn["result"]["turns"], 1)

    def test_already_requested_interrupt_never_launches(self):
        event = threading.Event()
        event.set()
        for execute in (engines.claude_print, engines.codex_exec):
            with self.subTest(engine=execute.__name__), mock.patch.object(engines.subprocess, "Popen") as launch:
                result = execute("hello", cwd=self.repo, resume="saved-session", interrupt=event)
                self.assertTrue(result["interrupted"])
                self.assertEqual(result["session_id"], "saved-session")
                launch.assert_not_called()

    def test_request_during_job_start_waits_for_creation_before_stopping(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine, delayed_start=True) as turn:
                turn["interrupt"].set()
                self.assertTrue(turn["absent"].wait(5))
                turn["stop_call"].assert_not_called()
                self.assertFalse(turn["done"].is_set())
                turn["start"].touch()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])
                turn["stop_call"].assert_called_once()

    def test_failed_stop_waits_for_natural_completion(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine, stop_error=True) as turn:
                self.wait_ready(turn)
                turn["interrupt"].set()
                self.assertTrue(turn["stopped"].wait(5))
                self.assertTrue(turn["error_notified"].wait(5))
                self.assertIn("fixture stop failed", turn["error_notifications"][0])
                self.assertFalse(turn["done"].wait(.6))
                self.assertEqual(len(turn["error_notifications"]), 1)
                turn["release"].touch()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])
                self.assertIn("unconfirmed", turn["result"]["interrupt_error"])
                turn["stop_call"].assert_called_once()

    def test_launcher_exit_does_not_release_live_descendants(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine, descendants=True) as turn:
                self.wait_ready(turn)
                turn["interrupt"].set()
                self.assertTrue(turn["stopped"].wait(5))
                self.assertFalse(turn["done"].wait(.1))
                turn["children_alive"].clear()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])

    def test_unavailable_status_never_means_terminated(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine, unknown=True) as turn:
                self.wait_ready(turn)
                turn["interrupt"].set()
                self.assertTrue(turn["status_failed"].wait(5))
                self.assertTrue(turn["error_notified"].wait(5))
                self.assertIn("fixture status unavailable", turn["error_notifications"][0])
                turn["release"].touch()
                self.assertFalse(turn["done"].wait(.6))
                self.assertEqual(len(turn["error_notifications"]), 1)
                turn["unavailable"].clear()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])
                self.assertIn("unconfirmed", turn["result"]["interrupt_error"])

    def test_failed_status_callback_does_not_release_termination_wait(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.assertLogs(engines.logger, level="ERROR"), \
                 self.turn(engine, stop_error=True, callback_error=True) as turn:
                self.wait_ready(turn)
                turn["interrupt"].set()
                self.assertTrue(turn["error_notified"].wait(5))
                self.assertFalse(turn["done"].wait(.3))
                turn["release"].touch()
                self.assertTrue(turn["done"].wait(5))
                self.assert_interrupted(turn["result"])

    def test_ordinary_completion_with_interrupt_option_does_not_stop(self):
        for engine in ("claude_print", "codex_exec"):
            with self.subTest(engine=engine), self.turn(engine) as turn:
                self.wait_ready(turn)
                self.assertEqual(len(turn["identities"]), 1)
                identity = turn["identities"][0]
                self.assertEqual(identity["pid"], turn["processes"][0].pid)
                self.assertTrue(platform.process_identity_live(identity))
                # A reloaded record still identifies the in-flight launcher until the turn ends.
                self.assertTrue(platform.process_identity_live(json.loads(json.dumps(identity))))
                turn["release"].touch()
                self.assertTrue(turn["done"].wait(5))
                self.assertFalse(platform.process_identity_live(identity))
                self.assertNotIn("interrupted", turn["result"])
                turn["stop_call"].assert_not_called()

    def test_ordinary_chat_job_keeps_confinement_environment_and_durable_timeout(self):
        for darwin in (False, True):
            for engine in ("claude_print", "codex_exec"):
                with self.subTest(darwin=darwin, engine=engine), \
                     mock.patch.object(platform, "_darwin", return_value=darwin), \
                     mock.patch.object(platform, "_user_temp", return_value=str(self.tmp)), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=RuntimeError("capture launch")) as launch:
                    execute = getattr(engines, engine)
                    extra_env = {"ALTITUDE_PROJECT": "fixture", "DBUS_SESSION_BUS_ADDRESS": "fixture-bus",
                                 "XDG_RUNTIME_DIR": str(self.tmp / "manager")}
                    # The direct integration's existing profile defines the writable surface.
                    direct_writable = None
                    direct_command = None
                    if engine == "claude_print":
                        with mock.patch.object(platform, "confined", wraps=platform.confined) as confined:
                            with self.assertRaisesRegex(RuntimeError, "capture launch"):
                                execute("hello", cwd=self.repo, extra_env=extra_env)
                            direct_writable = confined.call_args.args[1]
                            direct_command = launch.call_args.args[0]
                    with self.assertRaisesRegex(RuntimeError, "capture launch"):
                        execute("hello", cwd=self.repo, extra_env=extra_env, timeout=37,
                                interrupt=threading.Event())
                    command = launch.call_args.args[0]
                    launcher_env = launch.call_args.kwargs["env"]
                    self.assertEqual(launch.call_args.kwargs["cwd"], str(self.repo))
                    if darwin:
                        spec = json.loads(command[-1])
                        self.assertEqual(spec["mode"], "pipe")
                        self.assertEqual(spec["runtime_max"], 37)
                        child_env, child_command = spec["env"], spec["command"]
                        if direct_writable:
                            self.assertEqual(spec["writable"], [str(root) for root in direct_writable])
                            self.assertEqual(platform.seatbelt_profile(spec["writable"]),
                                             platform.confined(child_command, direct_writable)[2])
                    else:
                        for option in ("--wait", "--pipe", "--property=RuntimeMaxSec=37",
                                       "--property=TimeoutStopSec=5", "--property=KillMode=control-group",
                                       "--property=SendSIGKILL=yes"):
                            self.assertIn(option, command)
                        child = command[command.index("--") + 1:]
                        self.assertEqual(child[:2], [platform.ENV_BIN, "-i"])
                        index = child.index(getattr(engines.config, "CLAUDE_BIN" if engine == "claude_print" else "CODEX_BIN"))
                        child_env = dict(item.split("=", 1) for item in child[2:index])
                        child_command = child[index:]
                        self.assertEqual(launcher_env["DBUS_SESSION_BUS_ADDRESS"], "fixture-bus")
                    self.assertEqual(child_env["ALTITUDE_PROJECT"], "fixture")
                    self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", child_env)
                    self.assertNotIn("XDG_RUNTIME_DIR", child_env)
                    if direct_command:
                        self.assertEqual(child_command, direct_command[3:] if darwin else direct_command)
                    else:
                        for setting in engines.codex_sandbox(self.repo):
                            self.assertIn(setting, child_command)
