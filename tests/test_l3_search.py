"""#229: historical evidence through real coordinator transports, without provider calls."""
import json
import os
import subprocess
import tomllib
from unittest import mock

from tests.support import AltitudeCase, make_repo
from altitude import config, engines, l3, server, state as S, tasks as T


class TestL3Search(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.setenv("ALTITUDE_PROJECT", self.project)

    def task(self, title="Index migration"):
        task = T.new(self.project, title, "Fictional retrieval evidence.")
        task.update(state="running", attempt=1)
        S.save_task(self.project, task)
        return task["slug"]

    def chat(self, text, *, role="user", at="2030-01-01T00:00:00+00:00", **meta):
        return l3.chat_log(self.project, role, text, at=at, **meta)

    def lookup(self, query, *args, actor="l3"):
        result = self.alt("l3", "search", query, *args, env={"ALTITUDE_ACTOR": actor})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if "--json" in args else result.stdout

    def test_interrupted_replies_keep_their_marker_as_matches_and_context(self):
        self.chat("Check the rollout flags")
        self.chat("Rollout flags look", role="assistant", interrupted=True, at="2030-01-01T00:01:00+00:00")
        self.chat("Rollout instead: use the other branch", at="2030-01-01T00:02:00+00:00")
        self.chat("", role="assistant", interrupted=True, at="2030-01-01T00:03:00+00:00")
        self.chat("Rollout branch confirmed", role="assistant", at="2030-01-01T00:04:00+00:00")
        record = self.lookup("rollout", "--json")
        rows = {row["source"]: row for result in record["results"] for row in result["context"]}
        marked = {row["text"]: row.get("interrupted") for row in rows.values() if row["role"] == "assistant"}
        self.assertEqual(marked, {"Rollout flags look": True, "": True, "Rollout branch confirmed": None})

    def test_archived_and_active_conversations_questions_reports_and_digest_are_original_and_read_only(self):
        slug = self.task()
        original = T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE,
                             "Index migration: retain the old format only until Friday.", by="operator")
        T.message(self.project, slug, "l2", "Understood; the exception expires at the review.")
        T.block(self.project, slug, "Index migration: is the Friday review still required?", actor="l2")
        before = self.lookup("Index migration", "--json")
        references = [r["match"] for r in before["results"]]
        self.assertIn(f"{self.project}/task/{slug}/conversation#{original['id']}", references)
        self.assertEqual(before["matched"], 2, "status-backed question anchors are searchable")
        task = S.load_task(self.project, slug)
        task["state"] = "done"
        S.save_task(self.project, task)
        T._archive(self.project, slug)
        directory = S.task_dir(self.project, slug)
        S.write_json(directory / "report.json", {"decisions": [{"decision": "Index migration uses version 2.",
                                                                "condition": "Only after Friday's review."}]})
        (directory / "digest.md").write_text("Index migration was delivered with a time-limited exception.\n")
        active = self.task("Current rollout")
        T.message(self.project, active, T.OPERATOR_MESSAGE_ROLE, "Index migration: the review has now passed.")
        root = config.project_dir(self.project)
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
        record = self.lookup("index MIGRATION", "--limit", "20", "--json")
        self.assertEqual(record["matched"], 5)
        self.assertTrue(set(references).issubset({r["match"] for r in record["results"]}))
        excerpts = [row for result in record["results"] for row in result["context"]]
        found = next(row for row in excerpts if row["source"].endswith("#" + original["id"]))
        self.assertEqual((found["text"], found["at"], found["role"], found["by"]),
                         (original["text"], original["at"], original["role"], "operator"))
        report = next(row for row in excerpts if row["source"].endswith("report.json#/decisions/0/decision"))
        self.assertEqual(report["text"], "Index migration uses version 2.")
        self.assertEqual(report["date_kind"], "file_modified")
        self.assertIsNone(report["by"])
        self.assertTrue(any(row["text"] == "Only after Friday's review." for row in excerpts))
        self.assertEqual(files, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_later_correction_and_nonmatching_adjacent_condition_remain_visible(self):
        self.chat("Atlas rollout uses version 1 for the pilot.")
        self.chat("This is temporary, until the Friday review.", role="assistant")
        for index in range(25):
            self.chat(f"Unrelated discussion {index}.", at="2030-01-02T00:00:00+00:00")
        self.chat("Correction: Atlas rollout now uses version 2.", at="2030-01-03T00:00:00+00:00")
        self.chat("The pilot-only exception has expired.", at="2030-01-03T00:00:01+00:00")
        record = self.lookup("Atlas rollout", "--json")
        self.assertEqual(record["matched"], 2)
        self.assertEqual(record["results"][0]["match"], f"{self.project}/chat.jsonl#L28")
        text = json.dumps(record)
        for phrase in ("version 1", "version 2", "temporary, until the Friday review", "exception has expired"):
            self.assertIn(phrase, text)
        self.assertIn("not new authority", record["notice"])
        self.assertIn("Current instructions and task records govern", record["notice"])

    def test_orphan_tasks_preserve_available_chat_and_active_and_archived_evidence(self):
        self.chat("Atlas rollout uses version 2.")
        sources = {f"{self.project}/chat.jsonl#L1"}
        for title, archived in (("Active history", False), ("Archived history", True)):
            slug = self.task(title)
            message = T.message(self.project, slug, "l2", "Atlas rollout evidence.")
            if archived:
                T._archive(self.project, slug)
            directory = S.task_dir(self.project, slug)
            S.write_json(directory / "report.json", {"fyi": ["Atlas rollout report."]})
            (directory / "digest.md").write_text("Atlas rollout digest.")
            sources.update((f"{self.project}/task/{slug}/conversation#{message['id']}",
                            f"{self.project}/task/{slug}/report.json#/fyi/0",
                            f"{self.project}/task/{slug}/digest.md#"))
        S.append_event(self.project, "orphan-active", "fyi", text="Unresolvable event-only directory.")
        orphan = S.archive_dir(self.project) / "orphan-archived"
        orphan.mkdir(parents=True)
        S.write_json(orphan / "status.json", {})
        (orphan / "digest.md").write_text("Atlas rollout stray digest is not task evidence.")
        root = config.project_dir(self.project)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
        for actor in ("l2", "l3"):
            record = self.lookup("Atlas rollout", "--limit", "20", "--json", actor=actor)
            self.assertEqual(record["status"], "partial")
            self.assertEqual(record["matched"], 7)
            self.assertEqual({item["match"] for item in record["results"]}, sources)
            self.assertEqual(record["unavailable_task_count"], 2)
            self.assertEqual(record["unavailable_tasks"],
                             [f"{self.project}/task/orphan-active", f"{self.project}/task/orphan-archived"])
        missing = self.lookup("absent phrase", "--json")
        self.assertEqual((missing["status"], missing["matched"], missing["results"]), ("partial", 0, []))
        text = self.lookup("absent phrase")
        self.assertIn("Partial search: 2 tasks", text)
        self.assertIn("No decision inferred", text)
        self.assertIn(f"Unavailable task evidence: {self.project}/task/orphan-active", text)
        self.assertNotIn("No matching evidence found", text)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in root.rglob("*") if p.is_file()})

    def test_active_orphan_does_not_fall_back_to_same_slug_archive(self):
        slug = self.task()
        T.message(self.project, slug, "l2", "Archived evidence.")
        T._archive(self.project, slug)
        (S.tasks_dir(self.project) / slug).mkdir()
        record = self.lookup("Archived evidence", "--json")
        self.assertEqual((record["status"], record["matched"]), ("partial", 0))
        self.assertEqual(record["unavailable_tasks"], [f"{self.project}/task/{slug}"])

    def test_unresolved_status_variants_are_gaps_but_corrupt_or_unreadable_files_are_errors(self):
        slug = self.task()
        directory = S.task_dir(self.project, slug)
        status = directory / "status.json"
        original = status.read_bytes()
        for content in ("", "null", "{}"):
            with self.subTest(content=content):
                status.write_text(content)
                self.assertEqual(self.lookup("anything", "--json")["status"], "partial")
        status.write_bytes(original)
        self.chat("Available evidence")
        for name in ("status.json", "conversation.jsonl", "report.json", "digest.md"):
            path = directory / name
            saved = path.read_bytes() if path.exists() else None
            for failure in ("corrupt", "unreadable"):
                if failure == "corrupt" and name == "digest.md":
                    continue
                with self.subTest(name=name, failure=failure):
                    path.unlink(missing_ok=True)
                    path.mkdir() if failure == "unreadable" else path.write_text("corrupt\n")
                    result = self.alt("l3", "search", "Available evidence", "--json")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("evidence unavailable", result.stderr)
                    self.assertEqual(result.stdout, "")
                    path.rmdir() if failure == "unreadable" else path.unlink()
            if saved is not None:
                path.write_bytes(saved)

    def test_literal_query_count_excerpt_and_output_bounds_are_explicit(self):
        long_text = "prefix " * 400 + "[Atlas.*] only until Friday" + " suffix" * 400
        self.chat(long_text)
        record = self.lookup("[Atlas.*]", "--json")
        row = record["results"][0]["context"][0]
        self.assertEqual(row["text"], long_text[row["start"]:row["end"]])
        self.assertIn("[Atlas.*] only until Friday", row["text"])
        self.assertTrue(row["truncated"])
        self.assertLessEqual(len(row["text"]), 1200)
        self.assertEqual(self.lookup("Atlas.+", "--json")["status"], "no_results", "query is not a regex")
        for index in range(30):
            self.chat("Atlas " + "😀" * 1200 + str(index))
            (S.tasks_dir(self.project) / f"orphan-{index:02d}-{'x' * 65}").mkdir(parents=True)
        bounded = self.lookup("Atlas", "--limit", "20", "--json")
        self.assertEqual(bounded["status"], "partial")
        self.assertEqual(bounded["unavailable_task_count"], 30)
        self.assertEqual(len(bounded["unavailable_tasks"]), 20)
        self.assertEqual(bounded["matched"], 31)
        self.assertTrue(bounded["truncated"])
        self.assertLessEqual(len(bounded["results"]), 20)
        self.assertLessEqual(len((json.dumps(bounded) + "\n").encode()), 65536)
        text = self.lookup("Atlas", "--limit", "20")
        self.assertLessEqual(len(text.encode()), 65536)
        self.assertIn("Results omitted", text)
        self.assertIn("truncated", text)
        self.assertIn("30 tasks", text)
        self.assertIn("showing 20 unavailable sources", text)
        self.assertEqual(len(self.lookup("Atlas", "--limit", "1", "--json")["results"]), 1)

    def test_empty_missing_corrupt_and_invalid_evidence_never_invents_a_decision(self):
        self.assertEqual(self.lookup("missing", "--json")["status"], "no_results")
        self.assertIn("No matching evidence found. No decision inferred.", self.lookup("missing"))
        self.task()  # absent conversation/report files are normal
        self.assertEqual(self.lookup("missing", "--json")["matched"], 0)
        for args in (("",), (" " ,), ("x" * 201,), ("x", "--limit", "0"), ("x", "--limit", "21")):
            result = self.alt("l3", "search", *args, env={"ALTITUDE_ACTOR": "l3"})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("no_results", result.stdout)
        (config.project_dir(self.project) / "chat.jsonl").write_text("corrupt\n")
        result = self.alt("l3", "search", "missing", env={"ALTITUDE_ACTOR": "l3"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence unavailable", result.stderr)
        self.assertNotIn("No matching", result.stdout)

    def test_strict_project_isolation_filters_system_tool_and_foreign_evidence(self):
        other = "other-search-project"
        self.register(other)
        l3.chat_log(other, "user", "foreign-only Atlas decision")
        foreign = T.new(other, "Foreign task", "foreign-only")
        S.write_json(S.task_dir(other, foreign["slug"]) / "report.json", {"fyi": ["foreign-only"]})
        self.chat("system-only", trigger="restart")
        self.chat("system-only", role="assistant", trigger="report-landed")
        self.chat("tool-only", role="tool")
        self.chat("A human exchange", tools=[{"command": "tool-only"}])
        for query in ("foreign-only", "system-only", "tool-only"):
            self.assertEqual(self.lookup(query, "--json")["matched"], 0)
        denied = self.alt("--project", other, "l3", "search", "foreign-only", env={"ALTITUDE_ACTOR": "l2"})
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("launch project", denied.stderr)
        for args in (["--project", other, "l3", "search", "foreign-only"],
                     ["l3", "search", "x", "--file", "/etc/passwd"]):
            with self.assertRaisesRegex(ValueError, "socket fixes the project"):
                server.l3_verb_request(self.project, {"kind": "alt", "args": args})
        local_task = self.task()
        S.append_event(self.project, "orphan-active", "fyi")
        for target in ("chat.jsonl", f"tasks/{local_task}/conversation.jsonl", f"tasks/{local_task}/report.json",
                       f"tasks/{local_task}/status.json", "tasks/orphan-active/status.json", "archive/linked-task"):
            with self.subTest(target=target):
                path = config.project_dir(self.project) / target
                saved = path.read_bytes() if path.exists() else None
                path.unlink(missing_ok=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(config.project_dir(other) if target == "archive/linked-task"
                                else config.project_dir(other) / "chat.jsonl")
                try:
                    with self.assertRaisesRegex(ValueError, "outside the selected project"):
                        l3.search(self.project, "foreign-only")
                finally:
                    path.unlink()
                    if saved is not None:
                        path.write_bytes(saved)

    def test_fresh_rotated_and_cross_engine_resumed_l3_can_lookup_beyond_handoff_through_real_transports(self):
        make_repo(self.repo)
        self.chat("Atlas rollout: only during the pilot.", turn_id="original-decision")
        self.chat("Correction: Atlas rollout pilot is complete; version 2 now applies.",
                  at="2030-01-02T00:00:00+00:00")
        S.append_event(self.project, "orphan-task", "fyi")
        for index in range(30):
            self.chat(f"Later discussion {index}.", at="2030-01-03T00:00:00+00:00")
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        resumes = []

        def execute(text, **kwargs):
            self.assertIn('alt l3 search "literal text"', text)
            self.assertNotIn("only during the pilot", text, "old evidence is beyond the handoff")
            runtime = kwargs["cwd"]
            env = os.environ | kwargs["extra_env"]
            args = ["l3", "search", "Atlas rollout", "--json"]
            if "sandbox_settings" in kwargs:
                settings = tomllib.loads("\n".join(kwargs["sandbox_settings"]))
                adapter = settings["mcp_servers"]["altitude"]
                wire = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "coordinator", "arguments": {"kind": "alt", "args": args}}}
                result = subprocess.run([adapter["command"], *adapter["args"]], input=json.dumps(wire) + "\n",
                                        cwd=runtime, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                reply = json.loads(result.stdout)["result"]
                self.assertFalse(reply["isError"], reply)
                response = json.loads(reply["content"][0]["text"])
                self.assertEqual(response["returncode"], 0, response)
                evidence = json.loads(response["stdout"])
            else:
                self.assertIn("Bash(alt *)", kwargs["allowed_tools"])
                result = subprocess.run([str(runtime / "bin" / "alt"), *args], input="", cwd=runtime,
                                        env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                evidence = json.loads(result.stdout)
            self.assertEqual(evidence["matched"], 2)
            self.assertEqual(evidence["status"], "partial")
            self.assertEqual(evidence["unavailable_tasks"], [f"{self.project}/task/orphan-task"])
            self.assertIn("only during the pilot", json.dumps(evidence))
            self.assertIn("version 2 now applies", json.dumps(evidence))
            self.assertEqual(evidence["results"][-1]["match"], f"{self.project}/chat.jsonl#L1")
            resumes.append(kwargs.get("resume"))
            sid = kwargs.get("resume") or f"fictional-{len(resumes)}"
            return {"session_id": sid, "reported_session_id": sid, "text": "Evidence retrieved.", "usage": {}}

        with mock.patch.object(engines, "claude_print", side_effect=execute), \
             mock.patch.object(engines, "codex_exec", side_effect=execute):
            first, second = config.ENGINES
            for engine in (first, first, second, first, second):
                with mock.patch.object(l3, "_select", return_value={"engine": engine, "why": "fixture"}):
                    self.assertTrue(l3.turn(self.project, "Retrieve the earlier decision.")["completed"])
            l3.reset(self.project)
            with mock.patch.object(l3, "_select", return_value={"engine": second, "why": "fixture"}):
                self.assertTrue(l3.turn(self.project, "Retrieve after rotation.")["completed"])
        self.assertEqual(resumes, [None, "fictional-1", None, "fictional-1", "fictional-3", None])
