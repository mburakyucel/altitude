"""Fixed GitHub adapter and real release CLI admission, with only external transport replaced."""
import contextlib
import io
import json
import runpy
import subprocess
import urllib.request
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import releases, state as S, tasks as T


class ReleaseAPI(AltitudeCase):
    def test_json_writes_use_fixed_host_and_stdin_without_shell_interpolation(self):
        body = {"name": "Altitude v0.1.0", "body": "Literal $HOME and `command`\nNotes.", "draft": True}
        with mock.patch.object(releases.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, '{"id":21}', "")) as execute:
            self.assertEqual(releases._api("example/project", "releases", method="POST", data=body), {"id": 21})
        command = execute.call_args.args[0]
        self.assertEqual(command, ["gh", "api", "--hostname", "github.com", "--method", "POST",
                                   "--input", "-", "repos/example/project/releases"])
        self.assertEqual(json.loads(execute.call_args.kwargs["input"]), body)
        self.assertNotIn("shell", execute.call_args.kwargs)
        self.assertEqual(execute.call_args.kwargs["timeout"], 120)

    def test_upload_uses_file_input_and_only_github_upload_host(self):
        asset = self.tmp / "release asset.tar.gz"
        asset.write_bytes(b"fictional archive")
        with mock.patch.object(releases.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, '{"id":22,"state":"uploaded"}', "")) as execute:
            result = releases._api("example/project", "releases/21/assets?name=release.tar.gz",
                                   method="POST", upload=asset)
        self.assertEqual(result["state"], "uploaded")
        self.assertEqual(execute.call_args.args[0], [
            "gh", "api", "--hostname", "github.com", "--method", "POST", "-H",
            "Content-Type: application/octet-stream", "--input", str(asset),
            "https://uploads.github.com/repos/example/project/releases/21/assets?name=release.tar.gz"])
        self.assertEqual(execute.call_args.kwargs["input"], "")

    def test_missing_is_optional_and_errors_distinguish_refusal_from_uncertainty_without_body(self):
        for stderr, missing, refused in (
                ("private response HTTP 404", True, None), ("private response HTTP 404", False, True),
                ("private response HTTP 403", False, True), ("private response HTTP 422", False, True),
                ("private response HTTP 503", False, False), ("private response connection reset", False, False)):
            with self.subTest(stderr=stderr, missing=missing), mock.patch.object(
                    releases.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private stdout", stderr)):
                if refused is None:
                    self.assertIsNone(releases._api("example/project", "releases/21", missing=missing))
                    continue
                with self.assertRaises(releases.APIError) as failure:
                    releases._api("example/project", "releases/21?private-query", missing=missing)
                self.assertEqual(failure.exception.refused, refused)
                message = str(failure.exception)
                self.assertIn("request refused" if refused else "remote outcome may be uncertain", message)
                self.assertNotIn("private", message)


class ReleaseCLI(AltitudeCase):
    def setUp(self):
        super().setUp()
        task = T.new(self.project, "Approved publication", "Publish the approved release.")
        self.slug = task["slug"]
        task.update(state="running", attempt=1, worktree=str(self.repo))
        S.save_task(self.project, task)
        for key, value in (("ALTITUDE_ACTOR", "l2"), ("ALTITUDE_PROJECT", self.project),
                           ("ALTITUDE_TASK", self.slug), ("ALTITUDE_ATTEMPT", "1")):
            self.setenv(key, value)
        self.send = self.patch(urllib.request, "urlopen")
        self.github = self.patch(releases, "_api", side_effect=AssertionError("CLI must not call GitHub directly"))

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(str(ALT))["main"](list(arguments))
        return output.getvalue().strip()

    def test_owner_publish_and_check_send_only_fixed_task_identity_to_daemon(self):
        self.serving(19555)  # Transport is replaced; no server or reserved port is used.
        for check in (False, True):
            with self.subTest(check=check):
                result = {"status": "ready" if check else "published", "version": "v0.1.0-rc.2"}
                self.send.return_value = io.BytesIO(json.dumps(result).encode())
                arguments = ("task", "publish", self.slug, *(("--check",) if check else ()))
                self.assertEqual(json.loads(self.invoke(*arguments)), result)
                request = self.send.call_args.args[0]
                self.assertTrue(request.full_url.endswith("/api/task/publish"))
                self.assertEqual(json.loads(request.data), {
                    "project": self.project, "slug": self.slug, "attempt": "1", "check": check})
        self.github.assert_not_called()

    def test_other_task_project_missing_attempt_and_nonowners_refuse_before_transport(self):
        for arguments in (("task", "publish", "other-task"),
                          ("--project", "other-project", "task", "publish", self.slug)):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(SystemExit, "own task and project"):
                self.invoke(*arguments)
        self.setenv("ALTITUDE_ATTEMPT", None)
        with self.assertRaisesRegex(SystemExit, "own task and project"):
            self.invoke("task", "publish", self.slug)
        self.setenv("ALTITUDE_ATTEMPT", "1")
        for actor in ("l3", T.OPERATOR_MESSAGE_ROLE):
            self.setenv("ALTITUDE_ACTOR", actor)
            for mode in ((), ("--check",)):
                with self.subTest(actor=actor, mode=mode), self.assertRaisesRegex(SystemExit, "only the running task owner"):
                    self.invoke("task", "publish", self.slug, *mode)
        self.send.assert_not_called()
        self.github.assert_not_called()

    def test_invalid_modes_and_scope_options_never_reach_transport(self):
        for options in (("--grant",), ("--revoke",), ("--version", "v0.1.0"),
                        ("--source", "project"), ("--reason", "not a grant"),
                        ("--check", "--grant"), ("--unknown",), ("--gra",)):
            with self.subTest(options=options), self.assertRaises(SystemExit) as failure:
                self.invoke("task", "publish", self.slug, *options)
            self.assertEqual(failure.exception.code, 2)
        self.send.assert_not_called()
        self.github.assert_not_called()

    def test_coordinator_can_record_a_grant_but_still_reaches_real_scope_validation(self):
        self.setenv("ALTITUDE_ACTOR", "l3")
        with self.assertRaisesRegex(T.TransitionError, "supply a release version"):
            self.invoke("task", "publish", self.slug, "--grant", "--approval", "operator-message",
                        "--version", "not-a-version", "--sha", "a" * 40, "--files", str(self.tmp / "assets"),
                        "--source", "project", "--reason", "Recorded operator permission.")
        refused = [event for event in S.read_events(self.project, self.slug) if event["kind"] == "release-grant-refused"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["actor"], "l3")
        self.assertIsNone(S.load_task(self.project, self.slug).get("release_grant"))
        self.send.assert_not_called()
        self.github.assert_not_called()
