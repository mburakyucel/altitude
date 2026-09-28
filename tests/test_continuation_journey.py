"""Two checked deliveries share a real task, worktree and provider conversation.

The bare Git remote and task lifecycle are real; only hosted GitHub and worker execution
use deterministic fixtures. In particular, hosted squash merges do not retain PR ancestry.
"""
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, land, server, state as S, tasks as T, verify


class TestContinuationJourney(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, self_deploy=True, wip=1,
                      routing=[[{"engine": config.ENGINES[-1], "model": "fixture-model"}]])
        self.engine = FakeL2()
        self.engine.install(self)
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.remote = self.tmp / "origin.git"
        git("config", f"url.{self.remote}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        self.ghdir = self.fake_gh()
        (self.ghdir / "merge_git.txt").touch()
        queued = T.new(self.project, "Continue assigned work", "Deliver each authorized change in this task",
                       paths=["README.md", "altitude/"])
        self.slug = queued["slug"]
        dispatch.run(self.project, self.slug)
        self.initial = S.load_task(self.project, self.slug)
        self.worktree = Path(self.initial["worktree"])
        for key, value in dispatch.l2_env(self.project, self.slug, self.initial["attempt"]).items():
            self.setenv(key, value)

    def commit(self, path, content, message):
        target = self.worktree / path
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(content)
        git("add", path, cwd=self.worktree)
        git("commit", "-q", "-m", message, cwd=self.worktree)
        return git("rev-parse", "HEAD", cwd=self.worktree).strip()

    def resume_with_message(self, text):
        before = S.load_task(self.project, self.slug)
        T.block(self.project, self.slug, "Owner turn paused", actor="altd")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, text)
        dispatch.resume(self.project, self.slug)
        current = S.load_task(self.project, self.slug)
        for field in ("slug", "attempt", "session_id", "l2_engine", "launch_model", "worktree", "branch", "paths"):
            self.assertEqual(current[field], self.initial[field], field)
        self.assertNotEqual(current["agent_id"], before["agent_id"])
        self.assertIn(text, self.engine.calls[-1]["prompt"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertIn(message["id"], [row["id"] for row in T.task_messages(self.project, self.slug)])
        return current

    def write_report(self, deliveries):
        report = {"landed": {"prs": [{"number": number, "title": f"Delivery {number}",
                                      "merged": True, "merge_sha": sha} for number, sha in deliveries],
                             "main_runs": [{"id": "7", "conclusion": "success"}],
                             "deploy": "not-applicable"},  # disposable fixture has no deployed service
                  "review": [], "blocked": ""}
        S.write_json(S.task_dir(self.project, self.slug) / "report.json", report)

    def test_merged_delivery_contradiction_returns_to_owner_and_retains_acceptance(self):
        self.commit("README.md", "Delivered change\n", "Delivered change")
        delivery = land.land("Checked delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertTrue(delivery["merged"])
        merge_sha = git("rev-parse", "main", cwd=self.remote).strip()
        self.write_report([(delivery["pr"], merge_sha)])
        path = S.task_dir(self.project, self.slug) / "report.json"
        report = S.read_json(path)
        report.pop("blocked")
        report["follow_ups"] = ["Real-device acceptance remains with this owner."]
        S.write_json(path, report)
        verdict = verify.verify(self.project, self.slug)
        self.assertEqual(verdict["verdict"], "contradicted")
        self.assertIn("report.json lacks `blocked`", verdict["problems"])
        original = T.report(self.project, self.slug, verdict)
        with self.assertRaises(T.TransitionError):
            T.done(self.project, self.slug, digest="Delivery alone is not acceptance")

        message = T.message(self.project, self.slug, "l3",
                            "Correct the missing blocked field; retain the real-device acceptance question.")
        waiting = S.load_task(self.project, self.slug)
        self.assertEqual(waiting["resume_request"], message["id"])
        self.assertIn(self.slug, dispatch.resume_due(self.project))
        self.assertEqual(S.read_json(path), report)
        history = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "report-superseded"]
        self.assertEqual(history[-1]["report"], report)
        self.assertEqual(history[-1]["verified"]["verdict"], "contradicted")
        with self.assertRaises(T.TransitionError):
            T.report(self.project, self.slug, verdict)
        dispatch.resume(self.project, self.slug)
        resumed = S.load_task(self.project, self.slug)
        for key in ("slug", "attempt", "session_id", "worktree", "branch", "prs", "delivery", "paths"):
            self.assertEqual(resumed[key], original[key], key)
        self.assertEqual(resumed["state"], "running")
        self.assertIn(message["text"], self.engine.calls[-1]["prompt"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "contradicted")

        # The original owner publishes the correction and re-parks the retained acceptance.
        report["blocked"] = "Operator: confirm real-device acceptance."
        S.write_json(path, report)
        T.block(self.project, self.slug, report["blocked"], actor="l2", updates={"waiting_on": "operator"})
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "blocked")
        with self.assertRaises(T.TransitionError):
            T.done(self.project, self.slug, digest="Acceptance is still pending")
        current = S.load_task(self.project, self.slug)
        self.assertEqual(current["state"], "blocked")
        self.assertTrue(any(q["status"] == "open" for q in current["questions"]))
        self.assertEqual(current["prs"], [delivery["pr"]])
        self.assertEqual(len(self.engine.calls), 2)

    def test_dirty_deployment_survives_checked_delivery_and_same_owner_followup(self):
        deployment_head = git("rev-parse", "HEAD", cwd=self.repo)
        (self.repo / "README.md").write_text("Deployment staged version\n")
        git("add", "README.md", cwd=self.repo)
        (self.repo / "README.md").write_text("Deployment working version\n")
        (self.repo / "private.txt").write_text("Fictional private deployment note\n")
        index = (self.repo / ".git/index").read_bytes()
        self.commit("README.md", "First authorized delivery\n", "First authorized change")
        first = land.land("First checked delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((first["pr"], first["checks"], first["merged"]), (101, "pass", True))
        self.resume_with_message("Continue the same task with the second authorized delivery.")
        self.commit("README.md", "Second authorized delivery\n", "Second authorized change")
        second = land.land("Second checked delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((second["pr"], second["checks"], second["merged"]), (102, "pass", True))
        self.assertEqual(git("show", "main:README.md", cwd=self.remote), "Second authorized delivery\n")
        notes = dispatch.pull_after_done(self.project, S.load_task(self.project, self.slug))
        self.assertTrue(any("self-deploy refused" in note for note in notes))
        current = S.load_task(self.project, self.slug)
        self.assertEqual((current["state"], current["prs"]), ("running", [101, 102]))
        self.assertFalse(current.get("fault"), "deployment failure is separate from the delivery owner")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), deployment_head)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "README.md").read_text(), "Deployment working version\n")
        self.assertEqual((self.repo / "private.txt").read_text(), "Fictional private deployment note\n")
        self.assertNotIn("private.txt", git("ls-tree", "-r", "--name-only", "main", cwd=self.remote))

    def test_squash_continuation_failed_checks_retry_resume_report_and_archive(self):
        original_base = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        first_commit = self.commit("README.md", "First delivery.\n", "first owned change")
        (self.worktree / "altitude").mkdir()
        (self.worktree / "altitude/fixture.py").write_text("value = 1\n")
        git("add", "altitude/fixture.py", cwd=self.worktree)
        first = land.land("first checked delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((first["pr"], first["checks"], first["merged"]), (101, "pass", True))
        first_head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        first_merge = git("rev-parse", "main", cwd=self.remote).strip()
        self.assertEqual(git("show", "-s", "--format=%P", first_merge, cwd=self.worktree).strip(), original_base)
        self.assertNotEqual(first_head, first_merge)
        self.assertNotIn(first_commit, git("rev-list", "main", cwd=self.remote).splitlines())
        prior_events = S.read_events(self.project, self.slug)
        self.assertEqual(S.load_task(self.project, self.slug)["state"], "running")

        # GitHub may remove the first published head; no-work retries preserve its receipt.
        git("push", "-q", "origin", "--delete", self.initial["branch"], cwd=self.worktree)
        calls = len(self.gh_log())
        retry = land.land("confirm first delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((retry["pr"], retry["checks"], retry["merged"]), (101, "merged", True))
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "checks"], ["pr", "merge"])
                             for call in self.gh_log()[calls:]))
        self.assertEqual(git("branch", "--format=%(refname:short)", cwd=self.remote).splitlines(), ["main"])
        self.write_report([(101, first_merge)])
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "ok")

        # The daemon observes activation while this task still exists, without starting a service.
        notes = dispatch.self_deploy_fast_forward(self.project, self.slug)
        self.assertTrue(any("restart pending" in note for note in notes), notes)
        pending_path = config.MONITOR_DIR / dispatch.RESTART_PENDING
        self.assertEqual(S.read_json(pending_path)["head"], first_merge)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), first_merge)
        self.resume_with_message("Continue this task: deliver the remaining update in another PR.")

        # Main moves independently after the squash. Only the two follow-up commits may enter PR 102.
        (self.repo / "shared.txt").write_text("independent main work\n")
        git("add", "shared.txt", cwd=self.repo)
        git("commit", "-q", "-m", "independent delivery", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        next_base = git("rev-parse", "HEAD", cwd=self.repo).strip()
        self.commit("README.md", "First delivery.\nSecond delivery.\n", "second owned change")
        (self.worktree / "altitude/fixture.py").write_text("value = 2\n")
        git("add", "altitude/fixture.py", cwd=self.worktree)
        (self.worktree / "altitude/extra.py").write_text("extra = True\n")
        git("add", "altitude/extra.py", cwd=self.worktree)
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "contradicted",
                         "a first-delivery report cannot complete unpublished follow-up work")
        (self.ghdir / "checks.json").write_text('[{"bucket": "fail"}]')
        second = land.land("second checked delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((second["pr"], second["checks"], second["merged"]), (102, "fail", False))
        current = S.load_task(self.project, self.slug)
        self.assertEqual(current["prs"], [101, 102])
        self.assertEqual(current["delivery"]["number"], 102)
        self.assertEqual(current["delivery"]["base"], next_base)
        second_head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        self.assertEqual(current["delivery"]["head"], second_head)
        self.assertEqual(git("rev-list", "--count", "origin/main..HEAD", cwd=self.worktree).strip(), "2")
        self.assertEqual(git("diff", "--name-only", "origin/main..HEAD", cwd=self.worktree).splitlines(),
                         ["README.md", "altitude/extra.py", "altitude/fixture.py"])
        self.assertEqual((self.worktree / "shared.txt").read_text(), "independent main work\n")
        self.assertEqual(git("status", "--porcelain", cwd=self.worktree), "")
        self.assertEqual(git("rev-parse", "main", cwd=self.remote).strip(), next_base,
                         "a prior pass cannot merge the failed current candidate")
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "contradicted")

        (self.ghdir / "checks.json").write_text('[{"bucket": "pass"}]')
        checked = land.land("retry second delivery", cwd=self.worktree, merge=True, wait=0)
        self.assertEqual((checked["pr"], checked["checks"], checked["merged"]), (102, "pass", True))
        second_merge = git("rev-parse", "main", cwd=self.remote).strip()
        self.assertEqual(git("show", "-s", "--format=%P", second_merge, cwd=self.worktree).strip(), next_base)
        self.assertNotEqual(second_merge, second_head)
        for path, content in {"README.md": "First delivery.\nSecond delivery.\n",
                              "altitude/fixture.py": "value = 2\n",
                              "altitude/extra.py": "extra = True\n",
                              "shared.txt": "independent main work\n"}.items():
            self.assertEqual(git("show", f"main:{path}", cwd=self.remote), content, path)
        history = S.read_json(self.ghdir / "prs.json")
        self.assertEqual(history["101"]["headRefOid"], first_head)
        self.assertEqual(history["101"]["mergeCommit"]["oid"], first_merge)
        self.assertEqual(len([call for call in self.gh_log() if call[:2] == ["pr", "create"]]), 2)
        self.assertEqual(len([call for call in self.gh_log() if call[:2] == ["pr", "merge"]]), 2)
        self.assertEqual(S.read_events(self.project, self.slug)[:len(prior_events)], prior_events)
        self.assertEqual(verify.verify(self.project, self.slug)["verdict"], "contradicted",
                         "both merges succeeding does not make an earlier-delivery-only report current")

        deployment_head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        resumed = self.resume_with_message("Both deliveries are ready; verify the full task and report completion.")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), deployment_head)
        dispatch.self_deploy_fast_forward(self.project)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), second_merge)
        pending = S.read_json(pending_path)
        self.assertEqual(pending["head"], second_merge)
        self.assertEqual(pending["files"], ["altitude/extra.py", "altitude/fixture.py"])
        self.write_report([(101, first_merge), (102, second_merge)])
        self.engine.workers[resumed["agent_id"]].update(state="done", status="exited")
        finished = dispatch.poll(self.project)
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0].get("died"))
        server.on_l2_finished(self.project, finished[0])
        archived = S.load_task(self.project, self.slug)
        self.assertEqual((archived["state"], archived["verified"]["verdict"], archived["prs"]),
                         ("done", "ok", [101, 102]))
        self.assertEqual(S.task_dir(self.project, self.slug).parent, S.archive_dir(self.project))
        digest = (S.task_dir(self.project, self.slug) / "digest.md").read_text()
        self.assertIn("PR #101", digest)
        self.assertIn("PR #102", digest)
        self.assertEqual(len(self.engine.calls), 3, "one launch and two resumes of the same provider conversation")
        notes = dispatch.cleanup_after_done(self.project, archived)
        self.assertTrue(any("removed merged worktree" in note for note in notes), notes)
        self.assertFalse(self.worktree.exists())
