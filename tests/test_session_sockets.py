"""A confined job reaches no Unix socket of the desktop session: none in the per-user runtime folder, none in the shared
temporary folders and, where the kernel offers Landlock's scope, no abstract one, while its own sockets and temporary
folder work. Every socket here is a fictional fixture the test itself listens on."""

import errno
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import AltitudeCase
from altitude import platform

#: Run inside the job: connect to each named socket and report what happened, then make a socket of its own in its
#: temporary folder (the second argument) and an abstract one, and reach both.
PROBE = r'''
import json, socket, sys
def reach(address):
    with socket.socket(socket.AF_UNIX) as peer:
        try:
            peer.connect(address)
            return "reached"
        except OSError as exc:
            return exc.errno
places, folder = json.loads(sys.argv[1]), sys.argv[2]
seen = {name: reach(address if name != "abstract" else "\0" + address) for name, address in places.items()}
own = socket.socket(socket.AF_UNIX); own.bind(folder + "/own.sock"); own.listen()
mine = socket.socket(socket.AF_UNIX); mine.bind("\0" + places["abstract"] + "-own"); mine.listen()
seen["own"], seen["own abstract"] = reach(folder + "/own.sock"), reach("\0" + places["abstract"] + "-own")
open(folder + "/written", "w").write("job")
print(json.dumps(seen))
'''

#: What starts a job's scope outside bubblewrap: no_new_privs, which bubblewrap sets for its command and Landlock needs.
NO_NEW_PRIVS = ("import ctypes, os, sys; assert ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) == 0; "
                "os.execv(sys.argv[1], sys.argv[1:])")


def listen(address: str) -> socket.socket:
    server = socket.socket(socket.AF_UNIX)
    server.bind(address)
    server.listen()
    return server


@unittest.skipUnless(sys.platform == "linux", "Linux's sandbox")
class TestAbstractScope(AltitudeCase):

    def test_the_scope_refuses_abstract_sockets_made_outside_the_job_and_keeps_its_own(self):
        if not platform.abstract_scope():
            self.skipTest("this kernel offers no Landlock abstract-socket scope")
        name = f"fictional-desk-{os.getpid()}"
        self.addCleanup(listen("\0" + name).close)
        command = [sys.executable, "-I", "-c", PROBE, json.dumps({"abstract": name}), str(self.tmp)]
        outside = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(outside.stdout)["abstract"], "reached", outside.stderr)
        (self.tmp / "own.sock").unlink()
        scoped = subprocess.run([sys.executable, "-I", "-c", NO_NEW_PRIVS, sys.executable, "-I", "-c",
                                 platform.ABSTRACT_SCOPE, *command], capture_output=True, text=True, timeout=60)
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        self.assertEqual(json.loads(scoped.stdout), {"abstract": errno.EPERM, "own": "reached", "own abstract": "reached"})

    def test_the_sandbox_tool_says_whether_the_kernel_scopes_abstract_sockets(self):
        """Setup and doctor show it (tests/test_onboarding.py, tests/test_installation.py); a job starts either way."""
        for scoped in (True, False):
            with self.subTest(scoped=scoped):
                self.patch(platform, "abstract_scope", return_value=scoped)
                self.assertIs(platform.sandbox_tool()["scoped"], scoped)


@unittest.skipUnless(sys.platform == "linux", "Linux's sandbox")
class TestSandboxedJob(AltitudeCase):
    """Real bubblewrap, on a Linux host where it starts: not inside an Altitude worker, whose sandbox forbids new
    namespaces, a validation container, which cannot mount a fresh /proc, or a runner whose AppArmor refuses it user
    namespaces. There the cases skip; `make sandbox-vm` runs them in a throwaway VM where they start."""

    def setUp(self):
        super().setUp()
        try:
            ready = subprocess.run([platform.BWRAP, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "--dev",
                                    "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "true"], capture_output=True, text=True)
            why = ready.stderr.strip() if ready.returncode else None
        except FileNotFoundError:
            why = "not installed"
        if why:
            self.skipTest(f"bubblewrap does not start here: {why}")
        self.home = self.tmp / "home"
        (self.home / ".cache").mkdir(parents=True)
        self.patch(platform.Path, "home", return_value=self.home)
        self.root = Path(os.path.realpath(self.tmp))
        self.runtime = self.root / "run"
        (self.runtime / "fictional-session").mkdir(parents=True)
        (self.runtime / "coordinators").mkdir()
        self.patch(platform, "job_control_paths", return_value=(self.runtime,))
        self.work = self.root / "work"
        self.work.mkdir()
        self.temporary = Path(tempfile.mkdtemp(prefix="altitude-", dir=self.tmp))
        shared = tempfile.mkdtemp(prefix="fictional-desk-", dir="/tmp")
        self.addCleanup(shutil.rmtree, shared, True)
        self.places = {"visible": str(self.root / "visible"),
                       "runtime": str(self.runtime / "fictional-session/socket"),
                       "shared": os.path.join(shared, "socket"),
                       "broker": str(self.runtime / "coordinators/ab12"),
                       "abstract": f"fictional-desk-{os.getpid()}"}
        for name, address in self.places.items():
            self.addCleanup(listen(address if name != "abstract" else "\0" + address).close)

    def run_job(self, sockets=()) -> dict:
        # The case folder lies in the shared /tmp, which the job's own folder replaces; read-only again, it shows the
        # job's view of the fictional runtime folder in it, which only the runtime-folder mask hides (`visible` proves it).
        argv = platform.bubblewrap_profile([str(self.work)], [str(self.root)], temporary=str(self.temporary),
                                           sockets=sockets)
        done = subprocess.run([*argv, sys.executable, "-I", "-c", PROBE, json.dumps(self.places), "/tmp"],
                              capture_output=True, text=True, timeout=60, cwd=self.work)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_host_reaches_every_fixture_and_the_job_none_but_its_own(self):
        host = subprocess.run([sys.executable, "-I", "-c", PROBE, json.dumps(self.places), str(self.work)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(set(json.loads(host.stdout).values()), {"reached"}, host.stderr)
        seen = self.run_job()
        abstract = errno.EPERM if platform.abstract_scope() else "reached"
        self.assertEqual(seen, {"visible": "reached", "runtime": errno.ENOENT, "shared": errno.ENOENT, "broker": errno.ENOENT,
                                "abstract": abstract, "own": "reached", "own abstract": "reached"})
        self.assertEqual((self.temporary / "written").read_text(), "job", "the job's /tmp is its own folder")
        self.assertTrue((self.temporary / "own.sock").exists())

    def test_a_coordinator_reaches_its_own_broker_and_nothing_else_in_the_runtime_folder(self):
        seen = self.run_job(sockets=[self.places["broker"]])
        self.assertEqual((seen["broker"], seen["runtime"], seen["shared"]), ("reached", errno.ENOENT, errno.ENOENT))


class TestJobTemporaryFolder(AltitudeCase):
    """Every job's temporary folder is its own, and goes when the job ends."""

    host = "linux"

    def test_a_linux_job_gets_a_fresh_folder_that_its_service_manager_removes(self):
        env = {"PATH": "/usr/bin:/bin"}
        engine = platform.job_command("altitude-codex-w1.service", ["codex"], env)
        confined = platform.job_command("altitude-claude-c.service", ["claude"], env, writable=(self.tmp,))
        folders = []
        for argv in (engine, confined):
            stop = next(arg for arg in argv if arg.startswith("--property=ExecStopPost="))
            folder = stop.rsplit(" ", 1)[1]
            self.assertEqual(stop, f"--property=ExecStopPost=-{platform.RM} -rf -- {folder}")
            self.assertTrue(Path(folder).is_dir())
            self.assertEqual(Path(folder).stat().st_mode & 0o777, 0o700)
            self.assertTrue(Path(folder).name.startswith("altitude-"))
            folders.append(folder)
        self.assertNotEqual(*folders)
        self.assertIn(f"TMPDIR={folders[0]}", engine, "an engine's own sandbox gets the folder as TMPDIR")
        self.assertIn("TMPDIR=/tmp", confined, "Altitude's sandbox mounts the folder as /tmp")
        bound = [confined[i:i + 3] for i in range(len(confined)) if confined[i] == "--bind"]
        self.assertIn(["--bind", folders[1], os.path.realpath("/tmp")], bound)
        self.assertEqual(confined[-1], "claude")


if __name__ == "__main__":
    unittest.main()
