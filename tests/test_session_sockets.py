"""A confined job reaches no Unix socket of the desktop session: none in the per-user runtime folder, none in the shared
temporary folders and, where the kernel offers Landlock's scope, no abstract one, while its own sockets and temporary
folder work. It sees nothing of an engine's folders and files of the operator's own beyond the entries its launch
names. Every socket and folder here is a fictional fixture the test itself makes."""

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

from tests.support import NATIVE_PLATFORM, AltitudeCase
from altitude import config, engines, platform, repair

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

#: Run inside the job: read each named file, try to write the second argument and list the folder in the third.
FILES = r'''
import json, os, sys
seen = {}
for name, path in json.loads(sys.argv[1]).items():
    try:
        with open(path) as stream:
            seen[name] = stream.read()
    except OSError as exc:
        seen[name] = exc.errno
try:
    open(sys.argv[2], "a").close()
    seen["write"] = "written"
except OSError as exc:
    seen["write"] = exc.errno
try:
    seen["listing"] = sorted(os.listdir(sys.argv[3]))
except OSError as exc:
    seen["listing"] = exc.errno
print(json.dumps(seen))
'''

#: What starts a job's scope outside bubblewrap: no_new_privs, which bubblewrap sets for its command and Landlock needs.
NO_NEW_PRIVS = ("import ctypes, os, sys; assert ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) == 0; "
                "os.execv(sys.argv[1], sys.argv[1:])")


def macos_only(why: str):
    """Runs a case only on macOS; `make sandbox-vm` accepts this skip on its Linux guest and fails on any other."""
    return unittest.skipUnless(NATIVE_PLATFORM == "darwin", f"macOS only: {why}")


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

    def test_an_engines_folder_of_the_operators_own_shows_a_job_only_what_its_folder_links_to(self):
        operator = self.home / ".codex"
        (operator / "sessions").mkdir(parents=True)
        for name, text in (("auth.json", "fictional sign-in"), ("config.toml", "fictional settings"),
                           ("sessions/rollout.jsonl", "fictional conversation")):
            (operator / name).write_text(text)
        own = self.work / "codex"
        own.mkdir()
        (own / "config.toml").symlink_to(operator / "config.toml")
        argv = platform.bubblewrap_profile([str(self.work)], [str(operator / "config.toml")],
                                           temporary=str(self.temporary), hidden=[str(operator)])
        files = {"sign-in": str(operator / "auth.json"), "conversation": str(operator / "sessions/rollout.jsonl"),
                 "linked settings": str(own / "config.toml")}
        done = subprocess.run([*argv, sys.executable, "-I", "-c", FILES, json.dumps(files), str(operator / "config.toml"),
                               str(operator)], capture_output=True, text=True, timeout=60, cwd=self.work)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"sign-in": errno.ENOENT, "conversation": errno.ENOENT,
                                                   "linked settings": "fictional settings", "write": errno.EROFS,
                                                   "listing": ["config.toml"]})
        self.assertEqual((operator / "auth.json").read_text(), "fictional sign-in", "the host's folder is unchanged")


class HomeSandboxCase(AltitudeCase):
    """A fictional home with the operator's own places, seen from a job in the platform's own sandbox: bubblewrap on
    Linux, where `make sandbox-vm` runs it, and Seatbelt on macOS."""

    def setUp(self):
        super().setUp()
        # A dash, a dot and a space, which a profile must quote and match as written.
        self.home = Path(os.path.realpath(self.tmp)) / "fictional a-b.c home"
        self.home.mkdir()
        self.patch(platform.Path, "home", return_value=self.home)
        self.patch(config, "HOME", self.home)
        self.work = Path(os.path.realpath(self.tmp)) / "work"
        self.work.mkdir()
        self.temporary = tempfile.mkdtemp(prefix="altitude-", dir=self.tmp)
        # Not inside an Altitude worker or a validation run, nor on a Linux runner refusing user namespaces.
        try:
            ready = subprocess.run([*self.sandbox(), sys.executable, "-I", "-c", "pass"], capture_output=True,
                                   text=True, timeout=60)
            why = ready.stderr.strip() if ready.returncode else None
        except FileNotFoundError:
            why = "not installed"
        if why:
            self.skipTest(f"the platform's sandbox does not start here: {why}")

    def sandbox(self, read_only=(), hidden=(), writable=()) -> list[str]:
        roots = [str(self.work), *map(str, writable)]
        if NATIVE_PLATFORM == "darwin":
            return [platform.SANDBOX_EXEC, "-p", platform.seatbelt_profile(roots, list(read_only),
                                                                            temporary=self.temporary, hidden=hidden)]
        return platform.bubblewrap_profile(roots, list(read_only), temporary=self.temporary, hidden=hidden)

    def job(self, files: dict, write: Path, listing: Path, read_only=(), hidden=(), before=None, writable=()) -> dict:
        """What a job sees of `files`, the write and the listing; `before` runs once the job has started, before it
        looks."""
        go = self.work / "go"
        wait = f"import os, time\nwhile not os.path.exists({str(go)!r}): time.sleep(0.05)\n"
        job = subprocess.Popen([*self.sandbox(read_only, hidden, writable), sys.executable, "-I", "-c", wait + FILES,
                                json.dumps({name: str(path) for name, path in files.items()}), str(write), str(listing)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.work)
        if before:
            before()
        go.touch()
        out, err = job.communicate(timeout=60)
        self.assertEqual(job.returncode, 0, err)
        return json.loads(out)


class TestOperatorsClaudeFolder(HomeSandboxCase):
    """The operator's Claude folder and state file, named through the engine seam (`engines.operator_places`). A
    Claude worker reads only what its launch reopens: the entries its own folder links to, the project's notes and a
    session it resumes for the first time."""

    def setUp(self):
        super().setUp()
        self.operator = self.home / ".claude"
        for name, text in ((".credentials.json", "fictional sign-in"), ("settings.json", "fictional settings"),
                           ("skills/fixture/SKILL.md", "fictional skill"),
                           ("projects/-repo/memory/MEMORY.md", "fictional notes"),
                           ("projects/-repo/sid-old.jsonl", "fictional resumed session"),
                           ("projects/-repo/sid-other.jsonl", "fictional conversation")):
            (self.operator / name).parent.mkdir(parents=True, exist_ok=True)
            (self.operator / name).write_text(text)
        for name in (".claude.json", ".claude.json.tmp.1.fixture", "dotfiles/claude.json"):
            (self.home / name).parent.mkdir(parents=True, exist_ok=True)
            (self.home / name).write_text("fictional state")
        (self.home / ".claude.json.backup.fixture").symlink_to(self.home / "dotfiles/claude.json")
        self.patch(engines.claude, "OPERATOR_HOME", self.operator)

    def claude_files(self, own: Path) -> dict:
        return {"sign-in": self.operator / ".credentials.json", "state": self.home / ".claude.json",
                "conversation": self.operator / "projects/-repo/sid-other.jsonl",
                "linked settings": own / "settings.json", "linked skill": own / "skills/fixture/SKILL.md"}

    def test_a_claude_job_sees_only_what_its_launch_reopens_of_the_operators_claude_folder(self):
        hidden = platform._operator_places()
        self.assertLessEqual({str(self.operator), str(self.home / ".claude.json") + "*"}, set(hidden))
        own = self.work / "claude"
        resumed = self.operator / "projects/-repo/sid-old.jsonl"
        notes = self.operator / "projects/-repo/memory"
        engines.claude._own_folder(own, [])
        read_only = [str(path) for path in (*engines.claude._linked(), notes, resumed)]
        files = {**self.claude_files(own), "state copy": self.home / ".claude.json.tmp.1.fixture",
                 "linked state copy": self.home / ".claude.json.backup.fixture",
                 "notes": notes / "MEMORY.md", "resumed": resumed}
        seen = self.job(files, self.operator / "settings.json", self.operator, read_only, hidden)
        # Linux shows a hidden folder empty and refuses a hidden file, a device node on a mount without devices;
        # Seatbelt refuses both.
        missing, refused = (errno.EPERM, errno.EPERM) if NATIVE_PLATFORM == "darwin" else (errno.ENOENT, errno.EACCES)
        self.assertEqual(seen, {
            "sign-in": missing, "state": refused, "state copy": refused, "linked state copy": refused,
            "conversation": missing,
            "linked settings": "fictional settings", "linked skill": "fictional skill", "notes": "fictional notes",
            "resumed": "fictional resumed session",
            "write": errno.EPERM if NATIVE_PLATFORM == "darwin" else errno.EROFS,
            "listing": errno.EPERM if NATIVE_PLATFORM == "darwin" else ["projects", "settings.json", "skills"]})
        self.assertEqual((self.home / ".claude.json").read_text(), "fictional state", "the host's files are unchanged")
        self.assertEqual((self.operator / "settings.json").read_text(), "fictional settings")

    @macos_only("Seatbelt matches resolved paths and names made later")
    def test_a_linked_claude_folder_shows_a_job_its_linked_entries_and_nothing_else_of_the_target(self):
        target = self.home / "dotfiles/claude-folder"
        self.operator.rename(target)
        self.operator.symlink_to(target)
        own = self.work / "claude"
        engines.claude._own_folder(own, [])
        read_only = [str(path) for path in engines.claude._linked()]
        files = {**self.claude_files(own), "sign-in, by the target": target / ".credentials.json",
                 "settings, by the target": target / "settings.json"}
        seen = self.job(files, target / "settings.json", target, read_only, platform._operator_places())
        self.assertEqual(seen, {"sign-in": errno.EPERM, "state": errno.EPERM, "conversation": errno.EPERM,
                                "sign-in, by the target": errno.EPERM, "linked settings": "fictional settings",
                                "linked skill": "fictional skill", "settings, by the target": "fictional settings",
                                "write": errno.EPERM, "listing": errno.EPERM})

    @macos_only("Seatbelt refuses a name the state file's prefix starts")
    def test_a_state_file_copy_made_after_a_claude_job_starts_is_refused_inside_it(self):
        later = self.home / ".claude.json.backup.1700000000000"
        own = self.work / "claude"
        engines.claude._own_folder(own, [])
        seen = self.job({"later copy": later, "state": self.home / ".claude.json"}, later, self.home,
                        [str(path) for path in engines.claude._linked()], platform._operator_places(),
                        before=lambda: later.write_text("fictional state"))
        self.assertEqual((seen["later copy"], seen["state"], seen["write"]), (errno.EPERM,) * 3)

    @macos_only("Seatbelt hides the folder; bubblewrap's case is TestSandboxedJob")
    def test_a_job_sees_only_the_entries_its_codex_folder_links_to_of_the_operators_codex_folder(self):
        operator = self.home / ".codex"
        (operator / "sessions").mkdir(parents=True)
        for name, text in (("auth.json", "fictional sign-in"), ("config.toml", "fictional settings"),
                           ("sessions/rollout.jsonl", "fictional conversation")):
            (operator / name).write_text(text)
        self.patch(engines.codex, "operator_home", return_value=operator)
        hidden = platform._operator_places()
        self.assertIn(str(operator), hidden)
        own = self.work / "codex"
        engines.codex._own_folder(own, operator, [])
        files = {"sign-in": operator / "auth.json", "conversation": operator / "sessions/rollout.jsonl",
                 "linked settings": own / "config.toml"}
        seen = self.job(files, operator / "config.toml", operator, [str(operator / "config.toml")], hidden)
        self.assertEqual(seen, {"sign-in": errno.EPERM, "conversation": errno.EPERM,
                                "linked settings": "fictional settings", "write": errno.EPERM, "listing": errno.EPERM})
        self.assertEqual((operator / "config.toml").read_text(), "fictional settings", "the host's folder is unchanged")


class TestOperatorsCredentials(HomeSandboxCase):
    """The operator's stored credentials in the home (`platform.credential_places`), each holding a fictional key: a
    job reads none of them, also through a link, while a worker's GitHub CLI and Git still sign in to GitHub with the
    task's token (`engines.task_tool_env`)."""

    #: The places that are files; the others are folders.
    FILES = {".git-credentials", ".config/git/credentials", ".netrc", ".docker/config.json",
             ".config/containers/auth.json", ".npmrc", ".pypirc", ".cargo/credentials", ".cargo/credentials.toml",
             ".gem/credentials", ".terraform.d/credentials.tfrc.json", ".vault-token"}

    def setUp(self):
        super().setUp()
        self.keys = {}
        for place in platform.CREDENTIAL_HOME:
            key = self.home / place if place in self.FILES else self.home / place / "fixture-key"
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text("fictional key")
            self.keys[place] = key
        # A dotfiles manager's link: hidden where it leads.
        (self.home / "dotfiles").mkdir()
        (self.home / ".ssh").rename(self.home / "dotfiles/ssh")
        (self.home / ".ssh").symlink_to(self.home / "dotfiles/ssh")

    def test_a_job_reads_none_of_the_operators_stored_credentials(self):
        files = {**self.keys, "linked ssh, by the target": self.home / "dotfiles/ssh/fixture-key"}
        seen = self.job(files, self.home / ".ssh/written", self.home / ".ssh", hidden=platform._operator_places())
        # Linux shows a hidden folder empty and refuses a hidden file; Seatbelt refuses both.
        darwin = NATIVE_PLATFORM == "darwin"
        self.assertEqual(seen, {**{name: errno.EPERM if darwin else errno.EACCES if name in self.FILES else errno.ENOENT
                                   for name in files},
                                "write": errno.EPERM if darwin else "written",
                                "listing": errno.EPERM if darwin else ["written"]})
        self.assertFalse((self.home / "dotfiles/ssh/written").exists(), "a write lands in the job's own empty folder")
        for key in self.keys.values():
            self.assertEqual(key.read_text(), "fictional key", "the host's files are unchanged")

    @unittest.skipUnless(shutil.which("gh") and shutil.which("git"), "the GitHub CLI and Git")
    def test_a_workers_github_cli_and_git_sign_in_to_github_with_the_tasks_token(self):
        (self.home / ".gitconfig").write_text("[credential]\n\thelper = store\n")  # the operator's own helper
        env = engines.task_tool_env({"HOME": str(self.home), "PATH": os.environ["PATH"], "GH_TOKEN": "fictional-token"},
                                    self.work / "scratch")
        probe = ("import json, subprocess\n"
                 "def run(*args, text=None):\n"
                 "    done = subprocess.run(args, input=text, capture_output=True, text=True)\n"
                 "    return [done.returncode, done.stdout.strip()]\n"
                 "print(json.dumps({'token': run('gh', 'auth', 'token'),\n"
                 "                  'url': run('git', 'ls-remote', '--get-url', 'git@github.com:fixture/demo.git'),\n"
                 "                  'credential': run('git', 'credential', 'fill',\n"
                 "                                    text='protocol=https\\nhost=github.com\\npath=fixture/demo.git\\n\\n')}))\n")
        done = subprocess.run([*self.sandbox(hidden=platform._operator_places()), sys.executable, "-I", "-c", probe],
                              capture_output=True, text=True, timeout=60, cwd=self.work, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(done.stdout)
        self.assertEqual(seen["token"], [0, "fictional-token"], "gh keeps settings of its own, beside the hidden ones")
        self.assertEqual(seen["url"], [0, "https://github.com/fixture/demo.git"])
        self.assertEqual(seen["credential"], [0, "protocol=https\nhost=github.com\nusername=x-access-token\n"
                                                 "password=fictional-token"])


class TestRepairRecords(HomeSandboxCase):
    """Repairs' records (`repair.home`), which only the operator reads: a job sees none of them, even one whose roots
    hold Altitude's whole home, as a coordinator's turn's do, while it reads and writes the rest of that home."""

    def test_a_job_writing_altitudes_home_sees_no_repair(self):
        self.patch(config, "ROOT", Path(os.path.realpath(self.tmp)) / "altitude home")
        config.ensure_root()
        task = config.ROOT / "atlas/tasks/fixture"
        task.mkdir(parents=True)
        (task / "task.json").write_text("fictional task")
        earlier = repair.home() / ("a" * 32)
        earlier.mkdir()
        for name in ("repair.json", "steps.jsonl"):
            (earlier / name).write_text("fictional repair")
        later = repair.home() / ("b" * 32) / "repair.json"

        def started_later():
            later.parent.mkdir()
            later.write_text("fictional repair")

        files = {"task": task / "task.json", "record": earlier / "repair.json", "steps": earlier / "steps.jsonl",
                 "later record": later}
        seen = self.job(files, task / "written", repair.home(), hidden=platform._operator_places(),
                        writable=[config.ROOT], before=started_later)
        # Linux shows the hidden folder empty; Seatbelt refuses it.
        refused = errno.EPERM if NATIVE_PLATFORM == "darwin" else errno.ENOENT
        self.assertEqual(seen, {"task": "fictional task", "record": refused, "steps": refused, "later record": refused,
                                "write": "written", "listing": errno.EPERM if NATIVE_PLATFORM == "darwin" else []})
        self.assertTrue((task / "written").exists(), "the home is the host's")
        self.assertEqual(sorted(path.name for path in repair.home().iterdir()), ["a" * 32, "b" * 32])


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
