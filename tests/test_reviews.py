"""Captured second-engine review journeys use real task state and Git checkpoints."""
import hashlib
import concurrent.futures
import json
import http.client
from http.server import ThreadingHTTPServer
import socket
import threading
import subprocess
import unittest
import uuid
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, dispatch, engines, reviews, route, server, state as S, tasks as T, verify


class TestReviews(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        self.task = T.new(self.project, "Review captured revision", "Keep the public result stable.")
        self.slug = self.task["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        self.task.update(state="running", attempt=1, agent_id="owner-one", session_id="owner-session",
                         worktree=str(self.worktree), branch=f"worktree-{self.slug}", l2_engine=config.ENGINES[0])
        S.save_task(self.project, self.task)
        (S.task_dir(self.project, self.slug) / "brief.md").write_text("Acceptance: preserve the public API.\n")
        self.commit("value.py", "VALUE = 1\n")
        self.choice = {"engine": config.ENGINES[1], "model": "fixture-model", "label": "Second engine",
                       "allowance_known": True, "why": "A second configured engine is available"}
        self.pick = self.patch(route, "pick_review", return_value=self.choice)
        self.engine = self.patch(engines, "review", side_effect=self.success)

    def commit(self, filename, text):
        (self.worktree / filename).write_text(text)
        git("add", filename, cwd=self.worktree)
        git("commit", "-q", "-m", f"Update {filename}", cwd=self.worktree)
        return git("rev-parse", "HEAD", cwd=self.worktree).strip()

    def success(self, prompt, **kwargs):
        self.assertTrue(kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"}))
        return {"text": "One concrete finding.", "findings": [{"id": "f1", "severity": "medium", "title": "Missing fallback",
                "body": "An absent value must preserve the public result.", "path": "value.py", "line": 1}],
                "limitations": ["Captured files only; no tests executed."], "error": None,
                "termination_confirmed": True, "usage": {"input": 10, "output": 5}}

    def request(self, actor="l2", **kwargs):
        return reviews.request(self.project, self.slug, actor=actor, request_id=kwargs.pop("request_id", uuid.uuid4().hex),
                               **({"expected_attempt": 1} if actor == "l2" else {}), **kwargs)

    def run_review(self, review=None, **kwargs):
        review = review or self.request()
        return reviews.run(self.project, self.slug, review["id"], actor="l2", expected_attempt=1, **kwargs)

    def assess(self, review):
        return reviews.assess(self.project, self.slug, review["id"], actor="l2", expected_attempt=1,
                              dispositions=[{"finding_id": "f1", "disposition": "dismissed",
                                             "reason": "The API specifies a required value; fixture evidence covers absence."}],
                              reason="Checked the captured finding and the complete final candidate.")

    def pair(self):
        return {"base_sha": git("rev-parse", "origin/main", cwd=self.worktree).strip(),
                "head_sha": git("rev-parse", "HEAD", cwd=self.worktree).strip()}

    def test_detached_project_retains_saved_review_without_selecting_reviewer(self):
        completed = self.run_review()
        task = S.load_task(self.project, self.slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        with config.edit_projects() as projects:
            projects.pop(self.project)
        self.pick.reset_mock()
        saved = reviews.view(self.project, self.slug)
        self.assertEqual(saved["latest"]["id"], completed["id"])
        self.assertEqual(saved["latest"]["result"], completed["result"])
        self.assertEqual(saved["latest"]["coverage"], "unknown")
        self.assertFalse(saved["available"])
        self.assertFalse(saved["subjects"]["proposal"]["available"])
        self.pick.assert_not_called()

    def test_request_dedup_and_source_keep_operator_authority(self):
        source = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Please review the public API.")
        first = self.request(source_id=source["id"], focus="Public API")
        second = self.request(source_id=source["id"], focus="Public API")
        same_id = self.request(request_id=first["id"], source_id=source["id"], focus="Public API")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], same_id["id"])
        self.assertEqual(first["requested_by"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(len(S.load_task(self.project, self.slug)["reviews"]), 1)
        with self.assertRaises(T.TransitionError):
            reviews.withdraw(self.project, self.slug, first["id"], actor="l2", expected_attempt=1, reason="Skip")
        with self.assertRaises(T.TransitionError):
            self.request(request_id=first["id"], focus="Different scope")
        self.engine.assert_not_called()

    def test_restart_cancels_live_orphan_even_when_owner_identity_is_unchanged(self):
        self.request()
        task = S.load_task(self.project, self.slug)
        task["reviews"][-1].update(state="running", generation=task["agent_id"], worker={"unit": "fixture-review"})
        S.save_task(self.project, task)
        with mock.patch.object(engines, "review_active", return_value=True), \
                mock.patch.object(engines, "review_stop", return_value=True) as stop:
            reviews.poll(self.project)
        self.assertEqual(S.load_task(self.project, self.slug)["reviews"][-1]["state"], "cancelled")
        self.assertEqual(reviews.active_count(), 0)
        stop.assert_called_once()
        self.engine.assert_not_called()

    def test_planned_restart_waits_for_review_result_or_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                def during_run(prompt, **kwargs):
                    self.assertIn("adversarial review in flight", server.restart_waiting_for(check_activity=False))
                    with mock.patch.object(server, "_request_restart_unit") as restart:
                        with self.assertRaises(server.RestartBusy):
                            server.restart_service()
                        restart.assert_not_called()
                    if fail:
                        return {"error": "Fixture failure", "termination_confirmed": True}
                    return self.success(prompt, **kwargs)
                self.engine.side_effect = during_run
                previous = S.load_task(self.project, self.slug).get("reviews", [])
                requested = self.request(previous=previous[-1]["id"] if previous else None)
                result = self.run_review(requested)
                self.assertEqual(result["state"], "failed" if fail else "completed")
                self.assertNotIn("adversarial review in flight", server.restart_waiting_for(check_activity=False))
                if not fail:
                    self.assess(result)

    def test_capacity_contention_preserves_accepted_request_for_later_run(self):
        requested = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        before = S.load_task(self.project, self.slug)["reviews"]
        with mock.patch.object(config, "machine_wip", return_value=1):
            with self.assertRaisesRegex(T.TransitionError, "No machine capacity"):
                self.run_review(requested)
        self.assertEqual(S.load_task(self.project, self.slug)["reviews"], before)
        self.engine.assert_not_called()
        result = self.run_review(requested)
        self.assertEqual(result["id"], requested["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.engine.call_count, 1)

    def test_restart_admission_preserves_request_without_launch(self):
        requested = self.request()
        for exclusive in (False, True):
            with self.subTest(exclusive=exclusive):
                with config.restart_lock(exclusive=exclusive), mock.patch.object(config, "restart_in_progress", return_value=not exclusive):
                    with self.assertRaisesRegex(T.TransitionError, "activating an update"):
                        self.run_review(requested)
                self.assertEqual(S.load_task(self.project, self.slug)["reviews"][-1]["state"], "requested")
        self.engine.assert_not_called()
        self.assertEqual(self.run_review(requested)["state"], "completed")

    def test_owner_attempt_and_original_source_are_required(self):
        with self.assertRaises(T.TransitionError):
            reviews.request(self.project, self.slug, actor="l2", request_id=uuid.uuid4().hex, expected_attempt=0)
        with self.assertRaises(T.TransitionError):
            self.request(source_id="missing-original-message")
        source = T.message(self.project, self.slug, "l3", "Consider review", by="l3")
        with self.assertRaises(T.TransitionError):
            self.request(source_id=source["id"])
        self.assertFalse(S.load_task(self.project, self.slug).get("reviews"))

    def test_reported_open_delivery_request_uses_existing_continuation(self):
        task = S.load_task(self.project, self.slug)
        task.update(state="reported", prs=[7])
        S.save_task(self.project, task)
        S.write_json(S.task_dir(self.project, self.slug) / "report.json", {"landed": {"prs": [{"number": 7, "merged": False}]}})
        with mock.patch.object(verify, "gh", return_value={"state": "OPEN"}):
            review = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "blocked")
        self.assertEqual(task["resume_request"], review["id"])
        self.assertIn(review["id"], {row["id"] for row in T.pending(self.project, self.slug)})
        self.assertTrue(task["report_after"])
        self.engine.assert_not_called()

    def test_owner_stop_cancels_attached_review_without_resume(self):
        stopped = self.patch(engines, "stop_l2_worker", return_value="Fixture stopped")
        stop_review = self.patch(engines, "review_stop", return_value=True)
        def during_run(prompt, **kwargs):
            self.assertTrue(kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"}))
            dispatch.stop(self.project, self.slug, by=T.OPERATOR_MESSAGE_ROLE)
            return {"text": "Not current acceptance", "findings": [], "termination_confirmed": True}
        self.engine.side_effect = during_run
        result = self.run_review()
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("result", result)
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["state"], "blocked")
        self.assertTrue(task["stop_id"])
        self.assertFalse(reviews.view(self.project, self.slug)["available"])
        stop_review.assert_called_once()
        stopped.assert_called_once()

    def test_restart_observes_terminated_reviewer_without_relaunch(self):
        review = self.request()
        task = S.load_task(self.project, self.slug)
        task["reviews"][-1].update(state="running", generation=task["agent_id"], worker={"unit": "fixture-review"})
        S.save_task(self.project, task)
        with mock.patch.object(engines, "review_active", return_value=False):
            reviews.poll(self.project)
        self.assertEqual(S.load_task(self.project, self.slug)["reviews"][-1]["state"], "failed")
        self.assertEqual(reviews.active_count(), 0)
        self.assertEqual(self.run_review(review)["state"], "failed")
        self.engine.assert_not_called()

    def test_interruption_retains_capacity_until_termination_is_confirmed(self):
        def interrupted(prompt, **kwargs):
            kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"})
            raise KeyboardInterrupt()
        self.engine.side_effect = interrupted
        review = self.request()
        with mock.patch.object(engines, "review_active", return_value=None), \
                mock.patch.object(reviews, "_termination_fault") as fault, self.assertRaises(KeyboardInterrupt):
            self.run_review(review)
        current = S.load_task(self.project, self.slug)["reviews"][-1]
        self.assertEqual(current["state"], "running")
        self.assertTrue(current["cancel_requested"])
        self.assertEqual(reviews.active_count(), 1)
        fault.assert_called_once()
        self.assertNotIn((self.project, self.slug, review["id"]), reviews._inflight)
        with mock.patch.object(engines, "review_active", return_value=False):
            reviews.poll(self.project)
        self.assertEqual(reviews.active_count(), 0)
        self.assertEqual(self.engine.call_count, 1)

    def test_interruption_with_confirmed_termination_persists_failure_without_fault(self):
        def interrupted(prompt, **kwargs):
            kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"})
            raise SystemExit(7)
        self.engine.side_effect = interrupted
        with mock.patch.object(engines, "review_active", return_value=False), \
                mock.patch.object(reviews, "_termination_fault") as fault, self.assertRaises(SystemExit):
            self.run_review()
        current = S.load_task(self.project, self.slug)["reviews"][-1]
        self.assertEqual(current["state"], "failed")
        self.assertIn("interrupted", current["error"])
        self.assertNotIn("result", current)
        self.assertEqual(reviews.active_count(), 0)
        fault.assert_not_called()

    def test_path_shaped_request_id_cannot_escape_task_artifacts(self):
        for identity in ("../escape", str(self.tmp / "escape"), "review/child", ".", ".."):
            with self.subTest(identity=identity), self.assertRaises(T.TransitionError):
                self.request(request_id=identity)
        self.assertFalse((self.tmp / "escape").exists())

    def test_malformed_context_does_not_reserve_capacity(self):
        review = self.request()
        for context in ("message", 3, [None], [["nested"]]):
            with self.subTest(context=context), self.assertRaises(T.TransitionError):
                self.run_review(review, context_ids=context)
        self.assertEqual(reviews.active_count(), 0)
        self.engine.assert_not_called()

    def test_oversized_files_are_named_and_omitted_while_the_rest_is_reviewed(self):
        large = "x\n" * (1024 * 1024 + 1)
        self.commit("asset.bin", large)
        self.commit("retired.txt", large)
        self.commit("shrunk.txt", large)
        git("push", "origin", "HEAD:main", cwd=self.worktree)
        # The change leaves asset.bin untouched, deletes one large base file and adds another beside ordinary code.
        git("rm", "-q", "retired.txt", cwd=self.worktree)
        (self.worktree / "model.ort").write_text(large + "y")
        (self.worktree / "value.py").write_text("VALUE = 2\n")
        (self.worktree / "shrunk.txt").write_text("small\n")
        git("add", "model.ort", "value.py", "shrunk.txt", cwd=self.worktree)
        git("commit", "-q", "-m", "Swap the large asset", cwd=self.worktree)
        seen = {}

        def capture(prompt, **kwargs):
            snapshot = kwargs["snapshot"]
            seen["files"] = sorted(str(path.relative_to(snapshot / "source")) for path in (snapshot / "source").rglob("*") if path.is_file())
            seen["patch"] = (snapshot / "changes.patch").read_text()
            seen["context"] = json.loads((snapshot / "context.json").read_text())
            return self.success(prompt, **kwargs)
        self.engine.side_effect = capture
        with mock.patch.object(reviews, "_git", wraps=reviews._git) as commands:
            result = self.run_review()
        self.assertEqual(result["state"], "completed")
        omitted = [{"path": "asset.bin", "size": len(large)}, {"path": "model.ort", "size": len(large) + 1},
                   {"path": "retired.txt", "size": len(large)}, {"path": "shrunk.txt", "size": len(large)}]
        self.assertEqual(result["snapshot"]["omitted"], omitted)
        self.assertNotIn("asset.bin", seen["files"])
        self.assertNotIn("model.ort", seen["files"])
        self.assertNotIn("shrunk.txt", seen["files"])
        self.assertNotIn("shrunk.txt", seen["patch"])
        self.assertIn("value.py", seen["files"])
        self.assertIn("+VALUE = 2", seen["patch"])
        self.assertNotIn("model.ort", seen["patch"])
        self.assertNotIn("retired.txt", seen["patch"])
        for row in omitted:
            self.assertIn(f"{row['path']} ({row['size']} bytes)", " ".join(seen["context"]["limitations"]))
        batch = next(call for call in commands.call_args_list if "--batch" in call.args)
        self.assertNotIn(git("rev-parse", "HEAD:model.ort", cwd=self.worktree).strip(), batch.kwargs["stdin"].decode())
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_ordinary_change_records_no_omitted_files(self):
        result = self.run_review()
        self.assertEqual(result["snapshot"]["omitted"], [])
        self.assertNotIn("over 2 MiB", " ".join(result["snapshot"]["limitations"]))

    def test_images_require_selected_owner_account_and_preserve_limitation(self):
        task = S.load_task(self.project, self.slug)
        task["image_messages"] = [{"id": "visual", "at": S.now(), "role": T.OPERATOR_MESSAGE_ROLE,
                                   "text": "Use this state", "images": [{"id": "captured-image"}]}]
        S.save_task(self.project, task)
        failed = self.run_review()
        self.assertEqual(failed["state"], "failed")
        self.assertIn("textual account", failed["error"])
        self.engine.assert_not_called()
        account = T.message(self.project, self.slug, "l2", "The image depicts the compact task menu.")
        selected = self.request(previous=failed["id"])
        result = self.run_review(selected, context_ids=[account["id"]])
        self.assertEqual(result["state"], "completed")
        self.assertIn("visual", result["snapshot"]["context_ids"])
        self.assertIn("Original image bytes are not reviewed", result["snapshot"]["limitations"][0])

    def test_snapshot_matches_git_candidate_and_excludes_untracked_inputs(self):
        self.commit("AGENTS.md", "Keep the public API stable.\n")
        (self.worktree / "untracked-secret.txt").write_text("not selected")
        (self.worktree / ".claude").mkdir(exist_ok=True)
        (self.worktree / ".claude" / "ignored.txt").write_text("ignored")
        source = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Preserve the return value.")
        seen = {}
        def inspect(prompt, **kwargs):
            snapshot = kwargs["snapshot"]
            seen["snapshot"] = snapshot
            seen["context"] = json.loads((snapshot / "context.json").read_text())
            self.assertEqual((snapshot / "source" / "value.py").read_text(), "VALUE = 1\n")
            self.assertEqual((snapshot / "source" / "AGENTS.md").read_text(), "Keep the public API stable.\n")
            self.assertFalse((snapshot / "source" / ".git").exists())
            self.assertFalse((snapshot / "source" / "untracked-secret.txt").exists())
            self.assertFalse((snapshot / "source" / ".claude").exists())
            self.assertNotEqual(snapshot, kwargs["runtime"])
            self.assertNotIn("timeout", kwargs)
            return self.success(prompt, **kwargs)
        self.engine.side_effect = inspect
        result = self.run_review(context_ids=[source["id"]])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["snapshot"]["head"], self.pair()["head_sha"])
        self.assertEqual(result["snapshot"]["tree"], git("rev-parse", "HEAD^{tree}", cwd=self.worktree).strip())
        self.assertEqual(result["snapshot"]["context_ids"], [source["id"]])
        self.assertIn("public API", seen["context"]["brief"])
        expected_patch = git("diff", "origin/main", "HEAD", cwd=self.worktree)
        self.assertEqual((seen["snapshot"] / "changes.patch").read_text(), expected_patch)
        self.assertEqual(result["result"]["limitations"], ["Captured files only; no tests executed."])

    def test_dirty_tracked_changes_and_linked_files_do_not_launch(self):
        review = self.request()
        (self.worktree / "value.py").write_text("uncommitted\n")
        result = self.run_review(review)
        self.assertEqual(result["state"], "failed")
        self.assertIn("Commit", result["error"])
        self.engine.assert_not_called()
        self.commit("value.py", "VALUE = 2\n")
        (self.worktree / "outside").symlink_to(self.tmp)
        git("add", "outside", cwd=self.worktree)
        git("commit", "-q", "-m", "Symlink fixture", cwd=self.worktree)
        retry = self.request(previous=review["id"])
        result = self.run_review(retry)
        self.assertEqual(result["state"], "failed")
        self.assertIn("links", result["error"])
        self.engine.assert_not_called()

    def test_conflict_with_current_main_names_paths_and_records_no_request(self):
        def move_main(text):
            (self.repo / "value.py").write_text(text)
            git("add", "value.py", cwd=self.repo)
            git("commit", "-q", "-m", "Move main", cwd=self.repo)
            git("push", "-q", "origin", "main", cwd=self.repo)

        def assert_conflict(error):
            self.assertIn("conflicts with current main in value.py", error)
            self.assertIn("Reconcile the branch with origin/main", error)

        review = self.request()
        move_main("VALUE = 'main'\n")
        failed = self.run_review(review)
        self.assertEqual(failed["state"], "failed")
        assert_conflict(failed["error"])
        self.engine.assert_not_called()
        with self.assertRaises(T.TransitionError) as refused:
            self.request(previous=review["id"])
        assert_conflict(str(refused.exception))
        self.assertEqual([r["id"] for r in S.load_task(self.project, self.slug)["reviews"]], [review["id"]])

        git("merge", "-q", "-X", "ours", "-m", "Reconcile main", "origin/main", cwd=self.worktree)
        completed = self.run_review(self.request(previous=review["id"]))
        self.assertEqual(completed["state"], "completed")
        move_main("VALUE = 'moved again'\n")
        with self.assertRaises(T.TransitionError) as refused:
            self.assess(completed)
        assert_conflict(str(refused.exception))
        self.assertFalse(S.load_task(self.project, self.slug)["reviews"][-1].get("reconciled"))
        with self.assertRaises(T.TransitionError) as silent:
            reviews._git(self.worktree, "rev-parse", "--verify", "-q", "missing")
        self.assertTrue(str(silent.exception).endswith("git rev-parse failed: exit status 1"))

    def test_owner_can_edit_during_review_but_snapshot_stays_exact(self):
        checkpoint = self.pair()["head_sha"]
        def edit_after_capture(prompt, **kwargs):
            self.commit("value.py", "VALUE = 2\n")
            self.assertEqual((kwargs["snapshot"] / "source" / "value.py").read_text(), "VALUE = 1\n")
            return self.success(prompt, **kwargs)
        self.engine.side_effect = edit_after_capture
        result = self.run_review()
        self.assertEqual(result["snapshot"]["head"], checkpoint)
        self.assertEqual(result["coverage"], "earlier")
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())
        assessed = self.assess(result)
        self.assertEqual(assessed["coverage"], "assessed")
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_export_attributes_cannot_hide_or_transform_reviewed_source(self):
        self.commit("hidden.py", "TRACKED_VALUE = 7\n")
        self.commit("version.txt", "$Format:%H$\n")
        self.commit(".gitattributes", "hidden.py export-ignore\nversion.txt export-subst\n")
        def inspect(prompt, **kwargs):
            source = kwargs["snapshot"] / "source"
            self.assertTrue((source / "hidden.py").is_file(), "Export attributes must not hide tracked code from review")
            self.assertEqual((source / "hidden.py").read_text(), "TRACKED_VALUE = 7\n")
            self.assertEqual((source / "version.txt").read_text(), "$Format:%H$\n")
            return self.success(prompt, **kwargs)
        self.engine.side_effect = inspect
        self.assertEqual(self.run_review()["state"], "completed")

    def test_unknown_context_selection_cannot_silently_omit_requested_evidence(self):
        result = self.run_review(context_ids=["unavailable-source"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("unavailable message", result["error"])
        self.engine.assert_not_called()

    def test_selected_context_keeps_authority_corrections_and_marks_omitted_owner_evidence(self):
        original = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Keep all results.")
        owner = T.message(self.project, self.slug, "l2", "Test evidence: regression suite passes.", expected_attempt=1)
        coordinator = T.message(self.project, self.slug, "l3", "The approved scope includes missing values.", by="l3")
        correction = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Correction: exclude expired results.")
        captured = {}
        def inspect(prompt, **kwargs):
            captured.update(json.loads((kwargs["snapshot"] / "context.json").read_text()))
            return self.success(prompt, **kwargs)
        self.engine.side_effect = inspect
        result = self.run_review(context_ids=[original["id"]])
        self.assertEqual({row["id"] for row in captured["messages"]},
                         {original["id"], coordinator["id"], correction["id"]})
        self.assertNotIn(owner["id"], result["snapshot"]["context_ids"])
        self.assertEqual(result["coverage"], "current")
        self.assertEqual(result["snapshot"]["captured_context_hash"], reviews._hash(captured))
        self.assertNotEqual(result["snapshot"]["captured_context_hash"], result["snapshot"]["context_hash"])
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_default_context_includes_owner_test_evidence(self):
        owner = T.message(self.project, self.slug, "l2", "Verified empty and failure paths with deterministic tests.", expected_attempt=1)
        result = self.run_review()
        self.assertIn(owner["id"], result["snapshot"]["context_ids"])
        self.assertEqual(result["coverage"], "current")

    def test_total_snapshot_and_file_count_bounds_precede_blob_content_reads(self):
        # Sparse index entries represent real tracked files without duplicating large fixture bytes.
        self.commit("payload.txt", "x" * (2 << 20))
        oid = git("rev-parse", "HEAD:payload.txt", cwd=self.worktree).strip()
        for count, prefix in ((33, "large"), (10001, "many")):
            with self.subTest(count=count):
                if prefix == "many":
                    git("reset", "--hard", "HEAD~1", cwd=self.worktree)
                    oid = git("rev-parse", "HEAD:value.py", cwd=self.worktree).strip()
                paths = [f"{prefix}/{index:05}.txt" for index in range(count)]
                index = subprocess.run(["git", "update-index", "--index-info"], cwd=self.worktree,
                                       input="".join(f"100644 {oid}\t{path}\n" for path in paths),
                                       text=True, capture_output=True)
                self.assertEqual(index.returncode, 0, index.stderr)
                git("update-index", "--skip-worktree", *paths, cwd=self.worktree)
                git("commit", "-q", "-m", "Sparse tracked fixture", cwd=self.worktree)
                previous = S.load_task(self.project, self.slug).get("reviews", [])
                request = self.request(previous=previous[-1]["id"] if previous else None)
                with mock.patch.object(reviews, "_git", wraps=reviews._git) as git_calls:
                    result = self.run_review(request)
                self.assertEqual(result["state"], "failed")
                self.assertIn("bounds", result["error"].lower())
                self.assertFalse(any("--batch" in call.args for call in git_calls.call_args_list))
        self.engine.assert_not_called()

    def test_no_worker_orphan_keeps_capacity_reserved(self):
        self.engine.side_effect = lambda *args, **kwargs: {"error": "Launch interrupted", "termination_confirmed": False}
        result = self.run_review()
        reviews.poll(self.project)
        current = reviews.view(self.project, self.slug)["latest"]
        self.assertEqual(current["id"], result["id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(reviews.active_count(), 1)
        self.assertIn("unconfirmed", current["error"].lower())
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["state"], task["fault"]), ("blocked", "review-termination"))
        self.assertEqual(self.engine.call_count, 1)

    def test_disconnected_caller_cancels_request_and_releases_confirmed_capacity(self):
        def disconnected(prompt, **kwargs):
            self.assertTrue(kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"}))
            self.assertFalse(kwargs["on_wait"]())
            return {"error": "Caller disconnected", "termination_confirmed": True}
        self.engine.side_effect = disconnected
        result = self.run_review(on_wait=lambda: False)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(reviews.active_count(), 0)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())

    def test_http_stream_returns_result_and_disconnect_cancels_real_request(self):
        class Handler(server.Handler):
            def log_message(self, *_args):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        for disconnect in (False, True):
            with self.subTest(disconnect=disconnect):
                self.commit("value.py", f"VALUE = {3 if disconnect else 2}\n")
                previous = S.load_task(self.project, self.slug).get("reviews", [])
                if previous:
                    self.assess(previous[-1])
                request = self.request(previous=previous[-1]["id"] if previous else None)
                observed, finished = threading.Event(), threading.Event()
                release = threading.Event()
                def run_engine(prompt, **kwargs):
                    self.assertTrue(kwargs["on_start"]({"unit": "fixture", "pid": 12345, "started_ticks": "1"}))
                    try:
                        self.assertTrue(kwargs["on_wait"]())
                        observed.set()
                        self.assertTrue(release.wait(5))
                        if disconnect:
                            for _ in range(20):
                                if not kwargs["on_wait"]():
                                    return {"error": "Caller disconnected", "termination_confirmed": True}
                            self.fail("Disconnected HTTP caller was not detected")
                        return self.success(prompt, **kwargs)
                    finally:
                        finished.set()
                self.engine.side_effect = run_engine
                client = http.client.HTTPConnection(*httpd.server_address, timeout=5)
                body = json.dumps({"project": self.project, "slug": self.slug, "attempt": 1, "review_id": request["id"]})
                try:
                    client.request("POST", "/api/task/review/run", body, {"Content-Type": "application/json"})
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(observed.wait(5))
                    if disconnect:
                        response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                        response.close()
                        client.close()
                    release.set()
                    if not disconnect:
                        result = json.load(response)
                        self.assertTrue(result["ok"])
                        self.assertEqual(result["review"]["state"], "completed")
                    self.assertTrue(finished.wait(5))
                finally:
                    release.set()
                    client.close()
        # server_close below joins the request handler, including the durable final receipt.
        httpd.shutdown()
        httpd.server_close()
        result = reviews.view(self.project, self.slug)["latest"]
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(reviews.active_count(), 0)

    def test_changed_owner_cancels_during_wait(self):
        def changed(prompt, **kwargs):
            self.assertTrue(kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"}))
            task = S.load_task(self.project, self.slug)
            task["agent_id"] = "new-owner"
            S.save_task(self.project, task)
            self.assertFalse(kwargs["on_wait"]())
            return {"error": "Owner changed", "termination_confirmed": True}
        self.engine.side_effect = changed
        result = self.run_review()
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(reviews.active_count(), 0)

    def test_poller_cannot_reconcile_before_completed_result_is_durable(self):
        original_save = S.save_task
        observed = []
        def save(project, task):
            if task.get("reviews") and task["reviews"][-1]["state"] == "completed":
                observed.append(True)
                reviews.poll(project)
            return original_save(project, task)
        with mock.patch.object(S, "save_task", side_effect=save), mock.patch.object(engines, "review_active", return_value=False) as active:
            result = self.run_review()
        self.assertEqual(result["state"], "completed")
        self.assertTrue(observed)
        active.assert_not_called()

    def test_running_and_historical_reviews_offer_only_valid_actions(self):
        def inspect_running(prompt, **kwargs):
            latest = reviews.view(self.project, self.slug)["latest"]
            self.assertTrue(latest["can_cancel"])
            self.assertFalse(latest["can_withdraw"])
            return {"error": "Engine exited", "termination_confirmed": True}
        self.engine.side_effect = inspect_running
        failed = self.run_review()
        self.request(previous=failed["id"])
        history = reviews.view(self.project, self.slug)["history"]
        self.assertEqual(len(history), 2)
        for key in ("can_withdraw", "can_cancel", "can_retry", "can_review_latest"):
            self.assertFalse(history[0][key], key)

    def test_request_and_merge_share_actual_nonblocking_lock(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with reviews.merge_lock(self.project, self.slug):
                request = pool.submit(self.request, actor=T.OPERATOR_MESSAGE_ROLE)
                with self.assertRaisesRegex(T.TransitionError, "merge"):
                    request.result(timeout=3)
            admitted = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(admitted["state"], "requested")
        self.assertEqual(len(S.load_task(self.project, self.slug)["reviews"]), 1)

    def test_next_delivery_can_request_review_after_merged_checkpoint(self):
        completed = self.run_review()
        self.commit("value.py", "VALUE = 2\n")
        self.assess(completed)
        task = S.load_task(self.project, self.slug)
        task["review_merged_head"] = self.pair()["head_sha"]
        task["reviews"][-1]["merged_head"] = task["review_merged_head"]
        S.save_task(self.project, task)
        with self.assertRaises(T.TransitionError):
            self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        self.commit("value.py", "VALUE = 4\n")
        reviews.require_merge(self.project, self.slug, self.pair())
        saved = reviews.view(self.project, self.slug)["subjects"]["changes"]["latest"]
        self.assertEqual(saved["id"], completed["id"])
        self.assertTrue(saved["can_review_latest"])
        self.assertFalse(saved["can_withdraw"])
        later = self.request(actor=T.OPERATOR_MESSAGE_ROLE, previous=completed["id"])
        self.assertNotEqual(later["id"], completed["id"])
        self.assertEqual(later["state"], "requested")
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())

    def test_owner_replacement_before_start_cancels_result(self):
        def replace_owner(prompt, **kwargs):
            task = S.load_task(self.project, self.slug)
            task.update(attempt=2, agent_id="replacement-owner")
            S.save_task(self.project, task)
            self.assertFalse(kwargs["on_start"]({"unit": "fixture", "pid": 12345, "started_ticks": "1"}))
            return {"termination_confirmed": True, "text": "Not current acceptance", "findings": []}
        self.engine.side_effect = replace_owner
        result = self.run_review()
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("result", result)
        with self.assertRaises(T.TransitionError):
            self.request()

    def test_findings_need_complete_evidenced_dispositions(self):
        result = self.run_review()
        self.assertEqual(result["coverage"], "current")
        for dispositions in ([], [{"finding_id": "f1", "disposition": "fixed", "reason": ""}],
                             [{"finding_id": "other", "disposition": "fixed", "reason": "Evidence"}]):
            with self.subTest(dispositions=dispositions), self.assertRaises(T.TransitionError):
                reviews.assess(self.project, self.slug, result["id"], actor="l2", expected_attempt=1,
                               dispositions=dispositions, reason="Checked candidate")
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_open_findings_record_honestly_but_never_clear_merge(self):
        result = self.run_review()
        assess = lambda disposition, reason="Evidence", actor="l2", attempt=1: reviews.assess(
            self.project, self.slug, result["id"], actor=actor, expected_attempt=attempt, reason="Checked candidate",
            dispositions=[{"finding_id": "f1", "disposition": disposition, "reason": reason}])
        for disposition, reason, actor, attempt in (("open", "", "l2", 1), ("pending", "Evidence", "l2", 1),
                                                    ("open", "Evidence", T.OPERATOR_MESSAGE_ROLE, None), ("open", "Evidence", "l2", 2)):
            with self.subTest(disposition=disposition, actor=actor, attempt=attempt), self.assertRaises(T.TransitionError):
                assess(disposition, reason, actor, attempt)
        recorded = assess("open", "Needs a live experiment outside this task.")
        self.assertEqual(recorded["unresolved"], ["f1"])
        self.assertEqual(recorded["reconciled"]["reason"], "Checked candidate")
        self.assertEqual(recorded["result"]["findings"][0]["id"], "f1")
        with self.assertRaises(T.TransitionError) as refused:
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assertNotIsInstance(refused.exception, reviews.AssessmentRequired)
        self.assertIn("unresolved findings: f1", str(refused.exception))
        # Code changes still leave the open finding refusing merge rather than asking for reassessment only.
        self.commit("value.py", "VALUE = 2\n")
        with self.assertRaises(T.TransitionError) as refused:
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assertIn("unresolved findings", str(refused.exception))
        latest = reviews.view(self.project, self.slug)["latest"]
        self.assertFalse(latest["can_review_latest"] or latest["can_review_again"])
        resolved = assess("fixed", "Added the fallback and its regression test.")
        self.assertEqual(resolved["unresolved"], [])
        reviews.require_merge(self.project, self.slug, self.pair())
        self.assertEqual(S.load_task(self.project, self.slug)["reviews"][-1]["result"], recorded["result"])

    def test_open_findings_keep_an_operator_request_owner_unwaivable(self):
        result = self.run_review(self.request(actor=T.OPERATOR_MESSAGE_ROLE))
        reviews.assess(self.project, self.slug, result["id"], actor="l2", expected_attempt=1, reason="Checked candidate",
                       dispositions=[{"finding_id": "f1", "disposition": "open", "reason": "Unresolved design risk."}])
        with self.assertRaises(T.TransitionError):
            reviews.withdraw(self.project, self.slug, result["id"], actor="l2", expected_attempt=1, reason="Skip")
        # A replacement that finds nothing cannot launder the known open finding out of the merge gate.
        for actor in ("l2", T.OPERATOR_MESSAGE_ROLE):
            with self.subTest(actor=actor), self.assertRaisesRegex(T.TransitionError, "open findings"):
                self.request(actor=actor, previous=result["id"])
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.engine.side_effect = lambda prompt, **kwargs: (kwargs["on_start"]({"unit": "u", "pid": 1, "started_ticks": "1"}) and
                                                            {"termination_confirmed": True, "text": "No findings", "findings": []})
        reviews.assess(self.project, self.slug, result["id"], actor="l2", expected_attempt=1, reason="Checked candidate",
                       dispositions=[{"finding_id": "f1", "disposition": "fixed", "reason": "Added the fallback."}])
        replacement = self.run_review(self.request(previous=result["id"]))
        self.assertEqual(replacement["requested_by"], T.OPERATOR_MESSAGE_ROLE)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())

    def test_new_code_context_or_merge_pair_invalidates_assessment(self):
        result = self.run_review()
        self.assess(result)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, {**self.pair(), "head_sha": "a" * 40})
        self.commit("value.py", "VALUE = 3\n")
        with self.assertRaises(reviews.AssessmentRequired) as stale:
            reviews.require_merge(self.project, self.slug, self.pair())
        evidence = stale.exception.stale_reviews[0]
        self.assertEqual(evidence['id'], result['id'])
        self.assertEqual(set(evidence['changes']), {'head', 'tree'})
        self.assertIn(result['id'] + ' (changes)', str(stale.exception))
        assessed = self.assess(result)
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Correction: retain the empty result too.")
        self.assertEqual(reviews.view(self.project, self.slug)["latest"]["coverage"], "earlier")
        with self.assertRaises(reviews.AssessmentRequired) as stale:
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assertEqual(set(stale.exception.stale_reviews[0]['changes']), {'context_hash'})
        self.assertIn('alt task review assess --review-id ' + result['id'], str(stale.exception))
        self.assertIn('alt task messages ' + self.slug, str(stale.exception))
        self.assertIn(assessed['reconciled']['at'], str(stale.exception))
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_unfinished_request_takes_precedence_over_stale_assessment(self):
        completed = self.run_review()
        self.assess(completed)
        self.request(subject='proposal')
        T.message(self.project, self.slug, 'l3', 'New context invalidates the earlier assessment.')
        with self.assertRaises(T.TransitionError) as refused:
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assertNotIsInstance(refused.exception, reviews.AssessmentRequired)
        self.assertIn('must finish', str(refused.exception))

    def test_changed_main_base_requires_new_candidate_assessment(self):
        result = self.run_review()
        self.assess(result)
        (self.repo / "other.txt").write_text("Other owner's change\n")
        git("add", "other.txt", cwd=self.repo)
        git("commit", "-q", "-m", "Advance main", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", "main", cwd=self.worktree)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_assessment_preserves_concurrent_landing_fetch_receipt(self):
        result = self.run_review()
        branch = 'worktree-' + self.slug
        git('push', '-q', 'origin', branch, cwd=self.worktree)
        git('fetch', '-q', 'origin', branch, cwd=self.worktree)
        fetched_head = git('rev-parse', 'FETCH_HEAD', cwd=self.worktree)
        self.assertNotEqual(fetched_head, git('rev-parse', 'origin/main', cwd=self.worktree))
        self.assess(result)
        self.assertEqual(git('rev-parse', 'FETCH_HEAD', cwd=self.worktree), fetched_head)

    def test_failure_retry_preserves_operator_requirement(self):
        review = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        self.engine.side_effect = lambda *args, **kwargs: {"error": "Review engine exited", "termination_confirmed": True}
        failed = self.run_review(review)
        self.assertEqual(failed["state"], "failed")
        retry = self.request(previous=review["id"])
        self.assertEqual(retry["requested_by"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(retry["previous"], review["id"])
        with self.assertRaises(T.TransitionError):
            reviews.withdraw(self.project, self.slug, retry["id"], actor="l2", expected_attempt=1, reason="Skip")
        reviews.withdraw(self.project, self.slug, retry["id"], actor=T.OPERATOR_MESSAGE_ROLE, reason="Operator skips review")
        reviews.require_merge(self.project, self.slug, self.pair())
        self.assertEqual(self.engine.call_count, 1)

    def test_unavailable_or_lost_second_engine_is_explicit_and_does_not_launch(self):
        self.pick.return_value = {"engine": None, "why": "Only one engine is installed"}
        with self.assertRaisesRegex(T.TransitionError, "Only one engine"):
            self.request()
        view = reviews.view(self.project, self.slug)
        self.assertFalse(view["available"])
        self.assertEqual(view["history"], [])
        self.pick.return_value = self.choice
        review = self.request()
        self.pick.return_value = {"engine": None, "why": "Engine unavailable"}
        failed = self.run_review(review)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("no longer available", failed["error"])
        self.engine.assert_not_called()

    def test_cancel_before_worker_start_does_not_become_acceptance(self):
        review = self.request()
        def cancel_before_start(prompt, **kwargs):
            reviews.cancel(self.project, self.slug, review["id"], actor="l2", expected_attempt=1, reason="Stop review")
            self.assertFalse(kwargs["on_start"]({"unit": "fixture", "pid": 12345, "started_ticks": "1"}))
            return {"termination_confirmed": True, "text": "Should not be acceptance", "findings": []}
        self.engine.side_effect = cancel_before_start
        result = self.run_review(review)
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("result", result)
        self.assertEqual(reviews.active_count(), 0)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())

    def test_unconfirmed_termination_keeps_capacity_and_prevents_second_launch(self):
        self.engine.side_effect = lambda *args, **kwargs: {"error": "Interrupted", "termination_confirmed": False}
        result = self.run_review()
        self.assertEqual(result["state"], "running")
        self.assertEqual(reviews.active_count(), 1)
        repeated = self.run_review(result)
        self.assertEqual(repeated["id"], result["id"])
        self.assertEqual(self.engine.call_count, 1)
        with self.assertRaises(T.TransitionError):
            reviews.withdraw(self.project, self.slug, result["id"], actor="l2", expected_attempt=1, reason="Skip")

    def test_inflight_retry_and_another_task_cannot_launch_extra_reviewers(self):
        other = T.new(self.project, "Other owner", "Another independent task.")
        other_worktree = add_worktree(self.repo, other["slug"])
        other.update(state="running", attempt=1, agent_id="other-owner", l2_engine=config.ENGINES[0],
                     worktree=str(other_worktree), branch=f"worktree-{other['slug']}")
        S.save_task(self.project, other)
        review = self.request()
        def during_run(prompt, **kwargs):
            self.assertEqual(self.run_review(review)["state"], "running")
            duplicate = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
            self.assertEqual(duplicate["id"], review["id"])
            with self.assertRaisesRegex(T.TransitionError, "Another cross-engine review"):
                reviews.request(self.project, other["slug"], actor=T.OPERATOR_MESSAGE_ROLE, request_id=uuid.uuid4().hex)
            return self.success(prompt, **kwargs)
        self.engine.side_effect = during_run
        self.assertEqual(self.run_review(review)["state"], "completed")
        self.assertEqual(self.engine.call_count, 1)
        self.assertEqual(reviews.active_count(), 0)

    def test_view_is_read_only_and_completed_run_is_not_repeated(self):
        result = self.run_review()
        folder = S.task_dir(self.project, self.slug)
        before = {p.relative_to(folder): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}
        self.assertEqual(reviews.view(self.project, self.slug)["latest"]["state"], "completed")
        after = {p.relative_to(folder): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.run_review(result)["id"], result["id"])
        duplicate = self.request()
        self.assertEqual(duplicate["id"], result["id"])
        self.assertEqual(self.engine.call_count, 1)
        with self.assertRaisesRegex(T.TransitionError, "Assess the completed"):
            self.request(previous=result["id"])
        self.assess(result)
        rerun = self.request(previous=result["id"])
        self.assertNotEqual(rerun["id"], result["id"])
        self.assertEqual(self.engine.call_count, 1)

    def test_proposal_before_code_preserves_open_question_through_resume_and_review(self):
        git("reset", "--hard", "origin/main", cwd=self.worktree)
        proposal = T.message(self.project, self.slug, "l2", "Proposal: preserve cursors while filtering deleted rows.")
        task = T.block(self.project, self.slug, "Use this pagination design?", actor="l2", expected_attempt=1)
        question = next(q for q in task["questions"] if q["status"] == "open")
        original = json.loads(json.dumps(question))
        available = reviews.view(self.project, self.slug)
        self.assertTrue(available["subjects"]["proposal"]["available"])
        self.assertFalse(available["subjects"]["changes"]["available"])
        requested = self.request(actor=T.OPERATOR_MESSAGE_ROLE, subject="proposal")
        task = S.load_task(self.project, self.slug)
        self.assertEqual([q for q in task["questions"] if q["status"] == "open"], [original])
        self.assertEqual(task["resume_request"], requested["id"])
        with self.assertRaisesRegex(T.TransitionError, "Resume the current owner"):
            self.run_review(requested, proposal_id=proposal["id"])
        claim = T.claim_resume(self.project, self.slug)
        self.assertIn(requested["id"], [row["id"] for row in claim["messages"]])
        T.resume(self.project, self.slug, agent_id="resumed-owner", expected_claim=claim["id"], input_delivered=True)
        result = self.run_review(requested, proposal_id=proposal["id"], context_ids=[])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["snapshot"]["head"], result["snapshot"]["base"])
        self.assertEqual(result["snapshot"]["proposal"]["text"], proposal["text"])
        snapshot = S.task_dir(self.project, self.slug) / "reviews" / result["id"] / "snapshot"
        context = json.loads((snapshot / "context.json").read_text())
        self.assertEqual(context["proposal"]["id"], proposal["id"])
        self.assertIn(proposal["id"], result["snapshot"]["context_ids"])
        self.assertEqual((snapshot / "changes.patch").read_text(), "")
        self.assertIn("A proposal review is not implementation review", self.engine.call_args.args[0])
        self.assess(result)
        task = S.load_task(self.project, self.slug)
        self.assertEqual([q for q in task["questions"] if q["status"] == "open"], [original])
        self.assertTrue(reviews.view(self.project, self.slug)["subjects"]["proposal"]["latest"])
        self.assertIsNone(reviews.view(self.project, self.slug)["subjects"]["changes"]["latest"])
        self.assertEqual(self.engine.call_count, 1)
        task = T.block(self.project, self.slug, "Use this pagination design?", actor="l2", expected_attempt=1)
        self.assertEqual(task["state"], "blocked")
        self.assertEqual([q for q in task["questions"] if q["status"] == "open"], [original])

    def held_pr_question(self, text="Merge PR #42?"):
        task = S.load_task(self.project, self.slug)
        task.update(hold_merge="Operator review before merge", prs=[42], delivery={"number": 42, "head": "a" * 40, "at": S.now()})
        S.save_task(self.project, task)
        return {"questions": [{"question": text, "recommended_key": "approve", "options": [
            {"key": "approve", "label": "Approve merge", "text": "Approved: merge PR #42."},
            {"key": "changes", "label": "Request changes", "text": "Hold PR #42 for changes."}]}]}

    def test_changes_review_keeps_held_pr_merge_question_open(self):
        task = T.block(self.project, self.slug, "Merge PR #42?", actor="l2", expected_attempt=1,
                       updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions=self.held_pr_question())
        [original] = json.loads(json.dumps([q for q in task["questions"] if q["status"] == "open"]))
        cards = T.decisions(self.project)
        self.assertEqual([row["kind"] for row in cards], ["asks"])
        self.assertTrue(reviews.view(self.project, self.slug)["subjects"]["changes"]["available"])
        requested = self.request()
        self.assertEqual(S.load_task(self.project, self.slug)["resume_request"], requested["id"])
        claim = T.claim_resume(self.project, self.slug)
        T.resume(self.project, self.slug, agent_id="resumed-owner", expected_claim=claim["id"], input_delivered=True)
        result = self.run_review(requested)
        self.assertEqual(result["state"], "completed")
        self.assess(result)
        task = T.block(self.project, self.slug, "Merge PR #42?", actor="l2", expected_attempt=1)
        self.assertEqual([q for q in task["questions"] if q["status"] == "open"], [original])
        self.assertEqual(T.decisions(self.project), cards)
        self.assertEqual(task["hold_merge"], "Operator review before merge")
        self.assertIsNone(task.get("merge_approval"))
        self.assertIsNone(T.approved_pr(self.project, task))
        reviews.require_merge(self.project, self.slug, self.pair())
        self.assertEqual(self.engine.call_count, 1)

    def test_other_open_questions_refuse_changes_review(self):
        refusal = "Settle the open question before requesting changes review"
        for why, questions in (
                ("freeform question about the PR", None),
                ("question with options about another PR", {"questions": [{**self.held_pr_question("Merge PR #420 first?")["questions"][0],
                                                                             "options": [{"key": "go", "label": "Go", "text": "Merge PR #420."}],
                                                                             "recommended_key": "go"}]}),
                ("merge question without a held PR", "unheld")):
            with self.subTest(why):
                task = S.load_task(self.project, self.slug)
                task.update(state="running", questions=[], question_groups=[], hold_merge=None, prs=[], delivery=None,
                            resume_after=None, resume_request=None)
                S.save_task(self.project, task)
                if questions == "unheld":
                    questions = self.held_pr_question()
                    task = S.load_task(self.project, self.slug)
                    task.update(hold_merge=None)
                    S.save_task(self.project, task)
                else:
                    self.held_pr_question()
                T.block(self.project, self.slug, "Merge PR #42 as it stands?", actor="l2", expected_attempt=1,
                        questions=questions)
                self.assertEqual(reviews.view(self.project, self.slug)["subjects"]["changes"]["why"],
                                 refusal + "; only the held PR's merge question can stay open.")
                with self.assertRaisesRegex(T.TransitionError, refusal):
                    self.request()
        self.engine.assert_not_called()

    def test_proposal_source_and_merge_approval_require_owner_reassessment(self):
        proposal = T.message(self.project, self.slug, "l2", "Proposal: preserve the public result")
        review = self.run_review(self.request(subject="proposal"), proposal_id=proposal["id"])
        self.assess(review)
        self.commit("value.py", "VALUE = 2\n")
        latest = reviews.view(self.project, self.slug)["subjects"]["proposal"]["latest"]
        self.assertEqual(latest["coverage"], "earlier")
        self.assertEqual(latest["snapshot"]["proposal"]["text"], proposal["text"])
        with self.assertRaisesRegex(T.TransitionError, "changed after review assessment"):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(review)
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Approve merging this feature")
        with self.assertRaisesRegex(T.TransitionError, "changed after review assessment"):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(review)
        reviews.require_merge(self.project, self.slug, self.pair())
        self.assertEqual(self.engine.call_count, 1)

    def test_proposal_input_and_revisions_bind_freshness_without_code_changes(self):
        proposal = T.message(self.project, self.slug, "l2", "Proposal version one")
        review = self.run_review(self.request(subject="proposal"), proposal_id=proposal["id"])
        self.assess(review)
        reviews.require_merge(self.project, self.slug, self.pair())
        revised = T.message(self.project, self.slug, "l2", "Proposal version two with cursor continuity")
        self.assertEqual(reviews.view(self.project, self.slug)["subjects"]["proposal"]["latest"]["coverage"], "earlier")
        with self.assertRaisesRegex(T.TransitionError, "changed after review assessment"):
            reviews.require_merge(self.project, self.slug, self.pair())
        assessed = reviews.assess(self.project, self.slug, review["id"], actor="l2", expected_attempt=1,
                                  dispositions=[{"finding_id": "f1", "disposition": "fixed", "reason": "Second proposal adds the missing constraint"}],
                                  reason="Reviewed the revised proposal against original findings", proposal_id=revised["id"])
        self.assertEqual(assessed["reconciled"]["proposal_id"], revised["id"])
        self.assertEqual(assessed["coverage"], "assessed")
        self.assertEqual(assessed["snapshot"]["proposal"]["text"], proposal["text"])
        reviews.require_merge(self.project, self.slug, self.pair())

    def test_missing_proposal_never_invokes_provider_and_retry_preserves_subject(self):
        result = self.run_review(self.request(subject="proposal"))
        self.assertEqual(result["state"], "failed")
        self.assertIn("proposal-message", result["error"])
        self.engine.assert_not_called()
        authority = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Please review the proposal")
        retry = self.request(previous=result["id"])
        result = self.run_review(retry, proposal_id=authority["id"])
        self.assertEqual(result["state"], "failed")
        self.engine.assert_not_called()
        self.assertEqual(result["subject"], "proposal")

    def test_subject_requests_do_not_hide_unresolved_other_subject_or_its_freshness(self):
        changes = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        with self.assertRaisesRegex(T.TransitionError, "existing review"):
            self.request(subject="proposal")
        self.assertEqual(len(S.load_task(self.project, self.slug)["reviews"]), 1)
        changes = self.run_review(changes)
        self.assess(changes)
        proposal = T.message(self.project, self.slug, "l2", "Proposal for follow-up behavior")
        proposed = self.run_review(self.request(subject="proposal"), proposal_id=proposal["id"])
        self.assess(proposed)
        view = reviews.view(self.project, self.slug)
        self.assertEqual(view["subjects"]["changes"]["latest"]["id"], changes["id"])
        self.assertEqual(view["subjects"]["proposal"]["latest"]["id"], proposed["id"])
        with self.assertRaisesRegex(T.TransitionError, "changed after review assessment"):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(changes)
        reviews.require_merge(self.project, self.slug, self.pair())
        with self.assertRaisesRegex(T.TransitionError, "same proposal or changes"):
            self.request(subject="changes", previous=proposed["id"])

    def test_saved_fallback_provenance_survives_later_availability_changes(self):
        self.choice.update(engine=config.ENGINES[0], same_engine=True, fallback_reason="Alternate account unavailable")
        requested = self.request()
        result = self.run_review(requested)
        self.assertTrue(result["same_engine"])
        self.assertTrue(result["allowance_known"])
        self.assertEqual(result["fallback_reason"], "Alternate account unavailable")
        self.choice.update(engine=config.ENGINES[1], same_engine=False, fallback_reason="")
        view = reviews.view(self.project, self.slug)
        self.assertFalse(view["same_engine"])
        self.assertTrue(view["latest"]["same_engine"])
        self.assertEqual(self.engine.call_count, 1)

    def test_explicit_selection_routes_one_review_and_is_never_substituted(self):
        chosen = {**self.choice, "engine": config.ENGINES[0], "model": "chosen-model", "same_engine": True,
                  "fallback_reason": "Selected for this review."}
        self.pick.side_effect = lambda task, project, **selection: chosen if selection else self.choice
        review = self.request(model="chosen-model")
        self.assertEqual(review["selection"], {"engine": None, "model": "chosen-model"})
        self.assertEqual((review["engine"], review["model"]), (config.ENGINES[0], "chosen-model"))
        self.assertEqual(self.pick.call_args.kwargs, {"engine": None, "model": "chosen-model"})
        self.assertEqual(reviews.view(self.project, self.slug)["model"], "fixture-model")
        with self.assertRaisesRegex(T.TransitionError, "different focus or selection"):
            self.request(request_id=review["id"])
        # The selected model becomes unavailable: the run fails instead of launching the automatic choice.
        self.pick.side_effect = lambda task, project, **selection: ({"engine": None, "why": "Model exhausted"}
                                                                   if selection else self.choice)
        failed = self.run_review(review)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("no longer available", failed["error"])
        self.engine.assert_not_called()
        with self.assertRaisesRegex(T.TransitionError, "Model exhausted"):
            self.request(previous=review["id"], model="chosen-model")
        self.pick.side_effect = lambda task, project, **selection: chosen if selection else self.choice
        retry = self.request(previous=review["id"], model="chosen-model")
        self.assertEqual(retry["selection"], review["selection"])
        completed = self.run_review(retry)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(self.engine.call_args.kwargs["engine"], config.ENGINES[0])
        self.assertEqual(self.engine.call_args.kwargs["model"], "chosen-model")
        self.assertEqual(S.load_task(self.project, self.slug)["reviews"][-1]["selection"], review["selection"])

    def test_waiting_operator_request_can_be_reselected_without_losing_authority(self):
        review = self.request(actor=T.OPERATOR_MESSAGE_ROLE, subject="proposal", focus="Wording")
        self.assertEqual(review["model"], "fixture-model")
        chosen = {**self.choice, "model": "chosen-model"}
        self.pick.side_effect = lambda task, project, **selection: chosen if selection else self.choice
        with self.assertRaisesRegex(T.TransitionError, "--previous to select"):
            self.request(subject="proposal", model="chosen-model")
        self.assertEqual(self.request(subject="proposal")["id"], review["id"])
        replaced = self.request(previous=review["id"], model="chosen-model", request_id="selected")
        self.assertEqual((replaced["requested_by"], replaced["subject"], replaced["model"], replaced["focus"]),
                         (T.OPERATOR_MESSAGE_ROLE, "proposal", "chosen-model", "Wording"))
        self.assertEqual(self.request(previous=review["id"], model="chosen-model", request_id="selected")["id"], "selected")
        task = S.load_task(self.project, self.slug)
        prior = reviews._find(task, review["id"])
        self.assertEqual(prior["state"], "withdrawn")
        self.assertIn("Replaced by review selected", prior["withdrawal_reason"])
        self.assertIn("chosen-model", reviews._find(task, "selected")["message"]["text"])
        self.assertEqual(reviews.view(self.project, self.slug)["subjects"]["proposal"]["latest"]["id"], "selected")
        with self.assertRaises(T.TransitionError):
            reviews.withdraw(self.project, self.slug, "selected", actor="l2", expected_attempt=1, reason="Skip")
        # The owner cannot narrow the operator's scope by replacing or retrying; it can only add focus.
        narrowed = self.request(previous="selected", model="other-model", focus="Typos only", request_id="narrowed")
        self.assertEqual(narrowed["focus"], "Wording\nTypos only")
        self.assertEqual(self.request(previous="selected", model="other-model", focus="Typos only",
                                      request_id="narrowed")["id"], "narrowed")
        self.assertEqual(reviews._find(S.load_task(self.project, self.slug), "selected")["replaced_by"], "narrowed")
        automatic = self.request(previous="narrowed", request_id="automatic")
        self.assertEqual(automatic["id"], "narrowed")
        self.engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
