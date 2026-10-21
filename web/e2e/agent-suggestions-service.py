"""Project agent runs that suggested tasks, in the fictional project "harbor", through the real handlers and records:
Review each merge suggested three tasks this morning, L3 kept two and set one aside, and the operator made a task of
the second; Dependency check suggested two that L3 is checking now; Docs drift had both of its first run's
suggestions set aside by L3, and its second run's check missed its deadline. Only the engine seam is replaced:
workers are fictional records, so nothing launches a provider, and no L3 turn runs."""
from datetime import datetime, timedelta, timezone
import uuid

from service_support import configure, serve
from tests.support import make_repo
from altitude import config, engines, l3, project_agents as A, route, suggestions, tasks as T

PROJECT = "harbor"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

# The engine seam: each fictional worker's state and final answer, by worker id.
workers: dict[str, dict] = {}


def start_project_agent_run(engine, name, prompt, *, cwd, job_root, model, effort, runtime_max, extra_env):
    worker = "fixture-" + extra_env["ALTITUDE_AGENT_RUN"].replace("/", "-")
    workers[worker] = {"state": "working", "answer": ""}
    return {"returncode": 0, "agent": {"id": worker, "sessionId": f"session-{worker}"}}


def owned_worker(worker, *, job_root):
    row = workers.get(worker)
    return row and {"id": worker, "state": row["state"], "detail": "", "detail_at": 0,
                    "usage": {"input_tokens": 420_000, "output_tokens": 6_000}}


def make(name: str, brief: str, at: datetime) -> str:
    definition = {"name": name, "brief": brief, "schedules": [], "events": False, "tell": "found", "model": None,
                  "limit": 20}
    agent = A.change(PROJECT, "create", {"definition": definition}, by="operator", source="fixture", now=at)["agent"]
    A.change(PROJECT, "turn_on", {"agent": agent}, by="operator", source="fixture", now=at)
    return agent


def ran(agent: str, at: datetime, answer: str) -> int:
    """Runs `agent` now at `at`; its fictional worker answers five minutes later."""
    A.change(PROJECT, "run", {"agent": agent}, by="operator", source="fixture", now=at)
    A.start_next(PROJECT, now=at)
    workers[list(workers)[-1]].update(state="done", answer=answer)
    A.reconcile(PROJECT, now=at + timedelta(minutes=5))
    return A._load(PROJECT)["runs"][-1]["n"]


def main():
    configure()
    engines.start_project_agent_run = start_project_agent_run
    engines.owned_worker = owned_worker
    engines.observe_token_usage = lambda _engine, _session, _cursor, *, job_root: {"cursor": {}, "status": "observed",
                                                                                   "sessions": []}
    engines.worker_answer = lambda worker, *, job_root: workers[worker]["answer"]
    route.reserve = lambda _engine: None
    with config.add_project(PROJECT, path=make_repo(config.PROJECT_ROOTS[0] / PROJECT)):
        pass
    l3.save_info(PROJECT, {"session_id": "fictional-harbor", "engine_last": config.ENGINES[0], "turns": 1})

    merges = make("Review each merge", "Read the newest merged pull request and check that every behaviour it "
                  "changes has a test. Say which change lacks one and where the test would go.", NOW - timedelta(days=2))
    morning = NOW - timedelta(hours=3)
    n = ran(merges, morning, """Found: #811 changed the retry limit without a test.

#811 raised the retry limit in uploader.py from 3 to 5. No test covers the limit, and the CLI repeats the old \
number in its help text.

Suggest: Add a test for the uploader's retry limit
A case beside the other retry tests in tests/test_uploader.py that checks the limit of 5, so the next change to it
fails a test.

Suggest: Say the new retry limit in the CLI's help
The help text of harbor upload still says 3 retries; it should read 5, as uploader.py now does.

Suggest: Raise the CLI's retry limit to match
The CLI keeps its own limit of 3 and should read the uploader's.""")
    suggestions.post(PROJECT, f"agent:{merges}/{n}", [1, 2],
                     [(3, "the CLI reads the uploader's limit, so there is nothing to change")],
                     now=morning + timedelta(minutes=20))
    suggestions.decide(PROJECT, f"agent:{merges}/{n}/2", "accept", uuid.uuid4().hex, now=morning + timedelta(hours=1))
    T.new(PROJECT, "Say the new retry limit in the CLI's help", "Brief.", improvement=f"agent:{merges}/{n}/2")
    with A.S.project_lock(PROJECT):
        l3.unqueue_locked(PROJECT, l3._queue_rows(l3.queue_path(PROJECT))[-1]["id"])

    deps = make("Dependency check", "Check whether any dependency has a security fix.", NOW - timedelta(days=2))
    ran(deps, NOW - timedelta(minutes=15), """Found: two packages have security fixes.

The lock file pins left-pad 1.3.0 and is-odd 3.0.1; both have fixed releases.

Suggest: Bump left-pad to 1.3.1
Its fixed release closes the advisory; the tests pass with it.

Suggest: Bump is-odd to 3.0.2
Its fixed release closes the advisory; the tests pass with it.""")

    docs = make("Docs drift", "Compare the docs with the code and say where they disagree.", NOW - timedelta(days=2))
    first = ran(docs, NOW - timedelta(hours=6), """Found: two pages describe removed flags.

docs/upload.md and docs/sync.md still describe --fast, which was removed.

Suggest: Remove --fast from the upload page
docs/upload.md still lists the removed flag.

Suggest: Remove --fast from the sync page
docs/sync.md still lists the removed flag.""")
    suggestions.post(PROJECT, f"agent:{docs}/{first}", [],
                     [(1, "#809 already removes it"), (2, "#809 already removes it")],
                     now=NOW - timedelta(hours=5, minutes=40))
    ran(docs, NOW - timedelta(hours=4), """Found: the README's install line names an old package.

Suggest: Fix the README's install line
It names harbor-cli, which is now harbor.""")
    A.unchecked(PROJECT, NOW)
    serve()


if __name__ == "__main__":
    main()
