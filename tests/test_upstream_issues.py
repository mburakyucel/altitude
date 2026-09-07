"""A fictional project's upstream report publishes one issue, without cross-project recovery."""
import json
import os
import subprocess
import threading
import tomllib
import urllib.error
import urllib.request
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, engines, incidents, l3, server, state as S, tasks as T


REPORT = {"expected": "The fictional Atlas task resumes once.",
          "actual": "The task remains blocked.",
          "reproduction": "Create a toy project, block its task, then request resume.",
          "version": "example-build-123"}
ARGS = ["issue", "upstream", "--title", "Fictional resume defect", "-"]
TARGET = "https://github.com/product-fixture/altitude"


class TestUpstreamIssues(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:fictional/atlas.git", cwd=self.repo)
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "product-fixture/altitude")
        self.writes = []
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] == "gh":
                self.writes.append((args, kwargs))
                repository = args[args.index("--repo") + 1]
                return subprocess.CompletedProcess(args, 0, repository + "/issues/42\n", "")
            return real_run(args, **kwargs)

        self.run = self.patch(server.subprocess, "run", side_effect=run)

    def request(self, args=None, report=None, **extra):
        return server.l3_verb_request(self.project, {
            "kind": "alt", "args": ARGS if args is None else args,
            "stdin": json.dumps(REPORT if report is None else report), **extra})

    def test_report_only_uses_product_target_and_leaves_all_private_state_in_place(self):
        self.private_ledgers()
        self.register("altitude", path=self.tmp / "installation")
        for project in (self.project, "altitude"):
            directory = config.project_dir(project)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "incidents").mkdir()
            (directory / "incidents" / "private.md").write_text("Private fixture evidence")
            (directory / "conversation.jsonl").write_text("Private fixture conversation")
        before = {path: path.read_bytes() for project in (self.project, "altitude")
                  for path in config.project_dir(project).rglob("*") if path.is_file()}
        self.setenv("GH_REPO", "unrelated/repository")
        with mock.patch.object(T, "new") as task, mock.patch.object(l3, "queue_message") as wake, \
                mock.patch.object(incidents, "new_incident") as incident, \
                mock.patch.object(server, "spawn") as spawn:
            result = self.request(project="altitude", actor="operator")
        self.assertEqual(result["stdout"], TARGET + "/issues/42\n")
        task.assert_not_called(); wake.assert_not_called(); incident.assert_not_called(); spawn.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(S.list_tasks(self.project), [])
        self.assertEqual(S.list_tasks("altitude"), [])
        self.assertEqual(S.read_project_log("altitude"), [])
        self.assertFalse(incidents.FAULTS.exists())
        args, kwargs = self.writes[0]
        self.assertEqual(args, ["gh", "issue", "create", "--title=Fictional resume defect",
                                "--repo", TARGET, "--body-file", "-"])
        self.assertNotIn("GH_REPO", kwargs["env"])
        self.assertEqual(kwargs["cwd"], self.repo)
        self.assertIn("## Expected behavior\n" + REPORT["expected"], kwargs["input"])
        self.assertNotIn("Private fixture", kwargs["input"])
        events = S.read_project_log(self.project)
        self.assertEqual([(e["kind"], e["actor"], e["url"]) for e in events],
                         [("issue-upstream", "l3", TARGET + "/issues/42")])
        self.assertNotIn(REPORT["reproduction"], json.dumps(events))
        for args in (["issue", "new", "--title", "Local backlog", "-"],
                     ["issue", "comment", "42", "-"],
                     ["issue", "close", "42", "--reason", "completed"]):
            server.l3_verb_request(self.project, {"kind": "alt", "args": args, "stdin": ""})
            self.assertIn("https://github.com/fictional/atlas", self.writes[-1][0])

    def test_unset_seam_uses_installation_origin_even_without_a_managed_altitude_project(self):
        install = self.tmp / "product" / "checkout"
        make_repo(install)
        git("remote", "set-url", "origin", "git@github.com:product-fixture/altitude.git", cwd=install)
        with mock.patch.object(config, "REPO", install), \
                mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", None):
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
            git("remote", "set-url", "origin", "/tmp/non-github.git", cwd=install)
            with self.assertRaisesRegex(ValueError, "configure ALTITUDE_UPSTREAM_ISSUE_REPOSITORY"):
                self.request()
        self.assertEqual(len(self.writes), 1)

    def test_invalid_configuration_never_falls_back_to_local_origin(self):
        for target in ("", "https://other.test/team/repo", "owner/repo/issues/1", "--repo=other/repo"):
            with self.subTest(target=target), mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", target):
                with self.assertRaisesRegex(ValueError, "configure ALTITUDE_UPSTREAM_ISSUE_REPOSITORY"):
                    self.request()
        self.assertEqual(self.writes, [])

    def test_canonical_github_url_confirms_a_mixed_case_configured_target(self):
        with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "Product-Fixture/ALTITUDE"), \
                mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, TARGET + "/issues/42\n", "")) as run:
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
        self.assertIn("https://github.com/Product-Fixture/ALTITUDE", run.call_args.args[0])

    def test_report_shape_and_private_content_are_rejected_before_external_calls(self):
        for report in ({}, [], "raw incident dump", REPORT | {"evidence": "attachment"},
                       REPORT | {"actual": " "}, REPORT | {"version": 123}):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, "fictional/redacted JSON"):
                self.request(report=report)
        private = ["/home/example/private.txt", "/Users/example/private.txt", "~/secret", "$HOME/secret",
                   r"C:\Users\example\secret", "incidents/private.md", "I-20260907-123456.md",
                   "conversation.jsonl", "monitor/faults.json", "inbox.jsonl", "chat.jsonl",
                   "%2Fhome%2Fexample%2Fsecret", "ghp_" + "x" * 36, "github_pat_" + "x" * 50,
                   "sk-" + "x" * 30, "-----BEGIN OPENSSH PRIVATE KEY-----",
                   "Authorization: Bearer abcdef123456", "api_key=abcdef123456", '"password": "abcdef123456"',
                   "https://example:password@example.test"]
        for value in private:
            for field in REPORT:
                with self.subTest(value=value, field=field), self.assertRaisesRegex(ValueError, "Private"):
                    self.request(report=REPORT | {field: value})
            with self.subTest(title=value), self.assertRaisesRegex(ValueError, "Private"):
                self.request(args=["issue", "upstream", "--title", value, "-"])
        self.run.assert_not_called()
        self.assertEqual(S.read_project_log(self.project), [])
        report = {key: value for key, value in REPORT.items() if key != "version"}
        self.request(report=report | {"reproduction": "Set api_key=[REDACTED] in a toy project."})
        self.assertIn("## Altitude version\nunknown", self.writes[0][1]["input"])

    def test_unsupported_targets_actions_and_l2_are_denied(self):
        for suffix in (["--repo", "other/repo"], ["--target", "other"], ["--project", "altitude"],
                       ["--label", "bug"], ["--file", "incident.md"], ["--reason", "completed"],
                       ["--number", "42"], ["comment", "42"], ["close", "42"]):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.request(args=[*ARGS, *suffix])
        for operation in ("create", "comment", "close", "edit", "transfer"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["issue", operation, "42"]})
        for fields in ({"labels": []}, {"number": 42}, {"reason": "completed"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                server.issue_write(self.project, "upstream", json.dumps(REPORT), actor="l3", title="Defect", **fields)
        with self.assertRaisesRegex(ValueError, "L2"):
            server.issue_write(self.project, "upstream", json.dumps(REPORT), actor="l2", title="Defect")
        result = self.alt(*ARGS, env={"ALTITUDE_ACTOR": "l2"})
        self.assertIn("not available to an L2", result.stderr)
        result = self.alt(*ARGS, env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": self.project})
        self.assertIn("L3 must use its project-bound runtime alt socket", result.stderr)
        self.assertEqual(self.writes, [])

    def test_failures_are_actionable_and_do_not_echo_private_external_output(self):
        for failure in (OSError("/home/private/path"), subprocess.TimeoutExpired("gh", 120),
                        subprocess.CompletedProcess([], 1, "", "secret-token /home/private/path"),
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.CompletedProcess([], 0, "https://github.com/other/repo/issues/42", "")):
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with self.subTest(failure=failure), mock.patch.object(server.subprocess, "run", **kwargs):
                with self.assertRaisesRegex(ValueError, "alt issue upstream:") as error:
                    self.request()
                self.assertIn("before retrying", str(error.exception))
                self.assertNotIn("/home/private", str(error.exception))
                self.assertNotIn("secret-token", str(error.exception))
        self.assertEqual(S.read_project_log(self.project), [])

    def test_runtime_shim_and_mcp_admit_reporting_and_refuse_extra_authority(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        bindir = runtime / "bin"
        adapter = tomllib.loads("\n".join(engines.codex_l3_permissions(runtime, project=self.project)))["mcp_servers"]["altitude"]
        cases = [(ARGS, REPORT, False),
                 ([*ARGS, "--repo", "other/repo"], REPORT, True),
                 (["issue", "close", "42", "--reason", "completed", "--target", "upstream"], REPORT, True),
                 (ARGS, REPORT | {"actual": "Read incidents/private.md"}, True)]
        self.assertIn("Bash(alt issue upstream *)", l3.ALLOWED_TOOLS)
        for args, report, denied in cases:
            result = subprocess.run([str(bindir / "alt"), *args], input=json.dumps(report), cwd=runtime,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(bool(result.returncode), denied, result)
            wire = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "coordinator", "arguments": {"kind": "alt", "args": args, "stdin": json.dumps(report)}}}
            result = subprocess.run([adapter["command"], *adapter["args"]], input=json.dumps(wire) + "\n",
                                    cwd=runtime, env=os.environ | l3._l3_env(self.project, runtime),
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)["result"]
            self.assertEqual(reply["isError"], denied, reply)
            if not denied:
                self.assertEqual(json.loads(reply["content"][0]["text"])["stdout"], TARGET + "/issues/42\n")
        self.assertEqual(len(self.writes), 2)

    def test_operator_cli_http_uses_the_same_create_only_handler(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.setenv("ALTITUDE_PORT", str(http.server_port))
        self.setenv("ALTITUDE_HOST", "127.0.0.1")
        self.setenv("ALTITUDE_TLS", "0")
        result = subprocess.run([str(config.REPO / "bin" / "alt"), *ARGS], input=json.dumps(REPORT),
                                env=os.environ | {"ALTITUDE_ACTOR": "burak", "ALTITUDE_PROJECT": self.project},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, TARGET + "/issues/42\n")
        self.assertEqual(S.read_project_log(self.project)[0]["actor"], "operator")
        for extra in ({"repo": "other/repo"}, {"target": "other"}, {"labels": ["bug"]}, {"number": 42}):
            request = urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/issue", headers={
                "Content-Type": "application/json"}, data=json.dumps({"project": self.project, "operation": "upstream",
                "title": "Defect", "body": json.dumps(REPORT), **extra}).encode())
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.writes), 1)
