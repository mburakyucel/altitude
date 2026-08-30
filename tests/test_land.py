"""`alt land` runs the whole land-a-PR sequence offline here: a real temp repo with a bare
remote stands in for GitHub's git side, and a fake `gh` first on PATH answers view/create/
checks/merge/run-list from canned JSON while recording every argv it was called with."""
import contextlib, io, json, os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path
os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-land-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, land, state as S  # noqa: E402

# A stand-in `gh`: logs argv, answers from FAKE_GH_DIR state files (pr.json, checks.json,
# runs.json) with sensible defaults, and mutates pr.json the way GitHub would on create/merge.
GH = """#!/usr/bin/env python3
import json, os, sys
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
        print(read("pr.json", "{}"))
    else:
        print("no pull requests found for branch " + args[2], file=sys.stderr)
        sys.exit(1)
elif cmd == ("pr", "create"):
    body = {"number": 101, "url": "https://example.invalid/pr/101", "state": "OPEN"}
    with open(os.path.join(d, "pr.json"), "w") as f:
        json.dump(body, f)
    print(body["url"])
elif cmd == ("pr", "checks"):
    print(read("checks.json", '[{"bucket": "pass"}]'))
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

    def test_plain_and_merge_land_refuse_unprovenanced_history_before_mutation(self):
        (self.repo / "rogue-history.txt").write_text("direct commit\n")
        self.git("add", "rogue-history.txt")
        self.git("commit", "-q", "-m", "missing task trailer")
        self.leased_change()

        for merge in (False, True):
            with self.subTest(merge=merge), self.assertRaisesRegex(land.LandError, "without exact.*provenance"):
                land.land("must refuse", cwd=self.repo, wait=0, merge=merge)

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

    def test_result_head_comes_from_remote_tracking_branch(self):
        self.leased_change()
        pushed_head = "a" * 40
        real = land._run

        def fake(args, cwd, timeout=120):
            if args == ["git", "rev-parse", "origin/worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 0, pushed_head + "\n", "")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        res = land.land("fix: report remote", cwd=self.repo, wait=0)
        self.assertEqual(res["head"], pushed_head)
        self.assertNotEqual(res["head"], self.git("rev-parse", "HEAD").strip())

    def test_wait_zero_reports_pending_without_waiting(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pending"}]')
        res = land.land("fix: pending", cwd=self.repo, wait=0)
        self.assertEqual(res["checks"], "pending")
        self.assertFalse(res["merged"])
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "checks"]]), 1)

    def test_merge_hold_refuses_before_any_mutation(self):
        reason = "production migration is costly"
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = reason
        (d / "status.json").write_text(json.dumps(task))
        self.leased_change()
        commands = []
        real = land._run

        def record(args, cwd, timeout=120):
            commands.append(args)
            return real(args, cwd, timeout=timeout)

        land._run = record
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: held", cwd=self.repo, wait=0, merge=True)
        message = str(cm.exception)
        self.assertIn(reason, message)
        self.assertIn("alt task hold-merge fix-x --off", message)
        self.assertEqual(commands, [
            ["git", "rev-parse", "--show-toplevel"],
            ["git", "rev-parse", "--git-dir"],
            ["git", "symbolic-ref", "-q", "HEAD"],
        ])

    def test_merge_hold_refuses_with_explicit_paths(self):
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = "production migration is costly"
        (d / "status.json").write_text(json.dumps(task))
        self.leased_change()
        with self.assertRaises(land.LandError):
            land.land("fix: held", cwd=self.repo, wait=0, merge=True, paths="src")
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_merge_without_resolved_task_refuses(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            old = os.environ.pop(key)
            self.addCleanup(os.environ.__setitem__, key, old)
        self.leased_change()
        with self.assertRaisesRegex(land.LandError, "worktree-fix-x.*--project"):
            land.land("fix: unresolved", cwd=self.repo, wait=0, merge=True)
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

    def test_merge_without_hold_after_checks_pass(self):
        self.leased_change()
        res = land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(res["merged"])
        self.assertEqual(res["main_run"], {"databaseId": 7, "status": "completed", "conclusion": "success"})
        merge = next(args for args in self.gh_log() if args[:2] == ["pr", "merge"])
        self.assertEqual(merge[:5], ["pr", "merge", "101", "--squash", "--delete-branch"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], self.git("rev-parse", "HEAD").strip())

    def test_changed_pr_head_is_refused_atomically(self):
        self.leased_change()
        commands = []
        real = land._run

        def changed(args, cwd, timeout=120):
            if args[:3] == ["gh", "pr", "merge"]:
                commands.append(args)
                return subprocess.CompletedProcess(args, 1, "", "head branch was modified")
            return real(args, cwd, timeout=timeout)

        land._run = changed
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaisesRegex(land.LandError, "head branch was modified"):
            land.land("fix: guarded merge", cwd=self.repo, wait=0, merge=True)

        self.assertEqual(len(commands), 1)
        self.assertIn("--match-head-commit", commands[0])
        self.assertEqual(json.loads((self.ghdir / "pr.json").read_text())["state"], "OPEN")

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

    def test_no_merge_when_checks_fail(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}, {"bucket": "pass"}]')
        res = land.land("fix: red", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(res["checks"], "fail")
        self.assertFalse(res["merged"])
        self.assertIsNone(res["main_run"])
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

    def test_unknown_check_bucket_is_an_error_not_a_pass(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "neutral"}]')
        with self.assertRaisesRegex(land.LandError, "neutral"):
            land.land("fix: odd", cwd=self.repo, wait=0, merge=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_rerun_after_merge_does_not_resurrect_the_branch(self):
        self.leased_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        # GitHub deletes the remote head branch on merge; mirror that on the bare remote
        self.git("push", "-q", "origin", "--delete", "worktree-fix-x")
        res = land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(res["merged"])
        self.assertEqual(res["pr"], 101)
        self.assertIsNone(res["commit"])
        self.assertEqual(res["staged"], [])
        self.assertEqual(self.remote_heads(), ["main"])

    def test_new_changes_after_merge_are_refused(self):
        self.leased_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.leased_change("src/late.py")
        with self.assertRaisesRegex(land.LandError, "already merged"):
            land.land("fix: late", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_merged_no_op_asks_for_no_checks(self):
        self.leased_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.git("push", "-q", "origin", "--delete", "worktree-fix-x")
        (self.ghdir / "log.jsonl").unlink()  # only the second run's gh traffic is under test
        res = land.land("fix: merge me", cwd=self.repo, wait=0)
        self.assertTrue(res["merged"])
        self.assertEqual(res["checks"], "merged")  # its own value — never reported as a pass
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])

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

    def test_full_run_views_the_pr_twice(self):
        self.leased_change()
        land.land("fix: once", cwd=self.repo, wait=0)
        # the prefetch that gates committing, then the read-back after create — not a third
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "view"]]), 2)

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
