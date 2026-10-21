"""Review interfaces preserve owner identity and the operator's bounded action surface."""
import base64
import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import config, door, reviews, server, state as S, tasks as T


class TestReviewInterfaces(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.slug = T.new(self.project, "Review interfaces", "Review this bounded change.")["slug"]
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_TASK", self.slug)
        self.setenv("ALTITUDE_ATTEMPT", "2")
        self.setenv("ALTITUDE_ACTOR", "l2")

    def cli(self, *arguments, stdin="", files=None, door=True):
        """Run an owner verb as altd runs it for the owner's task door: the content of its input files arrives with
        the request, and `door=False` runs it in the owner's worker instead."""
        output = io.StringIO()
        if door:
            self.setenv("ALTITUDE_DOOR", "1")
            stdin = json.dumps({"stdin": stdin, "shown": {},
                                "files": {path: base64.b64encode(data).decode() for path, data in (files or {}).items()}})
        try:
            with contextlib.redirect_stdout(output), mock.patch("sys.stdin", io.StringIO(stdin)):
                runpy.run_path(str(ALT))["main"](list(arguments))
        finally:
            self.setenv("ALTITUDE_DOOR", None)
        return json.loads(output.getvalue())

    def post(self, body, path="/api/task/review"):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler._body = lambda **kwargs: body
        handler._refused = lambda: None  # routing only; tests/test_request_boundary.py covers the boundary
        handler._admit = lambda _parts: True  # and tests/test_access.py covers pairing and Altitude's own agents
        handler._from_agent = lambda: False
        handler._task_owner = lambda _body, **_: True  # and tests/test_worker_authority.py covers the task door
        handler._json = lambda value, status=200: (status, value)
        return handler.do_POST()

    def test_owner_request_reuses_explicit_source_and_identity(self):
        for subject in (None, "changes", "proposal"):
            arguments = ("--subject", subject) if subject else ()
            with self.subTest(subject=subject), mock.patch.object(reviews, "request", return_value={"id": "review"}) as request:
                result = self.cli("task", "review", "request", "--request-id", "submission",
                                  "--source-message", "chat-message", "--focus", "Failure paths",
                                  "--previous", "prior", *arguments)
                self.assertEqual(result, {"id": "review"})
                request.assert_called_once_with(self.project, self.slug, actor="l2", expected_attempt=2,
                                                request_id="submission", focus="Failure paths", source_id="chat-message",
                                                previous="prior", additional=False, following=False, subject=subject,
                                                engine=None, model=None)
        with mock.patch.object(reviews, "request", return_value={"id": "review"}) as request:
            self.cli("task", "review", "request", "--subject", "proposal", "--additional", "--focus", "The new addendum")
        self.assertEqual((request.call_args.kwargs["additional"], request.call_args.kwargs["previous"]), (True, None))
        # The step after a waiting PR names --next on its proposal review and on a skip.
        with mock.patch.object(reviews, "request", return_value={"id": "review"}) as request:
            self.cli("task", "review", "request", "--subject", "proposal", "--next")
        self.assertTrue(request.call_args.kwargs["following"])
        with mock.patch.object(reviews, "skip", return_value={"id": "review"}) as skip:
            self.cli("task", "review", "skip", "--subject", "proposal", "--next", "--reason", "Copy only.")
        skip.assert_called_once_with(self.project, self.slug, subject="proposal", reason="Copy only.", following=True,
                                     actor="l2", expected_attempt=2)
        with mock.patch.object(reviews, "request", return_value={"id": "review"}) as request:
            self.cli("task", "review", "request", "--previous", "prior", "--engine", config.ENGINES[-1], "--model", "chosen-model")
        self.assertEqual((request.call_args.kwargs["engine"], request.call_args.kwargs["model"]), (config.ENGINES[-1], "chosen-model"))

    def test_owner_review_cannot_cross_task_project_or_missing_attempt(self):
        with mock.patch.object(reviews, "request") as request:
            for arguments in (("task", "review", "request", "other-task"),
                              ("--project", "other-project", "task", "review", "request")):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(SystemExit, "own project/task"):
                    self.cli(*arguments)
            self.setenv("ALTITUDE_ATTEMPT", "")
            with self.assertRaisesRegex(SystemExit, "ALTITUDE_ATTEMPT"):
                self.cli("task", "review", "request")
        request.assert_not_called()

    def test_assess_reads_stdin_or_file_and_preserves_attempt_fence(self):
        payload = {"dispositions": [{"finding_id": "f1", "disposition": "fixed", "reason": "Added regression coverage"}],
                   "reason": "Final candidate includes the reviewed fix"}
        for proposal_id in (None, "revised-proposal"):
            proposal_args = ("--proposal-message", proposal_id) if proposal_id else ()
            for args, stdin in (((), json.dumps(payload)), (("--file", "assessment.json"), "")):
                with self.subTest(proposal_id=proposal_id, args=args), \
                        mock.patch.object(reviews, "assess", return_value={"state": "completed"}) as assess:
                    self.cli("task", "review", "assess", "--review-id", "review", *args, *proposal_args, stdin=stdin,
                             files={"assessment.json": json.dumps(payload).encode()})
                    assess.assert_called_once_with(self.project, self.slug, "review", actor="l2", expected_attempt=2,
                                                   proposal_id=proposal_id, **payload)
        with self.assertRaisesRegex(SystemExit, "invalid assessment JSON"):
            self.cli("task", "review", "assess", "--review-id", "review", stdin='{"reason":"missing findings"}')

    def test_cancel_and_withdraw_forward_owner_identity(self):
        for action in ("cancel", "withdraw"):
            with self.subTest(action=action), mock.patch.object(reviews, action, return_value={"id": "review"}) as operation:
                self.cli("task", "review", action, "--review-id", "review", "--reason", "Scope changed")
                operation.assert_called_once_with(self.project, self.slug, "review", actor="l2",
                                                   expected_attempt=2, reason="Scope changed")

    def test_run_uses_fixed_daemon_operation_and_returns_receipt(self):
        self.serving(8890)  # the transport is replaced; nothing listens
        for proposal_id in (None, "original-proposal"):
            arguments = ("--context-message", "source", "--proposal-message", proposal_id) if proposal_id else ()
            response = mock.MagicMock()
            response.__enter__.return_value = io.BytesIO(b'{"ok":true,"review":{"id":"review","state":"completed"}}')
            with self.subTest(proposal_id=proposal_id), mock.patch("urllib.request.urlopen", return_value=response) as transport:
                result = self.cli("task", "review", "run", "--review-id", "review", *arguments, door=False)
                self.assertEqual(result["state"], "completed")
                request = transport.call_args.args[0]
                self.assertTrue(request.full_url.endswith("/api/task/review/run"))
                self.assertEqual(json.loads(request.data), {"project": self.project, "slug": self.slug, "attempt": "2",
                                                           "review_id": "review",
                                                           **({"context_ids": ["source"], "proposal_id": proposal_id}
                                                              if proposal_id else {})})
                self.assertIsNone(transport.call_args.kwargs["timeout"])
        # The owner's `alt` sends the content of the files it shows; the daemon never opens a path it names.
        task = S.load_task(self.project, self.slug)
        task["worktree"] = str(config.project_path(self.project) / config.WORKTREE_ROOT / self.slug)
        S.save_task(self.project, task)
        (Path(task["worktree"]) / "docs").mkdir(parents=True)
        (Path(task["worktree"]) / "docs" / "spec.md").write_text("# Spec\n")
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"review":{"id":"review","state":"completed"}}')
        with mock.patch("urllib.request.urlopen", return_value=response) as transport:
            self.cli("task", "review", "run", "--review-id", "review", "--proposal-message", "proposal",
                     "--show", "docs/spec.md", door=False)
            self.assertEqual(json.loads(transport.call_args.args[0].data)["show"], {"docs/spec.md": "IyBTcGVjCg=="})
        with mock.patch.object(door, "MAX_REQUEST", 64), mock.patch("urllib.request.urlopen") as transport:
            with self.assertRaisesRegex(SystemExit, "^alt task review: this request is .+ send fewer or smaller files$"):
                self.cli("task", "review", "run", "--review-id", "review", "--show", "docs/spec.md", door=False)
            transport.assert_not_called()

    def test_streamed_execution_error_is_not_a_success_receipt(self):
        self.serving(8890)  # the transport is replaced; nothing listens
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'  {"ok":false,"error":"Review interrupted"}')
        with mock.patch("urllib.request.urlopen", return_value=response), self.assertRaisesRegex(SystemExit, "Review interrupted"):
            self.cli("task", "review", "run", "--review-id", "review", door=False)

    def test_operator_cannot_run_or_assess(self):
        self.setenv("ALTITUDE_ACTOR", T.OPERATOR_MESSAGE_ROLE)
        with mock.patch.object(reviews, "assess") as assess, mock.patch("urllib.request.urlopen") as transport:
            for action in ("run", "assess"):
                for arguments in ((), ("--proposal-message", "proposal")):
                    with self.subTest(action=action, arguments=arguments), self.assertRaisesRegex(SystemExit, "only the current L2"):
                        self.cli("task", "review", action, "--review-id", "review", *arguments)
            assess.assert_not_called()
            transport.assert_not_called()

    def test_operator_http_request_is_attributed_and_wakes_saved_resume(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        S.save_task(self.project, task)
        for subject in (None, "changes", "proposal"):
            fields = {"subject": subject} if subject else {}
            with self.subTest(subject=subject), mock.patch.object(reviews, "request", return_value={"id": "review"}) as request, \
                    mock.patch.object(server, "request_task_resume") as resume:
                status, value = self.post({"project": self.project, "slug": self.slug, "action": "request",
                                           "request_id": "submission", "focus": "Failure paths", **fields})
                self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "review"}}))
                request.assert_called_once_with(self.project, self.slug, actor=T.OPERATOR_MESSAGE_ROLE,
                                                request_id="submission", focus="Failure paths", previous=None, subject=subject)
                resume.assert_called_once_with(self.project, self.slug)

    def test_http_denials_and_execution_are_separate(self):
        base = {"project": self.project, "slug": self.slug}
        with mock.patch.object(reviews, "request") as request, mock.patch.object(reviews, "run") as run, \
                mock.patch.object(reviews, "assess") as assess:
            for extra in ({"action": "run", "review_id": "review"}, {"action": "assess"},
                          {"action": "run", "review_id": "review", "proposal_id": "proposal"},
                          {"action": "assess", "proposal_id": "proposal"},
                          {"action": "request"}, {"action": "request", "request_id": "x", "actor": "l2"},
                          {"action": "retry", "request_id": "x"}):
                with self.subTest(extra=extra):
                    self.assertEqual(self.post({**base, **extra})[0], 409)
            request.assert_not_called()
            run.assert_not_called()
            assess.assert_not_called()
        # The owner's `alt` sends the content of the files it shows; altd records exactly that content.
        for proposal_id, show in ((None, None), ("original-proposal", None), ("original-proposal", {"docs/spec.md": "IyBTcGVjCg=="})):
            fields = {**({"proposal_id": proposal_id} if proposal_id else {}), **({"show": show} if show else {})}
            with self.subTest(proposal_id=proposal_id, show=bool(show)), \
                    mock.patch.object(reviews, "run", return_value={"id": "review"}) as run:
                status, value = self.post({**base, "attempt": "2", "review_id": "review", "context_ids": ["source"], **fields},
                                          "/api/task/review/run")
                self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "review"}}))
                run.assert_called_once_with(self.project, self.slug, "review", actor="l2", expected_attempt=2,
                                            context_ids=["source"], proposal_id=proposal_id,
                                            show={"docs/spec.md": b"# Spec\n"} if show else None, on_wait=mock.ANY)

    def test_http_repeat_names_the_review_it_replaces_and_wake_failure_keeps_receipt(self):
        task = S.load_task(self.project, self.slug)
        task["state"] = "blocked"
        S.save_task(self.project, task)
        with mock.patch.object(reviews, "request", return_value={"id": "new-review"}) as request, \
                mock.patch.object(server, "request_task_resume", side_effect=OSError("wake unavailable")), \
                mock.patch.object(server, "log"):
            status, value = self.post({"project": self.project, "slug": self.slug, "action": "request",
                                       "request_id": "retry-submission", "review_id": "prior-review"})
        self.assertEqual((status, value), (200, {"ok": True, "review": {"id": "new-review"}}))
        request.assert_called_once_with(self.project, self.slug, actor=T.OPERATOR_MESSAGE_ROLE,
                                        request_id="retry-submission", focus="", previous="prior-review", subject=None)

    def test_execution_rejects_missing_attempt_and_returns_stale_owner_denial(self):
        payload = {"project": self.project, "slug": self.slug, "review_id": "review"}
        with mock.patch.object(reviews, "run") as run:
            for attempt in (None, True, {}, "invalid"):
                with self.subTest(attempt=attempt):
                    self.assertEqual(self.post({**payload, "attempt": attempt}, "/api/task/review/run")[0], 409)
            run.assert_not_called()
        with mock.patch.object(reviews, "run", side_effect=T.TransitionError("attempt 1 is no longer current")):
            status, value = self.post({**payload, "attempt": 1}, "/api/task/review/run")
        self.assertEqual((status, value), (409, {"ok": False, "error": "attempt 1 is no longer current"}))

    def test_http_unavailable_is_conflict_and_status_read_does_not_launch(self):
        with mock.patch.object(reviews, "request", side_effect=T.TransitionError("Second engine unavailable")):
            self.assertEqual(self.post({"project": self.project, "slug": self.slug, "action": "request",
                                        "request_id": "submission"}), (409, {"error": "Second engine unavailable"}))
        with mock.patch.object(reviews, "view", return_value={"history": []}) as view, \
                mock.patch.object(reviews, "request") as request, mock.patch.object(server.monitor, "sessions", return_value=[]):
            self.assertEqual(server.task_view(self.project, self.slug)["review"], {"history": []})
            self.assertEqual(self.cli("task", "review", "status", door=False), {"history": []})
        self.assertEqual(view.call_count, 2)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
