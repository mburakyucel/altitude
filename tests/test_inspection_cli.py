"""Compact L3 inspection verbs keep one text view and one stable full record."""
import contextlib
import io
import json
import runpy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from tests.support import ALT, AltitudeCase, make_repo
from altitude import config, digest, engines, incidents, l3, state as S


def cli(*argv: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(str(ALT))["main"](list(argv))
    return output.getvalue().strip()


class TestInspectionCLI(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.ghdir = self.fake_gh()
        self.register(self.project, path=self.repo, wip=1)
        self.setenv("ALTITUDE_PROJECT", self.project)
        self.setenv("ALTITUDE_ACTOR", "burak")

    def task(self, slug="inspect", **fields):
        row = {"slug": slug, "title": slug, "state": "running", "attempt": 1,
               "created": S.now(), "updated": S.now(), "dispatched": S.now(),
               "paths": [], "prs": [], "blocked_reason": None, "hold_merge": None,
               "l2_engine": "codex", "engine_model": "gpt-test"}
        row.update(fields)
        directory = S.tasks_dir(self.project) / slug
        directory.mkdir(parents=True, exist_ok=True)
        S.write_json(directory / "status.json", row)
        return row

    def test_task_report_text_and_json_include_all_sections_and_done_digest(self):
        self.task(state="done", verified={"verdict": "ok"})
        report = {"landed": {"prs": [{"number": 42, "title": "Inspect", "merged": True,
                                        "merge_sha": "abcdef1234567890"},
                                       {"number": 43, "title": "Closed", "merged": False, "merge_sha": None}],
                             "main_runs": [{"id": "9", "conclusion": "success"}], "deploy": "healthy"},
                  "review": [], "blocked": "", "decisions": [{"choice": "one reader"}],
                  "fyi": ["visible note"], "follow_ups": ["later item"],
                  "deviations": [{"reason": "none"}], "spend": {"turns": 2}}
        directory = S.task_dir(self.project, "inspect")
        S.write_json(directory / "report.json", report)
        (directory / "digest.md").write_text("Merged and verified.\n")

        text = cli("task", "report", "inspect")
        record = json.loads(cli("task", "report", "inspect", "--json"))

        for value in ("ok", "#42 merged", "Decisions:", "visible note", "later item",
                      "#43 not merged", "Deviations:", "turns=2", "Merged and verified"):
            self.assertIn(value, text)
        self.assertEqual(record["report"], report)
        self.assertEqual((record["verdict"], record["prs"][0]["merged"], record["digest"]),
                         ("ok", True, "Merged and verified."))

    def test_task_report_malformed_sections_keep_stable_text_and_raw_json(self):
        self.task(verified={"verdict": "contradicted"})
        malformed = {"landed": "merged", "blocked": {"why": "bad"}, "decisions": {"choice": "x"},
                     "fyi": [], "follow_ups": [], "deviations": [], "spend": ["many"]}
        S.write_json(S.task_dir(self.project, "inspect") / "report.json", malformed)

        text = cli("task", "report", "inspect")
        record = json.loads(cli("task", "report", "inspect", "--json"))

        self.assertIn("contradicted", text)
        self.assertIn("Errors:", text)
        self.assertEqual(record["report"], malformed)
        self.assertEqual((record["prs"], record["decisions"], record["spend"]), ([], [], {}))
        self.assertGreaterEqual(len(record["errors"]), 4)

    def test_messages_and_events_limit_newest_rows_with_relative_one_line_text(self):
        self.task()
        directory = S.task_dir(self.project, "inspect")
        rows = [{"id": str(i), "at": S.now(), "role": "l2" if i % 2 else "burak",
                 "by": "l2" if i % 2 else "burak", "text": f"message {i}\ncontinued"} for i in range(3)]
        (directory / "conversation.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        events = [{"at": S.now(), "kind": "new", "by": "l3"},
                  {"at": S.now(), "kind": "state", "frm": "queued", "to": "running", "by": "altd"},
                  {"at": S.now(), "kind": "stopped", "reason": "one\nreason", "by": "burak"}]
        (directory / "events.log").write_text("".join(json.dumps(row) + "\n" for row in events))

        message_text = cli("task", "messages", "inspect", "--last", "2")
        event_text = cli("task", "events", "inspect", "--last", "2")
        message_json = json.loads(cli("task", "messages", "inspect", "--last", "2", "--json"))
        event_json = json.loads(cli("task", "events", "inspect", "--last", "2", "--json"))

        self.assertNotIn("message 0", message_text)
        self.assertIn("message 1 continued", message_text)
        self.assertEqual(len(message_text.splitlines()), 2)
        self.assertRegex(message_text.splitlines()[0], r"^\[-?\d+[mhd]\]")
        self.assertIn("queued → running", event_text)
        self.assertIn("one reason", event_text)
        self.assertEqual(message_json, rows[-2:])
        self.assertEqual(event_json, events[-2:])

    def test_status_brief_is_under_ten_lines_and_json_keeps_full_status(self):
        self.task(paths=["shared.py"], prs=[17], state="blocked", blocked_reason="waiting for answer",
                  hold_merge="Burak review")
        self.task("holder", paths=["shared.py"], l2_engine="claude")
        S.write_json(self.ghdir / "prs.json", {"17": {"number": 17, "state": "OPEN",
            "mergedAt": None, "mergeCommit": None, "headRefName": "worktree-inspect", "headRefOid": "abc",
            "statusCheckRollup": [], "files": [{"path": "shared.py"}]}})

        text = cli("task", "status", "inspect", "--brief")
        record = json.loads(cli("task", "status", "inspect", "--json"))

        self.assertLessEqual(len(text.splitlines()), 10)
        for value in ("blocked", "codex/gpt-test", "attempt: 1", "#17 open", "Burak review",
                      "waiting for answer", "shared.py", "holder"):
            self.assertIn(value, text)
        self.assertEqual(record["verified"], None)
        self.assertEqual(record["prs"][0]["files"], ["shared.py"])

        self.task("pinned", state="queued", attempt=0, l2_engine=None, engine_model=None,
                  engine="claude", model="opus")
        self.assertIn("engine/model: claude/opus", cli("task", "status", "pinned", "--brief"))

    def test_queue_names_running_and_each_wait_reason(self):
        self.task("holder", paths=["shared.py"])
        self.task("lease-wait", state="queued", attempt=0, paths=["shared.py"])
        self.task("wip-wait", state="queued", attempt=0, paths=["other.py"])
        self.task("ask-burak", state="blocked", waiting_on="burak", blocked_reason="Choose a colour")

        text = cli("queue")
        record = json.loads(cli("queue", "--json"))

        self.assertIn(f"{self.project}/holder", text)
        self.assertIn("WIP limit", next(row["reason"] for row in record["waiting"] if row["slug"] == "lease-wait"))
        self.assertEqual(next(row["files"] for row in record["waiting"] if row["slug"] == "lease-wait"), [])
        self.assertEqual({row["kind"] for row in record["waiting"]}, {"wip", "waiting-burak"})
        self.assertIn("Choose a colour", text)

        S.write_json(config.MONITOR_DIR / "restart-pending.json", {"since": S.now(), "files": ["bin/alt"]})
        restarted = json.loads(cli("queue", "--json"))
        queued = [row for row in restarted["waiting"] if row["state"] == "queued"]
        self.assertEqual({row["kind"] for row in queued}, {"restart"})

    def test_repo_combines_git_restart_faults_and_systemd(self):
        (self.repo / "README.md").write_text("changed\n")
        (self.repo / "new.txt").write_text("new\n")
        S.write_json(config.MONITOR_DIR / "restart-pending.json", {"since": S.now(), "files": ["bin/alt"]})
        S.write_json(incidents.FAULTS, {"restart": {"count": 3, "last": S.now(), "incident": "I-1",
                                                           "detail": "private"}})
        service = {"unit": "altitude.service", "state": "active", "substate": "running", "pid": 321,
                   "last_restart": "Thu 2026-09-04 00:00:00 PDT", "error": None}
        with mock.patch.object(engines, "service_status", return_value=service):
            text = cli("repo")
            record = json.loads(cli("repo", "--json"))

        for value in ("main", "ahead 0, behind 0", "dirty files: 2", "1 files (bin/alt) since",
                      "restart=3", "active/running PID 321"):
            self.assertIn(value, text)
        self.assertEqual(record["dirty_files"], 2)
        self.assertNotIn("detail", record["faults"]["restart"])
        self.assertEqual(record["service"], service)

    def test_pr_uses_one_gh_call_and_has_stable_text_and_json(self):
        paths = [f"src/file-{i}.py" for i in range(12)]
        pr = {"number": 8, "state": "MERGED", "mergedAt": S.now(), "mergeCommit": {"oid": "merge-sha"},
              "headRefName": "worktree-x", "headRefOid": "head-sha", "files": [{"path": path} for path in paths],
              "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"},
                                    {"name": "lint", "status": "IN_PROGRESS"}]}
        S.write_json(self.ghdir / "prs.json", {"8": pr})

        text = cli("pr", "8")
        self.assertEqual(len(self.gh_log()), 1)
        (self.ghdir / "log.jsonl").unlink()
        record = json.loads(cli("pr", "8", "--json"))

        self.assertEqual(len(self.gh_log()), 1)
        self.assertIn("PR #8: MERGED", text)
        self.assertIn("1 passed, 0 failed, 1 pending", text)
        self.assertIn("+4 more", text)
        self.assertEqual(record["merge_sha"], "merge-sha")
        self.assertEqual(record["files"], paths)
        self.assertEqual(record["checks"]["total"], 2)

    def test_l3_tools_puts_ad_hoc_first_filters_days_and_groups_verbs(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        l3.chat_log(self.project, "assistant", "one", at=now,
                    tools=[{"name": "Bash", "command": "alt task report x"},
                           {"name": "Bash", "command": "git status --short"}])
        l3.chat_log(self.project, "assistant", "two", at=now,
                    tools=[{"name": "Bash", "command": "git status --short"},
                           {"name": "Bash", "command": "alt repo"}])
        l3.chat_log(self.project, "assistant", "old", at=old,
                    tools=[{"name": "Bash", "command": "python old.py"}])
        for i in range(12):
            l3.chat_log(self.project, "assistant", "bulk", at=now,
                        tools=[{"name": "Bash", "command": f"tool{i} --inspect"}])
        for _ in range(5):
            l3.chat_log(self.project, "assistant", "repeat", at=now,
                        tools=[{"name": "Bash", "command": "zzscan --everything"}])

        text = cli("l3", "tools", "--days", "7")
        record = json.loads(cli("l3", "tools", "--days", "7", "--json"))

        self.assertLess(text.index("git status"), text.index("alt task report"))
        git = next(group for group in record["groups"] if group["verb"] == "git")
        self.assertEqual(git["count"], 2)
        self.assertIn("zzscan --everything", text)
        self.assertIn("more ad-hoc groups", text)
        self.assertNotIn("python old.py", text)
        self.assertTrue({"git", "alt task report", "alt repo"}.issubset(
            {group["verb"] for group in record["groups"]}))

    def test_engine_command_capture_and_l3_persistence_are_bounded_for_both_engines(self):
        long = "x" * 250
        self.assertEqual(len(l3._tool_log([engines._tool("Bash", long)])[0]["command"]), 200)
        choices = [{"engine": "claude", "why": "test", "quota": {}},
                   {"engine": "codex", "why": "test", "quota": {}}]
        claude = {"text": "done", "session_id": "cl", "usage": {}, "context_tokens": 1, "cost": 0,
                  "turns": 1, "structured": None, "error": None, "limited": None,
                  "tools": [{"name": "Bash", "command": "alt repo" + long}]}
        wrapped = "/bin/bash -lc 'alt repo " + long + "'"
        codex = {"text": "done", "session_id": "cx", "reported_session_id": "cx", "usage": {"input_tokens": 1},
                 "returncode": 0, "error": None, "tools": [{"name": "Bash", "command": wrapped}]}
        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "claude_print", return_value=claude), \
             mock.patch.object(engines, "codex_exec", return_value=codex):
            l3.turn(self.project, "first")
            l3.turn(self.project, "second")
        assistant = [row for row in l3.chat_history(self.project, None) if row["role"] == "assistant"]
        self.assertEqual(len(assistant[0]["tools"][0]["command"]), 200)
        self.assertEqual(len(assistant[1]["tools"][0]["command"]), 200)
        self.assertTrue(assistant[1]["tools"][0]["command"].startswith("alt repo "))

        claude_event = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "alt queue"}}]}}
        result_event = {"type": "result", "result": "done", "session_id": "sid", "is_error": False}

        class Process:
            pid, returncode = 1, 0
            stdin = io.StringIO()
            stdout = io.StringIO(json.dumps(claude_event) + "\n" + json.dumps(result_event) + "\n")
            stderr = io.StringIO()
            def wait(self): return self.returncode
            def kill(self): self.returncode = -9

        with mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", return_value=Process()):
            parsed = engines.claude_print("prompt", cwd=self.tmp, settings=self.tmp / "settings.json")
        self.assertEqual(parsed["tools"], [{"name": "Bash", "command": "alt queue"}])

        events = "\n".join([json.dumps({"type": "thread.started", "thread_id": "cx"}),
                             json.dumps({"type": "item.completed", "item": {
                                 "type": "command_execution", "command": wrapped}})]) + "\n"
        process = SimpleNamespace(pid=1, returncode=0, communicate=lambda *_a, **_k: (events, ""))
        with mock.patch.object(engines.subprocess, "Popen", return_value=process):
            parsed = engines.codex_exec("prompt", cwd=self.tmp)
        self.assertEqual(parsed["tools"], [{"name": "Bash", "command": wrapped}])

    def test_service_status_is_one_read_only_systemctl_call(self):
        result = SimpleNamespace(returncode=0, stderr="", stdout=(
            "ActiveState=active\nSubState=running\nMainPID=99\n"
            "ActiveEnterTimestamp=Thu 2026-09-04 00:00:00 PDT\n"))
        with mock.patch.object(engines.subprocess, "run", return_value=result) as run:
            record = engines.service_status()
        self.assertEqual(run.call_count, 1)
        self.assertEqual((record["state"], record["pid"], record["last_restart"]),
                         ("active", 99, "Thu 2026-09-04 00:00:00 PDT"))
        self.assertNotIn("restart", run.call_args.args[0])

    def test_duplicate_cli_surfaces_are_removed_or_folded(self):
        self.task()
        help_result = self.alt("--help", env={"ALTITUDE_ACTOR": "burak"})
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertNotIn("agents", help_result.stdout)
        self.assertNotIn("digest", help_result.stdout)
        self.assertIn("monitor", help_result.stdout)
        shown = json.loads(cli("task", "show", "inspect"))
        status = json.loads(cli("task", "status", "inspect", "--json"))
        self.assertEqual(shown, status)


if __name__ == "__main__":
    unittest.main()
