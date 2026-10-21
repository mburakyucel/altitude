"""The operator's message reopens a done task in place: same record, conversation, session and deliveries.

The bare Git remote, retention cleanup and task lifecycle are real; only hosted GitHub and worker execution
use deterministic fixtures.
"""
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, land, server, state as S, tasks as T


class TestReopenDoneTask(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, wip=1, routing=[[{"engine": config.ENGINES[-1], "model": "fixture-model"}]])
        self.engine = FakeL2()
        self.engine.install(self)
        self.patch(server, "log", new=lambda _line: None)
        self.remote = self.tmp / "origin.git"
        git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        self.ghdir = self.fake_gh()
        (self.ghdir / "merge_git.txt").touch()
        self.slug = T.new(self.project, "Prepare demo material", "Write the demo notes", paths=["README.md"])["slug"]
        dispatch.run(self.project, self.slug)
        self.initial = S.load_task(self.project, self.slug)
        self.worktree = Path(self.initial["worktree"])
        for key, value in dispatch.l2_env(self.project, self.slug, self.initial["attempt"]).items():
            self.setenv(key, value)

    def commit(self, path, content, message):
        (self.worktree / path).write_text(content)
        git("add", path, cwd=self.worktree)
        git("commit", "-q", "-m", message, cwd=self.worktree)

    def deliver(self, content, deliveries):
        self.commit("README.md", content, "Demo notes")
        result = land.land("Demo notes", cwd=self.worktree, merge=True, wait=0)
        self.assertTrue(result["merged"])
        deliveries.append((result["pr"], git("rev-parse", "main", cwd=self.remote).strip()))
        return self.finish(deliveries)

    def finish(self, deliveries):
        S.write_json(S.task_dir(self.project, self.slug) / "report.json", {
            "landed": {"prs": [{"number": number, "title": f"Delivery {number}", "merged": True, "merge_sha": sha}
                               for number, sha in deliveries],
                       "main_runs": [{"id": "7", "conclusion": "success"}], "deploy": "not-applicable"},
            "review": [], "blocked": ""})
        agent = S.load_task(self.project, self.slug)["agent_id"]
        self.engine.workers[agent].update(state="done", status="exited")
        server.on_l2_finished(self.project, dispatch.poll(self.project)[0])
        done = S.load_task(self.project, self.slug)
        self.assertEqual(done["state"], "done")
        self.assertEqual(S.task_dir(self.project, self.slug).parent, S.archive_dir(self.project))
        return done

    def cleanup(self):
        task = S.load_task(self.project, self.slug)
        notes = dispatch.cleanup_task(self.project, task)
        with S.project_lock(self.project):
            task = S.load_task(self.project, self.slug)
            task["cleaned"] = S.now()
            S.save_task(self.project, task)
        return notes

    def reopen(self, text="What did the demo notes leave out?"):
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, text)
        reopened = S.load_task(self.project, self.slug)
        self.assertEqual(reopened["state"], "blocked")
        self.assertEqual(S.task_dir(self.project, self.slug).parent, S.tasks_dir(self.project))
        self.assertNotIn("cleaned", reopened)
        dispatch.resume(self.project, self.slug)
        running = S.load_task(self.project, self.slug)
        self.assertEqual(running["state"], "running")
        for field in ("attempt", "session_id", "l2_engine", "worktree", "branch", "paths", "prs"):
            self.assertEqual(running[field], self.initial[field] if field != "prs" else reopened["prs"], field)
        self.assertNotIn("reopened", running)
        prompt = self.engine.calls[-1]["prompt"]
        self.assertIn("The operator's message reopens this task, which was done on", prompt)
        self.assertIn(text, prompt)
        return message, prompt

    def test_merged_worktree_and_branch_are_rebuilt_and_the_task_finishes_again(self):
        deliveries = []
        self.deliver("First notes\n", deliveries)
        self.assertTrue(any("removed merged worktree" in note for note in self.cleanup()))
        self.assertFalse(self.worktree.exists())
        self.assertEqual(git("branch", "--list", self.initial["branch"], cwd=self.repo), "")

        message, prompt = self.reopen()
        self.assertIn("the task branch starts again from origin/main", prompt)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree).strip(), deliveries[0][1])
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.worktree).strip(), self.initial["branch"])
        rows = T.task_messages(self.project, self.slug)
        completion = next(row for row in rows if row.get("completion"))
        self.assertIn("PR #101", completion["digest"])
        self.assertLess(rows.index(completion), [row["id"] for row in rows].index(message["id"]))
        self.assertIn("PR #101", (S.task_dir(self.project, self.slug) / "digest.md").read_text(), "the latest completion's until it finishes again")
        kinds = [event["kind"] for event in S.read_events(self.project, self.slug)]
        self.assertIn("reopened", kinds)
        self.assertIn("report-superseded", kinds)
        self.assertIn("worktree-restored", kinds)

        done = self.deliver("First notes\nFollow-up notes\n", deliveries)
        self.assertEqual(done["prs"], [101, 102])
        self.assertIn("PR #102", (S.task_dir(self.project, self.slug) / "digest.md").read_text())
        self.assertEqual(len([row for row in T.task_messages(self.project, self.slug) if row.get("completion")]), 1)
        self.assertEqual(len(self.engine.calls), 2, "one launch and one resume of the same provider conversation")

    def test_a_kept_branch_with_local_commits_is_restored(self):
        self.deliver("First notes\n", [])
        self.commit("README.md", "First notes\nLocal draft\n", "Local draft")
        local = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        # The squash merge leaves the delivered commit off origin/main too.
        self.assertTrue(any("2 commit(s) not on origin/main" in note for note in self.cleanup()))
        self.assertFalse(self.worktree.exists())

        _message, prompt = self.reopen()
        self.assertIn(f"restored on its kept branch at {local[:12]}, with 2 commit(s) not on origin/main", prompt)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree).strip(), local)

    def test_a_worktree_still_present_is_kept(self):
        self.deliver("First notes\n", [])
        _message, prompt = self.reopen()
        self.assertIn("Its worktree was still in place.", prompt)

    def test_cleanup_racing_a_reopening_leaves_the_worktree(self):
        done = self.deliver("First notes\n", [])
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "One more question.")
        self.assertIn("reopened before cleanup", dispatch.cleanup_task(self.project, done))
        self.assertTrue(self.worktree.is_dir())

    def test_only_the_operator_reopens_and_a_rejected_task_stays_closed(self):
        self.deliver("First notes\n", [])
        with self.assertRaisesRegex(T.TransitionError, "only the operator's message reopens a done task"):
            T.message(self.project, self.slug, "l3", "Coordinator note.", summary="Coordinator note")
        self.assertEqual(S.load_task(self.project, self.slug)["state"], "done")
        rejected = T.new(self.project, "Retire the prototype", "Fictional work")["slug"]
        T.reject(self.project, rejected, "No longer needed.")
        with self.assertRaisesRegex(T.TransitionError, "cannot message the L2 in rejected state"):
            T.message(self.project, rejected, T.OPERATOR_MESSAGE_ROLE, "Bring it back.")

    def test_a_planned_task_waits_again_while_its_dependency_is_reopened(self):
        deliveries = []
        self.deliver("First notes\n", deliveries)
        self.reopen()
        planned = T.new(self.project, "Publish the demo", "Fictional work", after=self.slug)
        self.assertIn("planned_wait", planned)
        self.assertIn("planned_wait", T.release_dependency(self.project, planned["slug"]))
        self.finish(deliveries)
        self.assertNotIn("planned_wait", T.release_dependency(self.project, planned["slug"]))

    def test_an_answer_without_new_work_finishes_again_and_each_reopening_keeps_its_completion(self):
        deliveries = []
        self.deliver("First notes\n", deliveries)
        for turn in range(2):
            self.reopen(f"Question {turn + 1}?")
            done = self.finish(deliveries)  # the owner answers in chat and saves a fresh report of the same PR
            self.assertEqual(done["prs"], [101])
        completions = [row for row in T.task_messages(self.project, self.slug) if row.get("completion")]
        self.assertEqual(len(completions), 2)
        self.assertTrue(all("PR #101" in row["digest"] for row in completions))
        self.assertEqual(len(self.engine.calls), 3, "one launch and two resumes of the same provider conversation")

    def test_a_refused_reopening_leaves_the_task_archived_done(self):
        self.deliver("First notes\n", [])
        with S.project_lock(self.project):
            task = S.load_task(self.project, self.slug)
            task["daemon_request"] = {"id": "r1", "operation": "stop", "status": "pending"}
            S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "daemon request r1 owns this task"):
            T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "One more question.")
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "done")
        self.assertNotIn("reopened", task)
        self.assertEqual(S.task_dir(self.project, self.slug).parent, S.archive_dir(self.project))
        self.assertIn("PR #101", (S.task_dir(self.project, self.slug) / "digest.md").read_text())
        self.assertFalse([row for row in T.task_messages(self.project, self.slug) if row.get("completion") or row["role"] == T.OPERATOR_MESSAGE_ROLE])
