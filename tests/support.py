"""The one test fixture: a throwaway runtime home for the process, a private project per test case, and the
shared fakes (git repositories, the `gh` shim, quiet engines).

Import this before anything from `altitude`: it points HOME and ALTITUDE_HOME at a throwaway directory before
`altitude.config` freezes its paths (config refuses the live ~/.altitude from a unittest process anyway).
Run the suite from the repository root: `python3 -m unittest discover tests`.
"""
from __future__ import annotations

import atexit
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


@contextmanager
def container_namespace_metadata(root: Path):
    """Model image UID/GID 1000 without requiring CI's host account to have those IDs.

    Real filesystem content/type/mode/link/time observations remain intact. Native
    VM lanes separately verify actual user-namespace mapping and ownership changes.
    """
    original = Path.lstat
    def observed(path):
        value = original(path)
        if not path.is_relative_to(root):
            return value
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith('st_')}
        fields.update(st_uid=1000, st_gid=1000)
        return SimpleNamespace(**fields)
    with mock.patch.object(Path, 'lstat', new=observed):
        yield

REPO = Path(__file__).resolve().parent.parent
_NATIVE_SANDBOX_BINARY = shutil.which(os.environ.get("CODEX_BIN", "codex"))
_native_sandbox_command = None
# Resolved, so symlinked temporary roots (macOS /var -> /private/var) compare equal to resolved paths, and short,
# so Unix sockets under a case directory stay within the 104-byte macOS limit ($TMPDIR there is ~50 bytes).
SUITE = Path(tempfile.mkdtemp(prefix="altitude-tests-", dir="/tmp")).resolve()
tempfile.tempdir = str(SUITE)
atexit.register(shutil.rmtree, SUITE, ignore_errors=True)
OFFLINE_BIN = SUITE / "bin"
OFFLINE_COMMANDS = ("claude", "codex", "gh", "systemctl", "systemd-run", "journalctl", "launchctl", "service", "ssh", "curl", "wget", "podman")


def install_offline_guards() -> None:
    """Bootstrap before Altitude imports; child CLIs inherit the same closed external boundary.

    Prevent repeated PR checks from launching paid workers or touching the operator's service
    through inherited credentials, provider homes, absolute executables, or Python HTTP clients.
    Real Git repositories, fixture subprocesses and loopback HTTP remain available.
    """
    for key in list(os.environ):
        if (key.startswith(("CLAUDE", "CODEX", "OPENAI", "ANTHROPIC", "GH_", "GITHUB_", "AWS_", "AZURE_", "GOOGLE_", "NVM_",
                            "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
                or key in {"ALTITUDE_ACTOR", "ALTITUDE_TASK", "ALTITUDE_PROJECT", "ALTITUDE_ATTEMPT",
                           "ALTITUDE_SESSION_KEY", "ALTITUDE_ROOTS", "ALTITUDE_TLS_DIR", "ALTITUDE_HOST",
                           "ALTITUDE_PORT", "ALTITUDE_OPERATOR", "ALTITUDE_PRIMARY_ENGINE", "ALTITUDE_CONFIG",
                           "ALTITUDE_UPSTREAM_ISSUE_REPOSITORY", "DBUS_SESSION_BUS_ADDRESS",
                           "ALTITUDE_SERVICE", "ALTITUDE_PRIMARY_ENGINE", "ALTITUDE_BASE_BRANCH",
                           "SSH_AUTH_SOCK", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND",
                           "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}):
            os.environ.pop(key, None)
    for key, name in {"HOME": "home", "CODEX_HOME": "home/.codex", "CLAUDE_CONFIG_DIR": "home/.claude",
                      "XDG_CONFIG_HOME": "home/.config", "XDG_DATA_HOME": "home/.local/share",
                      "XDG_STATE_HOME": "home/.local/state", "XDG_CACHE_HOME": "home/.cache",
                      "XDG_RUNTIME_DIR": "runtime", "ALTITUDE_HOME": "altitude"}.items():
        path = SUITE / name
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    OFFLINE_BIN.mkdir(exist_ok=True)
    for name in OFFLINE_COMMANDS:
        path = OFFLINE_BIN / name
        path.write_text("#!/bin/sh\necho 'offline tests: external executable denied; install a fixture at the engine boundary' >&2\nexit 86\n")
        path.chmod(0o755)
    os.environ.update({"CLAUDE_BIN": str(OFFLINE_BIN / "claude"), "CODEX_BIN": str(OFFLINE_BIN / "codex"),
                       "PATH": f"{OFFLINE_BIN}:{os.environ.get('PATH', '')}", "GIT_TERMINAL_PROMPT": "0",
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_ALLOW_PROTOCOL": "file", "ALTITUDE_TIMERS": "0"})


def _offline_audit(event, args):
    if event == "subprocess.Popen":
        executable = os.fsdecode(args[0])
        if Path(executable).name in OFFLINE_COMMANDS:
            resolved = Path(shutil.which(executable) or executable).resolve()
            if resolved.exists() and not resolved.is_relative_to(SUITE) and tuple(args[1]) != _native_sandbox_command:
                raise AssertionError(f"offline tests denied external executable: {Path(executable).name}")
    elif event in ("socket.connect", "socket.bind"):
        sock, address = args
        if sock.family in (2, 10):  # IPv4 / IPv6, leave local fixture Unix sockets intact.
            host = address[0]
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == "localhost"
            if not local or address[1] == 8890:
                raise AssertionError("offline tests denied non-loopback network connection")


install_offline_guards()
sys.addaudithook(_offline_audit)


def run_native_sandbox_probe(runtime: Path, settings: list[str], probe: str, arguments: list[str]):
    """The existing explicit, zero-model native sandbox probe; no exec/turn invocation is permitted."""
    global _native_sandbox_command
    if os.environ.get("ALTITUDE_TEST_CODEX_SANDBOX") != "1":
        raise AssertionError("native sandbox probe requires ALTITUDE_TEST_CODEX_SANDBOX=1")
    if not _NATIVE_SANDBOX_BINARY:
        raise AssertionError("requested native sandbox verification requires the installed binary")
    command = [_NATIVE_SANDBOX_BINARY, "sandbox", "-P", "altitude-l3", "-C", str(runtime)]
    for setting in settings:
        command += ["-c", setting]
    command += ["--", sys.executable, "-c", probe, *arguments]
    _native_sandbox_command = tuple(command)
    try:
        return subprocess.run(command, cwd=runtime, env=engines.codex_env(), capture_output=True, text=True, timeout=45)
    finally:
        _native_sandbox_command = None


os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
sys.path.insert(0, str(REPO))
from altitude import access, config, engines, incidents, monitor, platform  # noqa: E402

ALT = REPO / "bin" / "alt"

#: Fake gh: every call is logged; the answers come from files the test writes into $FAKE_GH_DIR.
GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")

def read(name, default=None):
    p = os.path.join(d, name)
    return open(p).read() if os.path.exists(p) else default

def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)

def remote_oid(ref):
    found = subprocess.run(["git", "ls-remote", "--heads", "origin", ref], capture_output=True, text=True)
    return found.stdout.split()[0] if found.returncode == 0 and found.stdout.strip() else None

def pull_request(target):
    body = json.loads(read("prs.json", "{}")).get(str(target))
    if body is None:
        body = json.loads(read("pr.json", "null"))
    if body is None:
        fail("no pull requests found for branch " + str(target))
    body.setdefault("headRefName", "worktree-fix-x")
    body.setdefault("baseRefName", "main")
    body.setdefault("headRefOid", remote_oid(body["headRefName"]))
    body.setdefault("baseRefOid", remote_oid(body["baseRefName"]))
    return body

def connection(nodes):
    return {"nodes": nodes, "totalCount": len(nodes), "pageInfo": {"hasNextPage": False}}

if read("fail.txt") is not None:
    fail("fake gh failure", 2)
cmd = tuple(args[:2])
if cmd == ("pr", "view"):
    error = read("view_error.txt")
    if error is not None:  # a broken or logged-out gh, not a missing PR
        fail(error)
    if args[2] in json.loads(read("fail_prs.json", "[]")):
        fail("fake gh PR failure", 2)
    canned = json.loads(read("prs.json", "{}"))  # PRs by number
    if args[2] in canned:
        print(json.dumps(canned[args[2]]))
    elif read("pr.json") is not None:  # the branch's PR; its tips come from the fake remote
        print(json.dumps(pull_request(args[2])))
    else:
        fail("no pull requests found for branch " + args[2])
elif cmd == ("pr", "create"):
    previous = json.loads(read("pr.json", "null"))
    history = json.loads(read("prs.json", "{}"))
    if previous:
        history[str(previous["number"])] = previous
        open(os.path.join(d, "prs.json"), "w").write(json.dumps(history))
    number = max([100, *(int(n) for n in history)]) + 1
    body = {"number": number, "url": "https://example.invalid/pr/" + str(number), "state": "OPEN",
            "isCrossRepository": False, "isDraft": False,
            "baseRefName": args[args.index("--base") + 1], "headRefName": args[args.index("--head") + 1]}
    open(os.path.join(d, "pr.json"), "w").write(json.dumps(body))
    print(body["url"])
elif cmd == ("pr", "checks"):
    body = read("checks.json", '[{"bucket": "pass"}]')
    if not body.strip():
        fail("no checks reported on the 'x' branch")
    print(body)
elif cmd == ("api", "graphql"):
    variables = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args[:-1]) if arg in ("-F", "-f"))
    if read("graphql_response.json") is not None:
        print(read("graphql_response.json"))
    elif read("check_evidence.json") is not None:
        print(json.dumps({"data": {"repository": json.loads(read("check_evidence.json"))}}))
    else:
        body = pull_request(variables["number"])
        head = body["headRefOid"]
        conclusions = {"pass": "SUCCESS", "fail": "FAILURE", "skipping": "SKIPPED", "cancel": "CANCELLED"}
        checks = json.loads(read("checks.json", '[{"bucket": "pass"}]') or "[]")
        contexts = [{"__typename": "CheckRun", "name": item.get("name", "fixture-check-" + str(i)),
                     "status": "IN_PROGRESS" if item["bucket"] == "pending" else "COMPLETED",
                     "conclusion": conclusions.get(item["bucket"]), "isRequired": False,
                     "checkSuite": {"commit": {"oid": head}, "app": {"databaseId": 1, "slug": "fixture-ci"},
                                    "branch": {"name": body["headRefName"]}, "workflowRun": None,
                                    "matchingPullRequests": connection([{"number": body["number"],
                                        "baseRefName": body["baseRefName"], "headRefName": body["headRefName"]}])}}
                    for i, item in enumerate(checks)]
        tree = subprocess.check_output(["git", "rev-parse", head + "^{tree}"], text=True).strip()
        commit = {"oid": head, "tree": {"oid": tree},
                  "statusCheckRollup": {"contexts": connection(contexts)} if contexts else None}
        body.update(baseRef={"target": {"oid": remote_oid(body["baseRefName"])},
                             "branchProtectionRule": None, "rules": connection([])},
                    commits={"nodes": [{"commit": commit}]}, potentialMergeCommit=None)
        repository = {"nameWithOwner": variables["owner"] + "/" + variables["repo"], "pullRequest": body}
        open(os.path.join(d, "last_check_evidence.json"), "w").write(json.dumps(repository))
        print(json.dumps({"data": {"repository": repository}}))
elif cmd == ("pr", "merge"):
    body = pull_request(args[2])
    if read("merge_git.txt") is not None:
        # A composed journey opts in: the hosted merge also advances the real local bare remote.
        head = "refs/remotes/origin/" + body["headRefName"]
        base = "refs/remotes/origin/" + body["baseRefName"]
        tree = read("merge_tree.txt") or subprocess.check_output(
            ["git", "merge-tree", "--write-tree", base, head], text=True).strip()
        parents = ["-p", base] + (["-p", head] if "--merge" in args else [])
        head = subprocess.check_output(["git", "commit-tree", tree, *parents,
                                       "-m", "fixture hosted merge"], text=True).strip()
        merged = subprocess.run(["git", "push", "origin", head + ":main"], capture_output=True, text=True)
        if merged.returncode:
            fail(merged.stderr)
        oid = subprocess.check_output(["git", "rev-parse", head], text=True).strip()
        body.update(mergeCommit={"oid": oid}, mergedAt="2026-09-08T00:00:00Z")
    body["state"] = "MERGED"
    open(os.path.join(d, "pr.json"), "w").write(json.dumps(body))
elif cmd == ("pr", "edit"):
    pass
elif cmd == ("pr", "list"):
    print(read("pr_list.json", "[]"))
elif cmd[0] == "issue":
    # Incident issues: the whole repository lives in issues.json; every write lands there for assertions.
    repository = args[args.index("--repo") + 1]
    issues = json.loads(read("issues.json", "[]"))
    def find(target):
        target = target.rsplit("/", 1)[-1]
        return next(i for i in issues if str(i["number"]) == target)
    if cmd[1] == "list":
        fields = args[args.index("--json") + 1].split(",")
        label = args[args.index("--label") + 1] if "--label" in args else None
        print(json.dumps([{k: i[k] for k in fields} for i in issues if label is None or label in i["labels"]]))
    elif cmd[1] == "create":
        if read("issue_create_error.txt") is not None:
            fail(read("issue_create_error.txt"))
        number = max([100, *(i["number"] for i in issues)]) + 1
        issues.append({"number": number, "url": f"{repository}/issues/{number}", "state": "OPEN",
                       "title": next(a[len("--title="):] for a in args if a.startswith("--title=")),
                       "labels": [a[len("--label="):] for a in args if a.startswith("--label=")],
                       "body": sys.stdin.read(), "comments": [], "closed_reason": None})
        print(issues[-1]["url"])
    elif cmd[1] == "view":
        fields = args[args.index("--json") + 1].split(",")
        print(json.dumps({k: find(args[2])[k] for k in fields}))
    elif cmd[1] == "comment":
        find(args[2])["comments"].append(sys.stdin.read())
    elif cmd[1] == "close":
        issue = find(args[2])
        issue.update(state="CLOSED", closed_reason=args[args.index("--reason") + 1])
    else:
        fail("fake gh: unhandled " + " ".join(args), 64)
    open(os.path.join(d, "issues.json"), "w").write(json.dumps(issues))
elif cmd == ("run", "list"):
    print(read("runs.json", '[{"databaseId": 7, "status": "completed", "conclusion": "success"}]'))
elif cmd == ("run", "view"):
    print(read("run.json", '{"status": "completed", "conclusion": "success"}'))
elif cmd == ("auth", "token"):
    # The keyring answers only where the session bus is reachable.
    if read("token.txt") is None or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        fail("no oauth token found for github.com")
    print(read("token.txt"))
elif cmd == ("api", "user"):
    # Signed in through the keyring on the session bus or an exported token; otherwise unauthenticated.
    token = (read("token.txt") or "").strip()
    if not token or not (os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("GH_TOKEN") == token):
        fail("HTTP 401: Requires authentication (https://api.github.com/graphql)")
    print('{"login": "fixture-operator"}')
else:
    fail("fake gh: unhandled " + " ".join(args), 64)
'''


def fyi_rows(project: str) -> list[dict]:
    """The project's FYIs: the system rows in its chat (SPEC.md §5.2 note 3)."""
    from altitude import l3
    return [row for row in l3.chat_history(project, None)
            if row.get("role") == "system" and row.get("trigger") == "fyi"]


def git(*args: str, cwd: Path) -> str:
    """Run git in `cwd`; fail the test on error; return stdout."""
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {p.stderr or p.stdout}")
    return p.stdout


def make_repo(repo: Path) -> Path:
    """`repo` with one commit on main pushed to the bare `origin.git` beside it; `.claude/` is ignored."""
    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / ".gitignore").write_text(".claude/\n")
    (repo / "README.md").write_text("readme\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    origin = repo.parent / "origin.git"
    git("init", "-q", "--bare", str(origin), cwd=repo)
    git("remote", "add", "origin", str(origin), cwd=repo)
    git("push", "-q", "-u", "origin", "main", cwd=repo)
    return repo


def add_worktree(repo: Path, slug: str) -> Path:
    """The dispatcher's layout: `.claude/worktrees/<slug>` on branch `worktree-<slug>`."""
    path = repo / ".claude" / "worktrees" / slug
    git("worktree", "add", "-q", "-b", f"worktree-{slug}", str(path), cwd=repo)
    return path


class AltitudeCase(unittest.TestCase):
    """A private project per test case in the shared runtime home, gone again afterwards. HTTP requests reach
    their routes as this machine's own CLI does; a case about pairing and the access gate sets `gated`. A case whose
    fixtures stand in for one host's service manager (systemd-run and systemctl shims) names it in `host`. A worker
    launch reads no GitHub sign-in unless the case sets `github` and supplies its own `gh` fixture."""

    gated = False
    host: str | None = None
    github = False

    def setUp(self) -> None:
        super().setUp()
        if self.host:
            self.patch(platform.sys, "platform", self.host)
        config.ensure_root()
        if not self.gated:
            self.patch(access, "is_machine", return_value=True)
        if not self.github:
            self.patch(engines, "github_token", return_value="")
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=SUITE))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.project = self._testMethodName.replace("_", "-")[:64]
        self.register(self.project)

    def register(self, name: str, *, path: Path | None = None, **fields) -> dict:
        """Register a project for this case; its record and runtime directory are removed afterwards."""
        projects = config.load_projects()
        projects[name] = {"name": name, "path": str(path or self.repo), **fields}
        config.save_projects(projects)
        self.addCleanup(self._forget, name)
        return projects[name]

    @staticmethod
    def _forget(name: str) -> None:
        projects = config.load_projects()
        projects.pop(name, None)
        config.save_projects(projects)
        shutil.rmtree(config.project_dir(name), ignore_errors=True)

    def setenv(self, key: str, value: str | None) -> None:
        """Set (or unset, with None) an environment variable for the rest of the case."""
        old = os.environ.get(key)
        self.addCleanup(lambda: os.environ.update({key: old}) if old is not None else os.environ.pop(key, None))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def patch(self, target, name: str, *new, **kwargs):
        """`mock.patch.object` for the rest of the case; returns what it installed."""
        patcher = mock.patch.object(target, name, *new, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def quiet_engines(self, agents: list[dict] | None = None) -> None:
        """No usage hold, known quota, and only the given Claude agents: the dispatcher sees a free machine."""
        self.patch(engines, "claude_agents", return_value=list(agents or []))
        self.patch(engines, "usage_hold", return_value=None)
        self.patch(engines, "installation", return_value={"available": None, "why": "test installation"})
        self.patch(monitor, "quota", return_value={"known": True})

    def private_ledgers(self) -> None:
        """Point the home-wide files (monitor, incident index, faults, digest) at this case's directory."""
        (self.tmp / "monitor").mkdir(exist_ok=True)
        self.patch(config, "MONITOR_DIR", self.tmp / "monitor")
        self.patch(config, "INCIDENT_INDEX", self.tmp / "incidents.jsonl")
        self.patch(config, "DIGEST_FILE", self.tmp / "DIGEST.md")
        self.patch(incidents, "FAULTS", self.tmp / "monitor" / "faults.json")

    def fake_gh(self) -> Path:
        """Install the gh shim first on PATH; returns the directory the test writes its answers into."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        shim = bindir / "gh"
        shim.write_text(GH)
        shim.chmod(0o755)
        state = self.tmp / "gh-state"
        state.mkdir(exist_ok=True)
        self.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '')}")
        self.setenv("FAKE_GH_DIR", str(state))
        return state

    def gh_log(self) -> list[list[str]]:
        log = self.tmp / "gh-state" / "log.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def serving(self, port: int, host: str = "127.0.0.1") -> None:
        """Record a plain-HTTP altd at `port`, as the running service records itself for `alt` to reach."""
        from altitude import tls
        tls.publish({"host": host, "port": port, "tls": False, "tls_dir": config.TLS_DIR})
        self.addCleanup(tls.record().unlink, missing_ok=True)

    def alt(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run `bin/alt` with empty input against this runtime home."""
        merged = {**os.environ, "ALTITUDE_HOME": str(config.ROOT), **(env or {})}
        return subprocess.run([sys.executable, str(ALT), *args], input="", capture_output=True, text=True, env=merged)


# --- The operator terminal's job, without a service manager --------------------------------------------------------

#: The job's start: the shell's terminal becomes its controlling terminal and its input and output.
TERMINAL_LAUNCHER = ("import fcntl, os, sys, termios\nfd = os.open(sys.argv[1], os.O_RDWR)\nfor n in (0, 1, 2):\n"
                     "    os.dup2(fd, n)\nos.close(fd)\n"
                     "fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # macOS assigns no controlling terminal on open\n"
                     "os.execvp(sys.argv[2], sys.argv[2:])\n")


def terminal_session(leader: int) -> list[int]:
    """The processes still in a terminal's session."""
    rows = subprocess.run(["ps", "-Ao", "pid=,stat="], capture_output=True, text=True, check=True).stdout
    found = []
    for pid, state in (line.split() for line in rows.splitlines()):
        try:
            if os.getsid(int(pid)) == leader and not state.startswith("Z"):
                found.append(int(pid))
        except OSError:
            continue
    return found


def local_terminal_launch(unit: str, tty: str, path: Path) -> subprocess.Popen:
    """`terminal.launch` at the platform seam: the shell on `tty` in a session of its own, as its job runs it."""
    from altitude import terminal
    return subprocess.Popen([sys.executable, "-c", TERMINAL_LAUNCHER, tty, *terminal.shell_command()], cwd=path,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            env={**os.environ, **terminal.shell_env()}, start_new_session=True)


def local_terminal_stop(unit: str) -> None:
    """`terminal.stop`: hang up every process in the shell's session, then kill what remains after the grace period.
    The service manager's job also holds processes that leave the session; a session cannot."""
    import signal
    import time
    from altitude import terminal
    term = next((t for t in list(terminal._terminals.values()) if t.unit == unit), None)
    if term is None:
        return
    for sig in (signal.SIGHUP, signal.SIGKILL):
        for pid in terminal_session(term.proc.pid):
            try:
                os.kill(pid, sig)
            except OSError:
                continue
        deadline = time.monotonic() + terminal.CLOSE_GRACE_SECONDS
        while terminal_session(term.proc.pid) and time.monotonic() < deadline:
            time.sleep(.02)
