"""Captured second-engine review journeys use real task state and Git checkpoints."""
import hashlib
import concurrent.futures
import json
import subprocess
import unittest
import uuid
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, engines, reviews, route, state as S, tasks as T


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

    def test_owner_attempt_and_original_source_are_required(self):
        with self.assertRaises(T.TransitionError):
            reviews.request(self.project, self.slug, actor="l2", request_id=uuid.uuid4().hex, expected_attempt=0)
        with self.assertRaises(T.TransitionError):
            self.request(source_id="missing-original-message")
        source = T.message(self.project, self.slug, "l3", "Consider review", by="l3")
        with self.assertRaises(T.TransitionError):
            self.request(source_id=source["id"])
        self.assertFalse(S.load_task(self.project, self.slug).get("reviews"))

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
            self.assertEqual(kwargs["timeout"], 600)
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

    def test_oversize_tracked_file_is_refused_before_blob_content_read(self):
        self.commit("oversize.txt", "x" * ((2 << 20) + 1))
        with mock.patch.object(reviews, "_git", wraps=reviews._git) as git_calls:
            result = self.run_review()
        self.assertEqual(result["state"], "failed")
        self.assertIn("bounds", result["error"].lower())
        self.assertFalse(any("--batch" in call.args for call in git_calls.call_args_list))
        self.engine.assert_not_called()

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
            return {"error": "Timed out", "termination_confirmed": True}
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

    def test_new_code_context_or_merge_pair_invalidates_assessment(self):
        result = self.run_review()
        self.assess(result)
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, {**self.pair(), "head_sha": "a" * 40})
        self.commit("value.py", "VALUE = 3\n")
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(result)
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Correction: retain the empty result too.")
        self.assertEqual(reviews.view(self.project, self.slug)["latest"]["coverage"], "earlier")
        with self.assertRaises(T.TransitionError):
            reviews.require_merge(self.project, self.slug, self.pair())
        self.assess(result)
        reviews.require_merge(self.project, self.slug, self.pair())

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

    def test_failure_retry_preserves_operator_requirement(self):
        review = self.request(actor=T.OPERATOR_MESSAGE_ROLE)
        self.engine.side_effect = lambda *args, **kwargs: {"error": "Review timed out", "termination_confirmed": True}
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
        duplicate = self.request(previous=result["id"])
        self.assertEqual(duplicate["id"], result["id"])
        self.assertEqual(self.engine.call_count, 1)


if __name__ == "__main__":
    unittest.main()
