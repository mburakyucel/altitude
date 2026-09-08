"""`alt land` runs the whole land-a-PR sequence offline here: a real temp repo with a bare remote
stands in for GitHub's git side, and the fixture's fake `gh` first on PATH answers view/create/
checks/merge/run-list from canned JSON while recording every argv it was called with. The no-CI
merge gate also logs the exact candidate directory in which its fake test runner executes."""
import contextlib
import copy
import io
import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import ALT, AltitudeCase, git, make_repo
from altitude import land, state as S


class TestLand(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.ghdir = self.fake_gh()
        self.register("demo", path=self.repo)
        for key, value in (("ALTITUDE_PROJECT", "demo"), ("ALTITUDE_TASK", "fix-x"),
                           ("ALTITUDE_ACTOR", "burak"), ("ALTITUDE_ATTEMPT", "")):
            self.setenv(key, value)
        d = S.tasks_dir("demo") / "fix-x"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps(
            {"slug": "fix-x", "state": "running", "paths": ["src", "docs/NOTES.md"]}))
        make_repo(self.repo)
        git("checkout", "-q", "-b", "worktree-fix-x", cwd=self.repo)
        self.remote = self.tmp / "origin.git"
        self.git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git")
        self.git("remote", "set-url", "origin", "https://github.com/team/demo.git")

    def git(self, *args):
        return git(*args, cwd=self.repo)

    def gh_log(self):
        # Keep legacy command assertions readable; repository-binding tests inspect the raw log.
        return [args[:-2] if args[-2:] == ["--repo", "team/demo"] else args for args in super().gh_log()]

    def clone(self, name):
        """A second checkout of the same remote: the base or the branch moving under this worktree."""
        other = self.tmp / name
        git("clone", "-q", str(self.remote), str(other), cwd=self.tmp)
        git("config", "commit.gpgsign", "false", cwd=other)
        return other

    def leased_change(self, name="src/thing.py"):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("changed\n")

    def set_current_l2(self, *, state="running", attempt=1):
        path = S.tasks_dir("demo") / "fix-x" / "status.json"
        task = json.loads(path.read_text())
        task.update({"state": state, "attempt": attempt})
        path.write_text(json.dumps(task))
        self.setenv("ALTITUDE_ACTOR", "l2")
        self.setenv("ALTITUDE_ATTEMPT", str(attempt))

    def record_commands(self, answer=None):
        """Record every command land runs; `answer(args)` may return a CompletedProcess in place of one."""
        commands, real = [], land._run

        def record(args, cwd, timeout=120):
            commands.append(args)
            canned = answer(args) if answer else None
            return real(args, cwd, timeout=timeout) if canned is None else canned

        land._run = record
        self.addCleanup(setattr, land, "_run", real)
        return commands

    def assert_no_publish_mutation(self, commands):
        forbidden_git = {"fetch", "add", "commit", "push"}
        self.assertEqual([args for args in commands if args and args[0] == "gh"], [])
        self.assertEqual(
            [args for args in commands if len(args) > 1 and args[0] == "git" and args[1] in forbidden_git],
            [],
        )
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertNotIn("worktree-fix-x", self.remote_heads())

    def fake_runner(self, name, exit_code=0, output="", script=None):
        p = self.tmp / "bin" / name
        p.write_text("#!/usr/bin/env python3\n"
                     "import json, os, sys\n"
                     "with open(os.path.join(os.environ['FAKE_GH_DIR'], 'runner.jsonl'), 'a') as f:\n"
                     "    f.write(json.dumps({'argv': [os.path.basename(sys.argv[0])] + sys.argv[1:],\n"
                     "                        'cwd': os.path.realpath(os.getcwd())}) + '\\n')\n"
                     + (script or f"sys.stdout.write({output!r})\nsys.exit({exit_code})\n"))
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def runner_calls(self):
        log = self.ghdir / "runner.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

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
        other = self.clone("base-" + name.replace("/", "-"))
        git("checkout", "-q", "main", cwd=other)
        changed = other / name
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_text(content)
        git("add", name, cwd=other)
        git("commit", "-q", "-m", "the base moves on", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)

    def remote_heads(self):
        return sorted(git("branch", "--format=%(refname:short)", cwd=self.remote).split())

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

    def test_non_l2_automated_actors_cannot_land(self):
        self.leased_change()
        commands = self.record_commands()
        self.setenv("ALTITUDE_ACTOR", "l3")
        for actor in ("l3", "altd"):
            with self.subTest(actor=actor):
                os.environ["ALTITUDE_ACTOR"] = actor
                with self.assertRaisesRegex(land.LandError, "only the current L2 or Burak"):
                    land.land("must refuse", cwd=self.repo, wait=0)
        self.assert_no_publish_mutation(commands)

    def test_l2_must_own_the_running_attempt(self):
        self.set_current_l2(attempt=2)
        self.leased_change()
        commands = self.record_commands()

        os.environ["ALTITUDE_ATTEMPT"] = "1"
        with self.assertRaisesRegex(land.LandError, "attempt 1 is no longer current"):
            land.land("must refuse", cwd=self.repo, wait=0)

        os.environ["ALTITUDE_ATTEMPT"] = ""
        with self.assertRaisesRegex(land.LandError, "no longer current"):
            land.land("must refuse", cwd=self.repo, wait=0)

        os.environ["ALTITUDE_ATTEMPT"] = "2"
        task_path = S.tasks_dir("demo") / "fix-x" / "status.json"
        task = json.loads(task_path.read_text())
        task["state"] = "reported"
        task_path.write_text(json.dumps(task))
        with self.assertRaisesRegex(land.LandError, "task is not running"):
            land.land("must refuse", cwd=self.repo, wait=0)
        self.assert_no_publish_mutation(commands)

    def test_current_l2_can_land(self):
        self.set_current_l2()
        self.leased_change()
        result = land.land("fix: current publisher", cwd=self.repo, wait=0)
        self.assertEqual(result["pr"], 101)
        self.assertEqual(result["staged"], ["src/thing.py"])

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
            "Altitude-Task: demo/fix-x")
        remote_sha = git("rev-parse", "worktree-fix-x", cwd=self.remote).strip()
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

    def closing_link_fixture(self, links, *, remove_after_validation=False):
        """Script GitHub's relationship response; Git, bodies and landing remain real."""
        real = land._run
        self.pr_bodies = []
        self.observed_checks = False
        self.validated_links = False

        def answer(args):
            if args[:3] == ["gh", "repo", "view"]:
                self.validated_links = True
                return subprocess.CompletedProcess(args, 0, '{"defaultBranchRef":{"name":"main"}}', "")
            if args[:3] == ["gh", "pr", "checks"]:
                self.observed_checks = True
            if args[:3] in (["gh", "pr", "create"], ["gh", "pr", "edit"]) and "--body-file" in args:
                self.pr_bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
            if args[:3] != ["gh", "pr", "view"]:
                return None
            response = real(args, self.repo)
            if response.returncode == 0:
                pr = json.loads(response.stdout)
                pr["url"] = "https://github.com/acme/widget/pull/101"
                pr["closingIssuesReferences"] = ([] if remove_after_validation and self.validated_links else links)
                response.stdout = json.dumps(pr)
            return response

        return self.record_commands(answer)

    def test_complete_issue_delivery_creates_reuses_and_merges_linked_pr(self):
        self.set_current_l2()
        self.leased_change()
        commands = self.closing_link_fixture([
            {"url": "https://github.com/acme/widget/issues/42"},
            {"url": "https://github.com/acme/widget/issues/43"},
        ])
        body = self.tmp / "pr-body.md"
        body.write_text("Both acceptance scopes verified.\n\nCloses #42\nCloses #43\n")
        opened = land.land("fix: complete issues", cwd=self.repo, wait=0,
                           pr_body_file=str(body), closes_issues=[42, 43, 42])
        self.assertFalse(opened["merged"])
        self.assertEqual(self.pr_bodies, [body.read_text()])
        original_body = body.read_text()
        body.write_text("Both acceptance scopes verified after review.\n\nCloses #42\nCloses #43\n")
        updated = land.land("fix: complete issues", cwd=self.repo, wait=0,
                            pr_body_file=str(body), closes_issues=[42, 43])
        self.assertFalse(updated["merged"])
        self.assertEqual(self.pr_bodies, [original_body, body.read_text()])
        # A resumed owner repeats the declared scope; the existing PR retains its body.
        merged = land.land("fix: complete issues", cwd=self.repo, wait=0,
                           closes_issues=[42, 43], merge=True)
        self.assertTrue(merged["merged"])
        self.assertEqual(self.pr_bodies, [original_body, body.read_text()])
        self.assertFalse(any(args[:2] == ["gh", "issue"] for args in commands))

    def test_mentions_and_partial_work_do_not_declare_closure(self):
        self.leased_change()
        commands = self.closing_link_fixture([])
        (S.task_dir("demo", "fix-x") / "request.md").write_text("Design one part of GitHub issue #42.")
        body = self.tmp / "partial.md"
        body.write_text("Addresses #42; remaining implementation and operator acceptance are pending.\n")
        result = land.land("docs: partial design", cwd=self.repo, wait=0, pr_body_file=str(body), merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(self.pr_bodies, [body.read_text()])
        self.assertFalse(any(args[:2] == ["gh", "issue"] for args in commands))

    def test_missing_or_foreign_closing_links_refuse_merge(self):
        self.leased_change()
        links = []
        commands = self.closing_link_fixture(links)
        for response in ([], [{"url": "https://github.com/other/widget/issues/42"}],
                         [{"url": "https://github.com/acme/widget/issues/43"}]):
            links[:] = response
            with self.subTest(response=response), self.assertRaisesRegex(land.LandError, "lacks GitHub closing links"):
                land.land("fix: missing link", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_removed_closing_link_before_merge_refuses_merge(self):
        self.leased_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}],
                                             remove_after_validation=True)
        with self.assertRaisesRegex(land.LandError, "lacks GitHub closing links"):
            land.land("fix: scope changed", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertTrue(self.observed_checks)
        self.assertTrue(self.validated_links)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_merged_retry_routes_missing_closure_to_l3(self):
        self.leased_change()
        commands = self.closing_link_fixture([])
        landed = land.land("fix: historical delivery", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(landed["merged"])
        commands.clear()
        with self.assertRaisesRegex(land.LandError, "already merged without closing links.*L3"):
            land.land("fix: historical delivery", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] in (["gh", "pr", "merge"], ["gh", "pr", "edit"])
                             or args[:2] == ["gh", "issue"] for args in commands))

    def test_manual_closing_link_on_nondefault_target_refuses_merge(self):
        self.git("push", "origin", "HEAD:refs/heads/release")
        self.leased_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}])
        with self.assertRaisesRegex(land.LandError, "must target GitHub's default branch"):
            land.land("fix: release only", cwd=self.repo, wait=0, base="release", closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_closing_link_does_not_bypass_hold_or_failed_checks(self):
        self.leased_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}])
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}]')
        result = land.land("fix: linked but red", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(result["merged"])
        self.hold_merge()
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("fix: held", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_closes_issue_cli_rejects_invalid_numbers_before_publication(self):
        self.leased_change()
        for number in ("0", "-1", "42"):
            result = subprocess.run([str(ALT), "land", "--message", "fix: linked", "--wait", "0",
                                     "--closes-issue", number, "--merge"], cwd=self.repo,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lacks GitHub closing links" if number == "42" else "positive issue number", result.stderr)
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args in self.gh_log()))

    def test_rebased_push_retries_with_recorded_tip_lease_exactly_once(self):
        self.leased_change("src/original.py")
        self.git("add", "src/original.py")
        self.git("commit", "-q", "-m", "original", "-m", "Altitude-Task: demo/fix-x")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        recorded_tip = self.git("rev-parse", "origin/worktree-fix-x").strip()
        self.git("commit", "--amend", "-q", "-m", "rebased", "-m", "Altitude-Task: demo/fix-x")
        self.leased_change()
        commands = self.record_commands()
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
        tip_reads = 0

        def answer(args):
            nonlocal tip_reads
            if args == ["git", "rev-parse", "--verify", "-q", "refs/remotes/origin/worktree-fix-x"]:
                tip_reads += 1
                tip = recorded_tip if tip_reads == 1 else current_tip
                return subprocess.CompletedProcess(args, 0, tip + "\n", "")
            if args[:2] == ["git", "push"]:
                return subprocess.CompletedProcess(args, 1, "", "! [rejected] (non-fast-forward)")
            return None

        commands = self.record_commands(answer)
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
        commands = self.record_commands()
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

        def answer(args):
            if args == ["git", "fetch", "-q", "origin",
                        "+refs/heads/worktree-fix-x:refs/remotes/origin/worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: référence distante introuvable")
            if args == ["git", "ls-remote", "--exit-code", "--heads", "origin", "worktree-fix-x"]:
                return subprocess.CompletedProcess(args, 2, "", "")
            return None

        commands = self.record_commands(answer)
        res = land.land("fix: first push", cwd=self.repo, wait=0)
        self.assertEqual([a for a in commands if a[:2] == ["git", "push"]],
                         [["git", "push", "-u", "origin", "worktree-fix-x"]])
        self.assertIn(["git", "ls-remote", "--exit-code", "--heads", "origin", "worktree-fix-x"], commands)
        self.assertEqual(res["head"], self.git("rev-parse", "origin/worktree-fix-x").strip())
        self.assertEqual(res["replaced"], [])

    def test_inconsistent_remote_tracking_head_fails_the_pr_pin(self):
        self.leased_change()
        pushed_head = "a" * 40
        self.record_commands(lambda args: subprocess.CompletedProcess(args, 0, pushed_head + "\n", "")
                             if args == ["git", "rev-parse", "origin/worktree-fix-x"] else None)
        with self.assertRaisesRegex(land.LandError, "head moved"):
            land.land("fix: report remote", cwd=self.repo, wait=0)

    def test_wait_zero_reports_pending_without_waiting(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pending"}]')
        res = land.land("fix: pending", cwd=self.repo, wait=0)
        self.assertEqual(res["checks"], "pending")
        self.assertFalse(res["merged"])
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "checks"]]), 1)

    def hold_merge(self, reason="production migration is costly"):
        d = S.tasks_dir("demo") / "fix-x"
        task = json.loads((d / "status.json").read_text())
        task["hold_merge"] = reason
        (d / "status.json").write_text(json.dumps(task))
        return reason

    def test_merge_hold_refuses_before_any_mutation(self):
        reason = self.hold_merge()
        self.leased_change()
        commands = self.record_commands()
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
        self.hold_merge()
        self.leased_change()
        with self.assertRaises(land.LandError):
            land.land("fix: held", cwd=self.repo, wait=0, merge=True, paths="src")
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_merge_without_resolved_task_refuses(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            self.setenv(key, None)
        self.leased_change()
        with self.assertRaisesRegex(land.LandError, "worktree-fix-x.*--project"):
            land.land("fix: unresolved", cwd=self.repo, wait=0, merge=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_merge_hold_refuses_dry_run(self):
        self.hold_merge()
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
        commands = self.record_commands(
            lambda args: subprocess.CompletedProcess(args, 1, "", "head branch was modified")
            if args[:3] == ["gh", "pr", "merge"] else None)
        with self.assertRaisesRegex(land.LandError, "head branch was modified"):
            land.land("fix: guarded merge", cwd=self.repo, wait=0, merge=True)

        merges = [a for a in commands if a[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merges), 1)
        self.assertIn("--match-head-commit", merges[0])
        self.assertEqual(json.loads((self.ghdir / "pr.json").read_text())["state"], "OPEN")

    def test_merge_hold_without_merge_opens_pr_and_emits_notice(self):
        reason = self.hold_merge()
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
        other = self.clone("other")
        git("checkout", "-q", "worktree-fix-x", cwd=other)
        (other / "src" / "f.py").write_text("remote\n")
        git("add", "src/f.py", cwd=other)
        git("commit", "-q", "-m", "remote change", "-m", "Altitude-Task: demo/fix-x", cwd=other)
        git("push", "-q", cwd=other)
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
        other = self.clone("other-behind")
        git("checkout", "-q", "worktree-fix-x", cwd=other)
        (other / "src" / "remote.py").write_text("foreign\n")
        git("add", "src/remote.py", cwd=other)
        git("commit", "-q", "-m", "foreign", "-m", "Altitude-Task: demo/fix-x", cwd=other)
        git("push", "-q", cwd=other)
        foreign_tip = git("rev-parse", "worktree-fix-x", cwd=self.remote).strip()
        commands = self.record_commands()
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: must not rewind", cwd=self.repo, wait=0)
        message = str(cm.exception)
        self.assertIn(f"recorded remote tip: {foreign_tip}", message)
        self.assertIn(f"current remote tip: {foreign_tip}", message)
        self.assertIn("rebase by hand", message)
        self.assertEqual(len([a for a in commands if a[:2] == ["git", "push"]]), 1)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), local_tip)
        self.assertEqual(git("rev-parse", "worktree-fix-x", cwd=self.remote).strip(), foreign_tip)

    def test_resolved_task_with_empty_lease_fails_loudly(self):
        d = S.tasks_dir("demo") / "fix-x"
        (d / "status.json").write_text(json.dumps({"slug": "fix-x", "state": "running", "paths": []}))
        self.leased_change()
        (self.repo / "secrets.env").write_text("x\n")
        p = subprocess.run([sys.executable, str(ALT), "land", "--message", "msg", "--wait", "0"],
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

    # Where no workflow and no check exist, the exact merge candidate's local suite is the gate. Absence,
    # skipped checks, stale revisions, and unreadable test reports never become green.

    def test_no_ci_merges_after_a_green_local_suite_and_records_the_count(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: no ci", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"], {
            "command": "make test", "passed": True, "returncode": 0, "tests": 12, "skipped": 0,
            "expected_failures": 0, "error": None,
            "base": result["local_tests"]["base"], "head": result["local_tests"]["head"],
        })
        self.assertEqual(self.runner_log(), [["make", "test"]])

    def test_the_suite_runs_on_the_merge_candidate_not_on_this_worktree(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        land.land("fix: candidate", cwd=self.repo, wait=0, merge=True)
        candidate = Path(self.runner_calls()[0]["cwd"])
        self.assertNotEqual(candidate, self.repo.resolve())
        self.assertFalse(candidate.exists())
        self.assertEqual([line for line in self.git("worktree", "list").splitlines() if "candidate" in line], [])

    def test_a_base_change_that_breaks_the_head_fails_on_the_candidate(self):
        runner = ("import os\n"
                  "here = os.getcwd()\n"
                  "both = all(os.path.exists(os.path.join(here, 'src', f))\n"
                  "           for f in ('thing.py', 'integration.py'))\n"
                  "sys.stdout.write('Ran 4 tests in 0.1s\\n\\n'\n"
                  "                 + ('FAILED (failures=1)\\n' if both else 'OK\\n'))\n"
                  "sys.exit(1 if both else 0)\n")
        self.leased_change()
        self.advance_base("src/integration.py")
        self.no_checks()
        self.fake_runner("make", script=runner)
        result = land.land("fix: integration break", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertFalse(result["merged"])
        self.assertFalse(result["local_tests"]["passed"])
        self.assertEqual(result["local_tests"]["returncode"], 1)
        self.assertNotEqual(Path(self.runner_calls()[0]["cwd"]), self.repo.resolve())
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_no_ci_merge_pins_the_tested_head_revision(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: pinned", cwd=self.repo, wait=0, merge=True)
        head = self.git("rev-parse", "HEAD").strip()
        self.assertTrue(result["merged"])
        self.assertEqual(result["head"], head)
        self.assertEqual(result["local_tests"]["head"], head)
        self.assertEqual(result["local_tests"]["base"], self.git("rev-parse", "origin/main").strip())
        self.assertIn(["pr", "merge", "101", "--squash", "--delete-branch", "--match-head-commit", head],
                      self.gh_log())

    def test_a_base_that_moves_during_the_suite_is_not_merged(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        real = land._local_suite

        def moving(cwd, test_cmd):
            output = real(cwd, test_cmd)
            self.advance_base("src/late.py")
            return output

        self.patch(land, "_local_suite", side_effect=moving)
        result = land.land("fix: moving base", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertTrue(result["local_tests"]["passed"])
        self.assertIn("moved", result["local_tests"]["error"])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_ci_added_after_no_checks_classification_is_not_merged(self):
        """Regression: classification and candidate snapshot used to be separate, adopt-new-tip operations."""
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        real = land._checks_value

        def classify_then_add_workflow(root, number, pair):
            state = real(root, number, pair)
            self.advance_base(".github/workflows/late.yml", "on: [pull_request]\n")
            return state

        self.patch(land, "_checks_value", side_effect=classify_then_add_workflow)
        result = land.land("fix: classification race", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertFalse(result["merged"])
        self.assertIn("moved", result["local_tests"]["error"])
        self.assertEqual(self.runner_log(), [])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_checks_appearing_during_the_local_suite_block_the_merge(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        real = land._local_suite

        def suite_then_check(cwd, test_cmd):
            result = real(cwd, test_cmd)
            (self.ghdir / "checks.json").write_text('[{"bucket": "pass"}]')
            return result

        self.patch(land, "_local_suite", side_effect=suite_then_check)
        result = land.land("fix: check race", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertIn("checks changed", result["local_tests"]["error"])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_required_gate_appearing_during_local_suite_cannot_use_no_ci_fallback(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 4 tests in 0.1s\n\nOK\n")
        real = land._local_suite

        def require_gate_after_suite(cwd, command):
            result = real(cwd, command)
            evidence = json.loads((self.ghdir / "last_check_evidence.json").read_text())
            evidence["pullRequest"]["baseRef"]["branchProtectionRule"] = {
                "requiredStatusChecks": [{"context": "new-required-gate", "app": None}]}
            S.write_json(self.ghdir / "check_evidence.json", evidence)
            return result

        self.patch(land, "_local_suite", side_effect=require_gate_after_suite)
        result = land.land("fix: required gate race", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertIn("checks changed", result["local_tests"]["error"])
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_first_adoption_during_ordinary_checks_cannot_change_the_merge_target(self):
        self.leased_change()
        real = land._checks_state

        def adopt_after_checks(root, number):
            result = real(root, number)
            task = S.load_task("demo", "fix-x")
            task["adopted_pr"] = {"number": 102, "branch": "proposal/other"}
            S.save_task("demo", task)
            return result

        self.patch(land, "_checks_state", side_effect=adopt_after_checks)
        with self.assertRaisesRegex(land.LandError, "task adoption changed"):
            land.land("fix: ordinary target", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_candidate_has_the_single_parent_history_of_a_squash_merge(self):
        runner = ("import subprocess\n"
                  "parents = subprocess.check_output(['git', 'rev-list', '--parents', '-n', '1', 'HEAD'], "
                  "text=True).split()\n"
                  "single_parent = len(parents) == 2\n"
                  "sys.stdout.write('Ran 1 test in 0.1s\\n\\n' + ('OK\\n' if single_parent else "
                  "'FAILED (failures=1)\\n'))\n"
                  "sys.exit(0 if single_parent else 1)\n")
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", script=runner)
        result = land.land("fix: squash candidate", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 1)

    def test_zero_tests_is_not_a_green_local_gate(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 0 tests in 0.0s\n\nOK\n")
        result = land.land("fix: zero tests", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 0)
        self.assertIn("no passing tests", result["local_tests"]["error"])

    def test_all_skipped_tests_is_not_a_green_local_gate(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.1s\n\nOK (skipped=12)\n")
        result = land.land("fix: all skipped", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 0)
        self.assertEqual(result["local_tests"]["skipped"], 12)
        self.assertIn("no passing tests", result["local_tests"]["error"])

    def test_unittest_expected_failures_are_not_reported_as_passes(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 5 tests in 0.1s\n\nOK (skipped=1, expected failures=2)\n")
        result = land.land("fix: honest count", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 2)
        self.assertEqual(result["local_tests"]["skipped"], 1)
        self.assertEqual(result["local_tests"]["expected_failures"], 2)

    def test_candidate_cleanup_continues_when_git_remove_raises(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 3 tests in 0.1s\n\nOK\n")
        candidate_paths = []

        def fail_remove(args):
            if args[:4] == ["git", "worktree", "remove", "--force"] and "alt-land-candidate-" in args[-1]:
                candidate_paths.append(Path(args[-1]))
                raise land.LandError("simulated worktree-remove timeout")
            return None

        self.record_commands(fail_remove)
        result = land.land("fix: cleanup", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertIn("candidate cleanup failed", result["local_tests"]["error"])
        self.assertTrue(candidate_paths)
        self.assertTrue(all(not path.exists() for path in candidate_paths))
        self.assertNotIn("alt-land-candidate-", self.git("worktree", "list"))
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_candidate_cleanup_does_not_mask_the_original_merge_error(self):
        self.leased_change("src/collision.py")
        self.advance_base("src/collision.py", "incompatible base\n")
        self.no_checks()
        self.fake_runner("make", 0, "Ran 3 tests in 0.1s\n\nOK\n")

        def fail_remove(args):
            if args[:4] == ["git", "worktree", "remove", "--force"] and "alt-land-candidate-" in args[-1]:
                raise land.LandError("simulated cleanup failure")
            return None

        self.record_commands(fail_remove)
        result = land.land("fix: conflict cleanup", cwd=self.repo, wait=0, merge=True,
                           paths="src/collision.py")
        self.assertFalse(result["merged"])
        self.assertIn("does not merge cleanly", result["local_tests"]["error"])
        self.assertNotIn("cleanup failed", result["local_tests"]["error"])

    def test_no_ci_red_local_suite_blocks_the_merge(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 1, "Ran 12 tests in 0.4s\n\nFAILED (failures=1)\n")
        result = land.land("fix: red suite", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertFalse(result["merged"])
        self.assertFalse(result["local_tests"]["passed"])
        self.assertEqual(result["local_tests"]["tests"], 12)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_a_local_suite_that_cannot_run_blocks_rather_than_passes(self):
        self.leased_change()
        self.no_checks()
        result = land.land("fix: no runner", cwd=self.repo, wait=0, merge=True,
                           test_cmd="definitely-not-a-real-command")
        self.assertEqual(result["checks"], "none-configured")
        self.assertFalse(result["merged"])
        self.assertFalse(result["local_tests"]["passed"])
        self.assertIsNone(result["local_tests"]["returncode"])
        self.assertTrue(result["local_tests"]["error"])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_a_green_suite_with_an_unreadable_count_is_not_a_pass(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "everything is fine, trust me\n")
        result = land.land("fix: uncountable", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertFalse(result["local_tests"]["passed"])
        self.assertEqual(result["local_tests"]["returncode"], 0)
        self.assertIsNone(result["local_tests"]["tests"])
        self.assertIn("count", result["local_tests"]["error"])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_skipped_tests_are_excluded_from_the_reported_count(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK (skipped=2)\n")
        result = land.land("fix: some skips", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 10)
        self.assertEqual(result["local_tests"]["skipped"], 2)

    def test_gh_refusing_with_no_checks_reported_is_the_same_gate(self):
        self.leased_change()
        self.no_checks("")
        self.fake_runner("make", 0, "Ran 3 tests in 0.1s\n\nOK\n")
        result = land.land("fix: nothing reported", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 3)

    def test_test_cmd_override_is_the_command_that_gates(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("otherrunner", 0, "=== 5 passed, 2 skipped in 0.2s ===\n")
        result = land.land("fix: override", cwd=self.repo, wait=0, merge=True,
                           test_cmd="otherrunner -q tests")
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["command"], "otherrunner -q tests")
        self.assertEqual(result["local_tests"]["tests"], 5)
        self.assertEqual(result["local_tests"]["skipped"], 2)
        self.assertEqual(self.runner_log(), [["otherrunner", "-q", "tests"]])

    def test_cli_exposes_the_test_command_override(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("otherrunner", 0, "=== 5 passed, 2 skipped in 0.2s ===\n")
        run = subprocess.run(
            [sys.executable, str(ALT), "land", "--message", "fix: cli override", "--wait", "0", "--merge",
             "--test-cmd", "otherrunner -q tests"],
            cwd=self.repo, capture_output=True, text=True, env=dict(os.environ),
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["command"], "otherrunner -q tests")
        self.assertEqual(self.runner_log(), [["otherrunner", "-q", "tests"]])

    def test_no_checks_but_workflows_configured_never_reaches_the_local_suite(self):
        self.configure_ci()
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: unreported", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_workflows_only_on_the_base_branch_still_count_as_ci(self):
        self.git("checkout", "-q", "main")
        self.configure_ci()
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "worktree-fix-x")
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: base has ci", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_a_base_that_cannot_be_refreshed_fails_closed(self):
        self.leased_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        git("symbolic-ref", "HEAD", "refs/heads/gone", cwd=self.remote)
        git("branch", "-D", "main", cwd=self.remote)
        with self.assertRaisesRegex(land.LandError, r"origin(?:/| )main"):
            land.land("fix: unreadable base", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(self.runner_log(), [])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_skipped_checks_are_not_a_no_ci_repository(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "skipping"}]')
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: skipping", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_a_pass_mixed_with_a_skip_is_skipped_not_a_pass(self):
        self.leased_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pass"}, {"bucket": "skipping"}]')
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: half skipped", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_configured_checks_that_pass_merge_without_the_local_suite(self):
        self.configure_ci()
        self.leased_change()
        self.fake_runner("make", 1, "the local suite must not be consulted here\n")
        result = land.land("fix: green ci", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "pass")
        self.assertTrue(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

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

    def test_unresolved_task_is_refused_before_staging(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            self.setenv(key, None)
        self.leased_change()
        (self.repo / "anything.txt").write_text("also staged\n")
        with self.assertRaisesRegex(land.LandError, "cannot verify commit provenance"):
            land.land("fix: undeclared", cwd=self.repo, wait=0)
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual(self.gh_log(), [])


class TestCheckEvidence(AltitudeCase):
    """#266: real Git candidates and immutable workflow blobs through the shared GitHub transport."""
    git = TestLand.git
    clone = TestLand.clone
    advance_base = TestLand.advance_base
    leased_change = TestLand.leased_change
    workflow_path = ".github/workflows/checks.yml"
    condition = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    source = ("on: [pull_request, push]\njobs:\n  tests:\n    runs-on: ubuntu-latest\n"
              "    steps:\n      - run: echo tests\n  deploy:\n    if: " + condition
              + "\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo deploy\n")

    def setUp(self):
        super().setUp()
        self.ghdir = self.fake_gh()
        self.register("demo", path=self.repo)
        make_repo(self.repo)
        self.remote = self.tmp / "origin.git"
        self.git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git")
        self.git("remote", "set-url", "origin", "https://github.com/team/demo.git")
        workflow = self.repo / self.workflow_path
        workflow.parent.mkdir(parents=True)
        workflow.write_text(self.source)
        self.git("add", self.workflow_path)
        self.git("commit", "-q", "-m", "Initial workflow")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "worktree-fix-x")
        S.save_task("demo", {"slug": "fix-x", "state": "running", "paths": ["src", ".github/workflows"]})
        for key, value in {"ALTITUDE_PROJECT": "demo", "ALTITUDE_TASK": "fix-x",
                           "ALTITUDE_ACTOR": "burak", "ALTITUDE_ATTEMPT": ""}.items():
            self.setenv(key, value)
        self.leased_change()
        self.refresh()

    @staticmethod
    def connection(nodes):
        return {"nodes": nodes, "totalCount": len(nodes), "pageInfo": {"hasNextPage": False}}

    def refresh(self):
        (self.ghdir / "check_evidence.json").unlink(missing_ok=True)
        S.write_json(self.ghdir / "checks.json", [{"bucket": "pass"}])
        land.land("fixture candidate", cwd=self.repo, wait=0)
        self.evidence = json.loads((self.ghdir / "last_check_evidence.json").read_text())
        self.pr = self.evidence["pullRequest"]
        self.head, self.base = self.pr["headRefOid"], self.pr["baseRef"]["target"]["oid"]
        self.pair = {"number": 101, "base": "main", "branch": "worktree-fix-x",
                     "base_sha": self.base, "head_sha": self.head}

    def contexts(self):
        return self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]

    def classify(self):
        S.write_json(self.ghdir / "check_evidence.json", self.evidence)
        return land._checks_value(self.repo, 101, self.pair)

    def optional_deploy(self, *, required=False, event="pull_request", revision=None):
        check = copy.deepcopy(self.contexts()["nodes"][0])
        check.update(name="deploy", conclusion="SKIPPED", isRequired=required)
        check["checkSuite"].update(app={"databaseId": 15368, "slug": "github-actions"}, workflowRun={
            "event": event, "file": {"path": self.workflow_path, "repositoryName": "team/demo",
                "repositoryFileUrl": f"https://github.com/team/demo/blob/{revision or self.head}/{self.workflow_path}"}})
        self.contexts().update(self.connection([self.contexts()["nodes"][0], check]))
        S.write_json(self.ghdir / "checks.json", [{"bucket": "pass"}, {"bucket": "skipping"}])
        return check

    def merge_candidate(self, *, checks=True):
        tree = self.git("merge-tree", "--write-tree", self.base, self.head).strip()
        oid = self.git("commit-tree", tree, "-p", self.base, "-p", self.head, "-m", "Hosted test merge").strip()
        contexts = copy.deepcopy(self.contexts()["nodes"]) if checks else []
        for check in contexts:
            check["checkSuite"]["commit"]["oid"] = oid
        merge = {"oid": oid, "parents": {"totalCount": 2, "nodes": [{"oid": self.base}, {"oid": self.head}]},
                 "statusCheckRollup": {"contexts": self.connection(contexts)}}
        self.pr["potentialMergeCommit"] = merge
        return merge

    def diverge_base(self):
        self.advance_base("src/base.py")
        self.git("fetch", "-q", "origin", "main")
        self.base = self.git("rev-parse", "origin/main").strip()
        self.pair["base_sha"] = self.base
        self.pr["baseRef"]["target"]["oid"] = self.base

    def test_stale_base_metadata_with_incorporated_main_and_exact_merge_checks_lands(self):
        original_base = self.base
        self.diverge_base()
        self.git("merge", "--no-ff", "origin/main", "-m", "Incorporate main\n\nAltitude-Task: demo/fix-x")
        saved = json.loads((self.ghdir / "pr.json").read_text())
        saved["baseRefOid"] = original_base
        S.write_json(self.ghdir / "pr.json", saved)
        self.refresh()
        merge = self.merge_candidate()
        self.assertEqual(self.pr["baseRefOid"], original_base)
        self.assertNotEqual(self.base, original_base)
        self.assertEqual([p["oid"] for p in merge["parents"]["nodes"]], [self.base, self.head])
        self.assertEqual(self.classify(), "pass")
        self.assertTrue(land.land("checked current base", cwd=self.repo, wait=0, merge=True)["merged"])

    def test_real_base_movement_still_refuses(self):
        self.advance_base("src/moved.py")
        with self.assertRaisesRegex(land.LandError, "moved"):
            self.classify()

    def test_graphql_ref_head_and_pr_identity_must_match_pinned_refs(self):
        original = copy.deepcopy(self.evidence)
        for field in ("base", "head", "number", "branch"):
            with self.subTest(field=field):
                self.evidence = copy.deepcopy(original)
                self.pr = self.evidence["pullRequest"]
                if field == "base":
                    self.pr["baseRef"]["target"]["oid"] = "0" * 40
                else:
                    self.pr[{"head": "headRefOid", "number": "number", "branch": "headRefName"}[field]] = "wrong"
                with self.assertRaisesRegex(land.LandError, "moved"):
                    self.classify()

    def test_head_checks_require_current_base_ancestry(self):
        self.diverge_base()
        with self.assertRaisesRegex(land.LandError, "head checks do not include"):
            self.classify()

    def test_exact_test_merge_checks_cover_divergent_head(self):
        self.diverge_base()
        self.merge_candidate()
        self.assertEqual(self.classify(), "pass")

    def test_successful_merge_candidate_checks_supersede_failed_or_pending_head_checks(self):
        self.diverge_base()
        self.merge_candidate()
        check = self.contexts()["nodes"][0]
        for bucket, status, conclusion in (("fail", "COMPLETED", "FAILURE"), ("pending", "IN_PROGRESS", None)):
            with self.subTest(bucket=bucket):
                check.update(status=status, conclusion=conclusion)
                S.write_json(self.ghdir / "checks.json", [{"bucket": bucket}])
                self.assertEqual(self.classify(), "pass")

    def test_test_merge_parents_must_be_exact_ordered_base_and_head(self):
        merge = self.merge_candidate()
        for parents in ([self.head, self.base], ["0" * 40, self.head], [self.base, self.head, self.base]):
            with self.subTest(parents=parents):
                merge["parents"] = {"totalCount": len(parents), "nodes": [{"oid": sha} for sha in parents]}
                with self.assertRaisesRegex(land.LandError, "test merge checks"):
                    self.classify()

    def test_check_runs_and_status_contexts_bind_the_exact_candidate_commit(self):
        self.contexts()["nodes"][0]["checkSuite"]["commit"]["oid"] = "0" * 40
        with self.assertRaisesRegex(land.LandError, "unrelated"):
            self.classify()
        status = {"__typename": "StatusContext", "context": "legacy-tests", "state": "SUCCESS",
                  "isRequired": False, "commit": {"oid": "0" * 40}}
        self.contexts().update(self.connection([status]))
        with self.assertRaisesRegex(land.LandError, "unrelated"):
            self.classify()
        status["commit"]["oid"] = self.head
        self.assertEqual(self.classify(), "pass")

    def test_workflow_event_branch_and_related_pr_must_match(self):
        self.optional_deploy()
        original = copy.deepcopy(self.evidence)
        for field in ("event", "number", "baseRefName", "headRefName", "push_branch"):
            with self.subTest(field=field):
                self.evidence = copy.deepcopy(original)
                self.pr = self.evidence["pullRequest"]
                suite = self.contexts()["nodes"][-1]["checkSuite"]
                if field == "event":
                    suite["workflowRun"]["event"] = "workflow_dispatch"
                elif field == "push_branch":
                    suite["workflowRun"]["event"] = "push"
                    suite["branch"]["name"] = "unrelated"
                else:
                    suite["matchingPullRequests"]["nodes"][0][field] = "unrelated"
                with self.assertRaisesRegex(land.LandError, "workflow run does not belong"):
                    self.classify()

    def test_existing_pull_request_target_success_is_still_accepted(self):
        check = self.optional_deploy(event="pull_request_target")
        check.update(name="Remote tests / Python", conclusion="SUCCESS")
        self.contexts().update(self.connection([check]))
        S.write_json(self.ghdir / "checks.json", [{"bucket": "pass"}])
        self.assertEqual(self.classify(), "pass")

    def test_missing_required_check_is_blocked_for_classic_and_ruleset_policy(self):
        base = self.pr["baseRef"]
        for classic in (True, False):
            with self.subTest(classic=classic):
                base["branchProtectionRule"] = {"requiredStatusChecks": [{"context": "absent", "app": None}]} if classic else None
                base["rules"] = self.connection([] if classic else [{"type": "REQUIRED_STATUS_CHECKS",
                    "parameters": {"requiredStatusChecks": [{"context": "absent", "integrationId": None}]}}])
                self.assertEqual(self.classify(), "skipped")

    def test_required_app_and_requiredness_are_enforced(self):
        check = self.contexts()["nodes"][0]
        self.pr["baseRef"]["branchProtectionRule"] = {
            "requiredStatusChecks": [{"context": check["name"], "app": {"databaseId": 2}}]}
        check["isRequired"] = True
        self.assertEqual(self.classify(), "skipped")
        check["checkSuite"]["app"]["databaseId"] = 2
        self.assertEqual(self.classify(), "pass")
        check["isRequired"] = False
        self.assertEqual(self.classify(), "skipped")
        check.pop("isRequired")
        with self.assertRaisesRegex(land.LandError, "incomplete"):
            self.classify()

    def test_optional_canonical_push_only_job_does_not_block_successful_pr_checks(self):
        self.optional_deploy()
        self.assertEqual(self.classify(), "pass")
        result = land.land("applicable checks passed", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["checks"], "pass")
        query_calls = [a for a in self.gh_log() if a[:2] == ["api", "graphql"]]
        self.assertTrue(query_calls)
        self.assertTrue(all("owner=team" in a and "repo=demo" in a for a in query_calls))

    def test_required_or_non_actions_skipped_job_remains_blocked(self):
        check = self.optional_deploy(required=True)
        self.assertEqual(self.classify(), "skipped")
        check["isRequired"] = False
        check["checkSuite"]["app"]["slug"] = "unrelated-app"
        self.assertEqual(self.classify(), "skipped")

    def test_unsupported_or_ambiguous_job_source_cannot_prove_inapplicability(self):
        cases = {
            "unknown condition": self.source.replace(self.condition, "needs.tests.result == 'success'"),
            "condition true": self.source.replace(self.condition, "github.event_name == 'pull_request'"),
            "disjunction": self.source.replace(self.condition, self.condition + " || true"),
            "continued scalar": self.source.replace(self.condition, self.condition + "\n      || true"),
            "step condition": self.source.replace("    if:", "    steps:\n      - if:"),
            "custom name": self.source.replace("  tests:\n", "  tests:\n    name: deploy\n"),
            "duplicate if": self.source.replace("    runs-on: ubuntu-latest\n", "    if: true\n    runs-on: ubuntu-latest\n"),
            "duplicate jobs": self.source + self.source[self.source.index("jobs:"):],
            "yaml merge": self.source.replace("  deploy:\n", "  deploy:\n    <<: *defaults\n"),
            "matrix": self.source.replace("  deploy:\n", "  deploy:\n    strategy:\n      matrix: {os: [ubuntu-latest]}\n"),
            "job alias": self.source.replace("  deploy:\n", "  deploy: *other\n"),
            "quoted root jobs": self.source.replace("jobs:", '"jobs":'),
            "complex root jobs": self.source.replace("jobs:", "? jobs\n:"),
            "fake jobs inside quoted scalar": 'name: "A workflow name\njobs:\n  deploy:\n    if: ' + self.condition
                + '\n"\non: pull_request\njobs: {deploy: {runs-on: ubuntu-latest, if: false, steps: [{run: echo hi}]}}\n',
        }
        for label, source in cases.items():
            with self.subTest(source=label):
                (self.repo / self.workflow_path).write_text(source)
                self.refresh()
                self.optional_deploy()
                self.assertEqual(self.classify(), "skipped")

    def test_complete_expression_wrapper_is_supported(self):
        (self.repo / self.workflow_path).write_text(self.source.replace(self.condition, "${{ " + self.condition + " }}"))
        self.refresh()
        self.optional_deploy()
        self.assertEqual(self.classify(), "pass")

    def test_unpinned_foreign_or_wrong_workflow_source_is_not_proof(self):
        check = self.optional_deploy()
        original = copy.deepcopy(check["checkSuite"]["workflowRun"]["file"])
        cases = [{"repositoryName": "other/demo"}, {"path": ".github/workflows/other.yml"},
                 {"repositoryFileUrl": f"https://github.com/team/demo/blob/main/{self.workflow_path}"},
                 {"repositoryFileUrl": f"https://github.com/team/demo/blob/{'0' * 40}/{self.workflow_path}"},
                 {"repositoryFileUrl": f"https://github.com/other/demo/blob/{self.head}/{self.workflow_path}"},
                 {"repositoryFileUrl": None}]
        for changed in cases:
            with self.subTest(source=changed):
                check["checkSuite"]["workflowRun"]["file"] = {**original, **changed}
                self.assertEqual(self.classify(), "skipped")

    def test_working_tree_cannot_replace_the_executed_workflow_condition(self):
        (self.repo / self.workflow_path).write_text(self.source.replace(self.condition, "true"))
        self.refresh()
        self.optional_deploy()
        (self.repo / self.workflow_path).write_text(self.source)
        self.assertEqual(self.classify(), "skipped")

    def test_missing_or_truncated_workflow_blob_cannot_prove_inapplicability(self):
        self.optional_deploy()
        for blob in (None, {"text": self.source}, {"text": self.source, "isTruncated": True}):
            with self.subTest(blob=blob):
                S.write_json(self.ghdir / "workflow_blob.json", blob)
                self.assertEqual(self.classify(), "skipped")

    def test_merge_candidate_skip_needs_its_own_source_when_base_and_head_diverge(self):
        self.diverge_base()
        self.optional_deploy()
        merge = self.merge_candidate()
        self.assertEqual(self.classify(), "skipped")
        skipped = merge["statusCheckRollup"]["contexts"]["nodes"][-1]
        skipped["checkSuite"]["workflowRun"]["file"]["repositoryFileUrl"] = (
            f"https://github.com/team/demo/blob/{merge['oid']}/{self.workflow_path}")
        self.assertEqual(self.classify(), "pass")

    def test_truncated_connections_never_hide_other_gates(self):
        self.optional_deploy()
        original = copy.deepcopy(self.evidence)
        for target in ("rules", "contexts", "related"):
            for marker in ("next_page", "count"):
                with self.subTest(target=target, marker=marker):
                    self.evidence = copy.deepcopy(original)
                    self.pr = self.evidence["pullRequest"]
                    connection = (self.pr["baseRef"]["rules"] if target == "rules" else self.contexts() if target == "contexts"
                                  else self.contexts()["nodes"][-1]["checkSuite"]["matchingPullRequests"])
                    if marker == "next_page":
                        connection["pageInfo"]["hasNextPage"] = True
                    else:
                        connection["totalCount"] += 1
                    with self.assertRaisesRegex(land.LandError, "truncated"):
                        self.classify()

    def test_empty_merge_rollup_keeps_valid_head_checks(self):
        self.merge_candidate(checks=False)
        self.assertEqual(self.classify(), "pass")

    def test_all_inapplicable_jobs_do_not_become_a_green_gate(self):
        skipped = self.optional_deploy()
        self.contexts().update(self.connection([skipped]))
        S.write_json(self.ghdir / "checks.json", [{"bucket": "skipping"}])
        self.assertEqual(self.classify(), "skipped")

    def test_buckets_without_candidate_evidence_cannot_enter_no_ci_fallback(self):
        self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        for bucket in ("pass", "skipping"):
            with self.subTest(bucket=bucket):
                S.write_json(self.ghdir / "checks.json", [{"bucket": bucket}])
                with self.assertRaisesRegex(land.LandError, "no evidence"):
                    self.classify()

    def test_graphql_errors_and_partial_responses_refuse(self):
        for response in ({"data": {"repository": self.evidence}, "errors": [{"message": "missing permission"}]},
                         {"data": {"repository": None}}, {"data": {}}, "invalid JSON"):
            with self.subTest(response=type(response).__name__):
                S.write_json(self.ghdir / "graphql_response.json", response)
                with self.assertRaises(land.LandError):
                    self.classify()


if __name__ == "__main__":
    unittest.main()
