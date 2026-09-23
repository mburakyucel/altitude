"""I-20260903-075410/I-20260904-062512: L3 reads checkouts; altd owns privileged effects."""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, l3, server, state as S, tasks as T


class TestIssueVerbs(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.url = "https://github.com/team/project/issues/42"
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] == "gh":
                if args[1:3] == ["issue", "close"]:
                    return subprocess.CompletedProcess(args, 0, "", "Closed issue #42\n")
                return subprocess.CompletedProcess(args, 0, self.url + "\n", "")
            return real_run(args, **kwargs)

        self.run = self.enterContext(mock.patch.object(server.subprocess, "run", side_effect=run))

    def request(self, *args, body="Keep this requested backlog."):
        return server.l3_verb_request(self.project, {"kind": "alt", "args": ["issue", *args],
                                                    "stdin": body, "actor": "burak"})

    def test_l3_new_and_comment_publish_stdin_to_origin_and_record_one_event_each(self):
        self.setenv("GH_REPO", "other/private")
        body = "First line\n\nSecond line: $(never executed).\n"
        result = self.request("new", "--title=--requested", "--label=backlog", "--label=triage", "-", body=body)
        self.assertEqual(result, {"returncode": 0, "stdout": self.url + "\n", "stderr": ""})
        call = self.run.call_args
        self.assertEqual(call.args[0], ["gh", "issue", "create", "--title=--requested", "--label=backlog",
                                       "--label=triage", "--repo", "https://github.com/team/project", "--body-file", "-"])
        self.assertEqual(call.kwargs["input"], body)
        self.assertNotIn("GH_REPO", call.kwargs["env"])
        self.url += "#issuecomment-99"
        self.request("comment", "42", "-", body="Follow up")
        self.assertEqual(self.run.call_args.args[0], ["gh", "issue", "comment", "42", "--repo",
                                                    "https://github.com/team/project", "--body-file", "-"])
        events = S.read_project_log(self.project)
        self.assertEqual([(e["kind"], e["actor"], e["title"], e["url"]) for e in events],
                         [("issue-new", "l3", "--requested", self.url.split("#")[0]),
                          ("issue-comment", "l3", "Issue #42", self.url)])
        self.assertNotIn(body, json.dumps(events))

    def test_l3_close_maps_both_reasons_to_origin_and_records_the_receipt(self):
        self.setenv("GH_REPO", "other/private")
        for reason, gh_reason in (("completed", "completed"), ("not-planned", "not planned")):
            with self.subTest(reason=reason):
                result = self.request("close", "42", "--reason", reason, body="")
                self.assertEqual(result, {"returncode": 0, "stdout": self.url + "\n", "stderr": ""})
                call = self.run.call_args
                self.assertEqual(call.args[0], ["gh", "issue", "close", "42", "--repo",
                                               "https://github.com/team/project", "--reason", gh_reason])
                self.assertEqual(call.kwargs["cwd"], self.repo)
                self.assertEqual(call.kwargs["input"], "")
                self.assertNotIn("GH_REPO", call.kwargs["env"])
        events = S.read_project_log(self.project)
        self.assertEqual([(e["kind"], e["actor"], e["number"], e["reason"], e["url"]) for e in events],
                         [("issue-close", "l3", 42, reason, self.url) for reason in ("completed", "not-planned")])

    def test_close_refuses_extra_authority_and_published_text(self):
        invalid = [[], ["--reason", "duplicate"], ["--reason", "not planned"], ["--rea", "completed"],
                   ["--reason", "completed", "-"], ["--reason", "completed", "--comment", "Text"],
                   ["--reason", "completed", "--repo", "other/repo"],
                   ["--reason", "completed", "--project", "other"],
                   ["--reason", "completed", "--file", "/etc/passwd"]]
        for flags in invalid:
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.request("close", "42", *flags, body="")
        for number in ("0", "-1", "https://github.com/other/repo/issues/42"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.request("close", number, "--reason", "completed", body="")
        for body in ("Closing comment", f"Read {Path.home()}/private.txt", "See incidents/private.md"):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "no body"):
                self.request("close", "42", "--reason", "completed", body=body)
        for operation in ("close", "reopen", "delete", "edit", "transfer"):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "gh read"):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["issue", operation, "42"]})
        self.run.assert_not_called()
        self.assertEqual(S.read_project_log(self.project), [])

    def test_claude_boundary_refuses_private_paths_and_incident_files_before_gh(self):
        private = [f"Read {Path.home()}/private.txt", f"[evidence](file://{Path.home()}/private.txt)",
                   str(Path.home()).replace("/", "//") + "/private.txt",
                   str(Path.home()).replace("/", "/./") + "/private.txt",
                   "/tmp/../" + str(Path.home()).lstrip("/") + "/private.txt",
                   str(Path.home()).replace("/", "%2F") + "%2Fprivate.txt",
                   "See I-20260907-123456.md", "See I-20260907-123456-2.md#evidence",
                   "[evidence](incidents/custom.log)", "See incidents.jsonl"]
        for operation in (["new", "--title", "Backlog", "-"], ["comment", "42", "-"]):
            for body in private:
                with self.subTest(operation=operation, body=body), self.assertRaisesRegex(ValueError, "Private incident evidence boundary") as error:
                    self.request(*operation, body=body)
                self.assertNotIn("\n", str(error.exception))
        self.run.assert_not_called()
        self.assertEqual(S.read_project_log(self.project), [])
        # An incident identifier without an evidence file is safe to discuss publicly.
        self.request("new", "--title", "Backlog", "-", body="I-20260907-123456; docs/ARCHITECTURE.md")

    def test_issue_grammar_and_l2_refusal_have_no_external_effect(self):
        for args in (["list"], ["close", "42"], ["edit", "42"], ["reopen", "42"], ["delete", "42"], ["new", "-"],
                     ["new", "--title", "", "-"], ["new", "--title", "X", "body-file.md"],
                     ["comment", "0", "-"], ["comment", "https://github.com/other/repo/issues/1", "-"],
                     ["comment", "42", "--repo", "other/repo", "-"], ["new", "--tit", "X", "-"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.request(*args)
        self.run.assert_not_called()
        for args in (["new", "--title", "X", "-"], ["comment", "42", "-"],
                     ["close", "42", "--reason", "completed"]):
            result = self.alt("issue", *args, env={"ALTITUDE_ACTOR": "l2"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not available to an L2", result.stderr)
        with self.assertRaisesRegex(ValueError, "L2"):
            server.issue_write(self.project, "new", "body", actor="l2", title="X")
        with self.assertRaisesRegex(ValueError, "L2"):
            server.issue_write(self.project, "close", "", actor="l2", number=42, reason="completed")

    def test_failed_gh_and_non_github_origin_do_not_record_success(self):
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no origin")):
            with self.assertRaisesRegex(ValueError, "origin"):
                self.request("new", "--title", "X", "-")
        with mock.patch.object(server.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "https://github.com/team/project.git", ""),
                subprocess.CompletedProcess([], 1, "", "failure\nsecond line")]):
            with self.assertRaisesRegex(ValueError, "failure second line"):
                self.request("comment", "42", "-")
        with mock.patch.object(server.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "https://github.com/team/project.git", ""),
                subprocess.CompletedProcess([], 1, "", "closure refused")]):
            with self.assertRaisesRegex(ValueError, "closure refused"):
                self.request("close", "42", "--reason", "completed", body="")
        self.assertEqual(S.read_project_log(self.project), [])

    def test_operator_close_cli_and_api_validate_the_same_operation(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        for reason in ("completed", "not-planned"):
            result = self.alt("issue", "close", "42", "--reason", reason,
                              env={"ALTITUDE_ACTOR": "burak", "ALTITUDE_PROJECT": self.project,
                                   "ALTITUDE_HOST": "127.0.0.1", "ALTITUDE_PORT": str(http.server_port),
                                   "ALTITUDE_TLS": "0"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), self.url)
        payload = {"project": self.project, "operation": "close", "number": 42, "reason": "completed",
                   "actor": "l3"}

        def post(body):
            request = urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/issue",
                                             data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(request)

        with post(payload) as response:
            self.assertEqual(json.load(response), {"url": self.url})
        events = S.read_project_log(self.project)
        self.assertEqual([e["actor"] for e in events], ["operator"] * 3)
        self.run.reset_mock()
        for change in ({"reason": None}, {"reason": ""}, {"reason": "duplicate"}, {"reason": []},
                       {"number": True}, {"number": 0}, {"number": -1}, {"number": "42"},
                       {"number": "https://github.com/other/repo/issues/42"},
                       {"body": "Closing comment"}, {"body": None}, {"title": "X"}, {"labels": ["X"]},
                       {"repo": "other/repo"}, {"operation": "reopen"}, {"operation": "delete"},
                       {"operation": "comment", "body": "Text"}, {"operation": "new", "title": "X"}):
            with self.subTest(change=change), self.assertRaises(urllib.error.HTTPError) as error:
                post(payload | change)
            self.assertEqual(error.exception.code, 400)
            self.assertIn("alt issue", json.load(error.exception)["error"])
        self.run.assert_not_called()
        self.assertEqual(S.read_project_log(self.project), events)

    def test_close_through_coordinator_mcp_and_real_project_socket(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        settings = tomllib.loads("\n".join(engines.codex_l3_permissions(runtime, project=self.project)))
        adapter = settings["mcp_servers"]["altitude"]
        requests = [
            {"kind": "alt", "args": ["issue", "close", "42", "--reason", "completed"]},
            {"kind": "alt", "args": ["issue", "close", "42", "--reason", "not-planned"]},
            {"kind": "alt", "args": ["issue", "close", "42", "--reason", "completed", "--repo", "other/repo"]},
            {"kind": "gh", "args": ["issue", "close", "42"]},
        ]
        wire = "\n".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                     "params": {"name": "coordinator", "arguments": request}})
                         for i, request in enumerate(requests))
        result = subprocess.run([adapter["command"], *adapter["args"]], input=wire + "\n", cwd=runtime,
                                env=os.environ | l3._l3_env(self.project, runtime),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line)["result"] for line in result.stdout.splitlines()]
        self.assertEqual([reply["isError"] for reply in replies], [False, False, True, True])
        for reply in replies[:2]:
            self.assertEqual(json.loads(reply["content"][0]["text"])["stdout"].strip(), self.url)
        events = S.read_project_log(self.project)
        self.assertEqual([(e["actor"], e["reason"]) for e in events],
                         [("l3", "completed"), ("l3", "not-planned")])

    def test_operator_http_and_l3_socket_fix_the_actor(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/issue",
                                         data=json.dumps({"project": self.project, "operation": "new",
                                                          "body": "Requested backlog", "title": "Keep", "actor": "l3"}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.load(response), {"url": self.url})
        self.assertEqual(S.read_project_log(self.project)[0]["actor"], "operator")
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(l3.verb_socket_path(self.project)))
            client.sendall((json.dumps({"kind": "alt", "args": ["issue", "comment", "42", "-"],
                                       "stdin": "Follow-up", "actor": "burak"}) + "\n").encode())
            client.shutdown(socket.SHUT_WR)
            result = json.loads(b"".join(iter(lambda: client.recv(65536), b"")))
        self.assertEqual(result["stdout"].strip(), self.url)
        self.assertEqual(S.read_project_log(self.project)[1]["actor"], "l3")


def _claude_result(session="claude-l3"):
    return {"text": "read it", "session_id": session, "usage": {}, "context_tokens": 1, "cost": 0,
            "turns": 1, "structured": None, "error": None, "limited": None, "tools": []}


class TestL3CheckoutConfinement(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)

    @staticmethod
    def choice(engine):
        return {"engine": engine, "why": f"test chose {engine}", "quota": {}}

    def checkout_snapshot(self):
        return (git("rev-parse", "HEAD", cwd=self.repo),
                git("status", "--porcelain", "--untracked-files=all", cwd=self.repo),
                (self.repo / "README.md").read_text())

    def assert_read_git_shim_denies_fetch(self, runtime: Path, before):
        read = subprocess.run([str(runtime / "bin" / "git"), "log", "-1", "--oneline"],
                              capture_output=True, text=True)
        denied = subprocess.run([str(runtime / "bin" / "git"), "fetch", "origin"],
                                capture_output=True, text=True)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("init", read.stdout)
        self.assertEqual(denied.returncode, 77)
        self.assertIn("read-only", denied.stderr)
        self.assertEqual(self.checkout_snapshot(), before, "the denied Git write leaves checkout and Git metadata untouched")

    def test_git_evidence_reads_full_patches_and_blobs_without_diff_helpers(self):
        (self.repo / ".gitattributes").write_text("README.md diff=fixture\n")
        (self.repo / "README.md").write_text("inspect this exact change\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-m", "evidence", cwd=self.repo)
        marker = self.tmp / "helper-ran"
        helper = self.tmp / "diff-helper"
        helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        helper.chmod(0o700)
        git("config", "diff.external", str(helper), cwd=self.repo)
        git("config", "diff.fixture.textconv", str(helper), cwd=self.repo)
        git("config", "diff.fixture.cachetextconv", "true", cwd=self.repo)
        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        for engine in ("claude", "codex"):
            runtime = l3._l3_runtime(self.project, engine)
            self.addCleanup(l3._remove_runtime, runtime)
            for args in (("diff", "HEAD~", "HEAD"), ("show", "HEAD"), ("show", "HEAD:README.md"),
                         ("log", "-1", "-p")):
                with self.subTest(engine=engine, args=args):
                    read = subprocess.run([str(runtime / "bin" / "git"), *args], capture_output=True, text=True)
                    self.assertEqual(read.returncode, 0, read.stderr)
                    self.assertIn("inspect this exact change", read.stdout)
            for args in (("fetch", "origin"), ("config", "user.name", "No"),
                         ("show", "--textconv", "HEAD:README.md"), ("diff", "--ext-diff", "HEAD~", "HEAD"),
                         ("diff", "--output=" + str(marker), "HEAD~", "HEAD")):
                with self.subTest(engine=engine, denied=args):
                    denied = subprocess.run([str(runtime / "bin" / "git"), *args], capture_output=True, text=True)
                    self.assertEqual(denied.returncode, 77, denied.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual({p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}, before,
                         "inspection leaves checkout, config, index and textconv cache refs untouched")

    def test_i_20260903_075410_claude_l3_denies_checkout_write_and_reads(self):
        before, seen = self.checkout_snapshot(), {}
        l3.save_info(self.project, {"sessions": {"claude": {"session_id": "old-auto-session"}},
                                    "engine_last": "claude"})

        def fake_claude(_prompt, **kwargs):
            seen.update(kwargs)
            runtime = Path(kwargs["cwd"])
            self.assertEqual(runtime.parent, config.project_dir(self.project))
            self.assertTrue(runtime.name.startswith("l3-claude-"))
            self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
            self.assert_read_git_shim_denies_fetch(runtime, before)
            return _claude_result()

        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude):
            l3.turn(self.project, "Read the checkout, then try git fetch.")

        runtime = Path(seen["cwd"])
        self.assertFalse(runtime.exists(), "the per-turn Claude runtime is disposed after the engine exits")
        self.assertIsNone(seen["resume"], "the checkout-cwd auto-permission session is rotated once")
        self.assertEqual((seen["permission_mode"], seen["permission_prompts"], seen["restricted"]),
                         ("dontAsk", "none", True))
        self.assertEqual(seen["tools"], "Read,Grep,Glob,Bash")
        self.assertEqual(seen["add_dirs"], (self.repo, config.ROOT),
                         "restricted Claude retains read access to the Altitude home")
        self.assertNotIn("Edit", seen["tools"]); self.assertNotIn("Write", seen["tools"])
        self.assertNotIn("git fetch", seen["allowed_tools"])
        self.assertEqual(set(seen["allowed_tools"].split(",")),
                         {"Read", "Grep", "Glob", "Bash(alt *)", "Bash(git *)", "Bash(gh *)",
                          "Bash(journalctl *)", "Bash(systemctl *)"})
        self.assertEqual(l3.info(self.project)["sessions"]["claude"]["confinement_version"],
                         l3.L3_CONFINEMENT_VERSION)

    def test_i_20260903_075410_codex_l3_denies_checkout_write_and_reads(self):
        before, seen = self.checkout_snapshot(), {}

        def fake_codex(_prompt, **kwargs):
            seen.update(kwargs)
            runtime = Path(kwargs["cwd"])
            self.assertEqual(runtime.parent, config.project_dir(self.project))
            self.assertTrue(runtime.name.startswith("l3-codex-"))
            self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
            self.assert_read_git_shim_denies_fetch(runtime, before)
            return {"text": "read it", "session_id": "codex-l3", "reported_session_id": "codex-l3",
                    "usage": {"input_tokens": 1}, "error": None, "returncode": 0, "tools": []}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn(self.project, "Read the checkout, then try git fetch.")

        runtime = Path(seen["cwd"])
        settings = seen["sandbox_settings"]
        self.assertFalse(runtime.exists(), "the per-turn Codex runtime is disposed after the engine exits")
        self.assertTrue(seen["ignore_user_config"], "ambient sandbox settings cannot replace the L3 profile")
        self.assertIn('default_permissions="altitude-l3"', settings)
        self.assertFalse(any(value.startswith("sandbox_mode=") for value in settings))
        self.assertIn("permissions.altitude-l3.network.enabled=false", settings)
        self.assertIn("mcp_servers.altitude.required=true", settings)
        self.assertIn('mcp_servers.altitude.tools.coordinator.approval_mode="approve"', settings,
                      "headless MCP must not require a prompt under approval_policy=never")
        root_write = f'{json.dumps(str(config.ROOT.resolve()))}="write"'
        self.assertFalse(any(root_write in value for value in settings),
                         "Altitude state is writable only through the daemon verb socket")
        bus = f"/run/user/{l3.os.getuid()}/bus"
        self.assertTrue(any(bus in value and '="deny"' in value for value in settings),
                        "Codex cannot reconstruct the user bus and control the service")
        self.assertTrue(any(str(self.repo.resolve()) in value and '="read"' in value
                            for value in settings), "the deployment checkout is explicitly read-only")
        self.assertFalse(any("network.unix_sockets" in value for value in settings),
                         "the proxy Unix allowlist cannot permit Linux AF_UNIX sockets")
        self.assertFalse(any("network.domains" in value and '="allow"' in value for value in settings),
                         "direct GitHub and altd HTTP access stay blocked")

        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        service = {"unit": "altitude.service", "state": "active", "substate": "running", "pid": 123,
                   "last_restart": "today", "error": None}
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        def read(request):
            return service if request["kind"] == "service" else {
                "returncode": 0, "stdout": "checks are green\n", "stderr": ""}

        with mock.patch.object(server, "l3_verb_request", side_effect=lambda _project, request: read(request)):
            status = subprocess.run([str(runtime / "bin" / "systemctl"), "--user", "status",
                                     "altitude.service", "--no-pager"], capture_output=True, text=True)
            gh_read = subprocess.run([str(runtime / "bin" / "gh"), "pr", "checks", "7"],
                                     capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "altitude.service: active/running PID 123")
        self.assertEqual((gh_read.returncode, gh_read.stdout.strip()), (0, "checks are green"))
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", (runtime / "bin" / "systemctl").read_text())

    def test_sept7_coordinator_mcp_uses_real_broker_and_preserves_authority(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        # A model-writable module must never shadow the trusted adapter, even on a new launch.
        (runtime / "altitude.py").write_text("raise RuntimeError('runtime module executed')\n")
        settings = tomllib.loads("\n".join(engines.codex_l3_permissions(runtime, project=self.project)))
        adapter = settings["mcp_servers"]["altitude"]
        slug = "broker-transport-check"
        body = "Throwaway fixture only\n\nLiteral $(touch forbidden); never shell input.\n"
        requests = [
            {"method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"method": "tools/list"},
            *[{"method": "tools/call", "params": {"name": "coordinator", "arguments": args}} for args in [
                {"kind": "alt", "args": ["task", "new", "--title", "Broker transport check", "-"], "stdin": body},
                {"kind": "alt", "args": ["task", "status", slug, "--json"]},
                {"kind": "alt", "args": ["task", "show", slug]},
                {"kind": "alt", "args": ["--project", "elsewhere", "state"]},
                {"kind": "alt", "args": ["task", "new", "No", "--file", "/etc/passwd"]},
                {"kind": "alt", "args": ["project", "add", "forbidden"], "actor": "burak"},
                {"kind": "gh", "args": ["pr", "merge", "1"]},
                {"kind": "service", "unit": "restart altitude"},
                {"kind": "shell", "args": ["touch", str(self.repo / "forbidden")]},
            ]],
        ]
        wire = "\n".join(json.dumps({"jsonrpc": "2.0", "id": i, **r}) for i, r in enumerate(requests))
        result = subprocess.run([adapter["command"], *adapter["args"]], input=wire + "\n", cwd=runtime,
                                env=os.environ | l3._l3_env(self.project, runtime),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), len(requests))
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "coordinator")
        for reply in replies[2:5]:
            self.assertFalse(reply["result"]["isError"], reply)
            response = json.loads(reply["result"]["content"][0]["text"])
            self.assertEqual(json.loads(response["stdout"])["slug"], slug)
        for reply in replies[5:]:
            self.assertTrue(reply["result"]["isError"], reply)
        self.assertFalse((self.repo / "forbidden").exists())
        self.assertEqual([t["slug"] for t in S.list_tasks(self.project)], [slug])
        self.assertEqual((S.task_dir(self.project, slug) / "request.md").read_text(), body)

    @unittest.skipUnless(os.environ.get("ALTITUDE_TEST_CODEX_SANDBOX") == "1",
                         "opt in on a host with the real Codex Linux sandbox")
    def test_sept7_real_codex_sandbox_denies_checkout_state_git_and_network(self):
        from tests.support import run_native_sandbox_probe
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        probe = r'''
import errno, os, socket, subprocess, sys
from pathlib import Path
repo, state, broker, bus = sys.argv[1:]
assert (Path(repo) / "README.md").read_text() == "readme\n"
Path("scratch").write_text("allowed")
def denied(action):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EPERM, errno.EACCES, errno.EROFS), exc
    else:
        raise AssertionError("unauthorized operation succeeded")
for parent in (repo, state):
    denied(lambda: (Path(parent) / "forbidden").write_text("denied"))
for path in (broker, bus):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        denied(lambda: client.connect(path))
denied(lambda: socket.create_connection(("127.0.0.1", 8890), timeout=2))
git = subprocess.run(["/usr/bin/git", "-C", repo, "config", "probe.denied", "true"], capture_output=True)
assert git.returncode != 0, git
print("native sandbox: reads and scratch writes pass; checkout/state/Git/broker/bus/HTTP writes denied")
'''
        result = run_native_sandbox_probe(runtime, engines.codex_l3_permissions(runtime, project=self.project),
                                          probe, [str(self.repo), str(config.ROOT),
                                                  str(l3.verb_socket_path(self.project)), f"/run/user/{os.getuid()}/bus"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("native sandbox:", result.stdout)

    def test_i_20260903_075410_runtime_path_cannot_be_retargeted_to_the_checkout(self):
        before = self.checkout_snapshot()
        first = l3._l3_runtime(self.project, "codex")
        l3._remove_runtime(first)
        first.symlink_to(self.repo, target_is_directory=True)
        self.addCleanup(l3._remove_runtime, first)
        second = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, second)
        self.assertNotEqual(first, second)
        self.assertEqual(second.parent, config.project_dir(self.project))
        self.assertFalse(second.is_symlink())
        self.assertEqual(self.checkout_snapshot(), before,
                         "a symlink left by one turn cannot redirect the next turn's trusted setup")

    def test_i_20260903_075410_read_broker_rejects_github_writes(self):
        with mock.patch.object(server.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "documented gh read"):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["pr", "merge", "7"]})
        run.assert_not_called()

    def test_l3_sets_routing_through_broker_but_cannot_register_or_cross_projects(self):
        for options, expected in ((["--routing", "codex"], config.parse_routing("codex")),
                                  (["--unset-routing"], None)):
            result = server.l3_verb_request(self.project, {
                "kind": "alt", "args": ["project", "set", self.project, *options, "--reason", "test"]})
            self.assertEqual(result["returncode"], 0, result["stderr"])
            dispatch.run_settings(self.project)
            self.assertEqual(config.project(self.project).get("routing"), expected)
        events = [json.loads(line) for line in (config.project_dir(self.project) / "events.jsonl").read_text().splitlines()]
        self.assertEqual([(e["actor"], e["reason"]) for e in events], [("l3", "test")] * 2)
        for args in (["project", "add", "forbidden"], ["project", "remove", self.project],
                     ["project", "set", "other", "--routing", "codex", "--reason", "test"],
                     ["project", "set", self.project, "--wip", "5", "--reason", "test"]):
            result = server.l3_verb_request(self.project, {"kind": "alt", "args": args})
            self.assertNotEqual(result["returncode"], 0)
        self.assertNotIn("forbidden", config.load_projects())

    def test_routing_preferences_use_the_same_project_bound_reason_bearing_door(self):
        args = ["project", "set", self.project, "--routing", "claude:opus", "--reason", "available account"]
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": args})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        dispatch.run_settings(self.project)
        self.assertEqual(config.project(self.project)["routing"], config.parse_routing("claude:opus"))
        for invalid in (args[:-2], [*args[:2], "other", *args[3:]]):
            result = server.l3_verb_request(self.project, {"kind": "alt", "args": invalid})
            self.assertNotEqual(result["returncode"], 0)

    def test_i_20260903_075410_project_socket_ignores_a_forged_project(self):
        other = f"{self.project}-other"
        other_repo = self.tmp / "other-repo"; other_repo.mkdir()
        self.register(other, path=other_repo)
        local = T.new(self.project, "Shared", "local", actor="burak")
        private = T.new(other, "Shared", "private", actor="burak")
        private["title"] = "OTHER PROJECT PRIVATE TITLE"; S.save_task(other, private)
        self.assertNotEqual(l3.verb_socket_path(self.project), l3.verb_socket_path(other))

        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        def raw(request):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(l3.verb_socket_path(self.project)))
                client.sendall((json.dumps(request) + "\n").encode()); client.shutdown(socket.SHUT_WR)
                return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))

        response = raw({"kind": "alt", "project": other,
                        "args": ["task", "status", local["slug"], "--json"], "stdin": ""})
        self.assertEqual(response["returncode"], 0, response.get("stderr"))
        self.assertIn('"title": "Shared"', response["stdout"])
        self.assertNotIn("OTHER PROJECT PRIVATE TITLE", response["stdout"],
                         "attacker-controlled JSON cannot select a sibling project's daemon authority")
        abbreviated = raw({"kind": "alt",
                           "args": ["--pro", other, "task", "status", local["slug"], "--json"], "stdin": ""})
        self.assertIn("fixes the project", abbreviated["error"])
        self.assertNotIn("OTHER PROJECT PRIVATE TITLE", json.dumps(abbreviated),
                         "argparse abbreviations cannot override the project-bound socket")

    def test_i_20260903_075410_task_identifiers_cannot_traverse_projects(self):
        other = f"{self.project}-other"
        other_repo = self.tmp / "other-repo"; other_repo.mkdir()
        self.register(other, path=other_repo)
        victim = T.new(other, "Private victim", "private", actor="burak")
        traversal = f"../../{other}/tasks/{victim['slug']}"
        with self.assertRaisesRegex(ValueError, "invalid task slug"):
            S.load_task(self.project, traversal)
        with mock.patch.object(server.subprocess, "run") as run, \
             self.assertRaisesRegex(ValueError, "invalid task slug"):
            server.l3_verb_request(self.project, {
                "kind": "alt", "args": ["task", "message", traversal], "stdin": "overwrite"})
        run.assert_not_called()
        self.assertFalse((S.task_dir(other, victim["slug"]) / "inbox.jsonl").exists(),
                         "a traversal write is rejected before the daemon invokes alt")

    def test_i_20260903_075410_broker_rejects_daemon_file_reads(self):
        with mock.patch.object(server.subprocess, "run") as run:
            for option in ("--file", "--file=/etc/passwd", "--fil", "--fil=/etc/passwd",
                           "--fi", "--fi=/etc/passwd", "--f", "--f=/etc/passwd"):
                with self.subTest(option=option), self.assertRaisesRegex(ValueError, "only on stdin"):
                    args = ["task", "message", "safe-task", option]
                    if "=" not in option:
                        args.append("/etc/passwd")
                    server.l3_verb_request(self.project, {"kind": "alt", "args": args, "stdin": ""})
        run.assert_not_called()

        abbreviated = self.alt("--pro", self.project, "state", env={"ALTITUDE_ACTOR": "burak"})
        self.assertNotEqual(abbreviated.returncode, 0)
        self.assertIn("invalid choice", abbreviated.stderr,
                      "the ordinary CLI parser must not recreate broker-rejected option aliases")

    def test_i_20260903_075410_github_reads_cannot_select_another_repo(self):
        redirected = (
            ["pr", "view", "--repo", "other/private", "7"],
            ["pr", "view", "-Rother/private", "7"],
            ["pr", "view", "https://github.com/other/private/pull/7"],
            ["pr", "view", "other/private#7"],
        )
        with mock.patch.object(server.subprocess, "run") as run:
            for args in redirected:
                with self.subTest(args=args), self.assertRaisesRegex(ValueError, "documented gh read"):
                    server.l3_verb_request(self.project, {"kind": "gh", "args": args})
        run.assert_not_called()

        self.setenv("GH_REPO", "other/private")
        completed = subprocess.CompletedProcess([], 0, "green\n", "")
        with mock.patch.object(server.subprocess, "run", return_value=completed) as run:
            result = server.l3_verb_request(self.project, {"kind": "gh", "args": ["pr", "checks", "7"]})
        self.assertEqual(result["stdout"], "green\n")
        self.assertNotIn("GH_REPO", run.call_args.kwargs["env"])

    def test_i_20260903_075410_engine_adapters_receive_the_fail_closed_cli_flags(self):
        class ClaudeProcess:
            pid, returncode = 1, 0
            stdin = io.StringIO()
            stdout = io.StringIO(json.dumps({"type": "result", "result": "ok", "session_id": "sid",
                                               "is_error": False}) + "\n")
            stderr = io.StringIO()
            def wait(self): return self.returncode
            def kill(self): self.returncode = -9

        runtime = l3._l3_runtime(self.project, "claude")
        self.addCleanup(l3._remove_runtime, runtime)
        seen = {}
        def claude_popen(command, **kwargs):
            seen["claude"] = (command, kwargs)
            return ClaudeProcess()
        with mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=claude_popen):
            engines.claude_print("prompt", cwd=runtime, permission_mode="dontAsk", permission_prompts="none",
                                 restricted=True, tools=l3.L3_TOOLS, allowed_tools=engines.L3_ALLOWED_TOOLS,
                                 add_dirs=(self.repo, config.ROOT), settings=self.tmp / "settings.json")
        command = seen["claude"][0]
        self.assertIn("--restricted", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Bash")
        self.assertNotIn("Edit", command[command.index("--tools") + 1])

        settings = engines.codex_l3_permissions(runtime, project=self.project)
        codex_seen = {}
        codex_events = json.dumps({"type": "thread.started", "thread_id": "sid"}) + "\n"
        codex_process = mock.Mock(pid=2, returncode=0)
        codex_process.communicate.return_value = (codex_events, "")
        def codex_popen(command, **kwargs):
            codex_seen["command"] = command
            return codex_process
        with mock.patch.object(engines.subprocess, "Popen", side_effect=codex_popen):
            engines.codex_exec("prompt", cwd=runtime, sandbox_settings=settings, ignore_user_config=True)
        command = codex_seen["command"]
        self.assertIn("--ignore-user-config", command)
        for setting in settings:
            self.assertIn(setting, command)

    def test_i_20260903_075410_project_add_establishes_the_broker_before_l3_starts(self):
        name = f"{self.project}-added"
        repo = self.tmp / "added-repo"; repo.mkdir()
        self.addCleanup(self._forget, name)
        self.addCleanup(server.stop_l3_verb_brokers)
        server.Handler._seen_clients.clear()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = server.threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)

        def started(_key, _fn, project):
            self.assertEqual(project, name)
            if _fn is server.project_setup.run:
                _fn(project)
                return True
            self.assertTrue(l3.verb_socket_path(name).is_socket(),
                            "a newly registered project gets its capability boundary before its first L3 turn")
            return True

        body = json.dumps({"name": name, "path": str(repo)}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/project/add", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with mock.patch.object(server, "spawn", side_effect=started):
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["ok"])

    def test_i_20260903_075410_cli_added_project_gets_a_broker_before_server_turn(self):
        name = f"{self.project}-cli-added"
        repo = self.tmp / "cli-added-repo"; repo.mkdir()
        self.addCleanup(self._forget, name)
        self.addCleanup(server.stop_l3_verb_brokers)
        added = self.alt("project", "add", name, "--path", str(repo), env={"ALTITUDE_ACTOR": "burak"})
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertFalse(l3.verb_socket_path(name).exists())

        def turn(project, _prompt, **_kwargs):
            self.assertEqual(project, name)
            self.assertTrue(l3.verb_socket_path(name).is_socket(),
                            "every server-owned L3 turn reconciles a project added by another CLI process")
            return {"text": "ready"}

        with mock.patch.object(l3, "turn", side_effect=turn), \
             mock.patch.object(server, "request_l3_drain"):
            self.assertEqual(server.server_l3_turn(name, "hello")["text"], "ready")


class TestL3DaemonOperations(AltitudeCase):
    def task(self, state="running", title="Daemon operation"):
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": state, "attempt": 1, "agent_id": "agent-old", "session_id": "session-old",
                     "l2_engine": "claude"})
        S.save_task(self.project, task)
        return task

    def cli(self, *args, actor="l3"):
        return self.alt("--project", self.project, *args, env={"ALTITUDE_ACTOR": actor})

    def test_i_20260904_062512_l3_alt_crosses_the_daemon_socket(self):
        task = self.task(title="Socket stop")
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        result = subprocess.run([str(runtime / "bin" / "alt"), "task", "stop", task["slug"],
                                 "--reason", "Stop through altd"], input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)["request"]
        self.assertEqual((request["operation"], request["actor"], request["reason"]),
                         ("stop", "l3", "Stop through altd"))
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the L3 wrapper leaves the worker effect to the daemon operation runner")

    def test_i_20260904_062512_stop_requires_reason_records_once_and_runs_only_in_altd(self):
        task = self.task()
        missing = self.cli("task", "stop", task["slug"])
        self.assertNotEqual(missing.returncode, 0); self.assertIn("--reason", missing.stderr)

        first = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertEqual(first.returncode, 0, first.stderr)
        queued = json.loads(first.stdout)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the CLI process cannot stop the worker")
        again = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertTrue(json.loads(again.stdout)["idempotent"])
        events = [event for event in S.read_events(self.project, task["slug"])
                  if event["kind"] == "daemon-request"]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["task"], events[0]["operation"], events[0]["reason"], events[0]["by"]),
                         (task["slug"], "stop", "Abort the stale implementation", "l3"))
        self.assertEqual(events[0]["request_id"], queued["request"]["id"])

        with mock.patch.object(engines, "stop_l2_worker", return_value="stopped") as stop:
            completed = dispatch.run_task_operation(self.project, task["slug"])
            retried = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_called_once_with("claude", "agent-old", job_root=dispatch.l2_job_root(self.project, task["slug"]))
        self.assertEqual((completed["request"]["status"], completed["state"]), ("done", "blocked"))
        self.assertTrue(retried["idempotent"])
        self.assertEqual(S.load_task(self.project, task["slug"])["blocked_reason"],
                         "Abort the stale implementation")

        replacement = S.load_task(self.project, task["slug"])
        replacement.update({"state": "running", "agent_id": "agent-new", "session_id": "session-new"})
        S.save_task(self.project, replacement)
        old_retry = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertFalse(json.loads(old_retry.stdout)["idempotent"],
                         "an intervening worker lifecycle gets a new identity-fenced daemon handoff")
        self.assertEqual(len([event for event in S.read_events(self.project, task["slug"])
                              if event["kind"] == "daemon-request"]), 2,
                         "the new daemon handoff records its own reason-bearing operation event")

    def test_i_20260904_062512_each_daemon_verb_requires_reason_and_records_one_request(self):
        for operation, state in (("resume", "blocked"), ("stop", "running"), ("reject", "running")):
            with self.subTest(operation=operation):
                task = self.task(state=state, title=f"{operation} contract")
                missing = self.cli("task", operation, task["slug"])
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("--reason", missing.stderr,
                              f"{operation} must be rejected before any daemon handoff without a reason")
                reason = f"Reason for {operation}"
                first = self.cli("task", operation, task["slug"], "--reason", reason)
                second = self.cli("task", operation, task["slug"], "--reason", reason)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertTrue(json.loads(second.stdout)["idempotent"],
                                f"the same {operation} handoff is idempotent")
                events = [event for event in S.read_events(self.project, task["slug"])
                          if event["kind"] == "daemon-request"]
                self.assertEqual(len(events), 1)
                self.assertEqual((events[0]["operation"], events[0]["reason"], events[0]["by"], events[0]["task"]),
                                 (operation, reason, "l3", task["slug"]))

    def test_i_20260904_062512_each_daemon_verb_refuses_a_changed_target(self):
        for operation, state in (("resume", "blocked"), ("stop", "running"), ("reject", "running")):
            with self.subTest(operation=operation):
                task = self.task(state=state, title=f"stale {operation}")
                dispatch.request_task_operation(self.project, task["slug"], operation,
                                                f"Refuse stale {operation}", actor="burak")
                changed = S.load_task(self.project, task["slug"])
                changed["state"] = "reported"
                S.save_task(self.project, changed)
                with mock.patch.object(engines, "stop_l2_worker") as stop, \
                     mock.patch.object(engines, "remove_l2_worker") as remove, \
                     mock.patch.object(engines, "resume_l2") as resume:
                    result = dispatch.run_task_operation(self.project, task["slug"])
                stop.assert_not_called(); remove.assert_not_called(); resume.assert_not_called()
                self.assertEqual(result["request"]["status"], "refused")
                retry = dispatch.request_task_operation(self.project, task["slug"], operation,
                                                        f"Refuse stale {operation}", actor="burak")
                self.assertTrue(retry["idempotent"],
                                f"retrying refused {operation} cannot retarget a replacement")
                self.assertFalse(retry["queued"])

    def test_stale_target_is_refused_without_touching_the_replacement_worker(self):
        task = self.task(title="Stale stop")
        self.assertEqual(self.cli("task", "stop", task["slug"], "--reason", "Stop old worker").returncode, 0)
        changed = S.load_task(self.project, task["slug"]); changed["agent_id"] = "agent-new"; S.save_task(self.project, changed)
        with mock.patch.object(engines, "stop_l2_worker") as stop:
            result = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_not_called()
        self.assertEqual((result["request"]["status"], result["request"]["note"]),
                         ("refused", "worker identity changed"))
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

        refused = self.cli("task", "resume", task["slug"], "--reason", "Wrong state")
        self.assertNotEqual(refused.returncode, 0); self.assertIn("cannot resume from running", refused.stderr)

    def test_i_20260904_062512_identity_is_rechecked_at_the_stop_transition(self):
        task = self.task(title="Stop transition race")
        dispatch.request_task_operation(self.project, task["slug"], "stop", "Stop the observed worker",
                                        actor="burak")
        real_stop = dispatch.stop

        def replace_after_daemon_precheck(project, slug, **kwargs):
            replacement = S.load_task(project, slug)
            replacement.update({"agent_id": "agent-new", "session_id": "session-new"})
            S.save_task(project, replacement)
            return real_stop(project, slug, **kwargs)

        with mock.patch.object(dispatch, "stop", side_effect=replace_after_daemon_precheck), \
             mock.patch.object(engines, "stop_l2_worker") as stop:
            result = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_not_called()
        self.assertEqual((result["request"]["status"], result["request"]["note"]),
                         ("refused", f"{task['slug']}: worker identity changed"))
        live = S.load_task(self.project, task["slug"])
        self.assertEqual((live["state"], live["agent_id"], live["session_id"]),
                         ("running", "agent-new", "session-new"),
                         "the daemon's under-lock check leaves a replacement worker untouched")

    def test_explicit_resume_and_blocked_message_leave_only_durable_daemon_work(self):
        task = self.task(state="blocked", title="Daemon resume")
        requested = self.cli("task", "resume", task["slug"], "--reason", "Fault is fixed")
        self.assertEqual(requested.returncode, 0, requested.stderr)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["state"], "blocked"); self.assertTrue(current["resume_after"])
        self.assertEqual(dispatch.resume_due(self.project), [], "the explicit request belongs to one daemon runner")

        def resume_in_daemon(project, slug, **_kwargs):
            live = S.load_task(project, slug); live["state"] = "running"; S.save_task(project, live)
            return {"agent": {"id": "agent-new"}}

        with mock.patch.object(dispatch, "resume", side_effect=resume_in_daemon) as resume:
            result = dispatch.run_task_operation(self.project, task["slug"])
        resume.assert_called_once_with(self.project, task["slug"],
                                       daemon_request_id=current["daemon_request"]["id"])
        self.assertEqual((result["request"]["status"], result["state"]), ("done", "running"))

        other = self.task(state="blocked", title="Message resume")
        message = self.cli("task", "message", other["slug"], "Use the recorded decision.", actor="burak")
        self.assertEqual(message.returncode, 0, message.stderr)
        live = S.load_task(self.project, other["slug"])
        self.assertEqual(live["state"], "blocked", "the Burak CLI no longer launches a worker")
        self.assertTrue(live["resume_after"]); self.assertNotIn("daemon_request", live)

    def test_i_20260904_062512_resume_receipt_survives_a_crash_after_transition(self):
        task = self.task(state="blocked", title="Resume receipt")
        queued = dispatch.request_task_operation(
            self.project, task["slug"], "resume", "Fault is fixed", actor="burak")
        live = S.load_task(self.project, task["slug"])
        live["daemon_request"]["status"] = "executing"
        live.update({"state": "running", "agent_id": "agent-replacement", "session_id": "session-replacement"})
        S.save_task(self.project, live)

        with mock.patch.object(dispatch, "resume") as resume:
            result = dispatch.run_task_operation(self.project, task["slug"])
        resume.assert_not_called()
        self.assertEqual((result["request"]["id"], result["request"]["status"], result["state"]),
                         (queued["request"]["id"], "done", "running"),
                         "an executing resume that reached its target gets a successful crash-recovery receipt")

    def test_i_20260904_062512_same_reason_after_an_intervening_lifecycle_is_new_work(self):
        task = self.task(title="Repeated stop")
        reason = "Pause for the same maintenance window"
        first = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        with mock.patch.object(engines, "stop_l2_worker", return_value="stopped"):
            finished = dispatch.run_task_operation(self.project, task["slug"])
        immediate = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        self.assertTrue(immediate["idempotent"])
        self.assertEqual(immediate["request"]["id"], first["request"]["id"])
        self.assertEqual((finished["request"]["result_state"], finished["request"]["result_agent_id"]),
                         ("blocked", "agent-old"))

        T.resume(self.project, task["slug"], agent_id="agent-new", session_id="session-new")
        second = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        self.assertTrue(second["queued"]); self.assertFalse(second["idempotent"])
        self.assertNotEqual(second["request"]["id"], first["request"]["id"])
        self.assertEqual((second["request"]["agent_id"], second["request"]["session_id"]),
                         ("agent-new", "session-new"),
                         "an intervening resume gives the repeated reason a new identity-fenced request")
        self.assertEqual(len([event for event in S.read_events(self.project, task["slug"])
                              if event["kind"] == "daemon-request"]), 2)

    def test_l3_cannot_orphan_a_worker_or_release_buraks_merge_hold(self):
        task = self.task(title="Protected running task")
        blocked = self.cli("task", "block", task["slug"], "--reason", "Pause it")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not available to an L3", blocked.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

        held = S.load_task(self.project, task["slug"]); held["hold_merge"] = "security review"; S.save_task(self.project, held)
        released = self.cli("task", "hold-merge", task["slug"], "--off")
        self.assertNotEqual(released.returncode, 0)
        self.assertIn("only Burak", released.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["hold_merge"], "security review")

    def test_reject_worker_cleanup_runs_in_altd_and_the_l3_door_refuses_admin_commands(self):
        task = self.task(title="Daemon reject")
        requested = self.cli("task", "reject", task["slug"], "--reason", "No longer wanted")
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        with mock.patch.object(engines, "remove_l2_worker", return_value="removed") as remove:
            result = dispatch.run_task_operation(self.project, task["slug"])
            retry = dispatch.run_task_operation(self.project, task["slug"])
        remove.assert_called_once()
        self.assertEqual((result["request"]["status"], result["state"]), ("done", "rejected"))
        self.assertTrue(retry["idempotent"])

        denied = self.cli("dispatch", task["slug"])
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("not available to an L3", denied.stderr)

    def test_i_20260904_062512_altd_tick_schedules_the_durable_operation(self):
        task = self.task(title="Daemon tick")
        dispatch.request_task_operation(self.project, task["slug"], "stop", "Operator requested stop",
                                        actor="burak")
        with mock.patch.object(engines, "refresh_quotas"), \
             mock.patch.object(server, "drain_hook_faults"), \
             mock.patch.object(dispatch, "poll", return_value=[]), \
             mock.patch.object(server, "resume_stranded_reports"), \
             mock.patch.object(dispatch, "resume_due", return_value=[]), \
             mock.patch.object(server, "dispatch_waiting"), \
             mock.patch.object(server, "auto_restart"), \
             mock.patch.object(server, "morning_digest"), \
             mock.patch.object(server, "spawn") as spawn:
            server.tick()
        spawn.assert_any_call(f"task-operation:{self.project}:{task['slug']}",
                              dispatch.run_task_operation, self.project, task["slug"])

    def test_i_20260904_062512_browser_controls_persist_before_the_daemon_runner(self):
        task = self.task(title="Browser stop")
        with mock.patch.object(server, "spawn", return_value=True) as spawn:
            result = server.request_daemon_task_operation(
                self.project, task["slug"], "stop", "Stopped from the task page", actor="burak")
        self.assertTrue(result["queued"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the HTTP request leaves the privileged worker effect to altd")
        spawn.assert_called_once_with(
            f"task-operation:{self.project}:{task['slug']}", dispatch.run_task_operation,
            self.project, task["slug"])

        blocked = self.task(title="Browser resume note")
        question = T.block(self.project, blocked["slug"], "Which value?", actor="l2",
                           updates={"waiting_on": "burak"}, recommendation="Use the approved value.")["questions"][-1]
        server.Handler._seen_clients.clear()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = server.threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)

        def scheduled(*_args):
            self.assertEqual([row["text"] for row in T.pending(self.project, blocked["slug"])],
                             ["Which value?\nUse the approved value."],
                             "Burak's note exists before the daemon resume runner can start")
            return True

        body = json.dumps({"project": self.project, "slug": blocked["slug"], "question_id": question["id"],
                           "revision": question["revision"]}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/decide", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with mock.patch.object(server, "spawn", side_effect=scheduled):
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["queued"])

    def test_coordinator_done_refuses_to_orphan_a_running_worker(self):
        task = self.task(title="Live completion")
        with self.assertRaisesRegex(T.TransitionError, "cannot complete a running worker"):
            T.done(self.project, task["slug"], actor="l3", digest="not actually done")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")


if __name__ == "__main__":
    unittest.main()
