"""`alt land` runs the whole land-a-PR sequence offline here: a real temp repo with a bare
remote stands in for GitHub's git side, and a fake `gh` first on PATH answers view/create/
checks/merge/run-list from canned JSON while recording every argv it was called with. The no-CI
merge gate also logs the exact candidate directory in which its fake test runner executes."""
import contextlib, io, json, os, runpy, shlex, shutil, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-land-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, land, state as S  # noqa: E402

# A stand-in `gh`: logs argv, answers from FAKE_GH_DIR state files (pr.json, checks.json,
# runs.json) with sensible defaults, and mutates pr.json the way GitHub would on create/merge.
GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\\n")
def read(name, default):
    p = os.path.join(d, name)
    if os.path.exists(p):
        with open(p) as f:
            return f.read()
    return default
cmd = tuple(args[:2])
if cmd == ("pr", "view"):
    if os.path.exists(os.path.join(d, "view_error.txt")):  # a broken or logged-out gh, not a missing PR
        print(read("view_error.txt", ""), file=sys.stderr)
        sys.exit(1)
    if os.path.exists(os.path.join(d, "pr.json")):
        body = json.loads(read("pr.json", "{}") or "{}")
        def remote_oid(ref):
            found = subprocess.run(["git", "ls-remote", "--heads", "origin", ref],
                                   capture_output=True, text=True)
            return found.stdout.split()[0] if found.returncode == 0 and found.stdout.strip() else None
        head = body.get("headRefName") or "worktree-fix-x"
        base = body.get("baseRefName") or "main"
        body.setdefault("headRefName", head)
        body.setdefault("baseRefName", base)
        body.setdefault("headRefOid", remote_oid(head))
        body.setdefault("baseRefOid", remote_oid(base))
        print(json.dumps(body))
    else:
        print("no pull requests found for branch " + args[2], file=sys.stderr)
        sys.exit(1)
elif cmd == ("pr", "create"):
    body = {"number": 101, "url": "https://example.invalid/pr/101", "state": "OPEN",
            "baseRefName": args[args.index("--base") + 1], "headRefName": args[args.index("--head") + 1]}
    with open(os.path.join(d, "pr.json"), "w") as f:
        json.dump(body, f)
    print(body["url"])
elif cmd == ("pr", "checks"):
    body = read("checks.json", '[{"bucket": "pass"}]')
    if not body.strip():
        print("no checks reported on the 'x' branch", file=sys.stderr)
        sys.exit(1)
    print(body)
elif cmd == ("pr", "merge"):
    body = json.loads(read("pr.json", "{}") or "{}")
    body["state"] = "MERGED"
    with open(os.path.join(d, "pr.json"), "w") as f:
        json.dump(body, f)
elif cmd == ("pr", "edit"):
    pass
elif cmd == ("run", "list"):
    print(read("runs.json", '[{"databaseId": 7, "status": "completed", "conclusion": "success"}]'))
else:
    print("fake gh: unhandled " + " ".join(args), file=sys.stderr)
    sys.exit(64)
"""


class TestLand(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        self.tmp = Path(tempfile.mkdtemp(prefix="alt-land-case-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Point config.ROOT at a private home directly: an import-time reload here would be
        # clobbered by test_lifecycle's own reload under `unittest discover` (it loads later
        # alphabetically), and state/dispatch resolve paths lazily through the module object.
        old_root = config.ROOT
        config.ROOT = self.tmp / "home"
        self.addCleanup(setattr, config, "ROOT", old_root)
        ghbin = self.tmp / "ghbin"
        ghbin.mkdir()
        gh = ghbin / "gh"
        gh.write_text(GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.ghdir = self.tmp / "gh-state"
        self.ghdir.mkdir()
        self._setenv("PATH", f"{ghbin}:{os.environ.get('PATH', '')}")
        self._setenv("FAKE_GH_DIR", str(self.ghdir))
        self._setenv("ALTITUDE_HOME", str(config.ROOT))
        self._setenv("ALTITUDE_PROJECT", "demo")
        self._setenv("ALTITUDE_TASK", "fix-x")
        # Most test hosts (including locked-down CI) cannot create user namespaces. A small launcher exercises the
        # production command/env contract while individual adversarial tests inspect the real bwrap boundary itself.
        fake_bwrap = self.tmp / "bwrap"
        fake_bwrap.write_text("""#!/usr/bin/env python3
import os, shutil, subprocess, sys, tempfile
args = sys.argv[1:]
inside_env = {}
candidate = None
i = 0
while i < len(args):
    if args[i] == "--":
        command = args[i + 1:]
        break
    if args[i] == "--setenv":
        inside_env[args[i + 1]] = args[i + 2]
        i += 3
        continue
    if args[i] in ("--bind", "--ro-bind"):
        if args[i + 2] == "/source":
            candidate = args[i + 1]
        i += 3
        continue
    if args[i] == "--symlink":
        i += 3
        continue
    if args[i] == "--size":
        i += 2
        continue
    if args[i] in ("--dir", "--proc", "--dev", "--tmpfs", "--chdir", "--cap-drop"):
        i += 2
        continue
    i += 1
else:
    raise SystemExit(125)
if not candidate:
    raise SystemExit(126)
workspace = tempfile.mkdtemp(prefix="altitude-fake-bwrap-")
try:
    source = os.path.join(workspace, "source")
    private_home = os.path.join(workspace, "home")
    private_tmp = os.path.join(workspace, "tmp")
    os.makedirs(source)
    os.makedirs(private_home)
    os.makedirs(private_tmp)
    shutil.copytree(candidate, source, dirs_exist_ok=True, symlinks=True)
    open(os.path.join(workspace, ".altitude-sandbox-workspace"), "x").close()
    inside_env["HOME"] = private_home
    inside_env["TMPDIR"] = private_tmp
    inside_env["PATH"] = inside_env.get("PATH", "").replace("/workspace", workspace)
    # Production uses a fixed trusted shell launcher to copy /source into the
    # bounded tmpfs. The unit fake has already made that copy; execute only the
    # opaque argv following the launcher's $0 marker.
    marker = command.index("altitude-local-suite")
    command = command[marker + 1:]
    try:
        run = subprocess.run(command, cwd=source, env=inside_env)
    except FileNotFoundError as exc:
        print("bwrap: execvp: " + str(exc), file=sys.stderr)
        raise SystemExit(127)
finally:
    shutil.rmtree(workspace, ignore_errors=True)
raise SystemExit(run.returncode)
""")
        fake_bwrap.chmod(0o755)
        self._bwrap_patch = mock.patch.object(land, "BWRAP", fake_bwrap)
        self._bwrap_patch.start()
        self.addCleanup(self._bwrap_patch.stop)
        real_suite = land._local_suite
        self._suite_invocations = []

        def recorded_suite(cwd, test_cmd):
            self._suite_invocations.append({"argv": shlex.split(test_cmd), "cwd": str(cwd.resolve())})
            return real_suite(cwd, test_cmd)

        self._suite_patch = mock.patch.object(land, "_local_suite", side_effect=recorded_suite)
        self._suite_patch.start()
        self.addCleanup(self._suite_patch.stop)
        d = S.tasks_dir("demo") / "fix-x"
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps(
            {"slug": "fix-x", "state": "running", "paths": ["src", "docs/NOTES.md"]}))
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "README.md").write_text("readme\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "-u", "origin", "main")
        self.git("checkout", "-q", "-b", "worktree-fix-x")

    def _setenv(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.update({key: old}) if old is not None else os.environ.pop(key, None))

    def git(self, *args):
        p = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, f"git {' '.join(args)}: {p.stderr or p.stdout}")
        return p.stdout

    def gh_log(self):
        log = self.ghdir / "log.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def leased_change(self, name="src/thing.py"):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("changed\n")

    def fake_runner(self, name, exit_code=0, output="", script=None):
        # Candidate-local executables are deliberately allowed; they are the untrusted code Bubblewrap confines.
        p = self.repo / (".altitude-test-runner" if name == "make" else name)
        p.write_text("#!/usr/bin/env python3\n"
                     "import json, os, sys\n"
                     + (script or f"sys.stdout.write({output!r})\nsys.exit({exit_code})\n"))
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        paths = [p.name]
        if name == "make":
            (self.repo / "Makefile").write_text(".PHONY: test\ntest:\n\t@./.altitude-test-runner\n")
            paths.append("Makefile")
        self.git("add", *paths)
        self.git("commit", "-q", "-m", f"test runner {name}", "-m", "Altitude-Task: demo/fix-x")

    def runner_calls(self):
        return list(self._suite_invocations)

    def runner_log(self):
        return [call["argv"] for call in self.runner_calls()]

    def no_checks(self, body="[]"):
        (self.ghdir / "checks.json").write_text(body)

    def configure_ci(self):
        workflow = self.repo / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "ci.yml").write_text("on: [push]\n")
        self.git("add", ".github/workflows/ci.yml")
        self.git("commit", "-q", "-m", "ci", "-m", "Altitude-Task: demo/fix-x")

    def advance_base(self, name, content="base\n"):
        other = self.tmp / ("base-" + name.replace("/", "-"))
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True, capture_output=True)

        def og(*args):
            result = subprocess.run(["git", "-C", str(other), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"base git {' '.join(args)}: {result.stderr or result.stdout}")

        og("config", "user.email", "b@b")
        og("config", "user.name", "b")
        og("config", "commit.gpgsign", "false")
        og("checkout", "-q", "main")
        changed = other / name
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_text(content)
        og("add", name)
        og("commit", "-q", "-m", "the base moves on")
        og("push", "-q", "origin", "main")

    def test_refuses_on_main(self):
        self.git("checkout", "-q", "main")
        with self.assertRaisesRegex(land.LandError, "main"):
            land.land("msg", cwd=self.repo)

    def test_rebase_in_progress_refuses(self):
        self.leased_change()
        (self.repo / ".git" / "rebase-merge").mkdir()
        with self.assertRaisesRegex(land.LandError, "rebase is in progress"):
            land.land("msg", cwd=self.repo)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_refuses_outside_lease_and_stages_nothing(self):
        self.leased_change()
        (self.repo / "rogue.txt").write_text("outside\n")
        with self.assertRaisesRegex(land.LandError, "rogue.txt"):
            land.land("msg", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_plain_land_refuses_unprovenanced_history_before_mutation(self):
        (self.repo / "rogue-history.txt").write_text("direct commit\n")
        self.git("add", "rogue-history.txt")
        self.git("commit", "-q", "-m", "missing task trailer")
        self.leased_change()

        with self.assertRaisesRegex(land.LandError, "without exact.*provenance"):
            land.land("must refuse", cwd=self.repo, wait=0)

        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.gh_log(), [])
        self.assertNotIn("worktree-fix-x", self.remote_heads())

    def test_happy_path(self):
        self.leased_change("src/has space.py")
        self.leased_change("src/a[1].py")  # a bracket-expression name must stage as a literal, not a glob
        self.leased_change("docs/NOTES.md")
        self.assertIsNone(land._pr_view(self.repo, "worktree-fix-x"))
        res = land.land("fix: land the thing\n\nlonger body", cwd=self.repo, wait=0)
        self.assertEqual(res["pr"], 101)
        self.assertEqual(res["url"], "https://example.invalid/pr/101")
        self.assertEqual(res["checks"], "pass")
        self.assertFalse(res["merged"])
        self.assertIsNone(res["hold"])
        self.assertEqual(res["branch"], "worktree-fix-x")
        self.assertEqual(res["lease"], ["src", "docs/NOTES.md"])
        self.assertEqual(res["staged"], ["docs/NOTES.md", "src/a[1].py", "src/has space.py"])
        self.assertEqual(res["replaced"], [])
        self.assertIn("src/a[1].py", self.git("show", "--name-only", "--format=", "HEAD"))
        self.assertEqual(
            self.git("log", "-1", "--format=%B").strip(),
            "fix: land the thing\n\nlonger body\n\n"
            "Altitude-Task: demo/fix-x\nCo-Authored-By: Claude <noreply@anthropic.com>")
        remote_sha = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "worktree-fix-x"],
                                    capture_output=True, text=True).stdout.strip()
        self.assertEqual(remote_sha, self.git("rev-parse", "HEAD").strip())
        self.assertEqual(res["head"], remote_sha)
        creates = [a for a in self.gh_log() if a[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][creates[0].index("--title") + 1], "fix: land the thing")

    def test_idempotent_rerun(self):
        self.leased_change()
        land.land("fix: once", cwd=self.repo, wait=0)
        head = self.git("rev-parse", "HEAD").strip()
        res = land.land("fix: once", cwd=self.repo, wait=0)
        self.assertIsNone(res["commit"])
        self.assertEqual(res["staged"], [])
        self.assertEqual(res["pr"], 101)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "create"]]), 1)

    def test_rebased_push_retries_with_recorded_tip_lease_exactly_once(self):
        self.leased_change("src/original.py")
        self.git("add", "src/original.py")
        self.git("commit", "-q", "-m", "original", "-m", "Altitude-Task: demo/fix-x")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        recorded_tip = self.git("rev-parse", "origin/worktree-fix-x").strip()
        self.git("commit", "--amend", "-q", "-m", "rebased", "-m", "Altitude-Task: demo/fix-x")
        self.leased_change()
        commands = []
        real = land._run

        def fake(args, cwd, timeout=120):
            commands.append(args)
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        res = land.land("fix: retry", cwd=self.repo, wait=0)
        pushes = [a for a in commands if a[:2] == ["git", "push"]]
        pulls = [a for a in commands if a[:2] == ["git", "pull"]]
        self.assertEqual(len(pushes), 2)
        self.assertEqual(pulls, [])
        self.assertNotIn("--force-with-lease", " ".join(pushes[0]))
        self.assertIn(f"--force-with-lease=worktree-fix-x:{recorded_tip}", pushes[1])
        self.assertEqual(res["head"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(res["pr"], 101)
        self.assertEqual(len(res["replaced"]), 1)
        self.assertIn(recorded_tip[:7], res["replaced"][0])

    def test_refused_lease_reports_recorded_and_current_tips(self):
        self.leased_change()
        self.git("add", "src/thing.py")
        self.git("commit", "-q", "-m", "original", "-m", "Altitude-Task: demo/fix-x")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        recorded_tip = self.git("rev-parse", "origin/worktree-fix-x").strip()
        self.git("commit", "--amend", "-q", "-m", "rebased", "-m", "Altitude-Task: demo/fix-x")
        current_tip = "b" * 40
        commands = []
        tip_reads = 0
        real = land._run

        def fake(args, cwd, timeout=120):
            nonlocal tip_reads
            commands.append(args)
            if args == ["git", "rev-parse", "--verify", "-q", "refs/remotes/origin/worktree-fix-x"]:
                tip_reads += 1
                tip = recorded_tip if tip_reads == 1 else current_tip
                return subprocess.CompletedProcess(args, 0, tip + "\n", "")
            if args[:2] == ["git", "push"]:
                return subprocess.CompletedProcess(args, 1, "", "! [rejected] (non-fast-forward)")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: retry", cwd=self.repo, wait=0)
        message = str(cm.exception)
        self.assertIn(recorded_tip, message)
        self.assertIn(current_tip, message)
        self.assertIn("look at the foreign commits", message)
        self.assertIn("rebase by hand", message)
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), 2)
        self.assertEqual([a for a in commands if a[:2] == ["git", "pull"]], [])

    def test_up_to_date_push_does_not_force_or_refetch_after_push(self):
        self.leased_change()
        land.land("fix: first", cwd=self.repo, wait=0)
        commands = []
        real = land._run

        def fake(args, cwd, timeout=120):
            commands.append(args)
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        res = land.land("fix: first", cwd=self.repo, wait=0)
        pushes = [a for a in commands if a[:2] == ["git", "push"]]
        branch_fetches = [a for a in commands if a[:4] == ["git", "fetch", "-q", "origin"]
                            and a[-1].endswith(":refs/remotes/origin/worktree-fix-x")]
        self.assertEqual(pushes, [["git", "push", "-u", "origin", "worktree-fix-x"]])
        self.assertEqual(branch_fetches, [["git", "fetch", "-q", "origin",
                                           "+refs/heads/worktree-fix-x:refs/remotes/origin/worktree-fix-x"]])
        self.assertEqual(res["head"], self.git("rev-parse", "origin/worktree-fix-x").strip())

    def test_first_push_without_remote_tip_uses_plain_push_with_localized_fetch_error(self):
        self.leased_change()
        commands = []
        real = land._run

        def fake(args, cwd, timeout=120):
            commands.append(args)
            if args == ["git", "fetch", "-q", "origin",
                        "+refs/heads/worktree-fix-x:refs/remotes/origin/worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: référence distante introuvable")
            if args == ["git", "ls-remote", "--exit-code", "--heads", "origin", "worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 2, "", "")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        res = land.land("fix: first push", cwd=self.repo, wait=0)
        self.assertEqual([a for a in commands if a[:2] == ["git", "push"]],
                         [["git", "push", "-u", "origin", "worktree-fix-x"]])
        self.assertIn(["git", "ls-remote", "--exit-code", "--heads", "origin", "worktree-fix-x"], commands)
        self.assertEqual(res["head"], self.git("rev-parse", "origin/worktree-fix-x").strip())
        self.assertEqual(res["replaced"], [])

    def test_inconsistent_remote_tracking_head_fails_the_pr_pin(self):
        self.leased_change()
        pushed_head = "a" * 40
        real = land._run

        def fake(args, cwd, timeout=120):
            if args == ["git", "rev-parse", "origin/worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 0, pushed_head + "\n", "")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaisesRegex(land.LandError, "head moved"):
            land.land("fix: report remote", cwd=self.repo, wait=0)

    def test_wait_zero_reports_pending_without_waiting(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pending"}]')
        res = land.land("fix: pending", cwd=self.repo, wait=0)
        self.assertEqual(res["checks"], "pending")
        self.assertFalse(res["merged"])
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "checks"]]), 1)


    def test_merge_hold_refuses_with_explicit_paths(self):
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = "production migration is costly"
        (d / "status.json").write_text(json.dumps(task))
        self.leased_change()
        with self.assertRaises(land.LandError):
            land.land("fix: held", cwd=self.repo, wait=0, merge=True, paths="src")
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])


    def test_merge_hold_refuses_dry_run(self):
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = "production migration is costly"
        (d / "status.json").write_text(json.dumps(task))
        self.leased_change()
        with self.assertRaises(land.LandError):
            land.land("fix: held", cwd=self.repo, merge=True, dry_run=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])



    def test_merge_hold_without_merge_opens_pr_and_emits_notice(self):
        reason = "production migration is costly"
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = reason
        (d / "status.json").write_text(json.dumps(task))
        self.leased_change()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            res = land.land("fix: held", cwd=self.repo, wait=0)
        notice = stderr.getvalue()
        self.assertEqual(res["pr"], 101)
        self.assertFalse(res["merged"])
        self.assertEqual(res["hold"], reason)
        self.assertIn(reason, notice)
        self.assertIn("PR will be opened but not merged", notice)
        self.assertEqual(notice.count(reason), 1)
        self.assertIn(["pr", "create", "--base", "main", "--head", "worktree-fix-x", "--title",
                       "fix: held", "--body-file"], [args[:-1] for args in self.gh_log()])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])


    def remote_heads(self):
        p = subprocess.run(["git", "-C", str(self.remote), "branch", "--format=%(refname:short)"],
                           capture_output=True, text=True)
        return sorted(p.stdout.split())

    def test_detached_head_refuses(self):
        self.git("checkout", "-q", "--detach")
        self.leased_change()
        with self.assertRaisesRegex(land.LandError, "detached"):
            land.land("msg", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_refuses_when_branch_equals_base(self):
        self.git("checkout", "-q", "-b", "develop")
        self.leased_change()
        with self.assertRaisesRegex(land.LandError, "develop"):
            land.land("msg", cwd=self.repo, wait=0, base="develop")
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.remote_heads(), ["main"])

    def test_preexisting_remote_divergence_is_replaced_without_rebasing(self):
        # a real diverging remote: same branch, same file, different content in a second clone
        seed = self.repo / "src" / "f.py"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("base\n")
        self.git("add", "src/f.py")
        self.git("commit", "-q", "-m", "seed", "-m", "Altitude-Task: demo/fix-x")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True, capture_output=True)

        def og(*args):
            p = subprocess.run(["git", "-C", str(other), *args], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"other git {' '.join(args)}: {p.stderr or p.stdout}")

        og("config", "user.email", "o@o")
        og("config", "user.name", "o")
        og("config", "commit.gpgsign", "false")
        og("checkout", "-q", "worktree-fix-x")
        (other / "src" / "f.py").write_text("remote\n")
        og("add", "src/f.py")
        og("commit", "-q", "-m", "remote change", "-m", "Altitude-Task: demo/fix-x")
        og("push", "-q")
        seed.write_text("local\n")
        res = land.land("fix: conflict", cwd=self.repo, wait=0)
        self.assertEqual(res["head"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(self.git("rev-parse", "origin/worktree-fix-x").strip(), res["head"])
        # No pull/rebase state was created, so an idempotent follow-up remains safe.
        again = land.land("fix: conflict again", cwd=self.repo, wait=0)
        self.assertEqual(again["head"], res["head"])
        self.assertEqual(self.git("log", "--all", "-S", "<<<<<<<", "--oneline").strip(), "")

    def test_strictly_behind_branch_is_not_force_rewound(self):
        self.leased_change("src/f.py")
        self.git("add", "src/f.py")
        self.git("commit", "-q", "-m", "seed", "-m", "Altitude-Task: demo/fix-x")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        local_tip = self.git("rev-parse", "HEAD").strip()
        other = self.tmp / "other-behind"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True, capture_output=True)

        def og(*args):
            p = subprocess.run(["git", "-C", str(other), *args], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"other git {' '.join(args)}: {p.stderr or p.stdout}")

        og("config", "user.email", "o@o")
        og("config", "user.name", "o")
        og("config", "commit.gpgsign", "false")
        og("checkout", "-q", "worktree-fix-x")
        (other / "src" / "remote.py").write_text("foreign\n")
        og("add", "src/remote.py")
        og("commit", "-q", "-m", "foreign", "-m", "Altitude-Task: demo/fix-x")
        og("push", "-q")
        foreign_tip = subprocess.run(
            ["git", "-C", str(self.remote), "rev-parse", "worktree-fix-x"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        commands = []
        real = land._run

        def record(args, cwd, timeout=120):
            commands.append(args)
            return real(args, cwd, timeout=timeout)

        land._run = record
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: must not rewind", cwd=self.repo, wait=0)
        message = str(cm.exception)
        self.assertIn(f"recorded remote tip: {foreign_tip}", message)
        self.assertIn(f"current remote tip: {foreign_tip}", message)
        self.assertIn("rebase by hand", message)
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), 1)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), local_tip)
        self.assertEqual(subprocess.run(
            ["git", "-C", str(self.remote), "rev-parse", "worktree-fix-x"],
            check=True, capture_output=True, text=True,
        ).stdout.strip(), foreign_tip)

    def test_resolved_task_with_empty_lease_fails_loudly(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": []}))
        self.leased_change()
        (self.repo / "secrets.env").write_text("x\n")
        cli = Path(__file__).resolve().parent.parent / "bin" / "alt"
        p = subprocess.run([sys.executable, str(cli), "land", "--message", "msg", "--wait", "0"],
                           cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(f"task demo/fix-x {land.EMPTY_LEASE_MESSAGE}", p.stderr)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_explicit_paths_override_empty_task_lease(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": []}))
        self.leased_change()
        res = land.land("fix: explicit lease", cwd=self.repo, wait=0, paths="src")
        self.assertEqual(res["lease"], ["src"])
        self.assertEqual(res["staged"], ["src/thing.py"])

    def test_absolute_lease_entry_still_matches(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": ["/src"]}))
        self.leased_change("src/thing.py")
        res = land.land("fix: abs", cwd=self.repo, wait=0)
        self.assertEqual(res["staged"], ["src/thing.py"])

    def test_rename_crossing_the_lease_boundary_refuses(self):
        self.leased_change("src/keep.py")
        self.git("add", "src/keep.py")
        self.git("commit", "-q", "-m", "seed", "-m", "Altitude-Task: demo/fix-x")
        self.git("mv", "src/keep.py", "escaped.py")
        with self.assertRaisesRegex(land.LandError, "escaped.py"):
            land.land("msg", cwd=self.repo, wait=0)


    # R-006 / I-020: where no workflow and no check exist, the exact merge candidate's local suite is the
    # gate. Absence, skipped checks, stale revisions, and unreadable test reports never become green.




    def test_daemon_candidate_gate_returns_pair_bound_sandbox_evidence_and_fails_closed(self):
        self.fake_runner("make", 0, "Ran 7 tests in 0.4s\n\nOK (skipped=2, expected failures=1)\n")
        base = self.git("rev-parse", "origin/main").strip()
        head = self.git("rev-parse", "HEAD").strip()
        authority = {"worktree": str(self.repo.resolve()), "head_sha": head}

        result = land.daemon_candidate_gate("demo", "fix-x", 101, base, head, authority)

        self.assertEqual(result, {
            "sandboxed": True, "passed": True, "base_sha": base, "head_sha": head,
            "tests": 4, "skipped": 2, "expected_failures": 1,
        })
        self.assertNotEqual(Path(self.runner_calls()[-1]["cwd"]), self.repo.resolve())
        self.assertFalse(Path(self.runner_calls()[-1]["cwd"]).exists())

        with mock.patch.object(land, "_candidate") as candidate:
            refused = land.daemon_candidate_gate(
                "demo", "fix-x", 101, base, head,
                {"worktree": str(self.repo.resolve()), "head_sha": "f" * 40},
            )
        candidate.assert_not_called()
        self.assertTrue(refused["sandboxed"])
        self.assertFalse(refused["passed"])
        self.assertIsNone(refused["tests"])
        self.assertIn("does not match", refused["error"])











    def test_candidate_tree_limits_fail_before_checkout_materialization(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.leased_change("src/one")
        self.leased_change("src/two")
        self.git("add", "src")
        self.git("commit", "-q", "-m", "large candidate", "-m", "Altitude-Task: demo/fix-x")
        head = self.git("rev-parse", "HEAD").strip()
        with mock.patch.object(land, "SANDBOX_MAX_SOURCE_FILES", 2), \
             mock.patch.object(land, "_materialize_tree") as materialize, \
             self.assertRaisesRegex(land.LandError, "filesystem entries"):
            with land._candidate(self.repo, base, head):
                self.fail("an over-limit tree must not be materialized")
        materialize.assert_not_called()

    def test_candidate_blob_limit_fails_before_checkout_materialization(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.leased_change("src/payload")
        self.git("add", "src/payload")
        self.git("commit", "-q", "-m", "large blobs", "-m", "Altitude-Task: demo/fix-x")
        head = self.git("rev-parse", "HEAD").strip()
        with mock.patch.object(land, "SANDBOX_MAX_SOURCE_BYTES", 8), \
             mock.patch.object(land, "_materialize_tree") as materialize, \
             self.assertRaisesRegex(land.LandError, "blob bytes"):
            with land._candidate(self.repo, base, head):
                self.fail("an over-limit tree must not be materialized")
        materialize.assert_not_called()

    def test_candidate_checkout_ignores_host_configured_smudge_filter(self):
        sentinel = self.tmp / "host-filter-ran"
        global_config = self.tmp / "hostile-gitconfig"
        global_config.write_text(
            '[filter "hostile"]\n'
            f'\tsmudge = sh -c "echo ran > {sentinel}; cat"\n'
        )
        base = self.git("rev-parse", "HEAD").strip()
        (self.repo / ".gitattributes").write_text("victim filter=hostile\n")
        (self.repo / "victim").write_text("raw\n")
        self.git("add", ".gitattributes", "victim")
        self.git("commit", "-q", "-m", "hostile attributes", "-m", "Altitude-Task: demo/fix-x")
        head = self.git("rev-parse", "HEAD").strip()
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            with land._candidate(self.repo, base, head) as candidate:
                self.assertEqual((candidate / "victim").read_text(), "raw\n")
        self.assertFalse(sentinel.exists())

    def test_publication_ignores_host_clean_filter_and_runs_only_trusted_hooks(self):
        filter_sentinel = self.tmp / "host-clean-filter-ran"
        hook_sentinel = self.tmp / "trusted-pre-commit-ran"
        global_config = self.tmp / "hostile-publication-gitconfig"
        global_config.write_text(
            '[filter "hostile"]\n'
            f'\tclean = sh -c "echo ran > {filter_sentinel}; cat"\n'
            '\trequired = true\n'
        )
        hooks = self.tmp / "trusted-hooks"
        hooks.mkdir()
        for name in land.git_policy.REQUIRED_HOOKS:
            hook = hooks / name
            marker = (f"printf ran > {shlex.quote(str(hook_sentinel))}\n"
                      if name == "pre-commit" else "")
            hook.write_text("#!/bin/sh\n" + marker + "exit 0\n")
            hook.chmod(0o755)
        (self.repo / ".gitattributes").write_text("src/filtered.txt filter=hostile\n")
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "filtered.txt").write_text("raw candidate data\n")

        with mock.patch.object(config, "HOOKS", hooks), \
             mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            result = land.land("fix: sanitized publication", cwd=self.repo, wait=0,
                               paths=".gitattributes,src")

        self.assertEqual(result["commit"], self.git("rev-parse", "HEAD").strip())
        self.assertFalse(filter_sentinel.exists(), "host global clean/process filters must never execute")
        self.assertTrue(hook_sentinel.exists(), "the explicitly pinned trusted pre-commit hook must run")
        self.assertEqual(self.git("show", "HEAD:src/filtered.txt"), "raw candidate data\n")

    def test_candidate_merge_ignores_repository_custom_merge_driver(self):
        sentinel = self.tmp / "host-merge-driver-ran"
        start = self.git("rev-parse", "HEAD").strip()
        (self.repo / ".gitattributes").write_text("collision.txt merge=hostile\n")
        (self.repo / "collision.txt").write_text("base\n")
        self.git("add", ".gitattributes", "collision.txt")
        self.git("commit", "-q", "-m", "base collision")
        base = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "-b", "hostile-head", start)
        (self.repo / ".gitattributes").write_text("collision.txt merge=hostile\n")
        (self.repo / "collision.txt").write_text("head\n")
        self.git("add", ".gitattributes", "collision.txt")
        self.git("commit", "-q", "-m", "head collision")
        head = self.git("rev-parse", "HEAD").strip()
        self.git("config", "merge.hostile.driver", f"sh -c 'echo ran > {sentinel}; exit 0'")
        with self.assertRaisesRegex(land.LandError, "does not merge cleanly"):
            with land._candidate(self.repo, base, head):
                self.fail("the built-in conflict must not be hidden by the host driver")
        self.assertFalse(sentinel.exists())

    def test_raw_tree_dotgit_symlink_is_rejected_before_any_host_write(self):
        sentinel = self.tmp / "dotgit-symlink-target"
        sentinel.write_text("original\n")
        base = self.git("rev-parse", "HEAD").strip()
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.repo,
            input=str(sentinel), capture_output=True, text=True, check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "mktree"], cwd=self.repo,
            input=f"120000 blob {blob}\t.git\n", capture_output=True, text=True, check=True,
        ).stdout.strip()
        head = self.git("commit-tree", tree, "-p", base, "-m", "raw hostile tree").strip()
        with self.assertRaisesRegex(land.LandError, "unsafe Git metadata path"):
            with land._candidate(self.repo, base, head):
                self.fail("a dotgit tree entry must never be materialized")
        self.assertEqual(sentinel.read_text(), "original\n")








    def test_local_suite_sandbox_policy_exposes_only_candidate_and_system_toolchain(self):
        # The production value is intentionally sampled from the live same-UID
        # process table. Pin that sample here: parallel test/review processes
        # may otherwise enter or exit between building the argv and asserting
        # its value, making this policy-shape test race the host.
        uid_tasks = 137
        with mock.patch.object(land, "_uid_task_count", return_value=uid_tasks):
            command = land._sandbox_argv(self.repo, ["python3", "probe.py"])
        self.assertEqual(command[0], str(land.PRLIMIT))
        for limit in (
            f"--as={land.SANDBOX_RLIMIT_AS}:{land.SANDBOX_RLIMIT_AS}",
            f"--cpu={land.SANDBOX_RLIMIT_CPU}:{land.SANDBOX_RLIMIT_CPU}",
            f"--fsize={land.SANDBOX_RLIMIT_FSIZE}:{land.SANDBOX_RLIMIT_FSIZE}",
            f"--nofile={land.SANDBOX_RLIMIT_NOFILE}:{land.SANDBOX_RLIMIT_NOFILE}",
            "--core=0:0",
        ):
            self.assertIn(limit, command)
        nproc = next(value for value in command if value.startswith("--nproc="))
        soft, hard = map(int, nproc.removeprefix("--nproc=").split(":"))
        self.assertEqual(soft, hard)
        self.assertEqual(soft, uid_tasks + land.SANDBOX_RLIMIT_NPROC_BURST)
        self.assertGreater(command.index(str(land.BWRAP)), command.index("--"))
        for required in ("--die-with-parent", "--unshare-all", "--unshare-user", "--unshare-net",
                         "--disable-userns", "--clearenv"):
            self.assertIn(required, command)
        self.assertNotIn("--new-session", command)
        bind_pairs = [(command[index + 1], command[index + 2])
                      for index, value in enumerate(command[:-2]) if value == "--bind"]
        self.assertEqual(bind_pairs, [])
        ro_bind_pairs = [(command[index + 1], command[index + 2])
                         for index, value in enumerate(command[:-2]) if value == "--ro-bind"]
        self.assertEqual(ro_bind_pairs, [
            ("/usr", "/usr"), (str(self.repo.resolve()), land.SANDBOX_SOURCE),
            (str((self.repo / ".git").resolve()), f"{land.SANDBOX_RUN_ROOT}/.git"),
        ])
        size_at = command.index("--size")
        self.assertEqual(command[size_at + 1:size_at + 4],
                         [str(land.SANDBOX_WORKSPACE_BYTES), "--tmpfs", land.SANDBOX_WORKSPACE])
        chdir_at = command.index("--chdir")
        for directory in (land.SANDBOX_RUN_ROOT, "/workspace/tmp", "/workspace/home"):
            directory_at = next(i for i in range(len(command) - 1)
                                if command[i:i + 2] == ["--dir", directory])
            self.assertLess(directory_at, chdir_at)
        self.assertNotIn(["--tmpfs", "/tmp"], [command[i:i + 2] for i in range(len(command) - 1)])
        self.assertIn(["--remount-ro", "/"], [command[i:i + 2] for i in range(len(command) - 1)])
        self.assertIn(["--remount-ro", "/dev"], [command[i:i + 2] for i in range(len(command) - 1)])
        variables = {command[index + 1]: command[index + 2]
                     for index, value in enumerate(command[:-2]) if value == "--setenv"}
        self.assertEqual(set(variables), {"HOME", "TMPDIR", "PATH", "LANG", "LC_ALL"})
        self.assertEqual(variables["HOME"], land.SANDBOX_HOME)
        self.assertNotIn(str(config.ROOT), " ".join(command))
        for secret in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "ALTITUDE_HOME", "CODEX_HOME",
                       "CLAUDE_CONFIG_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME"):
            self.assertNotIn(secret, variables)


    def test_unrelated_same_uid_load_does_not_consume_the_candidate_process_allowance(self):
        """RLIMIT_NPROC is per UID: the absolute limit must include pre-existing workers."""
        probe = self.repo / "green-probe"
        probe.write_text("#!/bin/sh\nprintf 'Ran 1 test in 0.1s\\nOK\\n'\n")
        probe.chmod(0o755)
        current = land._uid_task_count()
        unrelated = 500
        with mock.patch.object(land, "_uid_task_count", return_value=current + unrelated):
            command = land._sandbox_argv(self.repo, ["./green-probe"])
            result = land._run_local_sandbox(self.repo, ["./green-probe"])
        nproc = next(value for value in command if value.startswith("--nproc="))
        expected = current + unrelated + land.SANDBOX_RLIMIT_NPROC_BURST
        self.assertEqual(nproc, f"--nproc={expected}:{expected}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stdout)

    def test_local_suite_mutates_a_copy_not_the_host_candidate(self):
        probe = self.repo / "copy-probe"
        probe.write_text("#!/bin/sh\nprintf changed > sandbox-created\nprintf 'Ran 1 test\\nOK\\n'\n")
        probe.chmod(0o755)
        result = land._run_local_sandbox(self.repo, ["./copy-probe"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.repo / "sandbox-created").exists())

    def test_local_suite_empty_file_flood_is_bounded_and_reaped(self):
        probe = self.repo / "inode-flood.py"
        probe.write_text(
            "import pathlib, time\n"
            "root = pathlib.Path('empty-files')\n"
            "root.mkdir()\n"
            "for number in range(10000):\n"
            "    (root / str(number)).touch()\n"
            "time.sleep(60)\n"
        )
        with mock.patch.object(land, "SANDBOX_FILE_BURST", 100), self.assertRaisesRegex(
            land.LandError, "file count exceeded"
        ):
            land._run_local_sandbox(self.repo, ["/usr/bin/python3", "inode-flood.py"])
        self.assertFalse((self.repo / "empty-files").exists())

    def test_local_suite_timeout_reaps_sigterm_ignoring_descendant(self):
        probe = self.repo / "hang-probe"
        pid_file = self.repo / "descendant.pid"
        probe.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        probe.chmod(0o755)
        with mock.patch.object(land, "LOCAL_TEST_TIMEOUT", 0.2), self.assertRaisesRegex(
            land.LandError, "timed out"
        ):
            land._run_local_sandbox(self.repo, ["./hang-probe"])
        child = int(pid_file.read_text())
        # The unit launcher has no real PID namespace, so init may briefly own
        # the dead child's zombie after its parent is killed.  Production
        # Bubblewrap owns and reaps that namespace; in either case no live
        # descendant may survive.
        self._assert_processes_gone([child])

    def test_local_suite_output_flood_is_bounded_and_reaped(self):
        probe = self.repo / "output-flood"
        pid_file = self.repo / "output-flood.pid"
        probe.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
            "block = b'x' * 65536\n"
            "while True:\n"
            "    os.write(1, block)\n"
        )
        probe.chmod(0o755)
        with mock.patch.object(land, "SANDBOX_OUTPUT_LIMIT", 128 * 1024), self.assertRaisesRegex(
            land.LandError, "output exceeded 131072 bytes"
        ):
            land._run_local_sandbox(self.repo, ["./output-flood"])
        self._assert_processes_gone([int(pid_file.read_text())])

    def test_local_suite_process_flood_is_bounded_and_reaped(self):
        probe = self.repo / "process-flood"
        pid_file = self.repo / "process-flood.pids"
        probe.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, signal, subprocess, sys, time\n"
            f"target = pathlib.Path({str(pid_file)!r})\n"
            "children = []\n"
            "target.write_text(str(__import__('os').getpid()))\n"
            "for _ in range(20):\n"
            "    child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
            "    children.append(child.pid)\n"
            "    with target.open('a') as record:\n"
            "        record.write(' ' + str(child.pid))\n"
            "        record.flush()\n"
            "        __import__('os').fsync(record.fileno())\n"
            "time.sleep(60)\n"
        )
        probe.chmod(0o755)
        with mock.patch.object(land, "SANDBOX_MAX_PROCESSES", 5), self.assertRaisesRegex(
            land.LandError, "process count exceeded 5"
        ):
            land._run_local_sandbox(self.repo, ["./process-flood"])
        pids = [int(value) for value in pid_file.read_text().split()]
        self.assertTrue(pids)
        self._assert_processes_gone(pids)

    def test_local_suite_setsid_process_flood_is_bounded_and_reaped(self):
        probe = self.repo / "setsid-flood"
        pid_file = self.repo / "setsid-flood.pids"
        probe.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, signal, subprocess, sys, time\n"
            f"target = pathlib.Path({str(pid_file)!r})\n"
            "target.write_text(str(os.getpid()))\n"
            "for _ in range(20):\n"
            "    child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], "
            "start_new_session=True)\n"
            "    with target.open('a') as record:\n"
            "        record.write(' ' + str(child.pid))\n"
            "        record.flush()\n"
            "        os.fsync(record.fileno())\n"
            "time.sleep(60)\n"
        )
        probe.chmod(0o755)
        with mock.patch.object(land, "SANDBOX_MAX_PROCESSES", 5), self.assertRaisesRegex(
            land.LandError, "process count exceeded 5"
        ):
            land._run_local_sandbox(self.repo, ["./setsid-flood"])
        pids = [int(value) for value in pid_file.read_text().split()]
        self.assertTrue(pids)
        self._assert_processes_gone(pids)

    def test_local_suite_memory_and_cpu_floods_fail_closed(self):
        cases = (
            ("memory", "blob = bytearray(96 * 1024 * 1024)\ntime.sleep(60)\n",
             "SANDBOX_MAX_RSS_BYTES", 48 * 1024 * 1024, "resident memory exceeded"),
            ("cpu", "while True:\n    pass\n",
             "SANDBOX_MAX_CPU_SECONDS", 0.05, "aggregate CPU exceeded"),
        )
        for name, body, setting, limit, message in cases:
            with self.subTest(resource=name):
                probe = self.repo / f"{name}-flood"
                pid_file = self.repo / f"{name}-flood.pid"
                probe.write_text(
                    "#!/usr/bin/env python3\n"
                    "import os, pathlib, time\n"
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                    + body
                )
                probe.chmod(0o755)
                with mock.patch.object(land, setting, limit), self.assertRaisesRegex(land.LandError, message):
                    land._run_local_sandbox(self.repo, [f"./{name}-flood"])
                self._assert_processes_gone([int(pid_file.read_text())])

    def _assert_processes_gone(self, pids):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            live = []
            for pid in pids:
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                except (OSError, IndexError):
                    continue
                if fields[0] != "Z":
                    live.append(pid)
            if not live:
                return
            time.sleep(0.05)
        self.fail(f"sandbox process(es) survived cleanup: {live}")

    def _skip_only_kernel_bwrap_denial(self, exc):
        """Skip only a host policy/user-namespace refusal, never an argv defect."""
        message = str(exc)
        denial = (
            "No permissions to create new namespace",
            "kernel does not allow non-privileged user namespaces",
            "unprivileged user namespace",
            "user namespaces are not supported",
        )
        if "bwrap:" in message and any(marker in message for marker in denial):
            self.skipTest(f"kernel/AppArmor denied Bubblewrap: {exc}")

    def test_real_bwrap_hides_credentials_home_outside_paths_and_network(self):
        """Exercise the kernel boundary when this host permits unprivileged Bubblewrap; denial is tested above."""
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "adversarial-candidate"
        candidate.mkdir()
        outside = self.tmp / "host-secret"
        outside.write_text("do-not-read")
        outside_write = self.tmp / "must-not-write"
        probe = candidate / "probe.py"
        probe.write_text(
            "import os, pathlib, socket, sys\n"
            f"outside = pathlib.Path({str(outside)!r})\n"
            f"outside_write = pathlib.Path({str(outside_write)!r})\n"
            "checks = [not any(k in os.environ for k in "
            "('GH_TOKEN','GITHUB_TOKEN','SSH_AUTH_SOCK','ALTITUDE_HOME','CODEX_HOME','CLAUDE_CONFIG_DIR')),\n"
            f"          not outside.exists(), os.environ.get('HOME') == {land.SANDBOX_HOME!r}]\n"
            "try:\n"
            "    outside_write.write_text('escape')\n"
            "    checks.append(False)\n"
            "except OSError:\n"
            "    checks.append(True)\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), 0.2)\n"
            "    checks.append(False)\n"
            "except OSError:\n"
            "    checks.append(True)\n"
            "print('Ran 1 test in 0.1s')\n"
            "print('OK' if all(checks) else 'FAILED')\n"
            "sys.exit(0 if all(checks) else 1)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap):
            try:
                run = land._run_local_sandbox(candidate, ["/usr/bin/python3", "probe.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                raise
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        self.assertFalse(outside_write.exists())

    def test_real_bwrap_candidate_git_history_is_self_contained_and_read_only(self):
        """The exact synthetic commit remains a usable repository after host /tmp disappears."""
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        base = self.git("rev-parse", "origin/main").strip()
        probe = self.repo / "src" / "history_probe.py"
        probe.parent.mkdir(exist_ok=True)
        probe.write_text(
            "import pathlib, subprocess, sys\n"
            f"base = {base!r}\n"
            "row = subprocess.check_output(['git', 'rev-list', '--parents', '-n1', 'HEAD'], "
            "text=True).strip().split()\n"
            "checks = [len(row) == 2, len(row) == 2 and row[1] == base]\n"
            "checks.append(subprocess.run(['git', 'diff', '--exit-code', 'HEAD^', 'HEAD', "
            "'--', 'README.md']).returncode == 0)\n"
            "checks.append(subprocess.run(['git', 'fsck', '--full'], capture_output=True).returncode == 0)\n"
            "try:\n"
            "    pathlib.Path('.git/refs/heads/escape').write_text('escape\\n')\n"
            "    checks.append(False)\n"
            "except OSError:\n"
            "    checks.append(True)\n"
            "print('Ran 1 test in 0.1s')\n"
            "print('OK' if all(checks) else 'FAILED')\n"
            "sys.exit(0 if all(checks) else 1)\n"
        )
        self.git("add", "src/history_probe.py")
        self.git("commit", "-q", "-m", "history probe", "-m", "Altitude-Task: demo/fix-x")
        head = self.git("rev-parse", "HEAD").strip()

        with land._candidate(self.repo, base, head) as candidate, \
             mock.patch.object(land, "BWRAP", real_bwrap):
            direct = subprocess.run(
                ["git", "-C", str(candidate), "rev-list", "--parents", "-n1", "HEAD"],
                capture_output=True, text=True,
            )
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertEqual(direct.stdout.strip().split()[1:], [base])
            fsck = subprocess.run(["git", "-C", str(candidate), "fsck", "--full"],
                                  capture_output=True, text=True)
            self.assertEqual(fsck.returncode, 0, fsck.stderr or fsck.stdout)
            unchanged = subprocess.run(
                ["git", "-C", str(candidate), "diff", "--exit-code", "HEAD^", "HEAD", "--", "README.md"],
                capture_output=True, text=True,
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr or unchanged.stdout)
            try:
                run = land._run_local_sandbox(candidate, ["/usr/bin/python3", "src/history_probe.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                raise
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)

    def test_real_bwrap_many_file_flood_hits_the_aggregate_workspace_cap(self):
        """Many individually small files cannot exhaust the host filesystem."""
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "disk-flood-candidate"
        candidate.mkdir()
        probe = candidate / "disk-flood.py"
        probe.write_text(
            "import pathlib\n"
            "root = pathlib.Path('many')\n"
            "root.mkdir()\n"
            "block = b'x' * 65536\n"
            "for number in range(1024):\n"
            "    (root / str(number)).write_bytes(block)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap), \
             mock.patch.object(land, "SANDBOX_WORKSPACE_BYTES", 8 * 1024 * 1024):
            try:
                run = land._run_local_sandbox(candidate, ["/usr/bin/python3", "disk-flood.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                raise
        self.assertNotEqual(run.returncode, 0, "the many-file flood escaped the tmpfs size cap")
        self.assertIn("No space left on device", run.stderr)
        self.assertFalse((candidate / "many").exists(), "the host candidate must remain read-only")

    def test_real_bwrap_empty_file_flood_hits_the_file_count_cap(self):
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "inode-flood-candidate"
        candidate.mkdir()
        probe = candidate / "inode-flood.py"
        probe.write_text(
            "import pathlib, time\n"
            "root = pathlib.Path('empty-files')\n"
            "root.mkdir()\n"
            "for number in range(100000):\n"
            "    (root / str(number)).touch()\n"
            "time.sleep(60)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap), \
             mock.patch.object(land, "SANDBOX_FILE_BURST", 200):
            try:
                land._run_local_sandbox(candidate, ["/usr/bin/python3", "inode-flood.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                self.assertIn("file count exceeded", str(exc))
            else:
                self.fail("the empty-file flood escaped the workspace file-count cap")
        self.assertFalse((candidate / "empty-files").exists(), "the host candidate must remain read-only")

    def test_real_bwrap_tmp_home_and_root_share_one_bounded_write_surface(self):
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "surface-candidate"
        candidate.mkdir()
        probe = candidate / "surface.py"
        probe.write_text(
            "import os, pathlib, sys\n"
            "checks = []\n"
            "for target in ('/escape', '/dev/escape'):\n"
            "    try:\n"
            "        pathlib.Path(target).write_text('escape')\n"
            "        checks.append(False)\n"
            "    except OSError:\n"
            "        checks.append(True)\n"
            "home = pathlib.Path(os.environ['HOME']) / 'allowed'\n"
            "tmp = pathlib.Path('/tmp') / 'allowed'\n"
            "home.write_text('home')\n"
            "tmp.write_text('tmp')\n"
            "checks += [home.is_file(), tmp.is_file()]\n"
            "print('Ran 1 test in 0.1s')\n"
            "print('OK' if all(checks) else 'FAILED')\n"
            "sys.exit(0 if all(checks) else 1)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap):
            try:
                run = land._run_local_sandbox(candidate, ["/usr/bin/python3", "surface.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                raise
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        self.assertFalse(Path("/escape").exists())
        self.assertFalse(Path("/dev/escape").exists())

    def test_real_bwrap_tmp_byte_flood_hits_the_workspace_cap(self):
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "tmp-flood-candidate"
        candidate.mkdir()
        (candidate / "tmp-flood.py").write_text(
            "import pathlib\n"
            "block = b'x' * 65536\n"
            "for number in range(1024):\n"
            "    (pathlib.Path('/tmp') / str(number)).write_bytes(block)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap), \
             mock.patch.object(land, "SANDBOX_WORKSPACE_BYTES", 8 * 1024 * 1024):
            try:
                run = land._run_local_sandbox(candidate, ["/usr/bin/python3", "tmp-flood.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                raise
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("No space left on device", run.stderr)

    def test_real_bwrap_home_empty_file_flood_hits_the_file_count_cap(self):
        real_bwrap = Path("/usr/bin/bwrap")
        if not real_bwrap.exists():
            self.skipTest("/usr/bin/bwrap is unavailable")
        candidate = self.tmp / "home-flood-candidate"
        candidate.mkdir()
        (candidate / "home-flood.py").write_text(
            "import os, pathlib, time\n"
            "root = pathlib.Path(os.environ['HOME']) / 'many'\n"
            "root.mkdir()\n"
            "for number in range(100000):\n"
            "    (root / str(number)).touch()\n"
            "time.sleep(60)\n"
        )
        with mock.patch.object(land, "BWRAP", real_bwrap), \
             mock.patch.object(land, "SANDBOX_FILE_BURST", 200):
            try:
                land._run_local_sandbox(candidate, ["/usr/bin/python3", "home-flood.py"])
            except land.LandError as exc:
                self._skip_only_kernel_bwrap_denial(exc)
                self.assertIn("file count exceeded", str(exc))
            else:
                self.fail("the HOME inode flood escaped the workspace file-count cap")










    def test_closed_unmerged_pr_refuses_before_any_mutation(self):
        self.leased_change()
        (self.ghdir / "pr.json").write_text(json.dumps(
            {"number": 55, "url": "https://example.invalid/pr/55", "state": "CLOSED"}))
        head = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(land.LandError, "closed"):
            land.land("fix: onto a closed pr", cwd=self.repo, wait=0)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.remote_heads(), ["main"])  # nothing pushed onto the closed PR's branch
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])

    def test_create_path_reads_the_pr_back_once(self):
        pr = land._ensure_pr(self.repo, "worktree-fix-x", "main", "fix: new", None, None, "demo/fix-x", pr=None)
        self.assertEqual(pr["number"], 101)
        # pr=None is "the caller looked and there is no PR", so no view before the create
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "create"], ["pr", "view"]])

    def test_not_prefetched_looks_before_creating(self):
        land._ensure_pr(self.repo, "worktree-fix-x", "main", "fix: new", None, None, "demo/fix-x")
        self.assertEqual([a[:2] for a in self.gh_log()],
                         [["pr", "view"], ["pr", "create"], ["pr", "view"]])

    def test_full_run_rechecks_the_pr_around_check_classification(self):
        self.leased_change()
        land.land("fix: once", cwd=self.repo, wait=0)
        # Prefetch + create read-back + snapshot + the before/after check-state bracket.
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "view"]]), 5)

    def test_gh_404_stops_the_run_before_any_mutation(self):
        self.leased_change()
        head = self.git("rev-parse", "HEAD").strip()
        message = "gh: Not Found (HTTP 404)"
        (self.ghdir / "view_error.txt").write_text(message)
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: no gh", cwd=self.repo, wait=0)
        self.assertIn("gh pr view worktree-fix-x", str(cm.exception))
        self.assertIn(message, str(cm.exception))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertIn("?? src/thing.py", self.git("status", "--short", "--untracked-files=all").splitlines())
        self.assertEqual((self.repo / "src" / "thing.py").read_text(), "changed\n")
        self.assertEqual(self.remote_heads(), ["main"])
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])
        doc = land.__doc__
        self.assertIn("authenticated `gh`", doc)  # the precondition this behaviour is documented by
        self.assertIn("nothing committed", doc)

    def test_dry_run_reports_a_distinct_checks_value(self):
        self.leased_change()
        res = land.land("msg", cwd=self.repo, dry_run=True)
        self.assertEqual(res["checks"], "dry-run")
        self.assertTrue(res["dry_run"])
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.gh_log(), [])

    def test_r014_merge_refuses_before_repo_checks_local_suite_or_merge(self):
        with mock.patch.object(land, "_git") as git, \
             mock.patch.object(land, "_checks_value") as checks, \
             mock.patch.object(land, "_run_local_sandbox") as local, \
             mock.patch.object(land, "_merge") as merge:
            with self.assertRaisesRegex(land.LandError, "trusted remote landing integration is pending"):
                land.land("fix: must only publish", cwd=self.repo, merge=True)
        git.assert_not_called()
        checks.assert_not_called()
        local.assert_not_called()
        merge.assert_not_called()

    def test_ignored_checkpoint_files_never_enter_land_lease_changes(self):
        (self.repo / ".gitignore").write_text(".altitude-checkpoints/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore worker checkpoints",
                 "-m", "Altitude-Task: demo/fix-x")
        checkpoint = self.repo / ".altitude-checkpoints" / "progress.md"
        checkpoint.parent.mkdir()
        checkpoint.write_text("durable through the broker\n")
        self.leased_change()

        changed = sorted(path for _xy, paths in land._changes(self.repo) for path in paths)

        self.assertEqual(changed, ["src/thing.py"])

    def test_unresolved_task_is_refused_before_staging(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            old = os.environ.pop(key)
            self.addCleanup(os.environ.__setitem__, key, old)
        self.leased_change()
        (self.repo / "anything.txt").write_text("also staged\n")
        with self.assertRaisesRegex(land.LandError, "cannot verify commit provenance"):
            land.land("fix: undeclared", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.gh_log(), [])
if __name__ == "__main__":
    unittest.main()
