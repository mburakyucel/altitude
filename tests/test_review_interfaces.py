"""Review interfaces preserve owner identity and the operator's bounded action surface."""
import contextlib
import io
import json
import runpy
import unittest
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import reviews, server, state as S, tasks as T


class TestReviewInterfaces(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.slug = T.new(self.project, "Review interfaces", "Review this bounded change.")["slug"]
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_TASK", self.slug)
        self.setenv("ALTITUDE_ATTEMPT", "2")
        self.setenv("ALTITUDE_ACTOR", "l2")

    def cli(self, *arguments, stdin=""):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch("sys.stdin", io.StringIO(stdin)):
            runpy.run_path(str(ALT))["main"](list(arguments))
        return json.loads(output.getvalue())

    def post(self, body, path="/api/task/review"):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler._body = lambda **kwargs: body
        handler._json = lambda value, status=200: (status, value)
        return handler.do_POST()

    def test_owner_request_reuses_explicit_source_and_identity(self):
        with mock.patch.object(reviews, "request", return_value={"id": "review"}) as request:
            result = self.cli("task", "review", "request", "--request-id", "submission",
                              "--source-message", "chat-message", "--focus", "Failure paths", "--previous", "prior")
        self.assertEqual(result, {"id": "review"})
        request.assert_called_once_with(self.project, self.slug, actor="l2", expected_attempt=2,
                                        request_id="submission", focus="Failure paths", source_id="chat-message", previous="prior")

    def test_owner_review_cannot_cross_task_project_or_missing_attempt(self):
        with mock.patch.object(reviews, "request") as request:
            for arguments in (("task", "review", "request", "other-task"),
                              ("--project", "other-project", "task", "review", "request")):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(SystemExit, "own task and project"):
                    self.cli(*arguments)
            self.setenv("ALTITUDE_ATTEMPT", "")
            with self.assertRaisesRegex(SystemExit, "ALTITUDE_ATTEMPT"):
                self.cli("task", "review", "request")
        request.assert_not_called()

    def test_assess_reads_stdin_or_file_and_preserves_attempt_fence(self):
        payload = {"dispositions": [{"finding_id": "f1", "disposition": "fixed", "reason": "Added regression coverage"}],
                   "reason": "Final candidate includes the reviewed fix"}
        path = self.tmp / "assessment.json"
        path.write_text(json.dumps(payload))
        for args, stdin in (((), json.dumps(payload)), (("--file", str(path)), "")):
            with mock.patch.object(reviews, "assess", return_value={"state": "completed"}) as assess:
                self.cli("task", "review", "assess", "--review-id", "review", *args, stdin=stdin)
            assess.assert_called_once_with(self.project, self.slug, "review", actor="l2", expected_attempt=2, **payload)
        with self.assertRaisesRegex(SystemExit, "invalid assessment JSON"):
            self.cli("task", "review", "assess", "--review-id", "review", stdin='{"reason":"missing findings"}')

    def test_cancel_and_withdraw_forward_owner_identity(self):
        for action in ("cancel", "withdraw"):
            with self.subTest(action=action), mock.patch.object(reviews, action, return_value={"id": "review"}) as operation:
                self.cli("task", "review", action, "--review-id", "review", "--reason", "Scope changed")
                operation.assert_called_once_with(self.project, self.slug, "review", actor="l2",
                                                   expected_attempt=2, reason="Scope changed")

    def test_run_uses_fixed_daemon_operation_and_returns_receipt(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"review":{"id":"review","state":"completed"}}')
        with mock.patch("urllib.request.urlopen", return_value=response) as transport:
            result = self.cli("task", "review", "run", "--review-id", "review", "--context-message", "source")
        self.assertEqual(result["state"], "completed")
        request = transport.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/task/review/run"))
        self.assertEqual(json.loads(request.data), {"project": self.project, "slug": self.slug, "attempt": "2",
                                                   "review_id": "review", "context_ids": ["source"]})
        self.assertEqual(transport.call_args.kwargs["timeout"], 660)

    def test_operator_cannot_run_or_assess(self):
        self.setenv("ALTITUDE_ACTOR", T.OPERATOR_MESSAGE_ROLE)
        for action in ("run", "assess"):
            with self.subTest(action=action), self.assertRaisesRegex(SystemExit, "only the current L2"):
                self.cli("task", "review", action, "--review-id", "review")

    def test_operator_http_request_is_attributed_and_wakes_saved_resume(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        S.save_task(self.project, task)
        with mock.patch.object(reviews, "request", return_value={"id": "review"}) as request, \
                mock.patch.object(server, "request_task_resume") as resume:
            status, value = self.post({"project": self.project, "slug": self.slug, "action": "request",
                                       "request_id": "submission", "focus": "Failure paths"})
        self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "review"}}))
        request.assert_called_once_with(self.project, self.slug, actor=T.OPERATOR_MESSAGE_ROLE,
                                        request_id="submission", focus="Failure paths", previous=None)
        resume.assert_called_once_with(self.project, self.slug)

    def test_http_denials_and_execution_are_separate(self):
        base = {"project": self.project, "slug": self.slug}
        with mock.patch.object(reviews, "request") as request, mock.patch.object(reviews, "run") as run:
            for extra in ({"action": "run", "review_id": "review"}, {"action": "assess"},
                          {"action": "request"}, {"action": "request", "request_id": "x", "actor": "l2"},
                          {"action": "retry", "request_id": "x"}):
                with self.subTest(extra=extra):
                    self.assertEqual(self.post({**base, **extra})[0], 409)
            request.assert_not_called()
            run.assert_not_called()
        with mock.patch.object(reviews, "run", return_value={"id": "review"}) as run:
            status, value = self.post({**base, "attempt": "2", "review_id": "review", "context_ids": ["source"]},
                                      "/api/task/review/run")
        self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "review"}}))
        run.assert_called_once_with(self.project, self.slug, "review", actor="l2", expected_attempt=2, context_ids=["source"])

    def test_http_retry_keeps_previous_identity_and_wake_failure_keeps_receipt(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        S.save_task(self.project, task)
        with mock.patch.object(reviews, "request", return_value={"id": "new-review"}) as request, \
                mock.patch.object(server, "request_task_resume", side_effect=OSError("wake unavailable")), \
                mock.patch.object(server, "log"):
            status, value = self.post({"project": self.project, "slug": self.slug, "action": "retry",
                                       "request_id": "retry-submission", "review_id": "prior-review"})
        self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "new-review"}}))
        request.assert_called_once_with(self.project, self.slug, actor=T.OPERATOR_MESSAGE_ROLE,
                                        request_id="retry-submission", focus="", previous="prior-review")

    def test_execution_rejects_missing_attempt_and_returns_stale_owner_denial(self):
        payload = {"project": self.project, "slug": self.slug, "review_id": "review"}
        with mock.patch.object(reviews, "run") as run:
            for attempt in (None, True, {}, "invalid"):
                with self.subTest(attempt=attempt):
                    self.assertEqual(self.post({**payload, "attempt": attempt}, "/api/task/review/run")[0], 409)
            run.assert_not_called()
        with mock.patch.object(reviews, "run", side_effect=T.TransitionError("attempt 1 is no longer current")):
            status, value = self.post({**payload, "attempt": 1}, "/api/task/review/run")
        self.assertEqual((status, value), (409, {"error": "attempt 1 is no longer current"}))

    def test_http_unavailable_is_conflict_and_status_read_does_not_launch(self):
        with mock.patch.object(reviews, "request", side_effect=T.TransitionError("Second engine unavailable")):
            self.assertEqual(self.post({"project": self.project, "slug": self.slug, "action": "request",
                                        "request_id": "submission"}), (409, {"error": "Second engine unavailable"}))
        with mock.patch.object(reviews, "view", return_value={"history": []}) as view, \
                mock.patch.object(reviews, "request") as request, mock.patch.object(server.monitor, "sessions", return_value=[]):
            self.assertEqual(server.task_view(self.project, self.slug)["review"], {"history": []})
            self.assertEqual(self.cli("task", "review", "status"), {"history": []})
        self.assertEqual(view.call_count, 2)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
