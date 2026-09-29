"""Fictional workflow shared by offline tests and the authorized native image gate.

Only engine installation/quota observations and the external CLI are fixtures. Application
records, Git guards, dispatch, session binding and Stop stay real. Native execution uses an
isolated ALTITUDE_HOME so the image's background daemon cannot race this synchronous driver.
This is application-function acceptance, not browser or provider compatibility evidence.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock


ENGINE = r'''
import json, os, sys, time, uuid
from pathlib import Path
prompt = sys.stdin.read()
session = sys.argv[-2] if 'resume' in sys.argv else str(uuid.uuid4())
if os.environ.get('ALTITUDE_TASK'):
    with Path('fixture-input.jsonl').open('a') as output:
        output.write(json.dumps({'session':session, 'prompt':prompt}) + '\n')
    print(json.dumps({'type':'thread.started', 'thread_id':session}), flush=True)
    time.sleep(90)
else:
    print(json.dumps({'type':'thread.started', 'thread_id':session}), flush=True)
    print(json.dumps({'type':'item.completed', 'item':{'type':'agent_message',
        'text':'Fictional coordinator connected.'}}), flush=True)
    print(json.dumps({'type':'turn.completed', 'usage':{'input_tokens':1, 'output_tokens':1}}), flush=True)
'''


@contextmanager
def fixture_engine(folder):
    from altitude import config, engines, monitor
    executable = folder / "fixture-engine"
    executable.write_text(f"#!{sys.executable}\n" + ENGINE)
    executable.chmod(0o755)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(config, "CODEX_BIN", str(executable)))
        stack.enter_context(mock.patch.object(engines, "installation", side_effect=lambda engine:
            {"available": engine == "codex", "why": "deterministic fixture CLI"}))
        stack.enter_context(mock.patch.object(engines, "usage_hold", return_value=None))
        stack.enter_context(mock.patch.object(engines, "claude_agents", return_value=[]))
        stack.enter_context(mock.patch.object(monitor, "quota", return_value={"known": True}))
        yield


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, timeout=15).stdout.strip()


def prepare(project, repo):
    from altitude import config, dispatch, engines, git_policy, l3, project_setup, server, state as S, tasks as T
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture Operator")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(".claude/\n")
    (repo / "AGENTS.md").write_text("Fictional container acceptance. No external operations.\n")
    (repo / "README.md").write_text("Fictional project.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Fictional initial commit")
    origin = repo.with_name("workflow-origin.git")
    git(repo, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-qu", "origin", "main")
    with config.add_project(project, path=repo, l2_engine="codex", l3_engine="codex"):
        git_policy.install_hooks(repo)
    server.start_l3(project)
    assert project_setup.read(project)["intro"]["state"] == "complete", project_setup.read(project)
    assert l3.chat_history(project)[-1]["text"] == "Fictional coordinator connected."
    task = T.new(project, "Container workflow", "Preserve this task across replacement.", hold_merge="Operator review")
    slug = task["slug"]
    try:
        dispatch.run(project, slug)
        running = S.load_task(project, slug)
        assert running["state"] == "running", running
        assert engines.worker_live(running["l2_engine"], running, job_root=dispatch.l2_job_root(project, slug))
        worktree = Path(running["worktree"])
        (worktree / "draft.txt").write_text("Unfinished fixture work.\n")
        dispatch.stop(project, slug, reason="Fixture operator Stop")
        stopped = S.load_task(project, slug)
        assert stopped["stop_id"] and stopped["session_id"] == running["session_id"]
        assert engines.worker_termination(stopped, job_root=dispatch.l2_job_root(project, slug)) is True
        message = T.message(project, slug, config.OPERATOR_ACTOR, "Resume the saved fixture draft.")
        assert dispatch.resume(project, slug) == {"waiting": True}  # A message does not release Stop.
        saved = {key: stopped[key] for key in ("slug", "session_id", "worktree", "hold_merge", "stop_id")}
        saved.update(message_id=message["id"], coordinator=l3.info(project))
        return saved
    finally:
        current = S.load_task(project, slug)
        if current.get("agent_id"):
            engines.stop_l2_worker(current["l2_engine"], current["agent_id"], job_root=dispatch.l2_job_root(project, slug))


def resume(project, saved, *, paused=False):
    from altitude import config, dispatch, engines, l3, platform, state as S, tasks as T
    slug = saved["slug"]
    current = S.load_task(project, slug)
    assert all(current[key] == saved[key] for key in ("session_id", "worktree", "hold_merge", "stop_id"))
    assert l3.info(project) == saved["coordinator"]
    assert Path(current["worktree"], "draft.txt").read_text() == "Unfinished fixture work.\n"
    assert saved["message_id"] in [row["id"] for row in T.pending(project, slug)]
    request = dispatch.request_task_operation(project, slug, "resume", "Continue fixture task",
        actor=config.OPERATOR_ACTOR, stop_id=current["stop_id"], deliver_reason=False)
    if paused:
        assert dispatch.run_task_operation(project, slug).get("held")
        held = S.load_task(project, slug)
        assert held["daemon_request"]["status"] == "pending" and held["stop_id"] == saved["stop_id"]
        assert saved["message_id"] in [row["id"] for row in T.pending(project, slug)]
        instance = platform.container_lifecycle()["instance"]
        assert platform.change_container_lifecycle("continue", instance)["ready"]
    try:
        dispatch.run_task_operation(project, slug)
        current = S.load_task(project, slug)
        assert current["daemon_request"]["status"] == "done", current["daemon_request"]
        assert current["daemon_request"]["id"] == request["request"]["id"]
        assert current["state"] == "running" and not current.get("stop_id"), current
        assert all(current[key] == saved[key] for key in ("session_id", "worktree", "hold_merge"))
        assert engines.worker_live(current["l2_engine"], current, job_root=dispatch.l2_job_root(project, slug))
        assert saved["message_id"] not in [row["id"] for row in T.pending(project, slug)]
        assert dispatch.run_task_operation(project, slug)["idempotent"]
        inputs = [json.loads(line) for line in Path(current["worktree"], "fixture-input.jsonl").read_text().splitlines()]
        assert len(inputs) == 2 and all(row["session"] == saved["session_id"] for row in inputs), inputs
        assert inputs[-1]["prompt"].count("Resume the saved fixture draft.") == 1
        coordinator = l3.turn(project, "Continue the fictional coordinator conversation.")
        assert coordinator["completed"], coordinator
        assert coordinator["session_id"] == saved["coordinator"]["session_id"]
        dispatch.stop(project, slug, reason="Fixture acceptance complete")
        stopped = S.load_task(project, slug)
        assert engines.worker_termination(stopped, job_root=dispatch.l2_job_root(project, slug)) is True
        assert stopped["hold_merge"] == saved["hold_merge"]
        return {"registered_project": True, "coordinator_connected": True, "task_stop_and_resume": True,
                "same_session_worktree_hold": True, "queued_message_delivered_once": True,
                "replacement_admission": paused}
    finally:
        current = S.load_task(project, slug)
        if current.get("agent_id"):
            engines.stop_l2_worker(current["l2_engine"], current["agent_id"], job_root=dispatch.l2_job_root(project, slug))


if __name__ == "__main__":
    # The real image daemon keeps its fresh default state. Only this synchronous fixture driver
    # owns these records; no provider discovery or periodic background work can enter the lane.
    os.environ["ALTITUDE_HOME"] = "/home/altitude/workflow-fixture-state"
    sys.path.insert(0, "/opt/altitude")
    from altitude import config, platform, state as S
    assert platform.containerized() and os.getuid() == 1000
    config.ensure_root()
    saved_path = config.ROOT / "workflow.json"
    with fixture_engine(config.ROOT):
        if sys.argv[1] == "prepare":
            S.write_json(saved_path, prepare("workflow-fixture", platform.CONTAINER_PROJECTS / "workflow-fixture"))
            result = {"prepared": True, "task_stopped_with_pending_message": True}
        elif sys.argv[1] == "replaced":
            assert not platform.container_lifecycle()["ready"]
            result = resume("workflow-fixture", S.read_json(saved_path), paused=True)
        else:
            raise ValueError("Unknown workflow stage")
    print(json.dumps(result))
