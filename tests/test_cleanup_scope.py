"""Done-time cleanup is scoped to one task's persisted L2 and L1 ownership records."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from altitude import config, dispatch, engines, improve, state as S


class TestDoneCleanupScope(unittest.TestCase):
    def test_task_ownership_is_checked_before_every_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            state = root / "state"
            a_slug = "task-a-with-a-deliberately-long-cleanup-slug"
            b_slug = "task-b-running-codex"

            def worktree(name):
                return repo / ".claude" / "worktrees" / name

            a_l2 = worktree(a_slug)
            a_done = worktree(f"{a_slug[:30]}-done-l1")
            a_flight = worktree(f"{a_slug[:30]}-in-flight-l1")
            a_locked = worktree(f"{a_slug[:30]}-locked-l1")
            a_unmerged = worktree(f"{a_slug[:30]}-unmerged-l1")
            b_codex = worktree(f"{b_slug[:30]}-implementer-1")
            orphan = worktree("orphan-with-no-task-record")

            task_a = {"slug": a_slug, "state": "done", "worktree": str(a_l2), "agent_id": "agent-a"}
            task_b = {"slug": b_slug, "state": "running", "l2_engine": "codex"}
            records = {
                a_slug: [
                    {"name": "done-l1", "role": "implementer", "engine": "claude", "done": "2026-08-30T01:00:00Z"},
                    {"name": "in-flight-l1", "role": "implementer", "engine": "codex", "done": None},
                    {"name": "locked-l1", "role": "implementer", "engine": "codex", "done": "2026-08-30T01:00:00Z"},
                    {"name": "unmerged-l1", "role": "implementer", "engine": "claude", "done": "2026-08-30T01:00:00Z"},
                ],
                b_slug: [
                    {"name": "implementer-1", "role": "implementer", "engine": "codex", "done": None},
                ],
            }
            for owner_slug, runs in records.items():
                directory = state / owner_slug / "l1"
                directory.mkdir(parents=True)
                for run in runs:
                    S.write_json(directory / f"{run['name']}.json", run)
            a_l2.mkdir(parents=True)

            def row(path, branch, *, locked=False):
                lines = [f"worktree {path}", f"branch refs/heads/{branch}"]
                if locked:
                    lines.append("locked cleanup-test")
                return "\n".join(lines) + "\n\n"

            porcelain = "".join([
                row(repo, "main"),
                row(a_l2, f"worktree-{a_slug}"),
                row(a_done, f"l1/{a_slug[:30]}-done-l1"),
                row(a_flight, f"l1/{a_slug[:30]}-in-flight-l1"),
                row(a_locked, f"l1/{a_slug[:30]}-locked-l1", locked=True),
                row(a_unmerged, f"l1/{a_slug[:30]}-unmerged-l1"),
                # Both branches below have no commits and would pass merge-base. Ownership must keep cleanup from asking.
                row(b_codex, f"l1/{b_slug[:30]}-implementer-1"),
                row(orphan, "worktree-orphan"),
            ])
            calls = []
            removed = []
            merge_checks = []

            def result(args, returncode=0, stdout="", stderr=""):
                return subprocess.CompletedProcess(args, returncode, stdout, stderr)

            def fake_run(args, **kwargs):
                calls.append(tuple(args))
                if args[:4] == ["git", "fetch", "-q", "origin"]:
                    return result(args)
                if args[:4] == ["git", "worktree", "list", "--porcelain"]:
                    return result(args, stdout=porcelain)
                if args[:3] == ["git", "merge-base", "--is-ancestor"]:
                    merge_checks.append(args[3])
                    return result(args, returncode=1 if args[3].endswith("-unmerged-l1") else 0)
                if args[:4] == ["git", "worktree", "remove", "--force"]:
                    removed.append(args[4])
                    return result(args)
                if args[:3] == ["git", "branch", "-D"]:
                    return result(args)
                raise AssertionError(f"unexpected subprocess: {args}")

            def fake_agents():
                calls.append(("claude_agents",))
                return []

            def fake_claude_rm(agent_id):
                calls.append(("claude_rm", agent_id))
                a_l2.rmdir()
                return "removed"

            patches = [
                mock.patch.object(config, "project_path", side_effect=lambda project: repo),
                mock.patch.object(S, "task_dir", side_effect=lambda project, slug: state / slug),
                mock.patch.object(S, "list_tasks", side_effect=lambda project, include_archive=False: [task_a, task_b]),
                mock.patch.object(engines, "claude_agents", side_effect=fake_agents),
                mock.patch.object(engines, "claude_rm", side_effect=fake_claude_rm),
                mock.patch.object(subprocess, "run", side_effect=fake_run),
                mock.patch.object(dispatch, "pull_after_done", return_value=[]),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                notes = dispatch.cleanup_after_done("altitude", task_a)

                events = [event for event in S.read_events("altitude", a_slug)
                          if event["kind"] == "cleanup-worktree"]

            self.assertEqual(set(removed), {str(a_done)})
            self.assertEqual([call for call in calls if call[:1] == ("claude_rm",)], [("claude_rm", "agent-a")])
            self.assertFalse({str(a_flight), str(a_locked), str(a_unmerged), str(b_codex), str(orphan)} & set(removed))
            self.assertNotIn(f"l1/{b_slug[:30]}-implementer-1", merge_checks)
            self.assertNotIn("worktree-orphan", merge_checks)
            self.assertFalse(any(call[:3] == ("git", "worktree", "prune") for call in calls))
            self.assertTrue(any("persisted L1 record has no done stamp" in note for note in notes))
            claude_rm_index = calls.index(("claude_rm", "agent-a"))
            for guard in [
                ("git", "fetch", "-q", "origin", "main"),
                ("git", "worktree", "list", "--porcelain"),
                ("claude_agents",),
                ("git", "merge-base", "--is-ancestor", f"worktree-{a_slug}", "origin/main"),
            ]:
                self.assertLess(calls.index(guard), claude_rm_index)

            by_path = {event["worktree"]: event for event in events}
            self.assertEqual(by_path[str(a_l2)]["action"], "removed")
            self.assertEqual(by_path[str(a_l2)]["reason"],
                             "task-owned branch is merged into origin/main; L2 agent removed via claude rm")
            self.assertEqual(by_path[str(a_done)]["action"], "removed")
            self.assertEqual(by_path[str(a_done)]["reason"], "task-owned branch is merged into origin/main")
            self.assertEqual(by_path[str(a_flight)]["action"], "deferred")
            self.assertEqual(by_path[str(a_flight)]["reason"], "persisted L1 record has no done stamp")
            self.assertEqual(by_path[str(a_locked)]["action"], "skipped")
            self.assertEqual(by_path[str(a_locked)]["reason"], "git worktree is locked")
            self.assertEqual(by_path[str(a_unmerged)]["action"], "skipped")
            self.assertEqual(by_path[str(a_unmerged)]["reason"], "branch has commits not on origin/main")
            self.assertNotIn(str(b_codex), by_path)
            self.assertNotIn(str(orphan), by_path)

    def test_unfinished_l1_defers_without_worktree_list_membership_or_success(self):
        for case, list_returncode in (("omitted", 0), ("failed", 2)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                repo.mkdir()
                state = root / "state"
                slug = f"deferred-l1-{case}"
                task = {"slug": slug, "state": "done"}
                l1_dir = state / slug / "l1"
                l1_dir.mkdir(parents=True)
                S.write_json(l1_dir / "implementer-1.json",
                             {"name": "implementer-1", "role": "implementer", "done": None})
                expected_path = str((repo / ".claude" / "worktrees" /
                                     f"{slug[:30]}-implementer-1").resolve())

                def result(args, returncode=0, stdout="", stderr=""):
                    return subprocess.CompletedProcess(args, returncode, stdout, stderr)

                def fake_run(args, **kwargs):
                    if args[:4] == ["git", "fetch", "-q", "origin"]:
                        return result(args)
                    if args[:4] == ["git", "worktree", "list", "--porcelain"]:
                        return result(args, returncode=list_returncode,
                                      stdout=f"worktree {repo}\nbranch refs/heads/main\n\n",
                                      stderr="simulated list failure" if list_returncode else "")
                    raise AssertionError(f"unexpected subprocess: {args}")

                agents = mock.Mock(return_value=[])
                claude_rm = mock.Mock()
                fault = mock.Mock()
                patches = [
                    mock.patch.object(config, "project_path", return_value=repo),
                    mock.patch.object(S, "task_dir", side_effect=lambda project, owner: state / owner),
                    mock.patch.object(S, "list_tasks", return_value=[task]),
                    mock.patch.object(engines, "claude_agents", agents),
                    mock.patch.object(engines, "claude_rm", claude_rm),
                    mock.patch.object(improve, "system_fault", fault),
                    mock.patch.object(subprocess, "run", side_effect=fake_run),
                    mock.patch.object(dispatch, "pull_after_done", return_value=[]),
                ]
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                    notes = dispatch.cleanup_after_done("altitude", task)
                    events = [event for event in S.read_events("altitude", slug)
                              if event["kind"] == "cleanup-worktree"]

                self.assertTrue(any(note.startswith("deferred worktree ") for note in notes), notes)
                self.assertEqual([(event["action"], event["worktree"], event["reason"]) for event in events],
                                 [("deferred", expected_path, "persisted L1 record has no done stamp")])
                claude_rm.assert_not_called()
                self.assertEqual(agents.call_count, 1 if list_returncode == 0 else 0)
                self.assertEqual(fault.call_count, 1 if list_returncode else 0)

    def test_merge_base_indeterminate_is_a_fault_and_never_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            state = root / "state"
            slug = "merge-base-indeterminate"
            worktree = repo / ".claude" / "worktrees" / slug
            task = {"slug": slug, "state": "done", "worktree": str(worktree), "agent_id": "agent-indeterminate"}
            (state / slug).mkdir(parents=True)
            porcelain = f"worktree {worktree}\nbranch refs/heads/worktree-{slug}\n\n"

            def result(args, returncode=0, stdout="", stderr=""):
                return subprocess.CompletedProcess(args, returncode, stdout, stderr)

            def fake_run(args, **kwargs):
                if args[:4] == ["git", "fetch", "-q", "origin"]:
                    return result(args)
                if args[:4] == ["git", "worktree", "list", "--porcelain"]:
                    return result(args, stdout=porcelain)
                if args[:3] == ["git", "merge-base", "--is-ancestor"]:
                    return result(args, returncode=128, stderr="fatal: bad revision")
                raise AssertionError(f"destructive or unexpected subprocess: {args}")

            claude_rm = mock.Mock()
            fault = mock.Mock()
            patches = [
                mock.patch.object(config, "project_path", return_value=repo),
                mock.patch.object(S, "task_dir", side_effect=lambda project, owner: state / owner),
                mock.patch.object(S, "list_tasks", return_value=[task]),
                mock.patch.object(engines, "claude_agents", return_value=[]),
                mock.patch.object(engines, "claude_rm", claude_rm),
                mock.patch.object(improve, "system_fault", fault),
                mock.patch.object(subprocess, "run", side_effect=fake_run),
                mock.patch.object(dispatch, "pull_after_done", return_value=[]),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                notes = dispatch.cleanup_after_done("altitude", task)
                events = [event for event in S.read_events("altitude", slug)
                          if event["kind"] == "cleanup-worktree"]

            claude_rm.assert_not_called()
            self.assertTrue(any("merge-base indeterminate (exit 128); stderr: fatal: bad revision" in note
                                for note in notes), notes)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["action"], "skipped")
            self.assertIn("stderr: fatal: bad revision", events[0]["reason"])
            fault.assert_called_once()
            self.assertEqual(fault.call_args.args[0], "cleanup-merge-base")
            self.assertIn("fatal: bad revision", fault.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
