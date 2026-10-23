"""Routines that change worktrees and branches, in the fictional project "harbor", through the real handlers, records
and Git: Clear old worktrees may change them, and its last run set two helper worktrees aside, deleted a merged
branch and kept a worktree with uncommitted work and a branch with work found nowhere else; Dependency check only
reports, and its last run's one request was refused; the built-in Tidy worktrees and branches waits to be turned on.
Only the engine seam is replaced, and GitHub's answer about merged pull requests: workers are fictional records, so
nothing launches a provider."""
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from service_support import configure, serve
from tests.support import git, make_repo
from altitude import config, engines, l3, project_agents as A, route, verify

PROJECT = "harbor"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
OLD = NOW - timedelta(days=10)

# The engine seam: each fictional worker's state, final answer and usage, by worker id.
workers: dict[str, dict] = {}


def start_project_agent_run(engine, name, prompt, *, cwd, job_root, model, effort, runtime_max, extra_env):
    worker = "fixture-" + extra_env["ALTITUDE_AGENT_RUN"].replace("/", "-")
    workers[worker] = {"state": "working", "answer": "", "usage": {}}
    return {"returncode": 0, "agent": {"id": worker, "sessionId": f"session-{worker}"}}


def owned_worker(worker, *, job_root):
    row = workers.get(worker)
    return row and {"id": worker, "state": row["state"], "detail": "", "detail_at": 0, "usage": row["usage"]}


def age(*paths: Path) -> None:
    """Every file and folder under `paths` last changed ten days ago."""
    stamp = OLD.timestamp()
    for top in paths:
        for folder, dirs, files in os.walk(top):
            for name in [*dirs, *files]:
                os.utime(os.path.join(folder, name), (stamp, stamp), follow_symlinks=False)
        os.utime(top, (stamp, stamp))


def worktree(repo: Path, name: str, *, unsaved: bool = False) -> None:
    """A worktree left under the project's worktree folder ten days ago, detached at main."""
    path = repo / config.WORKTREE_ROOT / name
    git("worktree", "add", "-q", "--detach", str(path), "main", cwd=repo)
    if unsaved:
        (path / "sync.py").write_text("# offline sync, half done\n")
    age(path, repo / ".git" / "worktrees" / name)


def branch(repo: Path, name: str, *, commit: bool = False) -> None:
    """A local branch made ten days ago at main, or one commit past it."""
    stamp = f"@{int(OLD.timestamp())} +0000"
    env = {**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
    subprocess.run(["git", "branch", name, "main"], cwd=repo, env=env, check=True, capture_output=True)
    if commit:
        tree = git("rev-parse", "main^{tree}", cwd=repo).strip()
        sha = subprocess.run(["git", "commit-tree", tree, "-p", "main", "-m", "fictional work"], cwd=repo, env=env,
                             check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "update-ref", f"refs/heads/{name}", sha], cwd=repo, env=env, check=True)


def make(name: str, brief: str, may: list[str]) -> str:
    definition = {"name": name, "brief": brief, "schedules": [{"days": [0], "time": "09:00"}], "events": False,
                  "tell": "found", "model": None, "limit": 20, "may": may}
    agent = A.change(PROJECT, "create", {"definition": definition}, by="operator", source="fixture", now=OLD)["agent"]
    A.change(PROJECT, "turn_on", {"agent": agent}, by="operator", source="fixture", now=OLD)
    return agent


def run(agent: str, at: datetime, requests: list[tuple[str, str]], answer: str) -> None:
    """A run started at `at` that asks Altitude for each change in turn, then ends four minutes later."""
    A.change(PROJECT, "run", {"agent": agent}, by="operator", source="fixture", now=at)
    A.start_next(PROJECT, now=at)
    n = A.agent_view(PROJECT, agent)["runs"][0]["n"]
    for i, (action, target) in enumerate(requests):
        A.act(PROJECT, f"{agent}/{n}", {"project": PROJECT, "run": f"{agent}/{n}", "kind": "act", "action": action,
                                        "target": target, "request": f"{agent}-{n}-{i}"})
    worker = list(workers)[-1]
    workers[worker].update(state="done", answer=answer, usage={"input_tokens": 1_900_000, "output_tokens": 8_000})
    A.reconcile(PROJECT, now=at + timedelta(minutes=4))


def main():
    configure()
    engines.start_project_agent_run = start_project_agent_run
    engines.owned_worker = owned_worker
    engines.observe_token_usage = lambda _engine, _session, _cursor, *, job_root: {"cursor": {}, "status": "observed",
                                                                                   "sessions": []}
    engines.worker_answer = lambda worker, *, job_root: workers[worker]["answer"]
    route.reserve = lambda _engine: None
    verify.gh = lambda _args, _repo, **_kw: []  # GitHub knows no merged pull request for these branches

    repo = make_repo(config.PROJECT_ROOTS[0] / PROJECT)
    with config.add_project(PROJECT, path=repo):
        pass
    l3.save_info(PROJECT, {"session_id": "fictional-harbor", "engine_last": config.ENGINES[0], "turns": 1})
    for name in ("agent-7c41e2d0", "agent-91d0c3f2"):
        worktree(repo, name)
    worktree(repo, "add-offline-sync-for-the-mobile-app-and-tablets", unsaved=True)
    branch(repo, "upload-retry-fix")
    branch(repo, "retry-limit-tests", commit=True)

    clear = make("Clear old worktrees", "Clear the worktrees and branches Altitude lists that nothing needs. Say what "
                 "you cleared and what you kept, and why.", ["worktrees"])
    reports = make("Dependency check", "Check whether any dependency has a security fix.", [])
    run(clear, NOW - timedelta(hours=2), [
        ("set-aside-worktree", "agent-7c41e2d0"), ("set-aside-worktree", "agent-91d0c3f2"),
        ("delete-branch", "upload-retry-fix"), ("set-aside-worktree", "add-offline-sync-for-the-mobile-app-and-tablets"),
        ("delete-branch", "retry-limit-tests")],
        "Found: 2 worktrees and 1 branch nothing needs.\n\nKept `add-offline-sync-for-the-mobile-app-and-tablets`: "
        "it has uncommitted changes to `sync.py`.")
    run(reports, NOW - timedelta(hours=1), [("delete-branch", "retry-limit-tests")],
        "Nothing: Every dependency is on its fixed release.")
    serve()


if __name__ == "__main__":
    main()
