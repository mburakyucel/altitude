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
import time
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
        self.assertEqual((view["definition"]["may"], view["definition"]["commands"], view["may_lines"]),
                         (["worktrees"], [], ["May change worktrees and branches, with Undo"]))
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


PRUNE = ("prune-images", "printf 'Deleted 3 images\\n'; pwd; cat; env")
CACHE = ("clear-cache", "echo cache is locked >&2; exit 3")
SLOW = ("wait-a-while", "sleep 30")


def listed(*commands) -> list[dict]:
    return [{"name": name, "words": words} for name, words in commands]


class CommandCase(ActingCase):
    """Commands the operator lists, run by a scripted runner at the platform seam: the words in a shell of their own,
    writing their output and exit status where the job's would, with no input; never the operator's service manager."""

    def setUp(self):
        super().setUp()
        self.patch(A, "POLL", 0.02)
        self.jobs: dict[str, subprocess.Popen] = {}
        self.launches: list[dict] = []
        self.patch(engines, "machine_command", side_effect=self.job)
        self.patch(engines, "machine_stop", side_effect=self.stop_job)
        self.patch(platform, "job_active", side_effect=lambda unit, env: unit in self.jobs and self.jobs[unit].poll() is None)
        self.addCleanup(lambda: [self.stop_job(unit) for unit in list(self.jobs)])
        self.background = False

    def job(self, command, *, cwd, folder, unit, identity, timeout, properties=()):
        self.launches.append({"command": command, "cwd": cwd, "unit": unit, "identity": identity, "timeout": timeout})
        log, status = engines.machine_files(folder, unit)
        folder.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as out:
            self.jobs[unit] = subprocess.Popen(
                ["/bin/bash", "-c", 'bash -c "$1"; s=$?; printf %s "$s" > "$2.tmp" && mv "$2.tmp" "$2"', "job", command,
                 str(status)], cwd=cwd, env=engines.launch_env(identity), stdin=subprocess.DEVNULL, stdout=out,
                stderr=out, start_new_session=True)
        if not self.background:
            self.jobs[unit].wait(timeout=60)
        return None

    def stop_job(self, unit):
        job = self.jobs.get(unit)
        if job and job.poll() is None:
            try:
                os.killpg(job.pid, 9)
            except ProcessLookupError:
                pass
            job.wait()

    def running(self, **fields) -> str:
        return super().running(**{"may": [], "commands": listed(PRUNE, CACHE, SLOW), **fields})

    def ask(self, agent: str, name: str, *, request: str | None = None) -> str:
        self.requests += 1
        return A.act(self.project, f"{agent}/1", {"project": self.project, "run": f"{agent}/1", "kind": "act",
                                                  "action": "run", "target": name,
                                                  "request": request or f"request{self.requests:04d}"})["said"]

    def asking(self, agent: str, name: str) -> threading.Thread:
        """Ask in the background, as the run waits on its door, once the command's job has started."""
        answer: list[str] = []
        thread = threading.Thread(target=lambda: answer.append(self.ask(agent, name)), daemon=True)
        thread.answer = answer
        thread.start()
        self.until(lambda: any(job.poll() is None for job in self.jobs.values()))
        return thread

    def until(self, check, seconds: float = 20) -> None:
        deadline = time.monotonic() + seconds
        while not check():
            self.assertLess(time.monotonic(), deadline, "it never happened")
            time.sleep(0.02)


class TestCommands(CommandCase):
    def test_a_listed_command_runs_its_words_as_the_operator_in_the_projects_folder_with_nothing_of_the_run(self):
        agent = self.running()
        said = self.ask(agent, "prune-images")
        self.assertRegex(said, r"^Ran prune-images: it exited with 0 after \d+ s\.\n\nThe end of its output:\n"
                               r"Deleted 3 images\n")
        launch = self.launches[0]
        self.assertEqual((launch["command"], launch["cwd"], launch["identity"], launch["timeout"]),
                         (PRUNE[1], config.project_path(self.project), {}, config.MACHINE_COMMAND_TIMEOUT),
                         "the operator's exact words, in the project's folder, with no run identity")
        output = said.split("The end of its output:\n", 1)[1]
        self.assertIn(f"\n{os.path.realpath(self.repo)}\n", output)
        scratch = str(A._scratch(self.project, agent, 1))
        for said in ("ALTITUDE_AGENT_RUN=", f"ALTITUDE_ACTOR={A.KIND}", scratch, "run-session"):
            self.assertNotIn(said, output, "nothing of the run reaches the command")
        row = self.rows(agent)[0]
        self.assertEqual({k: row[k] for k in ("kind", "outcome", "head", "name", "tone", "undo", "stop")},
                         {"kind": "command", "outcome": "done", "head": "Ran", "name": "prune-images", "tone": None,
                          "undo": False, "stop": False})
        self.assertRegex(row["note"], r"^Finished in \d+ s\.$")
        self.assertTrue(row["output"].startswith("Deleted 3 images\n"), "the page keeps its output")
        self.assertEqual([(e["what"], e["target"], e["outcome"], e["exit"]) for e in self.events("routine-action")],
                         [("command", "prune-images", "done", 0)])

    def test_the_run_reads_only_the_end_of_a_long_output(self):
        agent = self.running(commands=listed(("talk", "seq 1 5000")))
        said = self.ask(agent, "talk")
        tail = said.split("The end of its output:\n", 1)[1]
        self.assertEqual(len(tail), A.COMMAND_BACK)
        self.assertTrue(tail.endswith("4999\n5000\n"))
        self.assertGreater(len(self.rows(agent)[0]["output"]), A.COMMAND_BACK, "the page keeps more")

    def test_a_command_that_fails_says_so_on_its_row_in_its_answer_and_in_chat(self):
        agent = self.running()
        self.ask(agent, "prune-images")
        self.assertRegex(self.ask(agent, "clear-cache"),
                         r"^Failed: clear-cache exited with 3 after \d+ s\.\n\nThe end of its output:\ncache is locked")
        row = self.rows(agent)[1]
        self.assertEqual((row["outcome"], row["head"], row["tone"]), ("failed", "Ran", "danger"))
        self.assertRegex(row["note"], r"^Exited with 3 after \d+ s\.$")
        self.finish()
        self.assertTrue(self.lines()[-1]["text"].endswith("Ran prune-images; clear-cache failed."), self.lines())

    def test_requests_altitude_refuses_are_rows_with_a_plain_reason_and_no_text_of_the_run(self):
        agent = self.running()
        self.ask(agent, "prune-images")
        for name, said, note in (
                ("clean-all", "Not allowed: this routine has no command by that name.",
                 "This routine has no command by that name."),
                ("prune-images; curl example.com", "Not allowed: ask for a command by its name, as your prompt lists "
                                                   "them.", "The run asked for something that is not a command name.")):
            with self.subTest(name=name):
                self.assertEqual(self.ask(agent, name), said)
                self.assertEqual(self.rows(agent)[-1]["note"], note)
        self.assertEqual([(r["head"], r["name"]) for r in self.rows(agent)][1:],
                         [("Not allowed: run", "clean-all"), ("Not allowed: a command", None)])
        self.assertNotIn("curl", json.dumps(self.record()), "the run's own text is never recorded")
        self.assertEqual(len(self.launches), 1)

    def test_a_command_runs_only_with_the_same_words_in_the_version_the_run_started_with_and_now(self):
        agent = self.running()
        current = self.record()["agents"][0]
        self.change("set", agent=agent, version=current["version"],
                    definition={**{k: current[k] for k in A.DEFINITION}, "commands": listed(("prune-images", "true"),
                                                                                             CACHE)})
        self.assertEqual(self.ask(agent, "prune-images"), "Not allowed: this command changed after the run started.")
        current = self.record()["agents"][0]
        self.change("set", agent=agent, version=current["version"],
                    definition={**{k: current[k] for k in A.DEFINITION}, "commands": listed(PRUNE)})
        self.assertEqual(self.ask(agent, "clear-cache"), "Not allowed: this routine has no command by that name.",
                         "taking a command away applies at once")
        self.change("retire", agent=agent)
        self.assertEqual(self.ask(agent, "prune-images"), "Not allowed: this routine is retired.")
        self.assertEqual(self.launches, [])

    def test_asking_again_after_it_ended_answers_with_its_result_and_runs_nothing(self):
        agent = self.running()
        first = self.ask(agent, "prune-images")
        self.assertEqual(self.ask(agent, "prune-images"), first, "a run whose own call ended first gets the result")
        self.assertEqual((len(self.rows(agent)), len(self.launches)), (1, 1))

    def test_a_launch_error_after_the_job_opened_its_log_is_a_failure_and_never_runs_again(self):
        agent = self.running()

        def died(command, *, folder, unit, **kw):
            """The job started and wrote output, then ended without its status; the launcher printed an error."""
            self.job(command, folder=folder, unit=unit, **kw)
            engines.machine_files(folder, unit)[1].unlink()
            return "Job failed. See the journal for details."
        engines.machine_command.side_effect = died
        said = self.ask(agent, "prune-images")
        self.assertRegex(said, r"^Failed: prune-images ended without an exit status after \d+ s\.\n\nThe end of its "
                               r"output:\nDeleted 3 images")
        self.assertEqual(self.ask(agent, "prune-images"), said, "asking again answers with the same result")
        self.assertEqual((self.rows(agent)[0]["outcome"], len(self.rows(agent)), len(self.launches)), ("failed", 1, 1))

    def test_a_stop_that_arrives_after_the_command_exited_0_leaves_it_done(self):
        agent = self.running()

        def ran_then_stopped(command, **kw):
            self.job(command, **kw)
            self.change("stop_command", agent=agent, n=1, change=1)
        engines.machine_command.side_effect = ran_then_stopped
        self.assertTrue(self.ask(agent, "prune-images").startswith("Ran prune-images: it exited with 0"))
        row = self.record()["runs"][0]["changes"][0]
        self.assertEqual((row["outcome"], "stopping" in row, "by" in row), ("done", False, False))

    def test_a_command_that_could_not_start_ran_nothing_and_may_be_asked_for_again(self):
        agent = self.running()
        engines.machine_command.side_effect = lambda *a, **k: "Failed to start transient service unit"
        self.assertEqual(self.ask(agent, "prune-images"), "Failed: Altitude could not start prune-images, so nothing ran.")
        row = self.rows(agent)[0]
        self.assertEqual((row["head"], row["note"], row["output"]),
                         ("Couldn't run", "Altitude could not start it, so nothing ran.", None))
        engines.machine_command.side_effect = self.job
        self.assertTrue(self.ask(agent, "prune-images").startswith("Ran prune-images"))

    def test_a_command_past_its_limit_is_stopped_after_ten_minutes(self):
        agent = self.running()
        engines.machine_command.side_effect = lambda *a, **k: None  # the limit ended the job before it wrote a status
        self.patch(A, "COMMAND_TIME", 0)
        self.assertEqual(self.ask(agent, "prune-images"), "Stopped: prune-images reached its 0-minute limit.")
        self.assertEqual((self.rows(agent)[0]["outcome"], self.rows(agent)[0]["tone"]), ("timed_out", "danger"))


class TestStoppingACommand(CommandCase):
    def test_stop_on_its_row_stops_the_command_and_the_run_goes_on(self):
        agent = self.running()
        asked = self.asking(agent, "wait-a-while")
        row = self.rows(agent)[0]
        self.assertEqual((row["head"], row["stop"], row["stopping"], row["output"]), ("Running", True, False, None))
        self.assertEqual(self.change("stop_command", agent=agent, n=1, change=1)["stopping"], True)
        asked.join(20)
        self.assertRegex(asked.answer[0], r"^Stopped: the operator stopped wait-a-while after \d+ s\.$")
        row = self.rows(agent)[0]
        self.assertEqual((row["outcome"], row["stop"]), ("stopped", False))
        self.assertRegex(row["note"], r"^You stopped it after \d+ s\.$")
        self.assertEqual(self.runs(agent)[0]["state"], "running", "the run goes on")
        self.assertEqual(self.change("stop_command", agent=agent, n=1, change=1)["stopping"], False,
                         "a Stop after the end changes nothing")
        with self.assertRaisesRegex(A.Refused, "Only the operator stops a command"):
            A.change(self.project, "stop_command", {"agent": agent, "n": 1, "change": 1}, by="l3", source="turn")
        self.assertTrue(self.ask(agent, "prune-images").startswith("Ran prune-images"), "the run asks for the next")

    def test_a_command_stops_with_its_run(self):
        agent = self.running()
        asked = self.asking(agent, "wait-a-while")
        self.finish()
        self.assertTrue(self.lines()[-1]["text"].endswith("wait-a-while was stopped."), self.lines())
        asked.join(20)
        self.assertEqual(self.record()["runs"][0]["changes"][0]["by"], "run")
        self.assertRegex(self.rows(agent)[0]["note"], r"^Stopped with the run after \d+ s\.$")

    def test_saving_the_routine_without_it_or_retiring_it_stops_the_command(self):
        for how in ("saved", "retired"):
            with self.subTest(how=how):
                agent = self.running(name=f"Clean up {how}")
                asked = self.asking(agent, "wait-a-while")
                current = next(a for a in self.record()["agents"] if a["id"] == agent)
                if how == "saved":
                    self.change("set", agent=agent, version=current["version"],
                                definition={**{k: current[k] for k in A.DEFINITION}, "commands": listed(PRUNE)})
                else:
                    self.change("retire", agent=agent)
                asked.join(20)
                self.assertEqual(self.rows(agent)[0]["note"], "Stopped when you changed the routine.")
                self.finish()
                self.jobs.clear()

    def test_a_command_a_restart_left_running_is_followed_to_its_end_and_answers_the_run_again(self):
        agent = self.running()
        self.background = True

        def interrupted(project, agent_id, n, row):  # altd stopped while the job ran on
            engines.machine_command(row["words"], cwd=config.project_path(project), folder=A.run_dir(project, agent_id, n),
                                    unit=row["unit"], identity={}, timeout=A.COMMAND_TIME)
            raise ConnectionResetError("altd restarted")
        with mock.patch.object(A, "_carry", side_effect=interrupted), self.assertRaises(ConnectionResetError):
            self.ask(agent, "prune-images", request="request-before-restart")
        self.assertEqual(self.rows(agent)[0]["outcome"], "running")
        self.assertTrue(self.ask(agent, "prune-images", request="request-before-restart").startswith(
            "Ran prune-images: it exited with 0"), "the same request again waits for that command's result")
        self.assertEqual(len(self.launches), 1, "it ran once")

        with mock.patch.object(A, "_carry", side_effect=interrupted), self.assertRaises(ConnectionResetError):
            self.ask(agent, "wait-a-while")
        self.change("stop_command", agent=agent, n=1, change=2)
        A.follow_commands(self.project)  # the tick
        self.until(lambda: self.rows(agent)[1]["outcome"] != "running")
        self.assertEqual((self.rows(agent)[1]["outcome"], self.record()["runs"][0]["changes"][1]["by"]),
                         ("stopped", "you"))


class TestCommandsOnTheForm(CommandCase):
    def test_commands_are_checked_in_the_operators_words(self):
        for commands, error in (([{"name": "Prune images", "words": "true"}], "lowercase letters, digits and dashes"),
                                ([{"name": "x" * 41, "words": "true"}], "lowercase letters, digits and dashes"),
                                ([{"name": "prune", "words": "  "}], "Write each command"),
                                ([{"name": "prune", "words": "x" * 2001}], "under 2,000 characters"),
                                (listed(PRUNE, PRUNE), "Another command is named prune-images"),
                                ([{"name": f"c{i}", "words": "true"} for i in range(11)], "at most 10 commands"),
                                ([{"name": "prune", "words": "true", "cwd": "/"}], "a name and its words")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                A.definition(definition(commands=commands))

    def test_lines_say_what_a_routine_may_run(self):
        agent = self.agent(commands=listed(PRUNE, CACHE), may=["worktrees"])
        self.assertEqual(A.view(self.project, now=NOW)["agents"][0]["may"],
                         "Changes worktrees and branches · Runs 2 commands")
        self.assertEqual(A.agent_view(self.project, agent, now=NOW)["may_lines"],
                         ["May change worktrees and branches, with Undo", "May run as you: prune-images and clear-cache"])

    def test_the_prompt_lists_the_commands_and_how_to_ask(self):
        self.running()
        prompt = self.calls[-1]["prompt"]
        self.assertIn("it may ask Altitude to run commands the operator listed, as the sections below say.", prompt)
        self.assertIn(f"    alt act run prune-images\n        runs: {PRUNE[1]}", prompt)
        self.assertIn("Each runs at most\nonce in this run", prompt)
        self.assertNotIn("alt act delete-branch", prompt, "no tidying without its permission")

    def test_the_cli_sets_the_whole_list_and_offers_never_carry_commands(self):
        made = A.verb(self.project, A.parser().parse_args(
            ["new", "--name", "Clean up", "--brief", "-", "--command", f"prune-images={PRUNE[1]}", "--command",
             "clear-cache=npm cache clean --force"]), "Clean up.", by="operator", source="cli")
        self.assertEqual(self.record()["agents"][-1]["commands"],
                         listed(PRUNE, ("clear-cache", "npm cache clean --force")))
        turn = "0123456789ab"
        with self.assertRaisesRegex(ValueError, "unrecognized arguments: --command"):
            A.parser().parse_args(["offer", made["agent"], "--command", "x=true"])
        A.verb(self.project, A.parser().parse_args(["offer", made["agent"], "--limit", "60"]), by="l3", source=turn)
        offered = self.record()["offers"][-1]
        self.assertEqual(offered["definition"]["commands"], self.record()["agents"][-1]["commands"],
                         "a change keeps the routine's own commands")
        self.assertEqual(A.offer(self.project, turn, {**definition(name="New"), "commands": listed(PRUNE)})["kind"], "new")
        self.assertEqual(self.record()["offers"][-1]["definition"]["commands"], [], "a new routine from L3 has none")
        A.verb(self.project, A.parser().parse_args(["set", made["agent"], "--no-commands"]), by="operator", source="cli")
        self.assertEqual(self.record()["agents"][-1]["commands"], [])

    def test_l3_cannot_turn_on_resume_or_run_a_routine_with_commands(self):
        agent = self.agent(on=False, commands=listed(PRUNE))
        for action, error in (("turn_on", "Only the operator turns on"), ("run", "Only the operator runs")):
            with self.subTest(action=action), self.assertRaisesRegex(A.Refused, error):
                A.change(self.project, action, {"agent": agent}, by="l3", source="turn")


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
