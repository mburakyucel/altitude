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
from unittest import mock

from tests.support import ALT, AltitudeCase, add_worktree, git, make_repo
from altitude import land, state as S


class TestLand(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.ghdir = self.fake_gh()
        self.register("demo", path=self.repo)
        for key, value in (("ALTITUDE_PROJECT", "demo"), ("ALTITUDE_TASK", "fix-x"),
                           ("ALTITUDE_ACTOR", "burak"), ("ALTITUDE_ATTEMPT", "")):
            self.setenv(key, value)
        make_repo(self.repo)
        self.project_repo = self.repo
        self.repo = add_worktree(self.project_repo, "fix-x")
        S.save_task("demo", {"slug": "fix-x", "state": "running", "paths": ["src", "docs/NOTES.md"],
                             "worktree": str(self.repo), "branch": "worktree-fix-x"})
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

    def staged_change(self, name="src/thing.py"):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("changed\n")
        self.git("add", "--", name)
        self.selected_index = self.git("write-tree")

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
        self.assertEqual(self.git("write-tree"), self.selected_index)
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
        self.git("commit", "-q", "-m", "ci")

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
        with self.assertRaisesRegex(land.LandError, "main"):
            land.land("msg", cwd=self.project_repo)

    def test_rebase_in_progress_refuses(self):
        self.staged_change()
        Path(self.git("rev-parse", "--git-path", "rebase-merge").strip()).mkdir()
        with self.assertRaisesRegex(land.LandError, "rebase is in progress"):
            land.land("msg", cwd=self.repo)
        self.assertEqual(self.git("write-tree"), self.selected_index)

    def test_needed_file_outside_expected_paths_is_selected_without_private_file(self):
        self.staged_change("needed.txt")
        private = self.repo / "private.txt"
        private.write_text("Unpublished private notes\n")
        result = land.land("fix: selected file", cwd=self.repo, wait=0)
        self.assertEqual(result["staged"], ["needed.txt"])
        self.assertEqual(self.git("show", "HEAD:needed.txt"), "changed\n")
        self.assertNotIn("private.txt", self.git("ls-tree", "-r", "--name-only", "HEAD").splitlines())
        self.assertEqual(private.read_text(), "Unpublished private notes\n")
        self.assertIn("?? private.txt", self.git("status", "--short"))

    def test_non_l2_automated_actors_cannot_land(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        result = land.land("fix: current publisher", cwd=self.repo, wait=0)
        self.assertEqual(result["pr"], 101)
        self.assertEqual(result["staged"], ["src/thing.py"])

    def test_wrong_task_checkout_refuses_before_mutation_and_preserves_selection(self):
        self.staged_change()
        original = S.load_task("demo", "fix-x")
        commands = self.record_commands()
        for fields in ({"worktree": None}, {"worktree": str(self.tmp / "other-worktree")},
                       {"branch": "worktree-other"}):
            with self.subTest(fields=fields):
                S.save_task("demo", {**original, **fields})
                with self.assertRaisesRegex(land.LandError, "isolated worktree"):
                    land.land("must refuse", cwd=self.repo, wait=0)
        S.save_task("demo", original)
        other_repo = self.clone("unrelated-project-checkout")
        with mock.patch.object(land.config, "project_path", return_value=other_repo):
            with self.assertRaisesRegex(land.LandError, "isolated worktree"):
                land.land("must refuse", cwd=self.repo, wait=0)
        self.assert_no_publish_mutation(commands)

    def test_another_active_tasks_branch_refuses_before_publication(self):
        self.staged_change()
        S.save_task("demo", {"slug": "other", "state": "running", "branch": "worktree-fix-x"})
        commands = self.record_commands()
        with self.assertRaisesRegex(land.LandError, "already belongs to task other"):
            land.land("must refuse", cwd=self.repo, wait=0)
        self.assert_no_publish_mutation(commands)

    def test_current_task_cannot_publish_from_another_tasks_worktree(self):
        other = add_worktree(self.project_repo, "other")
        S.save_task("demo", {"slug": "other", "state": "running", "worktree": str(other),
                             "branch": "worktree-other"})
        (other / "selected.txt").write_text("Other task's selected work\n")
        git("add", "selected.txt", cwd=other)
        selected = git("write-tree", cwd=other)
        head = git("rev-parse", "HEAD", cwd=other)
        commands = self.record_commands()
        with self.assertRaisesRegex(land.LandError, "isolated worktree"):
            land.land("must refuse", cwd=other, wait=0)
        self.assertEqual(git("write-tree", cwd=other), selected)
        self.assertEqual(git("rev-parse", "HEAD", cwd=other), head)
        self.assertEqual(self.remote_heads(), ["main"])
        self.assertFalse(any(args[0] == "gh" or args[:2] in
                             (["git", "fetch"], ["git", "commit"], ["git", "push"]) for args in commands))

    def test_another_active_tasks_pr_refuses_before_selected_commit_or_push(self):
        self.staged_change()
        first = land.land("First reviewed publication", cwd=self.repo, wait=0)
        self.staged_change("src/second.py")
        S.save_task("demo", {"slug": "other", "state": "running", "prs": [first["pr"]]})
        commands = self.record_commands()
        with self.assertRaisesRegex(land.LandError, "already belongs to task other"):
            land.land("must refuse", cwd=self.repo, wait=0)
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), first["head"])
        self.assertEqual(git("rev-parse", "worktree-fix-x", cwd=self.remote).strip(), first["head"])
        self.assertFalse(any(args[:2] in (["git", "commit"], ["git", "push"], ["gh", "pr"])
                             and (args[0] == "git" or args[2] in ("create", "edit", "merge"))
                             for args in commands))

    def test_manual_history_and_old_labels_publish_without_message_repair(self):
        messages = ("Reviewed manual commit", "Reviewed inherited commit\n\nAltitude-Task: demo/other")
        commits = []
        for index, message in enumerate(messages):
            self.staged_change(f"src/manual-{index}.py")
            self.git("commit", "-q", "-m", message)
            commits.append(self.git("rev-parse", "HEAD").strip())
        self.staged_change()
        result = land.land("Publish reviewed history", cwd=self.repo, wait=0)
        self.assertEqual(result["pr"], 101)
        for commit, message in zip(commits, messages):
            self.assertEqual(git("show", "-s", "--format=%B", commit, cwd=self.remote).strip(), message)
            self.git("merge-base", "--is-ancestor", commit, "origin/worktree-fix-x")
        self.assertTrue(land.land("Resume delivery", cwd=self.repo, wait=0, merge=True)["merged"])

    def test_wrong_pr_target_refuses_before_commit_push_or_edit(self):
        self.staged_change()
        first = land.land("First reviewed publication", cwd=self.repo, wait=0)
        self.staged_change("src/second.py")
        pull = json.loads((self.ghdir / "pr.json").read_text())
        commands = self.record_commands()
        for change in ({"baseRefName": "release"}, {"headRefName": "proposal/other"}):
            with self.subTest(change=change):
                S.write_json(self.ghdir / "pr.json", {**pull, **change})
                with self.assertRaisesRegex(land.LandError, "not the expected"):
                    land.land("must refuse", cwd=self.repo, wait=0, pr_title="Must not edit")
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), first["head"])
        self.assertEqual(git("rev-parse", "worktree-fix-x", cwd=self.remote).strip(), first["head"])
        self.assertFalse(any(args[:2] in (["git", "commit"], ["git", "push"])
                             or args[:3] in (["gh", "pr", "create"], ["gh", "pr", "edit"], ["gh", "pr", "merge"])
                             for args in commands))

    def test_happy_path(self):
        self.staged_change("src/has space.py")
        self.staged_change("src/a[1].py")  # literal filename selected in the index
        self.staged_change("docs/NOTES.md")
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
            "fix: land the thing\n\nlonger body")
        remote_sha = git("rev-parse", "worktree-fix-x", cwd=self.remote).strip()
        self.assertEqual(remote_sha, self.git("rev-parse", "HEAD").strip())
        self.assertEqual(res["head"], remote_sha)
        creates = [a for a in self.gh_log() if a[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][creates[0].index("--title") + 1], "fix: land the thing")

    def test_idempotent_rerun(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        commands = self.closing_link_fixture([])
        (S.task_dir("demo", "fix-x") / "request.md").write_text("Design one part of GitHub issue #42.")
        body = self.tmp / "partial.md"
        body.write_text("Addresses #42; remaining implementation and operator acceptance are pending.\n")
        result = land.land("docs: partial design", cwd=self.repo, wait=0, pr_body_file=str(body), merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(self.pr_bodies, [body.read_text()])
        self.assertFalse(any(args[:2] == ["gh", "issue"] for args in commands))

    def test_missing_or_foreign_closing_links_refuse_merge(self):
        self.staged_change()
        links = []
        commands = self.closing_link_fixture(links)
        for response in ([], [{"url": "https://github.com/other/widget/issues/42"}],
                         [{"url": "https://github.com/acme/widget/issues/43"}]):
            links[:] = response
            with self.subTest(response=response), self.assertRaisesRegex(land.LandError, "lacks GitHub closing links"):
                land.land("fix: missing link", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_removed_closing_link_before_merge_refuses_merge(self):
        self.staged_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}],
                                             remove_after_validation=True)
        with self.assertRaisesRegex(land.LandError, "lacks GitHub closing links"):
            land.land("fix: scope changed", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertTrue(self.observed_checks)
        self.assertTrue(self.validated_links)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_merged_retry_routes_missing_closure_to_l3(self):
        self.staged_change()
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
        self.staged_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}])
        with self.assertRaisesRegex(land.LandError, "must target GitHub's default branch"):
            land.land("fix: release only", cwd=self.repo, wait=0, base="release", closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_closing_link_does_not_bypass_hold_or_failed_checks(self):
        self.staged_change()
        commands = self.closing_link_fixture([{"url": "https://github.com/acme/widget/issues/42"}])
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}]')
        result = land.land("fix: linked but red", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(result["merged"])
        self.hold_merge()
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("fix: held", cwd=self.repo, wait=0, closes_issues=[42], merge=True)
        self.assertFalse(any(args[:3] == ["gh", "pr", "merge"] for args in commands))

    def test_closes_issue_cli_rejects_invalid_numbers_before_publication(self):
        self.staged_change()
        for number in ("0", "-1", "42"):
            result = subprocess.run([str(ALT), "land", "--message", "fix: linked", "--wait", "0",
                                     "--closes-issue", number, "--merge"], cwd=self.repo,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lacks GitHub closing links" if number == "42" else "positive issue number", result.stderr)
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args in self.gh_log()))

    def test_rebased_push_retries_with_recorded_tip_lease_exactly_once(self):
        self.staged_change("src/original.py")
        self.git("commit", "-q", "-m", "original")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        recorded_tip = self.git("rev-parse", "origin/worktree-fix-x").strip()
        self.git("commit", "--amend", "-q", "-m", "rebased")
        self.staged_change()
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
        self.staged_change()
        self.git("commit", "-q", "-m", "original")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        recorded_tip = self.git("rev-parse", "origin/worktree-fix-x").strip()
        self.git("commit", "--amend", "-q", "-m", "rebased")
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
        self.staged_change()
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
        self.staged_change()

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
        self.staged_change()
        pushed_head = "a" * 40
        self.record_commands(lambda args: subprocess.CompletedProcess(args, 0, pushed_head + "\n", "")
                             if args == ["git", "rev-parse", "origin/worktree-fix-x"] else None)
        with self.assertRaisesRegex(land.LandError, "head moved"):
            land.land("fix: report remote", cwd=self.repo, wait=0)

    def test_wait_zero_reports_pending_without_waiting(self):
        self.staged_change()
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
        self.staged_change()
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

    def test_merge_without_resolved_task_refuses(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            self.setenv(key, None)
        self.staged_change()
        with self.assertRaisesRegex(land.LandError, "worktree-fix-x.*--project"):
            land.land("fix: unresolved", cwd=self.repo, wait=0, merge=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_merge_hold_refuses_dry_run(self):
        self.hold_merge()
        self.staged_change()
        with self.assertRaises(land.LandError):
            land.land("fix: held", cwd=self.repo, merge=True, dry_run=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_merge_without_hold_after_checks_pass(self):
        self.staged_change()
        res = land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(res["merged"])
        self.assertEqual(res["main_run"], {"databaseId": 7, "status": "completed", "conclusion": "success"})
        merge = next(args for args in self.gh_log() if args[:2] == ["pr", "merge"])
        self.assertEqual(merge[:5], ["pr", "merge", "101", "--squash", "--delete-branch"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], self.git("rev-parse", "HEAD").strip())

    def test_changed_pr_head_is_refused_atomically(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}, {"bucket": "pass"}]')
        res = land.land("fix: red", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(res["checks"], "fail")
        self.assertFalse(res["merged"])
        self.assertIsNone(res["main_run"])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_detached_head_refuses(self):
        self.git("checkout", "-q", "--detach")
        self.staged_change()
        with self.assertRaisesRegex(land.LandError, "detached"):
            land.land("msg", cwd=self.repo, wait=0)
        self.assertEqual(self.git("write-tree"), self.selected_index)

    def test_refuses_when_branch_equals_base(self):
        self.git("checkout", "-q", "-b", "develop")
        self.staged_change()
        with self.assertRaisesRegex(land.LandError, "develop"):
            land.land("msg", cwd=self.repo, wait=0, base="develop")
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.remote_heads(), ["main"])

    def test_preexisting_remote_divergence_is_replaced_without_rebasing(self):
        # a real diverging remote: same branch, same file, different content in a second clone
        seed = self.repo / "src" / "f.py"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("base\n")
        self.git("add", "src/f.py")
        self.git("commit", "-q", "-m", "seed")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        other = self.clone("other")
        git("checkout", "-q", "worktree-fix-x", cwd=other)
        (other / "src" / "f.py").write_text("remote\n")
        git("add", "src/f.py", cwd=other)
        git("commit", "-q", "-m", "remote change", cwd=other)
        git("push", "-q", cwd=other)
        seed.write_text("local\n")
        self.git("add", "src/f.py")
        res = land.land("fix: conflict", cwd=self.repo, wait=0)
        self.assertEqual(res["head"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(self.git("rev-parse", "origin/worktree-fix-x").strip(), res["head"])
        # No pull/rebase state was created, so an idempotent follow-up remains safe.
        again = land.land("fix: conflict again", cwd=self.repo, wait=0)
        self.assertEqual(again["head"], res["head"])
        self.assertEqual(self.git("log", "--all", "-S", "<<<<<<<", "--oneline").strip(), "")

    def test_strictly_behind_branch_is_not_force_rewound(self):
        self.staged_change("src/f.py")
        self.git("commit", "-q", "-m", "seed")
        self.git("push", "-q", "-u", "origin", "worktree-fix-x")
        local_tip = self.git("rev-parse", "HEAD").strip()
        other = self.clone("other-behind")
        git("checkout", "-q", "worktree-fix-x", cwd=other)
        (other / "src" / "remote.py").write_text("foreign\n")
        git("add", "src/remote.py", cwd=other)
        git("commit", "-q", "-m", "foreign", cwd=other)
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

    def test_empty_expected_paths_publish_only_selected_content_through_cli(self):
        task = S.load_task("demo", "fix-x")
        task["paths"] = []
        S.save_task("demo", task)
        self.staged_change()
        private = self.repo / "private.txt"
        private.write_text("Unpublished notes\n")
        run = subprocess.run([sys.executable, str(ALT), "land", "--message", "msg", "--wait", "0"],
                             cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["staged"], ["src/thing.py"])
        self.assertEqual(private.read_text(), "Unpublished notes\n")
        self.assertNotIn("private.txt", self.git("ls-tree", "-r", "--name-only", "HEAD"))

    def test_staged_rename_can_cross_expected_paths(self):
        self.staged_change("src/keep.py")
        self.git("commit", "-q", "-m", "seed")
        self.git("mv", "src/keep.py", "needed.py")
        result = land.land("move selected file", cwd=self.repo, wait=0)
        self.assertEqual(result["pr"], 101)
        self.assertEqual(self.git("show", "HEAD:needed.py"), "changed\n")
        self.assertFalse((self.repo / "src/keep.py").exists())

    def test_partial_selection_commits_index_bytes_and_keeps_later_work(self):
        self.staged_change("src/value.py")
        self.git("commit", "-q", "-m", "seed")
        target = self.repo / "src/value.py"
        target.write_text("selected hunk\nunchanged line\n")
        self.git("add", "src/value.py")
        selected = self.git("show", ":src/value.py")
        target.write_text("selected hunk\nworking hunk remains private\n")
        working = target.read_bytes()
        result = land.land("selected hunk only", cwd=self.repo, wait=0)
        self.assertEqual(result["staged"], ["src/value.py"])
        self.assertEqual(self.git("show", "HEAD:src/value.py"), selected)
        self.assertEqual(target.read_bytes(), working)
        self.assertEqual(self.git("diff", "--cached"), "")
        self.assertIn("working hunk remains private", self.git("diff"))
        self.assertEqual(git("show", "worktree-fix-x:src/value.py", cwd=self.remote), selected)

    def test_selected_literal_names_keep_leading_space_and_newline(self):
        names = [" leading.txt", "src/line\nbreak.txt"]
        for name in names:
            self.staged_change(name)
        result = land.land("literal selected names", cwd=self.repo, wait=0)
        self.assertEqual(result["staged"], names)
        for name in names:
            self.assertEqual(self.git("show", f"HEAD:{name}"), "changed\n")

    def test_unknown_check_bucket_is_an_error_not_a_pass(self):
        self.staged_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "neutral"}]')
        with self.assertRaisesRegex(land.LandError, "neutral"):
            land.land("fix: odd", cwd=self.repo, wait=0, merge=True)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    # Where no workflow and no check exist, the exact merge candidate's local suite is the gate. Absence,
    # skipped checks, stale revisions, and unreadable test reports never become green.

    def test_no_ci_merges_after_a_green_local_suite_and_records_the_count(self):
        self.staged_change()
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

    def local_policy(self, *, exit_code=0, output="Ran 12 tests in 0.4s\n\nOK\n"):
        self.patch(land.config, "LOCAL_CHECK_REPOSITORY", "team/demo")
        (self.repo / "web").mkdir(exist_ok=True)
        (self.repo / "web/package.json").write_text('{"packageManager":"pnpm@10.34.5"}')
        self.git("add", "web/package.json")
        self.fake_runner("pnpm", 0, "frozen dependencies installed\n")
        self.fake_runner("make", exit_code, output)
        self.pr_body = "The reviewed change.\n"

        def summary(args):
            if args[:2] == ["gh", "pr"] and args[2] == "view" and args[-2:] == ["--json", "body"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"body": self.pr_body}), "")
            if args[:3] == ["gh", "pr", "edit"] and "--body-file" in args:
                self.pr_body = Path(args[args.index("--body-file") + 1]).read_text()
            return None

        return self.record_commands(summary)

    def test_minimal_service_path_installs_candidate_pin_and_runs_full_gate(self):
        from tests.test_toolchain import nvm_fixture
        self.local_policy()
        self.staged_change()
        node_bin = nvm_fixture(self)
        self.fake_runner("pnpm", script=(
            "import subprocess\n"
            "assert json.load(open('package.json'))['packageManager'] == 'pnpm@10.34.5'\n"
            "assert '--frozen-lockfile' in sys.argv and '--store-dir' in sys.argv\n"
            "assert subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.21.0'\n"
            "print('candidate-pinned install')\n"))
        (self.tmp / "bin/pnpm").replace(node_bin / "pnpm")
        self.fake_runner("make", script=(
            "import subprocess\n"
            "assert sys.argv[1:] == ['check'] and os.environ['CI'] == 'true'\n"
            "assert subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.21.0'\n"
            "print('Ran 12 tests in 0.4s\\n\\nOK')\n"))
        result = land.land("fix: toolchain discovery", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"], result)
        self.assertTrue(result["local_tests"]["passed"])
        calls = self.runner_calls()
        self.assertEqual(Path(calls[0]["cwd"]), Path(calls[1]["cwd"]) / "web")
        self.assertEqual(calls[0]["argv"][:2], ["pnpm", "install"])
        self.assertEqual(result["local_tests"]["tree"], self.git("rev-parse", "HEAD^{tree}").strip())

    def test_project_local_policy_replaces_historical_failure_and_uses_full_candidate(self):
        self.local_policy()
        self.staged_change()
        self.advance_base("src/integration.py")
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}]')
        (self.ghdir / "runs.json").write_text('[{"databaseId": 7, "conclusion": "failure"}]')
        # The selected policy also handles a workflow still present on the pinned base.
        self.advance_base(".github/workflows/ci.yml", "on: [push]\n")
        self.fake_runner("make", script=(
            "import subprocess\n"
            "assert os.environ.get('CI') == 'true'\n"
            "assert os.path.exists('src/thing.py') and os.path.exists('src/integration.py')\n"
            "assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()\n"
            "os.makedirs('web/ui-artifacts/report/data', exist_ok=True)\n"
            "open('web/ui-artifacts/report/index.html', 'w').write('<img src=\"data/state.png\">')\n"
            "open('web/ui-artifacts/report/data/state.png', 'wb').write(b'fictional screenshot')\n"
            "os.makedirs('web/ui-artifacts/results', exist_ok=True)\n"
            "open('web/ui-artifacts/results/state.png', 'wb').write(b'fictional screenshot')\n"
            "os.makedirs('web/ui-artifacts/browser-config', exist_ok=True)\n"
            "print('Ran 12 tests in 0.4s\\n\\nOK')\n"))
        result = land.land("fix: local project policy", cwd=self.repo, wait=0, merge=True, test_cmd="false")
        tests = result["local_tests"]
        self.assertTrue(result["merged"])
        self.assertEqual(result["checks"], "local-pass")
        self.assertIsNone(result["main_run"])
        self.assertEqual(self.runner_log()[-1], ["make", "check"])
        self.assertIn("--frozen-lockfile", self.runner_log()[0])
        self.assertEqual(tests["head"], result["head"])
        self.assertEqual(self.git("rev-parse", tests["candidate"] + "^{tree}").strip(), tests["tree"])
        evidence = Path(tests["evidence"])
        self.assertEqual(json.loads((evidence / "result.json").read_text()), tests)
        self.assertIn("Ran 12 tests", (evidence / "check.log").read_text())
        report = evidence / "ui-artifacts/report"
        self.assertEqual((report / "index.html").read_text(), '<img src="data/state.png">')
        self.assertEqual((report / "data/state.png").read_bytes(), b"fictional screenshot")
        self.assertEqual([p.name for p in (evidence / "ui-artifacts").iterdir()], ["report"])
        self.assertIn(f"Tests: make check passed locally ({tests['candidate']})", self.pr_body)
        self.assertIn(f"base {tests['base']}, head {tests['head']}", self.pr_body)
        self.assertFalse(Path(self.runner_calls()[-1]["cwd"]).exists())

    def test_local_policy_is_selected_only_for_the_configured_repository(self):
        self.staged_change()
        self.configure_ci()
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}]')
        result = land.land("fix: ordinary hosted gate", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "fail")
        self.assertFalse(result["merged"])
        self.assertEqual(self.runner_log(), [])

    def test_project_local_failure_retains_evidence_without_passing_summary(self):
        self.local_policy(exit_code=1, output="Ran 12 tests in 0.4s\n\nFAILED (failures=1)\n")
        self.staged_change()
        result = land.land("fix: failing local check", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["checks"], "local-fail")
        evidence = Path(result["local_tests"]["evidence"])
        self.assertIn("FAILED", (evidence / "check.log").read_text())
        self.assertFalse((evidence / "ui-artifacts").exists())
        self.assertFalse(json.loads((evidence / "result.json").read_text())["passed"])
        self.assertNotIn("Tests:", self.pr_body)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_project_local_timeout_retains_output_and_failed_candidate_identity(self):
        self.local_policy()
        self.fake_runner("make", script=(
            "import time\n"
            "print('phase: Python complete\\nRan 12 tests in 0.4s', flush=True)\n"
            "sys.stderr.buffer.write(b'phase: web stalled \\xe2\\x82'); sys.stderr.flush()\n"
            "time.sleep(10)\n"))
        self.staged_change()
        with mock.patch.object(land, "LOCAL_TEST_TIMEOUT", 0.5):
            result = land.land("fix: retain timeout evidence", cwd=self.repo, wait=0, merge=True)
        tests = result["local_tests"]
        self.assertFalse(result["merged"])
        self.assertEqual(result["checks"], "local-fail")
        self.assertFalse(tests["passed"])
        self.assertIsNone(tests["returncode"])
        self.assertIsNone(tests["tests"])
        self.assertEqual(tests["head"], result["head"])
        self.assertEqual(self.git("rev-parse", tests["candidate"] + "^{tree}").strip(), tests["tree"])
        evidence = Path(tests["evidence"])
        self.assertEqual(json.loads((evidence / "result.json").read_text()), tests)
        output = (evidence / "check.log").read_text()
        self.assertIn("phase: Python complete\nRan 12 tests", output)
        self.assertIn("phase: web stalled \ufffd", output)
        self.assertIn("timed out", output)
        self.assertFalse(Path(self.runner_calls()[-1]["cwd"]).exists())
        self.assertNotIn("Tests:", self.pr_body)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_local_timeout_without_output_still_records_failure(self):
        self.fake_runner("quiet-suite", script="import time\ntime.sleep(10)\n")
        log = self.tmp / "check.log"
        with mock.patch.object(land, "LOCAL_TEST_TIMEOUT", 0.1):
            result = land._local_suite(self.repo, "quiet-suite", log=log)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["returncode"])
        self.assertIn("timed out", log.read_text())

    def test_project_local_browser_failure_retains_report_attachments_without_raw_duplicates(self):
        self.local_policy()
        self.fake_runner("make", script=(
            "from pathlib import Path\n"
            "report = Path('web/ui-artifacts/report')\n"
            "(report / 'data').mkdir(parents=True)\n"
            "(report / 'trace').mkdir()\n"
            "(report / 'trace/index.html').write_text('trace viewer')\n"
            "(report / 'index.html').write_text('<img src=\"data/failure.png\"><a href=\"data/trace.zip\">trace</a>')\n"
            "for name, data in [('failure.png', b'failure screenshot'), ('trace.zip', b'failure trace')]:\n"
            "    (report / 'data' / name).write_bytes(data)\n"
            "    raw = Path('web/ui-artifacts/results')\n"
            "    raw.mkdir(exist_ok=True)\n"
            "    (raw / name).write_bytes(data)\n"
            "print('Ran 12 tests in 0.4s\\n\\nFAILED (failures=1)')\n"
            "sys.exit(1)\n"))
        self.staged_change()
        result = land.land("fix: retain failed browser evidence", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["checks"], "local-fail")
        evidence = Path(result["local_tests"]["evidence"])
        report = evidence / "ui-artifacts/report"
        self.assertIn('src="data/failure.png"', (report / "index.html").read_text())
        self.assertIn('href="data/trace.zip"', (report / "index.html").read_text())
        self.assertEqual((report / "data/failure.png").read_bytes(), b"failure screenshot")
        self.assertEqual((report / "data/trace.zip").read_bytes(), b"failure trace")
        self.assertEqual((report / "trace/index.html").read_text(), "trace viewer")
        self.assertEqual([p.name for p in (evidence / "ui-artifacts").iterdir()], ["report"])
        self.assertIn("FAILED", (evidence / "check.log").read_text())
        self.assertFalse(json.loads((evidence / "result.json").read_text())["passed"])
        self.assertFalse(Path(self.runner_calls()[-1]["cwd"]).exists())
        self.assertNotIn("Tests:", self.pr_body)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_project_local_install_failure_does_not_run_or_claim_tests(self):
        self.local_policy()
        self.fake_runner("pnpm", 1, "installation failed\n")
        self.staged_change()
        result = land.land("fix: missing prerequisites", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["checks"], "local-fail")
        self.assertIn("installation", result["local_tests"]["error"])
        evidence = Path(result["local_tests"]["evidence"])
        self.assertIn("installation failed", (evidence / "install.log").read_text())
        self.assertEqual(json.loads((evidence / "result.json").read_text()), result["local_tests"])
        self.assertEqual(len(self.runner_log()), 1)
        self.assertNotIn("Tests:", self.pr_body)

    def test_project_local_gate_tests_held_pr_without_merging(self):
        self.local_policy()
        self.staged_change()
        task = S.load_task("demo", "fix-x")
        task["hold_merge"] = "operator UX review"
        S.save_task("demo", task)
        result = land.land("fix: held local check", cwd=self.repo, wait=0)
        self.assertEqual(result["checks"], "local-pass")
        self.assertFalse(result["merged"])
        self.assertIn("Tests:", self.pr_body)
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("fix: held local check", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_project_local_gate_rechecks_base_and_head_after_tests(self):
        for moving_ref in ("main", "worktree-fix-x"):
            with self.subTest(ref=moving_ref):
                self.local_policy()
                self.staged_change()
                real = land._local_suite

                def moving(cwd, command, **kwargs):
                    result = real(cwd, command, **kwargs)
                    other = self.clone("moved-" + moving_ref)
                    git("checkout", "-q", moving_ref, cwd=other)
                    (other / (moving_ref + ".txt")).write_text("late change\n")
                    git("add", ".", cwd=other)
                    git("commit", "-q", "-m", "move", cwd=other)
                    git("push", "-q", "origin", moving_ref, cwd=other)
                    return result

                with mock.patch.object(land, "_local_suite", side_effect=moving):
                    result = land.land("fix: reject stale pair", cwd=self.repo, wait=0, merge=True)
                self.assertFalse(result["merged"])
                self.assertEqual(result["checks"], "local-fail")
                self.assertIn("moved", result["local_tests"]["error"])
                self.assertNotIn("Tests:", self.pr_body)
                evidence = Path(result["local_tests"]["evidence"]) / "result.json"
                self.assertIn("moved", json.loads(evidence.read_text())["error"])

    def test_project_local_gate_preserves_late_hold_and_required_reviews(self):
        self.local_policy()
        self.staged_change()
        real = land._local_suite

        def hold(cwd, command, **kwargs):
            result = real(cwd, command, **kwargs)
            task = S.load_task("demo", "fix-x")
            task["hold_merge"] = "operator security review"
            S.save_task("demo", task)
            return result

        with mock.patch.object(land, "_local_suite", side_effect=hold):
            with self.assertRaisesRegex(land.LandError, "merge hold"):
                land.land("fix: late hold", cwd=self.repo, wait=0, merge=True)
        task = S.load_task("demo", "fix-x")
        task["hold_merge"] = None
        S.save_task("demo", task)
        pr = json.loads((self.ghdir / "pr.json").read_text())
        pr["reviewDecision"] = "CHANGES_REQUESTED"
        (self.ghdir / "pr.json").write_text(json.dumps(pr))
        with self.assertRaisesRegex(land.LandError, "outstanding required reviews"):
            land.land("fix: review refusal", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_project_local_gate_refuses_required_hosted_checks_even_after_passing_tests(self):
        self.local_policy()
        self.staged_change()
        real = land._local_suite

        def require_check(cwd, command, **kwargs):
            result = real(cwd, command, **kwargs)
            evidence = json.loads((self.ghdir / "last_check_evidence.json").read_text())
            evidence["pullRequest"]["baseRef"]["rules"]["nodes"] = [{"type": "REQUIRED_STATUS_CHECKS",
                "parameters": {"requiredStatusChecks": [{"context": "hosted", "integrationId": 1}]}}]
            evidence["pullRequest"]["baseRef"]["rules"]["totalCount"] = 1
            S.write_json(self.ghdir / "check_evidence.json", evidence)
            return result

        with mock.patch.object(land, "_local_suite", side_effect=require_check):
            with self.assertRaisesRegex(land.LandError, "remove hosted required checks"):
                land.land("fix: rule appears", cwd=self.repo, wait=0, merge=True)
        self.assertNotIn("Tests:", self.pr_body)
        self.assertFalse(any(a[:2] == ["pr", "merge"] for a in self.gh_log()))

    def test_the_suite_runs_on_the_merge_candidate_not_on_this_worktree(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", script=runner)
        result = land.land("fix: squash candidate", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 1)

    def test_zero_tests_is_not_a_green_local_gate(self):
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 0 tests in 0.0s\n\nOK\n")
        result = land.land("fix: zero tests", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 0)
        self.assertIn("no passing tests", result["local_tests"]["error"])

    def test_all_skipped_tests_is_not_a_green_local_gate(self):
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.1s\n\nOK (skipped=12)\n")
        result = land.land("fix: all skipped", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 0)
        self.assertEqual(result["local_tests"]["skipped"], 12)
        self.assertIn("no passing tests", result["local_tests"]["error"])

    def test_unittest_expected_failures_are_not_reported_as_passes(self):
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 5 tests in 0.1s\n\nOK (skipped=1, expected failures=2)\n")
        result = land.land("fix: honest count", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 2)
        self.assertEqual(result["local_tests"]["skipped"], 1)
        self.assertEqual(result["local_tests"]["expected_failures"], 2)

    def test_candidate_cleanup_continues_when_git_remove_raises(self):
        self.staged_change()
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
        self.staged_change("src/collision.py")
        self.advance_base("src/collision.py", "incompatible base\n")
        self.no_checks()
        self.fake_runner("make", 0, "Ran 3 tests in 0.1s\n\nOK\n")

        def fail_remove(args):
            if args[:4] == ["git", "worktree", "remove", "--force"] and "alt-land-candidate-" in args[-1]:
                raise land.LandError("simulated cleanup failure")
            return None

        self.record_commands(fail_remove)
        result = land.land("fix: conflict cleanup", cwd=self.repo, wait=0, merge=True)
        self.assertFalse(result["merged"])
        self.assertIn("does not merge cleanly", result["local_tests"]["error"])
        self.assertNotIn("cleanup failed", result["local_tests"]["error"])

    def test_no_ci_red_local_suite_blocks_the_merge(self):
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 1, "Ran 12 tests in 0.4s\n\nFAILED (failures=1)\n")
        result = land.land("fix: red suite", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertFalse(result["merged"])
        self.assertFalse(result["local_tests"]["passed"])
        self.assertEqual(result["local_tests"]["tests"], 12)
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_a_local_suite_that_cannot_run_blocks_rather_than_passes(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK (skipped=2)\n")
        result = land.land("fix: some skips", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 10)
        self.assertEqual(result["local_tests"]["skipped"], 2)

    def test_gh_refusing_with_no_checks_reported_is_the_same_gate(self):
        self.staged_change()
        self.no_checks("")
        self.fake_runner("make", 0, "Ran 3 tests in 0.1s\n\nOK\n")
        result = land.land("fix: nothing reported", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "none-configured")
        self.assertTrue(result["merged"])
        self.assertEqual(result["local_tests"]["tests"], 3)

    def test_test_cmd_override_is_the_command_that_gates(self):
        self.staged_change()
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
        self.staged_change()
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
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: unreported", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_workflows_only_on_the_base_branch_still_count_as_ci(self):
        self.advance_base(".github/workflows/ci.yml", "on: [push]\n")
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: base has ci", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_a_base_that_cannot_be_refreshed_fails_closed(self):
        self.staged_change()
        self.no_checks()
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        git("symbolic-ref", "HEAD", "refs/heads/gone", cwd=self.remote)
        git("branch", "-D", "main", cwd=self.remote)
        with self.assertRaisesRegex(land.LandError, r"origin(?:/| )main"):
            land.land("fix: unreadable base", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(self.runner_log(), [])
        self.assertEqual([a for a in self.gh_log() if a[:2] == ["pr", "merge"]], [])

    def test_skipped_checks_are_not_a_no_ci_repository(self):
        self.staged_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "skipping"}]')
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: skipping", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "skipped")
        self.assertFalse(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_a_pass_with_a_nonrequired_skip_merges_without_local_fallback(self):
        self.staged_change()
        (self.ghdir / "checks.json").write_text('[{"bucket": "pass"}, {"bucket": "skipping"}]')
        self.fake_runner("make", 0, "Ran 12 tests in 0.4s\n\nOK\n")
        result = land.land("fix: half skipped", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "pass")
        self.assertTrue(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_configured_checks_that_pass_merge_without_the_local_suite(self):
        self.configure_ci()
        self.staged_change()
        self.fake_runner("make", 1, "the local suite must not be consulted here\n")
        result = land.land("fix: green ci", cwd=self.repo, wait=0, merge=True)
        self.assertEqual(result["checks"], "pass")
        self.assertTrue(result["merged"])
        self.assertIsNone(result["local_tests"])
        self.assertEqual(self.runner_log(), [])

    def test_rerun_after_merge_does_not_resurrect_the_branch(self):
        self.staged_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        # GitHub deletes the remote head branch on merge; mirror that on the bare remote
        self.git("push", "-q", "origin", "--delete", "worktree-fix-x")
        res = land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(res["merged"])
        self.assertEqual(res["pr"], 101)
        self.assertIsNone(res["commit"])
        self.assertEqual(res["staged"], [])
        self.assertEqual(self.remote_heads(), ["main"])

    def test_merged_followup_requires_verified_merge_evidence(self):
        self.staged_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.staged_change("src/late.py")
        with self.assertRaisesRegex(land.LandError, "previous PR identity, final head or merge is unavailable"):
            land.land("fix: late", cwd=self.repo, wait=0)
        self.assertEqual(self.git("write-tree"), self.selected_index)

    def test_merged_no_op_asks_for_no_checks(self):
        self.staged_change()
        land.land("fix: merge me", cwd=self.repo, wait=0, merge=True)
        self.git("push", "-q", "origin", "--delete", "worktree-fix-x")
        (self.ghdir / "log.jsonl").unlink()  # only the second run's gh traffic is under test
        res = land.land("fix: merge me", cwd=self.repo, wait=0)
        self.assertTrue(res["merged"])
        self.assertEqual(res["checks"], "merged")  # its own value — never reported as a pass
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])

    def test_closed_unmerged_pr_refuses_before_any_mutation(self):
        self.staged_change()
        (self.ghdir / "pr.json").write_text(json.dumps(
            {"number": 55, "url": "https://example.invalid/pr/55", "state": "CLOSED"}))
        head = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(land.LandError, "closed"):
            land.land("fix: onto a closed pr", cwd=self.repo, wait=0)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.remote_heads(), ["main"])  # nothing pushed onto the closed PR's branch
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])

    def test_create_path_reads_the_pr_back_once(self):
        pr = land._ensure_pr(self.repo, "worktree-fix-x", "main", "fix: new", None, None, "demo/fix-x", pr=None)
        self.assertEqual(pr["number"], 101)
        # pr=None is "the caller looked and there is no PR", so no view before the create
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "create"], ["pr", "view"]])

    def test_full_run_rechecks_the_pr_around_check_classification(self):
        self.staged_change()
        land.land("fix: once", cwd=self.repo, wait=0)
        # Prefetch + create read-back + snapshot + the before/after check-state bracket.
        self.assertEqual(len([a for a in self.gh_log() if a[:2] == ["pr", "view"]]), 5)

    def test_gh_404_stops_the_run_before_any_mutation(self):
        self.staged_change()
        head = self.git("rev-parse", "HEAD").strip()
        message = "gh: Not Found (HTTP 404)"
        (self.ghdir / "view_error.txt").write_text(message)
        with self.assertRaises(land.LandError) as cm:
            land.land("fix: no gh", cwd=self.repo, wait=0)
        self.assertIn("gh pr view worktree-fix-x", str(cm.exception))
        self.assertIn(message, str(cm.exception))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertIn("A  src/thing.py", self.git("status", "--short", "--untracked-files=all").splitlines())
        self.assertEqual((self.repo / "src" / "thing.py").read_text(), "changed\n")
        self.assertEqual(self.remote_heads(), ["main"])
        self.assertEqual([a[:2] for a in self.gh_log()], [["pr", "view"]])
        doc = land.__doc__
        self.assertIn("authenticated `gh`", doc)  # the precondition this behaviour is documented by
        self.assertIn("nothing committed", doc)

    def test_dry_run_reports_a_distinct_checks_value(self):
        self.staged_change()
        res = land.land("msg", cwd=self.repo, dry_run=True)
        self.assertEqual(res["checks"], "dry-run")
        self.assertTrue(res["dry_run"])
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.gh_log(), [])

    def test_unresolved_task_refusal_preserves_selection(self):
        for key in ("ALTITUDE_PROJECT", "ALTITUDE_TASK"):
            self.setenv(key, None)
        self.staged_change()
        (self.repo / "anything.txt").write_text("unselected work\n")
        with self.assertRaisesRegex(land.LandError, "--project"):
            land.land("fix: undeclared", cwd=self.repo, wait=0)
        self.assertEqual(self.git("write-tree"), self.selected_index)
        self.assertEqual(self.gh_log(), [])


class TestCheckEvidence(AltitudeCase):
    """Real Git candidates and required/optional results through the shared GitHub transport."""
    git = TestLand.git
    clone = TestLand.clone
    advance_base = TestLand.advance_base
    staged_change = TestLand.staged_change
    workflow_path = ".github/workflows/checks.yml"
    source = ("on: [pull_request, push]\njobs:\n  tests:\n    runs-on: ubuntu-latest\n"
              "    steps:\n      - run: echo tests\n")

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
        self.project_repo = self.repo
        self.repo = add_worktree(self.project_repo, "fix-x")
        S.save_task("demo", {"slug": "fix-x", "state": "running", "paths": ["src", ".github/workflows"],
                             "worktree": str(self.repo), "branch": "worktree-fix-x"})
        for key, value in {"ALTITUDE_PROJECT": "demo", "ALTITUDE_TASK": "fix-x",
                           "ALTITUDE_ACTOR": "burak", "ALTITUDE_ATTEMPT": ""}.items():
            self.setenv(key, value)
        self.staged_change()
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
        self.pair = {"number": 101, "base": "main", "branch": self.pr["headRefName"],
                     "base_sha": self.base, "head_sha": self.head}

    def contexts(self):
        return self.pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]

    def classify(self):
        S.write_json(self.ghdir / "check_evidence.json", self.evidence)
        return land._checks_value(self.repo, 101, self.pair)

    def optional_deploy(self, *, required=False, event="pull_request"):
        check = copy.deepcopy(self.contexts()["nodes"][0])
        check.update(name="deploy", conclusion="SKIPPED", isRequired=required)
        check["checkSuite"].update(app={"databaseId": 15368}, workflowRun={"event": event})
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
        self.git("merge", "--no-ff", "origin/main", "-m", "Incorporate main")
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
                self.assertFalse(land.land("required check missing", cwd=self.repo, wait=0, merge=True)["merged"])

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

    def test_optional_skip_does_not_block_successful_pr_checks(self):
        self.optional_deploy()
        self.assertEqual(self.classify(), "pass")
        result = land.land("applicable checks passed", cwd=self.repo, wait=0, merge=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["checks"], "pass")
        query_calls = [a for a in self.gh_log() if a[:2] == ["api", "graphql"]]
        self.assertTrue(query_calls)
        self.assertTrue(all("owner=team" in a and "repo=demo" in a for a in query_calls))

    def test_adopted_pr_checks_block_then_pass_and_preserve_history(self):
        self.git("push", "-q", "origin", "HEAD:proposal/external")
        pull = json.loads((self.ghdir / "pr.json").read_text())
        pull.update(url="https://github.com/team/demo/pull/101", headRefName="proposal/external",
                    isCrossRepository=False, isDraft=False, reviewDecision="APPROVED")
        S.write_json(self.ghdir / "pr.json", pull)
        land.land("adopt assigned PR", cwd=self.repo, wait=0, adopt_pr=101,
                  expected_head=self.head, reason="Assigned external PR with optional deployment")
        receipt = S.load_task("demo", "fix-x")["adopted_pr"]
        self.refresh()
        self.optional_deploy()
        self.assert_blocked_checks()
        self.assertEqual(self.classify(), "pass")
        (self.ghdir / "merge_git.txt").touch()
        result = land.land("applicable checks passed", cwd=self.repo, wait=0, merge=True)
        self.assertEqual((result["pr"], result["checks"], result["merged"]), (101, "pass", True))
        self.assertEqual(S.load_task("demo", "fix-x")["adopted_pr"], receipt)
        self.assertEqual(self.git("show", "-s", "--format=%P", "origin/main").strip(), f"{self.base} {self.head}")

    def test_policy_required_skipped_job_cannot_be_excluded_by_optional_metadata(self):
        self.optional_deploy()
        base = self.pr["baseRef"]
        for classic in (True, False):
            with self.subTest(classic=classic):
                base["branchProtectionRule"] = {"requiredStatusChecks": [{"context": "deploy", "app": None}]} if classic else None
                base["rules"] = self.connection([] if classic else [{"type": "REQUIRED_STATUS_CHECKS",
                    "parameters": {"requiredStatusChecks": [{"context": "deploy", "integrationId": None}]}}])
                self.assertEqual(self.classify(), "skipped")
                self.assertFalse(land.land("required deploy skipped", cwd=self.repo, wait=0, merge=True)["merged"])

    def test_required_skip_blocks_and_nonrequired_skip_is_provider_independent(self):
        check = self.optional_deploy(required=True)
        self.assertEqual(self.classify(), "skipped")
        check["isRequired"] = False
        check["checkSuite"].update(app={"databaseId": 987}, workflowRun=None)
        check["name"] = "Remote optional check / custom name"
        self.assertEqual(self.classify(), "pass")

    def test_skips_use_requiredness_and_candidate_association_without_workflow_source(self):
        check = self.optional_deploy()
        for event in ("pull_request", "pull_request_target", "push"):
            with self.subTest(event=event):
                check["checkSuite"]["workflowRun"] = {"event": event}
                self.assertEqual(self.classify(), "pass")
        self.assertFalse(any("expression=" in arg for call in self.gh_log() for arg in call))

    def test_skipped_check_with_unknown_requiredness_or_wrong_candidate_refuses(self):
        check = self.optional_deploy()
        for value in (None, "false", 0):
            with self.subTest(requiredness=value):
                check["isRequired"] = value
                with self.assertRaisesRegex(land.LandError, "lacks requiredness"):
                    self.classify()
        check.pop("isRequired")
        with self.assertRaisesRegex(land.LandError, "incomplete"):
            self.classify()
        check["isRequired"] = False
        check["checkSuite"]["commit"]["oid"] = "0" * 40
        with self.assertRaisesRegex(land.LandError, "unrelated"):
            self.classify()

    def test_merge_candidate_accepts_nonrequired_skip_when_base_and_head_diverge(self):
        self.diverge_base()
        self.optional_deploy()
        self.merge_candidate()
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

    def test_all_skipped_jobs_do_not_become_a_green_gate(self):
        skipped = self.optional_deploy()
        self.contexts().update(self.connection([skipped]))
        S.write_json(self.ghdir / "checks.json", [{"bucket": "skipping"}])
        self.assertEqual(self.classify(), "skipped")

    def assert_blocked_checks(self):
        check = self.contexts()["nodes"][0]
        original = copy.deepcopy(check)
        base = self.pr["baseRef"]
        cases = [("COMPLETED", conclusion, "fail") for conclusion in
                 ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE")]
        cases += [("IN_PROGRESS", None, "pending"), ("QUEUED", "SKIPPED", "pending"),
                  ("COMPLETED", "SKIPPED", "skipped"), ("COMPLETED", "NEUTRAL", "skipped"),
                  ("COMPLETED", None, "skipped")]
        for required in (False, True):
            check["isRequired"] = required
            base["branchProtectionRule"] = {"requiredStatusChecks": [
                {"context": check["name"], "app": None}]} if required else None
            for status, conclusion, expected in cases:
                with self.subTest(required=required, status=status, conclusion=conclusion):
                    check.update(status=status, conclusion=conclusion)
                    self.assertEqual(self.classify(), expected)
                    result = land.land("check is not green", cwd=self.repo, wait=0, merge=True)
                    self.assertEqual((result["checks"], result["merged"]), (expected, False))
                    self.assertIsNone(result["local_tests"])
        check.clear()
        check.update(original)
        base["branchProtectionRule"] = None

    def test_optional_skip_does_not_hide_required_or_nonrequired_blockers(self):
        self.optional_deploy()
        self.assert_blocked_checks()

    def test_status_contexts_require_success_and_never_use_the_check_run_skip_exemption(self):
        self.optional_deploy()
        status = {"__typename": "StatusContext", "context": "legacy-tests",
                  "commit": {"oid": self.head}}
        self.contexts().update(self.connection([*self.contexts()["nodes"], status]))
        for required in (False, True):
            status["isRequired"] = required
            self.pr["baseRef"]["branchProtectionRule"] = {"requiredStatusChecks": [
                {"context": "legacy-tests", "app": None}]} if required else None
            for state, expected in (("SUCCESS", "pass"), ("FAILURE", "fail"), ("ERROR", "fail"),
                                    ("PENDING", "pending"), ("SKIPPED", "skipped")):
                with self.subTest(required=required, state=state):
                    status["state"] = state
                    self.assertEqual(self.classify(), expected)

    def test_pending_required_check_waits_then_merges_on_success(self):
        self.optional_deploy()
        check = self.contexts()["nodes"][0]
        check.update(status="IN_PROGRESS", conclusion=None, isRequired=True)
        self.pr["baseRef"]["branchProtectionRule"] = {"requiredStatusChecks": [
            {"context": check["name"], "app": None}]}
        self.assertEqual(self.classify(), "pending")

        def finish_check(_seconds):
            check.update(status="COMPLETED", conclusion="SUCCESS")
            S.write_json(self.ghdir / "check_evidence.json", self.evidence)

        with mock.patch.object(land, "time", wraps=land.time) as clock:
            clock.sleep.side_effect = finish_check
            result = land.land("required check finishes", cwd=self.repo, wait=10, merge=True)
        clock.sleep.assert_called_once()
        self.assertEqual((result["checks"], result["merged"]), ("pass", True))

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
