"""`alt land` runs the whole land-a-PR sequence offline here: a real temp repo with a bare
remote stands in for GitHub's git side, and a fake `gh` first on PATH answers view/create/
checks/merge/run-list from canned JSON while recording every argv it was called with."""
import json, os, shutil, stat, subprocess, sys, tempfile, unittest
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

    def test_refuses_outside_lease_and_stages_nothing(self):
        self.leased_change()
        (self.repo / "rogue.txt").write_text("outside\n")
        with self.assertRaisesRegex(land.LandError, "rogue.txt"):
            land.land("msg", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_happy_path(self):
        self.leased_change("src/has space.py")
        self.leased_change("src/a[1].py")  # a bracket-expression name must stage as a literal, not a glob
        self.leased_change("docs/NOTES.md")
        res = land.land("fix: land the thing\n\nlonger body", cwd=self.repo, wait=0)
        self.assertEqual(res["pr"], 101)
        self.assertEqual(res["url"], "https://example.invalid/pr/101")
        self.assertEqual(res["checks"], "pass")
        self.assertFalse(res["merged"])
        self.assertEqual(res["branch"], "worktree-fix-x")
        self.assertEqual(res["lease"], ["src", "docs/NOTES.md"])
        self.assertEqual(res["staged"], ["docs/NOTES.md", "src/a[1].py", "src/has space.py"])
        self.assertIn("src/a[1].py", self.git("show", "--name-only", "--format=", "HEAD"))
        self.assertEqual(
            self.git("log", "-1", "--format=%B").strip(),
            "fix: land the thing\n\nlonger body\n\n"
            "Altitude-Task: demo/fix-x\nCo-Authored-By: Claude <noreply@anthropic.com>")
        remote_sha = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "worktree-fix-x"],
                                    capture_output=True, text=True).stdout.strip()
        self.assertEqual(remote_sha, self.git("rev-parse", "HEAD").strip())
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

    def test_nonff_push_retries_exactly_once(self):
        self.leased_change()
        calls = {"push": 0, "pull": 0}
        real = land._run

        def fake(args, cwd, timeout=120):
            if args[:2] == ["git", "push"]:
                calls["push"] += 1
                if calls["push"] == 1:
                    return subprocess.CompletedProcess(args, 1, "", "! [rejected] (non-fast-forward)")
                return real(args, cwd, timeout=timeout)
            if args[:2] == ["git", "pull"]:
                calls["pull"] += 1
                return subprocess.CompletedProcess(args, 0, "", "")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        res = land.land("fix: retry", cwd=self.repo, wait=0)
        self.assertEqual(calls, {"push": 2, "pull": 1})
        self.assertEqual(res["pr"], 101)

    def test_second_nonff_rejection_is_an_error(self):
        self.leased_change()
        calls = {"push": 0, "pull": 0}
        real = land._run

        def fake(args, cwd, timeout=120):
            if args[:2] == ["git", "push"]:
                calls["push"] += 1
                return subprocess.CompletedProcess(args, 1, "", "! [rejected] (non-fast-forward)")
            if args[:2] == ["git", "pull"]:
                calls["pull"] += 1
                return subprocess.CompletedProcess(args, 0, "", "")
            return real(args, cwd, timeout=timeout)

        land._run = fake
        self.addCleanup(setattr, land, "_run", real)
        with self.assertRaisesRegex(land.LandError, "again"):
            land.land("fix: retry", cwd=self.repo, wait=0)
        self.assertEqual(calls, {"push": 2, "pull": 1})

    def test_wait_zero_reports_pending_without_waiting(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pending"}]')
        res = land.land("fix: pending", cwd=self.repo, wait=0)
        self.assertEqual(res["checks"], "pending")
        self.assertFalse(res["merged"])
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "checks"]]), 1)

    def test_merge_after_checks_pass(self):
        self.leased_change()
        res = land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(res["merged"])
        self.assertEqual(res["main_run"], {"databaseId": 7, "status": "completed", "conclusion": "success"})
        self.assertIn(["pr", "merge", "101", "--squash", "--delete-branch"], self.gh_log())

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

    def test_conflicted_rebase_leaves_a_state_the_next_run_refuses(self):
        # a real diverging remote: same branch, same file, different content in a second clone
        seed = self.repo / "src" / "f.py"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("base\n")
        self.git("add", "src/f.py")
        self.git("commit", "-q", "-m", "seed")
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
        og("commit", "-q", "-m", "remote change")
        og("push", "-q")
        seed.write_text("local\n")
        with self.assertRaisesRegex(land.LandError, "mid-rebase"):
            land.land("fix: conflict", cwd=self.repo, wait=0)
        # the worktree is now mid-rebase; the follow-up run must refuse, never commit conflict markers
        with self.assertRaisesRegex(land.LandError, "rebase is in progress"):
            land.land("fix: conflict again", cwd=self.repo, wait=0)
        self.assertEqual(self.git("log", "--all", "-S", "<<<<<<<", "--oneline").strip(), "")

    def test_resolved_task_with_empty_lease_refuses(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": []}))
        self.leased_change()
        (self.repo / "secrets.env").write_text("x\n")
        with self.assertRaisesRegex(land.LandError, "lease is empty"):
            land.land("msg", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")

    def test_absolute_lease_entry_still_matches(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": ["/src"]}))
        self.leased_change("src/thing.py")
        res = land.land("fix: abs", cwd=self.repo, wait=0)
        self.assertEqual(res["staged"], ["src/thing.py"])

    def test_rename_crossing_the_lease_boundary_refuses(self):
        self.leased_change("src/keep.py")
        self.git("add", "src/keep.py")
        self.git("commit", "-q", "-m", "seed")
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

    def test_dry_run_reports_a_distinct_checks_value(self):
        self.leased_change()
        res = land.land("msg", cwd=self.repo, dry_run=True)
        self.assertEqual(res["checks"], "dry-run")
        self.assertTrue(res["dry_run"])
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.gh_log(), [])

    def test_unresolved_task_stages_everything_without_the_trailer(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            old = os.environ.pop(key)
            self.addCleanup(os.environ.__setitem__, key, old)
        self.leased_change()
        (self.repo / "anything.txt").write_text("also staged\n")
        res = land.land("fix: undeclared", cwd=self.repo, wait=0)
        self.assertEqual(res["lease"], land.UNDECLARED)
        self.assertIn("anything.txt", res["staged"])
        body = self.git("log", "-1", "--format=%B")
        self.assertNotIn("Altitude-Task", body)
        self.assertIn("Co-Authored-By: Claude <noreply@anthropic.com>", body)


if __name__ == "__main__":
    unittest.main()
