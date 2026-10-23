"""Routines that act: the fence a routine's form sets, `alt act` through the run's door, the tidy rule Altitude checks
itself, each change's record, event and chat sentence, Undo and what it keeps from later runs, the set-aside folder's
end, L3's narrower authority and the run's unchanged box. Repositories and names are fictional; the run's engine is
scripted at the engine seam."""
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from unittest import mock
from datetime import timedelta
from pathlib import Path

from tests.support import REPO, AltitudeCase, add_worktree, git, make_repo
from tests.test_project_agents import NOW, AgentCase, definition
from altitude import (config, engines, git_policy, l3, platform, project_agents as A, server, state as S,
                      tasks as T, tidy, verify)

HELPER = "agent-7c41e2d0"


class ActingCase(AgentCase):
    def setUp(self):
        super().setUp()
        self.calls = self.launching()
        self.patch(platform, "folder_in_use", return_value=False)
        self.patch(verify, "gh", return_value=[])
        self.requests = 0

    def age(self, *paths: Path, days: int = 10) -> None:
        """Every file and folder under `paths` last changed `days` before NOW."""
        stamp = (NOW - timedelta(days=days)).timestamp()
        for top in paths:
            for folder, dirs, files in os.walk(top):
                for name in [*dirs, *files]:
                    os.utime(os.path.join(folder, name), (stamp, stamp), follow_symlinks=False)
            os.utime(top, (stamp, stamp))

    def helper(self, name: str = HELPER, *, days: int = 10) -> Path:
        """A worktree a coding agent's helper made and left behind: detached at main, with an ignored file."""
        path = self.repo / ".claude" / "worktrees" / name
        git("worktree", "add", "-q", "--detach", str(path), "main", cwd=self.repo)
        (path / ".claude").mkdir()
        (path / ".claude" / "settings.local.json").write_text("{}\n")  # ignored: `.claude/` is in .gitignore
        self.age(path, self.repo / ".git" / "worktrees" / name, days=days)
        return path

    def branch(self, name: str, *, days: int = 10, commit: bool = False) -> str:
        """A local branch made `days` before NOW at main, or one commit past it."""
        stamp = f"@{int((NOW - timedelta(days=days)).timestamp())} +0000"
        env = {**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
        subprocess.run(["git", "branch", name, "main"], cwd=self.repo, env=env, check=True, capture_output=True)
        if commit:
            tree = git("rev-parse", "main^{tree}", cwd=self.repo).strip()
            sha = subprocess.run(["git", "commit-tree", tree, "-p", "main", "-m", "fictional work"], cwd=self.repo,
                                 env=env, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "update-ref", f"refs/heads/{name}", sha], cwd=self.repo, env=env, check=True)
        return git("rev-parse", f"refs/heads/{name}", cwd=self.repo).strip()

    def running(self, **fields) -> str:
        agent = self.agent(**{"may": ["worktrees"], **fields})
        self.change("run", agent=agent)
        A.start_next(self.project, NOW)
        self.assertEqual(self.runs(agent)[-1]["state"], "running")
        return agent

    def finish(self) -> None:
        """The running run ends with a report, so the next can start."""
        self.ended("Nothing: checked.")
        A.reconcile(self.project, NOW)

    def act(self, agent: str, action: str, target: str, *, n: int = 1, request: str | None = None) -> str:
        self.requests += 1
        return A.act(self.project, f"{agent}/{n}", {"project": self.project, "run": f"{agent}/{n}", "kind": "act",
                                                    "action": action, "target": target,
                                                    "request": request or f"request{self.requests:04d}"}, now=NOW)["said"]

    def rows(self, agent: str, n: int = 1) -> list[dict]:
        return A.run_view(self.project, agent, n, now=NOW)["changes"]

    def events(self, kind: str) -> list[dict]:
        return [e for e in S.read_project_log(self.project, None) if e["kind"] == kind]


class TestTheFence(ActingCase):
    def test_a_routine_with_the_permission_sets_a_worktree_aside_and_undo_puts_it_back_exactly(self):
        path = self.helper()
        agent = self.running()
        said = self.act(agent, "set-aside-worktree", HELPER)
        self.assertEqual(said, f"Set aside worktree {HELPER}. The operator can undo it until Oct 28.")
        self.assertFalse(path.exists())
        aside = self.repo / ".claude" / "worktrees" / ".set-aside" / f"{agent}-1-1" / HELPER
        self.assertTrue((aside / ".claude" / "settings.local.json").is_file(), "set aside on the same disk, whole")
        self.assertEqual(self.rows(agent), [{
            "n": 1, "kind": "worktree", "outcome": "done", "head": "Set aside worktree", "name": HELPER,
            "note": "Removed for good Oct 28 unless you undo it.", "undone": False, "undo": True, "refused": None}])
        self.assertEqual([(e["agent"], e["run"], e["change"], e["what"], e["target"], e["outcome"])
                          for e in self.events("routine-action")], [(agent, 1, 1, "worktree", HELPER, "done")])

        receipt = self.change("undo", agent=agent, n=1, change=1)
        self.assertEqual((receipt["undone"], receipt["refused"]), ([1], []))
        self.assertEqual((path / ".claude" / "settings.local.json").read_text(), "{}\n", "ignored files come back too")
        self.assertIn(str(path), git("worktree", "list", cwd=self.repo))
        row = self.rows(agent)[0]
        self.assertEqual((row["undone"], row["undo"], row["note"]),
                         (True, False, "Put back Oct 14. This routine leaves it alone from now on."))
        self.assertEqual([e["outcome"] for e in self.events("routine-undo")], ["undone"])
        self.age(path, self.repo / ".git" / "worktrees" / HELPER)
        self.assertEqual(self.act(agent, "set-aside-worktree", HELPER), "Kept: You put it back Oct 14.",
                         "what the operator put back stays put")

    def test_a_routine_that_only_reports_is_refused_with_a_plain_reason_and_nothing_changes(self):
        path = self.helper()
        agent = self.running(may=[])
        self.assertEqual(A.door(self.project, f"{agent}/1", {"project": self.project, "run": f"{agent}/1",
                                                            "kind": "act", "action": "delete-branch", "target": "x",
                                                            "request": "door-request"})["done"], False,
                         "the run's door carries alt act")
        self.assertEqual(self.act(agent, "set-aside-worktree", HELPER),
                         "Not allowed: this routine only reports; the operator can let it change worktrees and "
                         "branches on its form.")
        self.assertTrue(path.is_dir())
        self.assertEqual([(r["head"], r["name"], r["note"], r["undo"]) for r in self.rows(agent)],
                         [("Not allowed: delete branch", "x", "This routine only reports.", False),
                          ("Not allowed: set aside worktree", HELPER, "This routine only reports.", False)])
        self.assertEqual([e["outcome"] for e in self.events("routine-action")], ["refused", "refused"])
        self.assertIsNone(A.run_view(self.project, agent, 1, now=NOW)["line"])

    def test_the_fence_is_the_version_the_run_ran_with_narrowed_by_later_changes(self):
        self.helper()
        widened = self.running(may=[], name="Widened")
        version = self.record()["agents"][-1]["version"]
        self.change("set", agent=widened, version=version, definition=definition(name="Widened", may=["worktrees"]))
        self.assertTrue(self.act(widened, "set-aside-worktree", HELPER).startswith("Not allowed"),
                        "a permission added during a run applies from the next run")
        self.finish()
        narrowed = self.running(name="Narrowed")
        version = self.record()["agents"][-1]["version"]
        self.change("set", agent=narrowed, version=version, definition=definition(name="Narrowed", may=[]))
        self.assertTrue(self.act(narrowed, "set-aside-worktree", HELPER).startswith("Not allowed"),
                        "a permission taken away applies at once")

    def test_a_run_makes_at_most_twenty_requests_and_a_repeated_request_gets_its_first_answer(self):
        agent = self.running()
        for n in range(A.ACTS):
            self.assertTrue(self.act(agent, "delete-branch", f"missing-{n}").startswith("Kept: It is not one of"))
        self.assertEqual(self.act(agent, "delete-branch", "missing-x"), "Not allowed: this run used its 20 requests.")
        self.assertEqual(self.act(agent, "delete-branch", "missing-y"), "Not allowed: this run used its 20 requests.")
        self.assertEqual(len(self.rows(agent)), A.ACTS + 1, "requests past the limit leave no rows")
        self.assertEqual(self.act(agent, "delete-branch", "other", request="request0001"),
                         "Kept: It is not one of this project's local branches.")
        self.assertEqual(len(self.rows(agent)), A.ACTS + 1)

    def test_a_request_names_only_what_altitude_lists_itself(self):
        self.helper()
        agent = self.running()
        copy = f"project-agent-{agent}-1"
        for target in (copy, "..", "../..", str(self.repo), "main", f"{HELPER}/..", f"./{HELPER}", "-f", "--all"):
            with self.subTest(target=target):
                self.assertTrue(self.act(agent, "set-aside-worktree", target).startswith(
                    "Kept: It is not one of this project's worktrees"))
        self.assertTrue((self.repo / ".claude" / "worktrees" / copy).is_dir())
        for bad in ("", "x" * 301, "a\nb"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Ask with alt act"):
                self.act(agent, "set-aside-worktree", bad)
        with self.assertRaisesRegex(ValueError, "Ask with alt act"):
            self.act(agent, "run-command", "make clean")


class TestTheTidyRule(ActingCase):
    def kept(self, agent: str, target: str) -> str:
        said = self.act(agent, "set-aside-worktree", target)
        self.assertTrue((self.repo / ".claude" / "worktrees" / target).is_dir(), said)
        return said

    def test_a_worktree_with_work_in_it_or_in_use_is_kept(self):
        agent = self.running()
        dirty = self.helper("agent-dirty")
        (dirty / "notes.txt").write_text("unsaved\n")
        self.age(dirty, self.repo / ".git" / "worktrees" / "agent-dirty")
        self.assertEqual(self.kept(agent, "agent-dirty"), "Kept: It has uncommitted changes.")

        loose = self.helper("agent-loose")
        git("-c", "user.name=Fictional", "-c", "user.email=fictional@example.com", "commit", "-q",
            "--allow-empty", "-m", "fictional", cwd=loose)
        self.age(loose, self.repo / ".git" / "worktrees" / "agent-loose")
        self.assertEqual(self.kept(agent, "agent-loose"), "Kept: Its commits are on no branch.")

        self.helper("agent-recent", days=2)
        self.assertEqual(self.kept(agent, "agent-recent"), "Kept: It changed 2 days ago.")
        recent_ignored = self.helper("agent-ignored")
        os.utime(recent_ignored / ".claude" / "settings.local.json", ((NOW - timedelta(days=1)).timestamp(),) * 2)
        self.assertEqual(self.kept(agent, "agent-ignored"), "Kept: It changed yesterday.",
                         "a change to an ignored file counts")

        self.helper("agent-open")
        platform.folder_in_use.return_value = True
        self.assertEqual(self.kept(agent, "agent-open"), "Kept: A program has it open.")
        platform.folder_in_use.side_effect = OSError("lsof failed")
        self.assertEqual(self.kept(agent, "agent-open"), "Kept: Altitude could not tell whether a program has it open.")

    def test_a_tasks_own_worktree_or_one_made_while_a_task_was_open_is_kept(self):
        agent = self.running()
        self.helper("fix-upload-retry")
        S.save_task(self.project, {"slug": "fix-upload-retry", "title": "Fix the upload retry", "state": "blocked",
                                   "created": (NOW - timedelta(days=30)).isoformat()})
        self.assertEqual(self.kept(agent, "fix-upload-retry"), "Kept: It is task “Fix the upload retry”’s worktree.")
        self.helper()
        self.assertEqual(self.kept(agent, HELPER),
                         "Kept: Task “Fix the upload retry” was open when it was made and still is.",
                         "a helper copy of a task that is still open stays")
        S.save_task(self.project, {"slug": "fix-upload-retry", "title": "Fix the upload retry", "state": "done",
                                   "created": (NOW - timedelta(days=30)).isoformat()})
        self.assertTrue(self.act(agent, "set-aside-worktree", HELPER).startswith("Set aside"))
        self.assertEqual(self.kept(agent, "fix-upload-retry"), "Kept: It is task “Fix the upload retry”’s worktree.",
                         "an ended task's worktree stays task cleanup's, so reopening the task checks out its branch")

    def test_a_branch_is_deleted_only_when_merged_quiet_and_free_and_comes_back_at_its_recorded_commit(self):
        agent = self.running()
        sha = self.branch("docs-typo-fix")
        self.branch("upload-retry-fix", commit=True)
        self.branch("add-offline-sync", days=2)
        self.branch("retry-limit-tests", commit=True)
        add_worktree(self.repo, "busy")
        cases = {"main": "Kept: It is the main branch.",
                 "worktree-busy": "Kept: It is checked out in busy.",
                 "add-offline-sync": "Kept: It moved 2 days ago.",
                 "retry-limit-tests": "Kept: It has 1 commit found nowhere else.",
                 "upload-retry-fix": "Kept: It has 1 commit found nowhere else."}
        for name, said in cases.items():
            with self.subTest(branch=name):
                self.assertEqual(self.act(agent, "delete-branch", name), said)
        self.assertEqual(self.act(agent, "delete-branch", "docs-typo-fix"),
                         "Deleted branch docs-typo-fix. Merged into main.")
        self.assertEqual(git("branch", "--list", "docs-typo-fix", cwd=self.repo), "")

        merged = git("rev-parse", "refs/heads/upload-retry-fix", cwd=self.repo).strip()
        verify.gh.return_value = [{"number": 213, "headRefOid": merged, "isCrossRepository": True},
                                  {"number": 214, "headRefOid": merged, "isCrossRepository": False}]
        self.assertEqual(self.act(agent, "delete-branch", "upload-retry-fix"),
                         "Deleted branch upload-retry-fix. Merged by #214.")
        self.assertIn(["pr", "list", "--head", "upload-retry-fix", "--base", "main", "--state", "merged", "-L", "20",
                       "--json", "number,headRefOid,isCrossRepository"], [c.args[0] for c in verify.gh.call_args_list])

        undo = f"refs/altitude/undo/{agent}/1/{len(cases) + 1}"
        git("update-ref", undo, merged, cwd=self.repo)  # a task worker can write refs
        refused = self.change("undo", agent=agent, n=1, change=len(cases) + 1)
        self.assertEqual(refused["refused"], [{"n": len(cases) + 1,
                                               "reason": "its saved commit no longer matches Altitude's record."}])
        row = self.rows(agent)[len(cases)]
        self.assertEqual((row["undo"], row["refused"]), (True, "its saved commit no longer matches Altitude's record."))
        git("update-ref", undo, sha, cwd=self.repo)
        self.assertEqual(self.change("undo", agent=agent, n=1, change="all")["undone"], [len(cases) + 1, len(cases) + 2])
        self.assertEqual(git("rev-parse", "refs/heads/docs-typo-fix", cwd=self.repo).strip(), sha)
        self.assertEqual(git("for-each-ref", "refs/altitude", cwd=self.repo), "", "Undo drops what it kept")

    def test_github_is_asked_before_the_project_lock_is_taken(self):
        agent = self.running()
        sha = self.branch("upload-retry-fix", commit=True)
        free = []

        def take():
            with S.project_lock(self.project):
                pass

        def lookup(*_args, **_kwargs):
            taker = threading.Thread(target=take, daemon=True)
            taker.start()
            taker.join(5)
            free.append(not taker.is_alive())
            return [{"number": 214, "headRefOid": sha, "isCrossRepository": False}]
        verify.gh.side_effect = lookup
        self.assertEqual(self.act(agent, "delete-branch", "upload-retry-fix"),
                         "Deleted branch upload-retry-fix. Merged by #214.")
        self.assertEqual(free, [True], "the project's other writers are not held up while GitHub answers")

    def test_a_tip_that_moves_while_github_answers_is_kept(self):
        agent = self.running()
        stamp = f"@{int((NOW - timedelta(days=10)).timestamp())} +0000"
        old = {**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}

        def move(name: str, to: str, env: dict | None = None) -> None:
            subprocess.run(["git", "update-ref", f"refs/heads/{name}", to], cwd=self.repo, env=env, check=True)

        def on_top(sha: str) -> str:
            tree = git("rev-parse", f"{sha}^{{tree}}", cwd=self.repo).strip()
            return subprocess.run(["git", "commit-tree", tree, "-p", sha, "-m", "more fictional work"], cwd=self.repo,
                                  env=old, check=True, capture_output=True, text=True).stdout.strip()
        cases = {"a new tip whose move looks old": ("upload-retry-fix", lambda sha: move(
                     "upload-retry-fix", on_top(sha), old), "Kept: It has 2 commits found nowhere else."),
                 "away and back to the same tip": ("retry-limit-tests", lambda sha: (
                     move("retry-limit-tests", on_top(sha)), move("retry-limit-tests", sha)), "Kept: It moved today.")}
        for case, (name, moving, said) in cases.items():
            with self.subTest(case):
                sha = self.branch(name, commit=True)
                calls = []

                def lookup(*_args, **_kwargs):
                    if not calls:
                        moving(sha)
                    calls.append(1)
                    return [{"number": 214, "headRefOid": sha, "isCrossRepository": False}]
                verify.gh.side_effect = lookup
                self.assertEqual(self.act(agent, "delete-branch", name), said)
                self.assertTrue(git("branch", "--list", name, cwd=self.repo).strip(), "the branch stays")

    def test_nothing_changes_when_altitude_cannot_keep_the_commit_for_undo(self):
        path = self.helper()
        self.branch("docs-typo-fix")
        agent = self.running()
        git("update-ref", f"refs/altitude/undo/{agent}/1", "main", cwd=self.repo)  # its undo refs cannot be written
        self.assertTrue(self.act(agent, "set-aside-worktree", HELPER).startswith("Failed: "))
        self.assertTrue(self.act(agent, "delete-branch", "docs-typo-fix").startswith("Failed: "))
        self.assertTrue(path.is_dir())
        self.assertIn("docs-typo-fix", git("branch", "--list", "docs-typo-fix", cwd=self.repo))
        self.assertEqual([r["outcome"] for r in A._load(self.project)["runs"][-1]["changes"]], ["failed", "failed"])

    def test_undo_all_records_each_row_when_git_fails_for_one(self):
        path = self.helper()
        self.branch("docs-typo-fix")
        agent = self.running()
        self.act(agent, "set-aside-worktree", HELPER)
        self.act(agent, "delete-branch", "docs-typo-fix")
        with mock.patch.object(tidy, "_branches", side_effect=RuntimeError("not a git repository")):
            receipt = self.change("undo", agent=agent, n=1, change="all")
        self.assertEqual(receipt["undone"], [1])
        self.assertEqual(receipt["refused"], [{"n": 2, "reason": "not a git repository"}])
        self.assertTrue(path.is_dir())
        self.assertEqual([(r["undone"], r["undo"]) for r in self.rows(agent)], [(True, False), (False, True)])
        self.assertEqual([e["outcome"] for e in self.events("routine-undo")], ["undone", "refused"])

    def test_a_set_aside_worktree_is_removed_for_good_after_fourteen_days(self):
        agent = self.running()
        path = self.helper()
        on_branch = self.repo / ".claude" / "worktrees" / "finished-task"
        git("worktree", "add", "-q", "-b", "finished-task", str(on_branch), "main", cwd=self.repo)
        self.age(on_branch, self.repo / ".git" / "worktrees" / "finished-task")
        self.act(agent, "set-aside-worktree", HELPER)
        self.act(agent, "set-aside-worktree", "finished-task")
        A.aside(self.project, NOW + timedelta(days=13))
        self.assertTrue(all(not r.get("purged") for r in self.record()["runs"][-1]["changes"]))
        A.aside(self.project, NOW + timedelta(days=14))
        self.assertFalse((self.repo / ".claude" / "worktrees" / ".set-aside" / f"{agent}-1-1" / HELPER).exists())
        self.assertNotIn(".set-aside", git("worktree", "list", cwd=self.repo))
        self.assertEqual(self.rows(agent)[0]["note"],
                         "Removed for good Oct 28. Undo restores it from its branch, without ignored files.")
        self.assertEqual(self.change("undo", agent=agent, n=1, change="all")["undone"], [1, 2])
        self.assertFalse((path / ".claude").exists(), "ignored files are gone for good")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=on_branch).strip(), "finished-task")
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path).strip(), "HEAD", "detached again")

    def test_task_cleanup_shares_the_rule_for_what_would_be_lost(self):
        path = self.helper("fix-x")
        (path / "notes.txt").write_text("unsaved\n")
        self.assertEqual(tidy.unsaved(path), "uncommitted")
        (path / "notes.txt").unlink()
        self.assertIsNone(tidy.unsaved(path))


class TestRecordsAndWords(ActingCase):
    def test_altitudes_own_sentence_follows_the_summary_in_chat_recent_runs_and_the_next_prompt(self):
        self.helper()
        self.branch("docs-typo-fix")
        agent = self.running()
        self.act(agent, "set-aside-worktree", HELPER)
        self.act(agent, "delete-branch", "docs-typo-fix")
        self.act(agent, "delete-branch", "main")
        self.ended("Nothing: every worktree and branch was checked.\n\nKept main.")
        A.reconcile(self.project, NOW)
        self.assertEqual(self.lines()[-1]["text"],
                         "**Dependency check** found nothing. Set aside 1 worktree and deleted 1 branch.",
                         "a run that changed something says so even when Tell me is only for findings")
        self.assertEqual(A.agent_view(self.project, agent, now=NOW)["runs"][0]["line"],
                         "every worktree and branch was checked. Set aside 1 worktree and deleted 1 branch.")
        view = A.run_view(self.project, agent, 1, now=NOW)
        self.assertTrue(view["undo_all"])
        self.assertEqual([r["head"] for r in view["changes"]], ["Set aside worktree", "Deleted branch", "Kept branch"])
        self.change("run", agent=agent)
        A.start_next(self.project, NOW)
        self.assertIn("Altitude's record of what it changed: Set aside 1 worktree and deleted 1 branch.",
                      self.calls[-1]["prompt"])

    def test_the_prompt_says_how_to_ask_and_lists_the_projects_worktrees_and_branches(self):
        self.helper()
        self.branch("docs-typo-fix")
        acting = self.running(name="Tidy")
        prompt = self.calls[-1]["prompt"]
        for said in ("alt act set-aside-worktree NAME", "alt act delete-branch NAME", "at most 20 requests",
                     f"- {HELPER}: no task names it; detached; its Git record changed",
                     "- docs-typo-fix: on main; moved 10 days ago"):
            self.assertIn(said, prompt)
        self.assertNotIn(f"- project-agent-{acting}-1", prompt, "a run's own copy is never listed")
        self.finish()
        self.running(name="Reporter", may=[])
        prompt = self.calls[-1]["prompt"]
        self.assertIn("change anything outside this copy", prompt)
        self.assertNotIn("alt act", prompt)

    def test_the_built_in_tidy_routine_says_what_it_may_change_before_it_is_turned_on(self):
        shown = {b["key"]: b for b in A.view(self.project, now=NOW)["builtins"]}
        self.assertEqual(shown["tidy"]["line"],
                         "Mondays at 09:00 · Clears worktrees and branches nothing needs, with Undo")
        self.assertIn(f"of {self.project} that nothing needs", shown["tidy"]["terms"])
        self.assertIsNone(shown["build-health"]["terms"])
        agent = self.change("builtin", key="tidy")["agent"]
        view = A.agent_view(self.project, agent, now=NOW)
        self.assertEqual((view["definition"]["may"], view["may_line"]),
                         (["worktrees"], "May change worktrees and branches, with Undo"))
        listed = A.view(self.project, now=NOW)["agents"][0]
        self.assertEqual((listed["triggers"], listed["may"]),
                         ("Mondays at 09:00", "Changes worktrees and branches"))

    def test_the_permission_is_part_of_the_definition_offers_and_the_cli(self):
        with self.assertRaisesRegex(ValueError, "worktrees and branches, or nothing"):
            A.definition(definition(may=["everything"]))
        self.assertEqual(A._changes(A.definition(definition()), A.definition(definition(may=["worktrees"]))),
                         ["May now change worktrees and branches"])
        self.assertEqual(A._changes(A.definition(definition(may=["worktrees"])), A.definition(definition())),
                         ["No longer changes worktrees and branches"])
        made = A.verb(self.project, A.parser().parse_args(["new", "--name", "Tidy", "--brief", "-", "--may", "worktrees"]),
                      "Tidy up.", by="operator", source="cli")
        self.assertEqual(self.record()["agents"][-1]["may"], ["worktrees"])
        A.verb(self.project, A.parser().parse_args(["set", made["agent"], "--may", "none"]), by="operator",
               source="cli")
        self.assertEqual(self.record()["agents"][-1]["may"], [])
        legacy = self.record()
        for d in (legacy["agents"][-1], *(v["definition"] for v in legacy["versions"])):
            d.pop("may")
        S.write_json(A._path(self.project), legacy)
        self.assertEqual(A._load(self.project)["agents"][-1]["may"], [], "a routine saved before acting only reports")


class TestAuthority(ActingCase):
    def test_l3_may_pause_stop_and_undo_but_not_turn_on_resume_or_run_a_routine_that_may_change_things(self):
        self.helper()
        agent = self.running()
        self.act(agent, "set-aside-worktree", HELPER)
        turn = "0123456789ab"
        l3.chat_log(self.project, "user", "Undo the tidy and pause it", trigger="chat", turn_id=turn)
        self.patch(l3, "chat_turn", return_value=turn)
        self.patch(T, "_decision_messages", return_value=[{"id": turn}])

        def l3_verb(*args):
            return server._l3_verb_request(self.project, {"kind": "alt", "args": ["agent", *args], "stdin": ""})
        self.assertEqual(json.loads(l3_verb("undo", agent, "--run", "1", "1")["stdout"])["undone"], [1])
        self.finish()
        l3_verb("pause", agent)
        with self.assertRaisesRegex(ValueError, "Only the operator turns on Dependency check"):
            l3_verb("resume", agent)
        with self.assertRaisesRegex(ValueError, "Only the operator runs Dependency check"):
            l3_verb("run", agent)
        fresh = self.agent(on=False, name="Fresh", may=["worktrees"])
        with self.assertRaisesRegex(ValueError, "Only the operator turns on Fresh"):
            l3_verb("on", fresh)
        self.change("resume", agent=agent)
        self.assertEqual(self.record()["agents"][0]["state"], "on", "the operator's own press turns it back on")

    def test_only_a_routine_run_has_alt_act(self):
        for actor in ("l2", "l3", "operator"):
            env = {**os.environ, "ALTITUDE_ACTOR": actor, "ALTITUDE_PROJECT": self.project,
                   "ALTITUDE_AGENT_RUN": "0123456789ab/1"}
            if actor == "operator":
                env.pop("ALTITUDE_ACTOR")
            result = subprocess.run([sys.executable, str(REPO / "bin" / "alt"), "act", "delete-branch", "x"], env=env,
                                    capture_output=True, text=True, timeout=120)
            with self.subTest(actor=actor):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("only a routine run asks Altitude", result.stderr)


class TestTheRunsBox(ActingCase):
    """The fence widens only what Altitude does on a run's request: the run's own box is the same with or without it."""

    def test_a_run_that_may_change_things_launches_in_the_same_box_as_one_that_only_reports(self):
        acting = self.running(name="Tidy")
        self.finish()
        self.running(name="Reporter", may=[])
        first, second = ({k: v for k, v in call.items() if k not in ("name", "prompt", "cwd", "job_root", "extra_env")}
                         for call in self.calls[-2:])
        self.assertEqual(first, second)
        self.assertEqual({k for k in self.calls[-1]["extra_env"]}, {k for k in self.calls[-2]["extra_env"]})

    def test_a_runs_git_folders_are_its_own_record_only(self):
        agent = self.running()
        copy = self.repo / ".claude" / "worktrees" / f"project-agent-{agent}-1"
        writable, read_only = git_policy.worker_git_access(copy, shared=False)
        record = self.repo / ".git" / "worktrees" / copy.name
        self.assertEqual(writable, (record,))
        argv = " ".join(platform.bubblewrap_profile([str(copy), *map(str, writable)], [str(p) for p in read_only],
                                                    temporary=str(self.tmp)))
        common = os.path.realpath(self.repo / ".git")
        self.assertNotIn(f"--bind {common} ", argv, "branches and refs stay read-only")
        self.assertNotIn(f"--bind {os.path.realpath(self.repo / '.claude' / 'worktrees')} ", argv)


@unittest.skipUnless(sys.platform == "linux", "Linux's sandbox")
class TestTheRunsBoxForReal(ActingCase):
    """Real bubblewrap where it starts (not inside an Altitude worker or a runner that refuses user namespaces; `make
    sandbox-vm` runs it): a run that may change things still cannot set a worktree aside or delete a branch itself."""

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

    def test_the_runs_own_attempts_fail(self):
        helper = self.helper()
        self.branch("docs-typo-fix")
        agent = self.running()
        copy = self.repo / ".claude" / "worktrees" / f"project-agent-{agent}-1"
        writable, read_only = git_policy.worker_git_access(copy, shared=False)
        temporary = self.tmp / "job-tmp"
        temporary.mkdir()
        # A real project sits in the home folder, which the box shows read-only; this fictional one sits under /tmp,
        # which the box replaces with the job's own folder, so it is shown read-only here the same way.
        argv = platform.bubblewrap_profile([str(copy), *map(str, writable)],
                                           [str(self.repo), *(str(p) for p in read_only)], temporary=str(temporary))

        def boxed(*command: str) -> subprocess.CompletedProcess:
            return subprocess.run([*argv, *command], capture_output=True, text=True, timeout=60, cwd=copy)
        seen = boxed("git", "-C", str(self.repo), "branch", "--list", "docs-typo-fix")
        self.assertEqual((seen.returncode, seen.stdout.strip()), (0, "docs-typo-fix"), seen.stderr)
        self.assertEqual(boxed("test", "-f", str(helper / "README.md")).returncode, 0, "the run sees the worktree")
        for command in (["git", "-C", str(self.repo), "branch", "-D", "docs-typo-fix"],
                        ["git", "-C", str(self.repo), "worktree", "move", str(helper), str(self.repo / "away")],
                        ["rm", "-rf", str(helper)]):
            with self.subTest(command=command[3:5]):
                done = boxed(*command)
                self.assertNotEqual(done.returncode, 0, done.stdout)
                self.assertIn("Read-only file system", done.stderr)
        self.assertTrue((helper / "README.md").is_file())
        self.assertTrue(git("branch", "--list", "docs-typo-fix", cwd=self.repo).strip())


del AgentCase
