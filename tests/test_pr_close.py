"""Project-scoped PR cleanup verifies GitHub outcomes and retains delivery records."""
import json
import os
import subprocess
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from unittest import mock

from tests.support import ALT, AltitudeCase, git, make_repo
from altitude import config, engines, l3, server, state as S


class TestPrClose(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("branch", "superseded", cwd=self.repo)
        git("push", "origin", "superseded", cwd=self.repo)
        self.remote = self.repo.parent / "origin.git"
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.repository = "https://github.com/team/project"
        self.url = self.repository + "/pull/42"
        self.reads = []
        self.state = "OPEN"
        self.write_error = None
        self.write_state = "CLOSED"
        self.calls = []
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] != "gh":
                return real_run(args, **kwargs)
            self.calls.append((args, kwargs))
            if args[1:3] == ["pr", "view"]:
                value = self.reads.pop(0) if self.reads else self.record(self.state)
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, subprocess.CompletedProcess):
                    return value
                return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
            self.assertEqual(args[1:3], ["pr", "close"])
            self.state = self.write_state
            if isinstance(self.write_error, Exception):
                raise self.write_error
            return subprocess.CompletedProcess(args, self.write_error or 0, "", "fixture write result")

        self.enterContext(mock.patch.object(server.subprocess, "run", side_effect=run))

    def record(self, state):
        return {"number": 42, "url": self.url, "state": state}

    def request(self, *args, body="", **fields):
        return server.l3_verb_request(self.project, {"kind": "alt", "args": ["pr", *args],
                                                    "stdin": body, **fields})

    def http(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        return http

    def post(self, http, payload):
        request = urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/pr/close",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=10)

    def test_authorized_closure_fixes_origin_and_retains_branches_and_archive(self):
        self.setenv("GH_REPO", "other/private")
        archive = config.project_dir(self.project) / "archive" / "superseded"
        archive.mkdir(parents=True)
        report = archive / "report.json"
        report.write_text('{"delivery":"retained"}\n')
        before = (git("show-ref", cwd=self.repo), git("show-ref", cwd=self.remote), report.read_bytes())
        result = json.loads(self.request("close", "42", actor="operator")["stdout"])
        self.assertEqual(result, self.record("CLOSED") | {"outcome": "closed"})
        view = ["gh", "pr", "view", "42", "--repo", self.repository, "--json", "number,url,state"]
        self.assertEqual([args for args, _ in self.calls],
                         [view, ["gh", "pr", "close", "42", "--repo", self.repository], view])
        for _, kwargs in self.calls:
            self.assertEqual(kwargs["cwd"], self.repo)
            self.assertNotIn("GH_REPO", kwargs["env"])
        self.assertEqual(self.calls[1][1]["input"], "")
        self.assertEqual(before, (git("show-ref", cwd=self.repo), git("show-ref", cwd=self.remote),
                                  report.read_bytes()))
        events = S.read_project_log(self.project)
        self.assertEqual(len(events), 1)
        self.assertEqual({key: events[0][key] for key in ("kind", "actor", *result)},
                         {"kind": "pr-close", "actor": "l3", **result})
        self.assertIn("Bash(alt pr *)", l3.ALLOWED_TOOLS)

    def test_repeat_closed_and_merged_are_verified_without_mutation(self):
        server.pr_close(self.project, 42, actor="operator")
        for state, outcome in (("CLOSED", "already-closed"), ("MERGED", "merged")):
            with self.subTest(state=state):
                self.state = state
                self.calls.clear()
                self.assertEqual(server.pr_close(self.project, 42, actor="operator"),
                                 self.record(state) | {"outcome": outcome})
                self.assertEqual([args[1:3] for args, _ in self.calls], [["pr", "view"]])

    def test_uncertain_writes_are_decided_by_verified_state(self):
        for failure in (None, 1, subprocess.TimeoutExpired("gh", 30), OSError("fixture unavailable")):
            for state in ("CLOSED", "MERGED", "OPEN"):
                with self.subTest(failure=failure, state=state):
                    self.state = "OPEN"
                    self.write_error, self.write_state = failure, state
                    events = S.read_project_log(self.project)
                    if state == "OPEN":
                        with self.assertRaisesRegex(ValueError, "remains open"):
                            server.pr_close(self.project, 42, actor="operator")
                        self.assertEqual(S.read_project_log(self.project), events)
                    else:
                        result = server.pr_close(self.project, 42, actor="operator")
                        self.assertEqual(result, self.record(state) | {
                            "outcome": "merged" if state == "MERGED" else "closed"})

    def test_unavailable_or_malformed_reads_never_confirm_success(self):
        invalid = [None, [], {}, self.record("UNKNOWN"), self.record("CLOSED") | {"number": 43},
                   self.record("CLOSED") | {"number": True}, self.record("CLOSED") | {"number": "42"},
                   self.record("CLOSED") | {"url": "https://github.com/other/project/pull/42"},
                   self.record("CLOSED") | {"url": None}, self.record("CLOSED") | {"url": []},
                   subprocess.CompletedProcess([], 0, "broken json", ""),
                   subprocess.CompletedProcess([], 1, "", "unavailable"),
                   subprocess.TimeoutExpired("gh", 30), OSError("fixture unavailable")]
        for after_write in (False, True):
            for value in invalid:
                with self.subTest(after_write=after_write, value=value):
                    self.state = "OPEN"
                    self.calls.clear()
                    self.reads = ([self.record("OPEN")] if after_write else []) + [value]
                    self.write_error = subprocess.TimeoutExpired("gh", 30)
                    with self.assertRaisesRegex(ValueError, "unconfirmed"):
                        server.pr_close(self.project, 42, actor="operator")
                    self.assertEqual(S.read_project_log(self.project), [])
                    self.assertEqual(sum(args[1:3] == ["pr", "close"] for args, _ in self.calls),
                                     int(after_write))

    def test_broker_rejects_other_targets_options_stdin_and_raw_writes(self):
        invalid = [[], ["0"], ["-1"], ["https://github.com/other/project/pull/42"], ["branch"],
                   ["42", "43"], ["42", "--repo", "other/project"], ["42", "--project", "other"],
                   ["42", "--delete-branch"], ["42", "--comment", "Text"], ["42", "--json"],
                   ["42", "--file", "/tmp/comment"]]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.request("close", *args)
        for body in ("comment", "\n", "@/tmp/comment"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.request("close", "42", body=body)
        for operation in ("close", "merge", "reopen", "edit"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["pr", operation, "42"]})
        self.assertEqual(self.calls, [])
        self.assertEqual(S.read_project_log(self.project), [])

    def test_actor_and_direct_target_denials_precede_github(self):
        for actor in ("l2", "burak", "", None):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                server.pr_close(self.project, 42, actor=actor)
        for number in (None, True, 0, -1, "42", "https://github.com/other/project/pull/42"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                server.pr_close(self.project, number, actor="operator")
        result = self.alt("pr", "close", "42", env={"ALTITUDE_ACTOR": "l2"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not available to an L2", result.stderr)
        result = self.alt("pr", "close", "42", env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": self.project})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("project-bound runtime alt socket", result.stderr)
        for origin in ("https://example.invalid/team/project.git", "../other.git"):
            git("remote", "set-url", "origin", origin, cwd=self.repo)
            with self.assertRaisesRegex(ValueError, "origin"):
                server.pr_close(self.project, 42, actor="operator")
        self.assertEqual(self.calls, [])

    def test_operator_cli_and_http_fix_actor_and_reject_payload_expansion(self):
        http = self.http()
        env = {"ALTITUDE_ACTOR": "burak", "ALTITUDE_PROJECT": self.project, "ALTITUDE_HOST": "127.0.0.1",
               "ALTITUDE_PORT": str(http.server_port), "ALTITUDE_TLS": "0"}
        result = self.alt("pr", "close", "42", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.record("CLOSED") | {"outcome": "closed"})
        payload = {"project": self.project, "number": 42}
        with self.post(http, payload) as response:
            self.assertEqual(json.load(response), self.record("CLOSED") | {"outcome": "already-closed"})
        events = S.read_project_log(self.project)
        self.assertEqual([event["actor"] for event in events], ["operator", "operator"])
        self.calls.clear()
        for change in ({"number": True}, {"number": "42"}, {"number": 0}, {"body": None}, {"body": "text"},
                       {"actor": "l3"}, {"repo": "other/project"}, {"delete_branch": True},
                       {"comment": "text"}, {"project": "unregistered-project"}):
            with self.subTest(change=change), self.assertRaises(urllib.error.HTTPError) as error:
                self.post(http, payload | change)
            self.assertEqual(error.exception.code, 400)
        for args in (("0",), ("42", "--json"), ("42", "--delete-branch"), ("42", "--repo", "other/project")):
            result = self.alt("pr", "close", *args, env=env)
            self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, str(ALT), "pr", "close", "42"],
                                input="unrequested comment", env=os.environ | env,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no body", result.stderr)
        self.assertEqual(self.calls, [])
        self.assertEqual(S.read_project_log(self.project), events)

    def test_runtime_shims_on_each_engine_use_project_bound_broker(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                runtime = l3._l3_runtime(self.project, engine)
                self.addCleanup(l3._remove_runtime, runtime)
                env = os.environ | l3._l3_env(self.project, runtime)
                self.state = "OPEN"
                result = subprocess.run([str(runtime / "bin" / "alt"), "pr", "close", "42"],
                                        input="", cwd=runtime, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), self.record("CLOSED") | {"outcome": "closed"})
                for args in (["pr", "close", "42", "--project", "other"],
                             ["pr", "close", "42", "--delete-branch"]):
                    denied = subprocess.run([str(runtime / "bin" / "alt"), *args], input="", cwd=runtime,
                                            env=env, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(denied.returncode, 0)
        self.assertEqual([event["actor"] for event in S.read_project_log(self.project)],
                         ["l3"] * len(config.ENGINES))

    def test_mcp_coordinator_closes_and_denies_cross_project_or_raw_write(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        adapter = tomllib.loads("\n".join(engines.codex_l3_permissions(runtime, project=self.project)))["mcp_servers"]["altitude"]
        requests = [{"kind": "alt", "args": ["pr", "close", "42"], "actor": "operator"},
                    {"kind": "alt", "args": ["pr", "close", "42", "--project", "other"]},
                    {"kind": "gh", "args": ["pr", "close", "42"]}]
        wire = "\n".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                     "params": {"name": "coordinator", "arguments": request}})
                         for i, request in enumerate(requests)) + "\n"
        result = subprocess.run([adapter["command"], *adapter["args"]], input=wire, cwd=runtime,
                                env=os.environ | l3._l3_env(self.project, runtime),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line)["result"] for line in result.stdout.splitlines()]
        self.assertEqual([reply["isError"] for reply in replies], [False, True, True])
        receipt = json.loads(json.loads(replies[0]["content"][0]["text"])["stdout"])
        self.assertEqual(receipt, self.record("CLOSED") | {"outcome": "closed"})
        self.assertEqual([event["actor"] for event in S.read_project_log(self.project)], ["l3"])
