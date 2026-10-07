"""Issue #617: confined terminal cleanup reads sessions without executing ps."""
from unittest import mock

from tests.support import AltitudeCase
from altitude import platform


class SessionProcesses(AltitudeCase):
    def test_linux_reads_session_and_zombie_state_from_procfs(self):
        proc = self.tmp / "proc"
        proc.mkdir()
        (proc / "net").mkdir()
        for pid, session, state in ((40, 40, "S"), (41, 40, "R"), (42, 40, "Z"), (43, 43, "S")):
            (proc / str(pid)).mkdir()
            (proc / str(pid) / "stat").write_text(f"{pid} (a ) tricky name) {state} 1 40 {session} 0\n")
        (proc / "44").mkdir()  # exits between enumeration and stat read
        with mock.patch.object(platform, "_darwin", return_value=False), \
             mock.patch.object(platform, "PROC", proc), \
             mock.patch.object(platform.subprocess, "run", side_effect=PermissionError("ps refused")):
            self.assertEqual(sorted(platform.session_processes(40)), [40, 41])

    def test_mac_reads_libproc_zombies_and_skips_vanished_or_unreadable_processes(self):
        def session(pid):
            if pid == 44:
                raise ProcessLookupError("exited")
            if pid == 45:
                raise PermissionError("unreadable")
            return 43 if pid == 43 else 40

        def bsd(pid):
            if pid == 46:
                raise ProcessLookupError("exited after getsid")
            return platform._BSDInfo(status=platform.SZOMB if pid == 42 else 2)

        with mock.patch.object(platform, "_darwin", return_value=True), \
             mock.patch.object(platform, "_pids", return_value=list(range(40, 47))), \
             mock.patch.object(platform.os, "getsid", side_effect=session), \
             mock.patch.object(platform, "_bsd", side_effect=bsd), \
             mock.patch.object(platform.subprocess, "run", side_effect=PermissionError("ps refused")):
            self.assertEqual(platform.session_processes(40), [40, 41])

    def test_mac_process_table_failure_is_not_an_empty_session(self):
        with mock.patch.object(platform, "_darwin", return_value=True), \
             mock.patch.object(platform, "_pids", side_effect=PermissionError("table refused")):
            with self.assertRaisesRegex(PermissionError, "table refused"):
                platform.session_processes(40)
