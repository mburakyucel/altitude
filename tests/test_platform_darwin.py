"""The macOS side of the platform seam against fixtures: a fake launchctl, fixture process tables, coalitions and
sockets. It runs on any host; scripts/platform_probe.py exercises the same mechanisms natively on a Mac."""
import ctypes.util
import json
import os
import plistlib
import signal
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import platform, terminal


def described(label: str, *, state="running", pid=4242, exited="(never exited)", coalition=900, path=None) -> str:
    """`launchctl print` for one job, trimmed to the fields the seam reads."""
    lines = [f"gui/501/{label} = {{", "\tactive count = 1"]
    if path:
        lines.append(f"\tpath = {path}")
    lines += [f"\tstate = {state}", "", "\tprogram = /usr/bin/python3"]
    if pid:
        lines.append(f"\tpid = {pid}")
    lines.append(f"\tlast exit code = {exited}")
    if coalition:
        lines += ["", "\tresource coalition = {", f"\t\tID = {coalition}", "\t\ttype = resource", "\t}"]
    return "\n".join(lines + ["}", ""])


class Launchd:
    """A fake launchctl: `jobs` maps a label to its print text; every command is recorded."""

    def __init__(self):
        self.jobs: dict[str, str] = {}
        self.disabled: set[str] = set()
        self.commands: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = [str(part) for part in argv]
        self.commands.append(argv[1:])
        verb = argv[1]
        if verb == "print":
            label = argv[2].rsplit("/", 1)[1]
            if label not in self.jobs:
                return subprocess.CompletedProcess(argv, platform.NOT_FOUND, "", "Could not find service")
            return subprocess.CompletedProcess(argv, 0, self.jobs[label], "")
        if verb == "print-disabled":
            rows = "".join(f'\t\t"{label}" => disabled\n' for label in sorted(self.disabled))
            return subprocess.CompletedProcess(argv, 0, f"disabled services = {{\n{rows}\t}}\n", "")
        if verb == "bootout":
            self.jobs.pop(argv[2].rsplit("/", 1)[1], None)
        return subprocess.CompletedProcess(argv, 0, "", "")


class DarwinCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(platform.sys, "platform", "darwin")
        self.patch(platform.host_platform, "machine", return_value="arm64")
        self.patch(platform.host_platform, "mac_ver", return_value=("26.6.2", ("", "", ""), ""))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.patch(platform.Path, "home", return_value=self.home)
        self.launchd = Launchd()
        self.patch(platform.subprocess, "run", side_effect=self.launchd)


class Service(DarwinCase):
    def test_definition_is_a_launch_agent_that_restarts_on_failure_with_the_saved_environment(self):
        text = platform.definition(Path("/tmp/A 100% \"trial\""), Path("/opt/homebrew/bin/python3.12"),
                                   Path("/tmp/install.json"), {"PATH": "/opt/homebrew/bin:/usr/bin", "ALTITUDE_TLS": "0"})
        agent = plistlib.loads(text.encode())
        self.assertEqual(agent["Label"], platform.LABEL)
        self.assertEqual(agent["ProgramArguments"],
                         ["/opt/homebrew/bin/python3.12", "-B", '/tmp/A 100% "trial"/current/bin/alt', "serve"])
        self.assertEqual(agent["EnvironmentVariables"], {"ALTITUDE_CONFIG": "/tmp/install.json", "ALTITUDE_SERVICE": "1",
                                                         "ALTITUDE_TLS": "1", "PATH": "/opt/homebrew/bin:/usr/bin"})
        self.assertEqual((agent["RunAtLoad"], agent["KeepAlive"], agent["Umask"]), (True, {"SuccessfulExit": False}, 0o077))
        self.assertEqual(agent["StandardErrorPath"], str(self.home / "Library/Logs/altitude/altd.log"))
        self.assertEqual(platform.service_path(), self.home / "Library/LaunchAgents/dev.altitude.altd.plist")
        for value in ("/tmp/app\n", "/tmp/app\r", "/tmp/app\x00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.definition(Path(value), Path("/usr/bin/python3"), Path("/tmp/install.json"), {"PATH": "/bin"})

    def test_status_speaks_the_installation_vocabulary(self):
        path = platform.service_path()
        self.assertEqual(platform.status(), {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "",
                                             "FragmentPath": "", "MainPID": "0", "UnitFileState": ""})
        path.parent.mkdir(parents=True)
        path.write_text("defined")
        self.assertEqual(platform.status()["LoadState"], "loaded")  # defined, not bootstrapped: starts at login
        self.assertEqual((platform.status()["ActiveState"], platform.status()["UnitFileState"]), ("inactive", "enabled"))
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL, path=path)
        self.assertEqual({key: platform.status()[key] for key in ("ActiveState", "MainPID", "FragmentPath")},
                         {"ActiveState": "active", "MainPID": "4242", "FragmentPath": str(path)})
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL, state="not running", pid=None, exited="1", path=path)
        self.assertEqual(platform.status()["ActiveState"], "failed")
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL, path="/elsewhere/dev.altitude.altd.plist")
        self.assertEqual(platform.status()["FragmentPath"], "/elsewhere/dev.altitude.altd.plist")
        self.launchd.disabled.add(platform.LABEL)
        self.assertEqual(platform.status()["UnitFileState"], "disabled")

    def test_unreadable_launchd_is_explicit(self):
        self.patch(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 5, "", "Input/output error"))
        with self.assertRaisesRegex(RuntimeError, "Native user service failed: Input/output error"):
            platform.status()
        self.patch(platform.subprocess, "run", side_effect=OSError("fixture unavailable"))
        with self.assertRaisesRegex(RuntimeError, "Native user service unavailable"):
            platform.status()

    def test_control_bootstraps_restarts_by_reading_the_definition_again_and_stops_what_the_service_started(self):
        stopped = self.patch(platform, "_stop_members", return_value=True)
        target, domain = f"gui/{os.getuid()}/{platform.LABEL}", f"gui/{os.getuid()}"
        platform.control("start")
        self.assertEqual(self.launchd.commands[-1], ["bootstrap", domain, str(platform.service_path())])
        self.assertTrue(platform.logs_dir().is_dir())
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL)
        platform.control("start")
        self.assertEqual(self.launchd.commands[-1], ["kickstart", target])
        platform.control("restart")
        self.assertEqual(self.launchd.commands[-2:], [["bootout", target],
                                                      ["bootstrap", domain, str(platform.service_path())]])
        stopped.assert_called_once_with(900)
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL)
        self.launchd.commands.clear()
        platform.control("stop")
        platform.control("stop")  # already stopped: nothing to do
        platform.control("reload")  # launchd reads the definition when bootstrapping
        platform.control("disable")
        self.assertEqual(self.launchd.commands, [["print", target], ["bootout", target], ["print", target],
                                                 ["disable", target]])
        with self.assertRaisesRegex(ValueError, "Unknown application service operation"):
            platform.control("mask")

    def test_a_service_process_that_outlives_stop_is_not_a_stopped_service(self):
        self.patch(platform, "_stop_members", return_value=False)
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL)
        with self.assertRaisesRegex(RuntimeError, "still running"):
            platform.control("stop")

    def test_logs_read_the_service_log(self):
        self.assertEqual(platform.logs(), "")
        platform.logs_dir().mkdir(parents=True)
        (platform.logs_dir() / "altd.log").write_text("".join(f"line {n}\n" for n in range(150)))
        self.assertEqual(platform.logs().splitlines(), [f"line {n}" for n in range(50, 150)])
        self.assertEqual(platform.job_logs_hint("altitude"), str(platform.logs_dir() / "altd.log"))
        self.assertEqual(platform.job_logs_hint("altitude-restart-1"), str(platform.logs_dir() / "altitude-restart-1.log"))

    def test_the_source_checkout_service_stays_on_linux(self):
        from altitude import config, server, source_tls
        self.assertFalse(platform.source_service())
        self.patch(config, "RELEASE", None)
        with self.assertRaisesRegex(RuntimeError, "restart the source server by hand"):
            server.restart_service()
        with self.assertRaisesRegex(RuntimeError, "Linux source service"):
            source_tls.prepare(self.tmp)
        with mock.patch.object(platform.sys, "platform", "linux"):
            self.assertTrue(platform.source_service())

    def test_service_evidence_reports_state_exit_and_coalition_memory(self):
        self.patch(platform, "_members", return_value=[(10, "1"), (11, "2")])
        self.patch(platform, "_footprint", side_effect=lambda pid: pid << 20)
        record = platform.service_status("altitude.service", {})
        self.assertEqual(record["error"], "Unit not loaded or load state unavailable; termination/resource evidence is unknown.")
        self.launchd.jobs["dev.altitude.job.altitude-review-a"] = described("dev.altitude.job.altitude-review-a",
                                                                           state="not running", pid=None, exited="3")
        record = platform.service_status("altitude-review-a.service", {})
        self.assertEqual((record["state"], record["load_state"], record["exec_main_status"], record["memory_current"]),
                         ("inactive", "loaded", "3", str(21 << 20)))
        self.launchd.jobs[platform.LABEL] = described(platform.LABEL)
        record = platform.service_status("altitude", {})
        self.assertEqual((record["state"], record["pid"], record["error"]), ("active", 4242, None))


class Jobs(DarwinCase):
    def spec(self, argv: list[str]) -> dict:
        self.assertEqual(argv[:4], [sys.executable, "-I", "-B", "-c"])
        self.assertEqual(argv[5], str(Path(platform.__file__).resolve().parent.parent))
        self.assertEqual(argv[6], "launch")
        return json.loads(argv[7])

    def test_job_commands_describe_the_job_the_launcher_starts(self):
        env = {"PATH": "/usr/bin:/bin", "ALTITUDE_TASK": "task"}
        spec = self.spec(platform.job_command("altitude-codex-w1.service", ["codex", "exec"], env, runtime_max=60))
        self.assertEqual(spec, {"label": "dev.altitude.job.altitude-codex-w1", "mode": "pipe", "command": ["codex", "exec"],
                                "env": env, "runtime_max": 60, "writable": None})
        spec = self.spec(platform.job_command("altitude-claude-c.service", ["claude"], env,
                                              writable=(Path("/work/tree"), Path("/home/.altitude"))))
        self.assertEqual(spec["writable"], ["/work/tree", "/home/.altitude"])
        log, status = self.tmp / "machine.log", self.tmp / "machine.exit"
        spec = self.spec(platform.logged_job_command("altitude-machine-p-1.service", "brew list", log=log, status=status,
                                                     env=env, timeout=300))
        self.assertEqual((spec["mode"], spec["log"], spec["runtime_max"]), ("logged", str(log), 300))
        self.assertEqual(spec["command"][:2] + spec["command"][3:], ["/bin/bash", "-c", "altitude-machine", "brew list",
                                                                     str(status)])
        spec = self.spec(platform.detached_job_command("altitude-restart-1", ["python3", "restart.py"], path="/usr/bin"))
        self.assertEqual((spec["mode"], spec["env"]["PATH"], spec["log"]),
                         ("detached", "/usr/bin", str(platform.logs_dir() / "altitude-restart-1.log")))
        self.assertTrue(spec["env"]["TMPDIR"])

    def test_job_environment_keeps_the_users_temporary_directory_and_no_bus(self):
        self.patch(platform, "_user_temp", return_value="/private/var/folders/xy/T/")
        for prepare in (platform.job_env, platform.manager_env):
            env = prepare({"XDG_RUNTIME_DIR": "/run/user/501", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x", "PATH": "/bin"})
            self.assertEqual(env, {"TMPDIR": "/private/var/folders/xy/T/", "PATH": "/bin"})

    def test_a_job_is_active_while_launchd_runs_it_or_its_coalition_has_members(self):
        label = "dev.altitude.job.altitude-codex-w1"
        members = self.patch(platform, "_members", return_value=[])
        self.assertFalse(platform.job_active("altitude-codex-w1.service", {}))
        self.launchd.jobs[label] = described(label, state="spawn scheduled", pid=None)
        self.assertTrue(platform.job_active("altitude-codex-w1.service", {}))  # bootstrapped, about to start
        self.launchd.jobs[label] = described(label, state="not running", pid=None, exited="0", coalition=None)
        self.assertFalse(platform.job_active("altitude-codex-w1.service", {}))
        (platform._jobs() / label).mkdir(parents=True)
        (platform._jobs() / label / "coalition").write_text("77")
        members.return_value = [(123, "1")]  # a descendant that left launchd's view still holds the job
        self.assertTrue(platform.job_active("altitude-codex-w1.service", {}))
        members.assert_called_with(77)
        members.side_effect = OSError("process table unreadable")
        with self.assertRaisesRegex(RuntimeError, "Worker unit status is unavailable"):
            platform.job_active("altitude-codex-w1.service", {})
        self.launchd.jobs[label] = "garbled"
        self.patch(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 5, "", "denied"))
        with self.assertRaises(RuntimeError):
            platform.job_active("altitude-codex-w1.service", {})
        with self.assertRaisesRegex(RuntimeError, "identity is unavailable"):
            platform.job_active("", {})

    def test_stop_takes_every_coalition_member_then_removes_the_job(self):
        label = "dev.altitude.job.altitude-claude-c"
        self.launchd.jobs[label] = described(label, coalition=31)
        (platform._jobs() / label).mkdir(parents=True)
        stopped = self.patch(platform, "_stop_members", return_value=True)
        platform.job_stop("altitude-claude-c.service", {}, timeout=15)
        stopped.assert_called_once_with(31, limit=15)
        self.assertIn(["bootout", f"gui/{os.getuid()}/{label}"], self.launchd.commands)
        self.assertFalse((platform._jobs() / label).exists())


class Processes(DarwinCase):
    """A fixture process table: pid -> (parent, start, coalition, session, environment, name)."""

    def setUp(self):
        super().setUp()
        self.table = {}
        self.killed = []
        self.patch(platform, "_pids", side_effect=lambda: list(self.table))
        self.patch(platform, "_bsd", side_effect=self.bsd)
        self.patch(platform, "_coalition_of", side_effect=lambda pid: self.row(pid)["coalition"])
        self.patch(platform, "_environment", side_effect=lambda pid: self.row(pid)["env"])
        self.patch(platform.os, "getsid", side_effect=lambda pid: self.row(pid)["session"])
        self.patch(platform.os, "kill", side_effect=self.kill)
        self.patch(platform.time, "sleep")

    def add(self, pid, *, parent=1, start=1, coalition=1, session=1, env=(), name=b"proc", zombie=False):
        self.table[pid] = {"parent": parent, "start": start, "coalition": coalition, "session": session,
                           "env": list(env), "name": name, "zombie": zombie}

    def row(self, pid):
        if pid not in self.table:
            raise FileNotFoundError(3, "no such process")
        return self.table[pid]

    def bsd(self, pid):
        row = self.row(pid)
        return platform._BSDInfo(ppid=row["parent"], start_sec=row["start"], start_usec=0, name=row["name"],
                                 comm=row["name"][:15], status=platform.SZOMB if row["zombie"] else 2)

    def kill(self, pid, sig):
        self.killed.append((pid, sig))
        if sig == signal.SIGKILL or sig == signal.SIGTERM and not self.table[pid].get("ignores", False):
            del self.table[pid]

    def test_identity_liveness_and_name(self):
        self.add(40, start=7, name=b"zsh")
        self.assertEqual(platform.process_start(40), "7000000")
        self.assertTrue(platform.process_running(40, "7000000"))
        self.assertFalse(platform.process_running(40, "8000000"))  # the pid was reused
        self.assertIsNone(platform.process_running(40, "unknown"))
        self.add(41, zombie=True)
        self.assertFalse(platform.process_running(41, platform.process_start(41)))
        self.assertEqual(platform.process_name(40), "zsh")
        self.assertIsNone(platform.process_name(99))
        with self.assertRaises(FileNotFoundError):
            platform.process_start(99)

    def test_stop_signals_every_member_checked_again_and_escalates_after_the_grace(self):
        self.add(1)  # launchd: another coalition
        self.add(50, coalition=9)
        self.add(51, coalition=9, session=51)
        self.add(52, coalition=9, start=3)
        self.table[52]["ignores"] = True  # ignores SIGTERM
        clock = iter([0, 0, 1, 6, 6, 7, 7])
        self.patch(platform.time, "monotonic", side_effect=lambda: next(clock))
        self.assertTrue(platform._stop_members(9, spare=50))
        self.assertEqual(self.killed, [(51, signal.SIGTERM), (52, signal.SIGTERM), (52, signal.SIGKILL)])
        self.assertEqual(sorted(self.table), [1, 50])

    def test_a_reused_pid_is_not_signalled_and_survivors_are_reported(self):
        self.add(60, coalition=9, start=1)
        self.table[60]["ignores"] = True
        starts = iter([1, 2])  # listed with start 1; by the time of the signal the pid names a new process

        def bsd(pid):
            info = self.bsd(pid)
            info.start_sec = next(starts, 2)
            return info
        self.patch(platform, "_bsd", side_effect=bsd)
        clock = iter([0, 0, 61])
        self.patch(platform.time, "monotonic", side_effect=lambda: next(clock))
        self.assertFalse(platform._stop_members(9, limit=60))
        self.assertEqual(self.killed, [])

    def test_session_signal_reaches_the_session_and_marked_processes_only(self):
        self.add(70, session=70)
        self.add(71, session=70)
        self.add(72, session=72, env=[b"ALTITUDE_TERMINAL=t1"])
        self.add(73, session=73, env=[b"ALTITUDE_TERMINAL=t2"])
        self.add(74, session=74)
        platform.signal_session(70, b"ALTITUDE_TERMINAL=t1", signal.SIGHUP)
        self.assertEqual(self.killed, [(70, signal.SIGHUP), (71, signal.SIGHUP), (72, signal.SIGHUP)])

    def test_terminal_signal_reaches_its_coalition_but_not_the_supervisor(self):
        job = platform._jobs() / "dev.altitude.job.altitude-terminal-t1"
        job.mkdir(parents=True)
        (job / "coalition").write_text("12")
        (job / "supervisor").write_text("80")
        (job / "leader").write_text("81")
        for pid in (80, 81, 82):
            self.add(pid, coalition=12)
        self.add(83, coalition=13)
        proc = mock.Mock(pid=5)
        self.assertEqual(platform.terminal_leader("t1", proc), 81)
        self.assertIsNone(platform.terminal_leader("t2", proc))
        platform.signal_terminal("t1", proc, b"ALTITUDE_TERMINAL=t1", signal.SIGHUP)
        self.assertEqual(self.killed, [(81, signal.SIGHUP), (82, signal.SIGHUP)])

    def test_agent_connection_uses_coalitions_and_socket_holders(self):
        peer, local = ("127.0.0.1", 51000), ("127.0.0.1", 8443)
        handle = platform._tcp_handle(platform.ipaddress.ip_address("127.0.0.1"), 51000,
                                      platform.ipaddress.ip_address("127.0.0.1"), 8443)
        held = {}
        self.patch(platform, "_tcp_handles", side_effect=lambda pid: held[pid])
        self.patch(platform, "_altitude_coalitions", return_value={20})
        altd = os.getpid()
        for case, holder, refused in (("browser", dict(parent=1, coalition=5), False),
                                      ("worker job", dict(parent=1, coalition=20), True),
                                      ("altd child", dict(parent=altd, coalition=5), True)):
            with self.subTest(case=case):
                self.table.clear()
                self.add(altd, coalition=5)
                self.add(4000, **holder)
                held.clear()
                held.update({altd: set(), 4000: {handle}})
                self.assertEqual(terminal.agent_connection(peer, local), refused)
        self.table[4000].update(parent=1, coalition=5)
        held[4000] = set()  # the client end is held by nobody visible (hidden descriptors): unidentified, refused
        self.assertTrue(terminal.agent_connection(peer, local))

    def test_socket_ports_and_handles(self):
        self.assertEqual(platform._socket_port(int.from_bytes((8443).to_bytes(2, "big"), sys.byteorder)), 8443)
        mapped = platform.ipaddress.ip_address("::ffff:127.0.0.1")
        self.assertEqual(platform._tcp_handle(mapped, 1, platform.ipaddress.ip_address("::1"), 2),
                         f"tcp:{mapped}:1>::1:2")


class Confinement(DarwinCase):
    def test_profile_allows_signals_only_inside_the_sandbox_and_writes_only_under_the_roots(self):
        self.patch(platform, "_user_temp", return_value="/private/var/folders/ab/cd/T/")
        text = platform.seatbelt_profile(["/private/tmp/work tree", '/private/tmp/q"uote'])
        self.assertTrue(text.startswith("(version 1)(allow default)(deny signal)(allow signal (target same-sandbox))"
                                        "(deny file-write*)(allow file-write* "))
        for root in ('(subpath "/private/tmp/work tree")', '(subpath "/private/tmp/q\\"uote")',
                     '(subpath "/private/var/folders/ab/cd")', '(subpath "/dev")'):
            self.assertIn(root, text)

    def test_limits_replace_the_address_space_cap_with_a_footprint_watcher(self):
        argv = platform.limited_command(["ffmpeg", "-i", "x"], memory=1 << 30, cpu=10, output=5)
        self.assertEqual(argv[3:], [str(1 << 30), "10", "5", "ffmpeg", "-i", "x"])
        self.assertNotIn("RLIMIT_AS", argv[2])
        self.assertIn("proc_pid_rusage", argv[2])
        with mock.patch.object(platform.sys, "platform", "linux"):
            self.assertIn("RLIMIT_AS", platform.limited_command(["x"], memory=1, cpu=1, output=1)[2])

    def test_native_libraries_are_found_in_homebrew(self):
        self.patch(platform, "HOMEBREW", (self.tmp / "opt", self.tmp / "usr"))
        (self.tmp / "usr/lib").mkdir(parents=True)
        (self.tmp / "usr/lib/liblcms2.dylib").write_text("")
        with mock.patch("ctypes.util.find_library", return_value=None):
            self.assertEqual(platform.find_library("lcms2"), str(self.tmp / "usr/lib/liblcms2.dylib"))
            self.assertIsNone(platform.find_library("absent"))


class Supervisor(DarwinCase):
    """The launchd end of a job, run in-process with launchd's parts replaced."""

    def setUp(self):
        super().setUp()
        self.patch(platform, "_coalition_of", return_value=44)
        self.stopped = self.patch(platform, "_stop_members", return_value=True)
        self.patch(platform, "_running", return_value=True)
        self.patch(platform.os, "execv", side_effect=SystemExit("removed"))
        self.patch(platform, "CAFFEINATE", "/usr/bin/true")  # the power assertion; a fixture needs none
        self.job = self.tmp / "job"
        self.job.mkdir()
        self.out = self.tmp / "out.log"

    def supervise(self, command, **spec):
        spec = {"label": "dev.altitude.job.x", "mode": "pipe", "command": command, "env": {"PATH": "/usr/bin:/bin"},
                "cwd": str(self.tmp), "stdin": os.devnull, "launcher": [1, "1"], "1": {"file": str(self.out)},
                "2": {"file": str(self.out)}, **spec}
        (self.job / "spec.json").write_text(json.dumps(spec))
        with self.assertRaisesRegex(SystemExit, "removed"):
            platform._supervise(self.job)
        platform.os.execv.assert_called_with(platform.LAUNCHCTL, [platform.LAUNCHCTL, "remove", "dev.altitude.job.x"])
        return int((self.job / "status").read_text())

    def test_the_command_runs_and_what_it_left_is_stopped_before_the_status_is_recorded(self):
        self.assertEqual(self.supervise(["/bin/sh", "-c", "echo ran; exit 3"]), 3)
        self.assertEqual(self.out.read_text(), "ran\n")
        self.assertEqual((self.job / "coalition").read_text(), "44")
        self.stopped.assert_called_with(44, spare=os.getpid())
        self.assertTrue((self.job / "started").exists())

    def test_the_time_limit_stops_the_command(self):
        def stop(coalition, spare):
            try:
                os.kill(int((self.job / "leader").read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
            return True
        self.stopped.side_effect = stop
        self.assertEqual(self.supervise(["/bin/sleep", "30"], runtime_max=0.2), 128 + signal.SIGTERM)

    def test_a_command_that_cannot_start_reads_as_127(self):
        self.assertEqual(self.supervise([str(self.tmp / "missing")]), 127)
        self.assertIn("missing", self.out.read_text())

    def test_a_job_whose_launcher_has_gone_cleans_up_after_itself(self):
        platform._running.return_value = False
        with self.assertRaisesRegex(SystemExit, "removed"):
            (self.job / "spec.json").write_text(json.dumps({
                "label": "dev.altitude.job.x", "command": ["/usr/bin/true"], "env": {}, "cwd": str(self.tmp),
                "stdin": os.devnull, "launcher": [1, "1"], "1": {"file": os.devnull}, "2": {"file": os.devnull}}))
            platform._supervise(self.job)
        self.assertFalse(self.job.exists())
