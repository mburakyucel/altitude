"""Project agents that run on events the operator sends, in the fictional project "harbor", through the real handlers
and records: Review each merge took three merged pull requests in one run, replayed the latest with Run now, and runs
now with three more waiting for its next run; Flaky tests paused itself after three event runs couldn't finish, so it
waits in Needs you; Issue triage has the event trigger and is not on yet. The project names a fictional GitHub
repository, so the agent page shows every example. Beyond that name, only the engine seam is replaced: workers are
fictional records, so nothing launches a provider."""
from datetime import datetime, timedelta, timezone

from service_support import configure, serve
from tests.support import make_repo
from altitude import config, engines, github_intake, l3, project_agents as A, route

PROJECT = "harbor"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

# The engine seam: each fictional worker's state, final answer, failure and usage, by worker id.
workers: dict[str, dict] = {}


def start_project_agent_run(engine, name, prompt, *, cwd, job_root, model, effort, runtime_max, extra_env):
    worker = "fixture-" + extra_env["ALTITUDE_AGENT_RUN"].replace("/", "-")
    workers[worker] = {"state": "working", "answer": "", "detail": "", "usage": {}}
    return {"returncode": 0, "agent": {"id": worker, "sessionId": f"session-{worker}"}}


def owned_worker(worker, *, job_root):
    row = workers.get(worker)
    return row and {"id": worker, "state": row["state"], "detail": row["detail"], "detail_at": 0, "usage": row["usage"]}


def stop_l2_worker(_engine, worker, *, job_root):
    workers[worker]["state"] = "stopped"
    return "stopped"


def send(agent: str, text: str, key: str, at: datetime) -> None:
    A.change(PROJECT, "send", {"agent": agent, "text": text, "key": key}, by="operator", source="cli", now=at)


def finish(at: datetime, minutes: int, state: str, *, answer: str = "", detail: str = "") -> None:
    """Starts the waiting run at `at`; its fictional worker ends `minutes` later."""
    A.start_next(PROJECT, now=at)
    worker = list(workers)[-1]
    workers[worker].update(state=state, answer=answer, detail=detail,
                           usage={"input_tokens": 380_000 + 9_000 * minutes, "output_tokens": 6_000})
    A.reconcile(PROJECT, now=at + timedelta(minutes=minutes))


def make(name: str, brief: str, *, on: bool = True, at: datetime) -> str:
    definition = {"name": name, "brief": brief, "schedules": [], "events": True, "tell": "found", "model": None,
                  "limit": 20}
    agent = A.change(PROJECT, "create", {"definition": definition}, by="operator", source="fixture", now=at)["agent"]
    if on:
        A.change(PROJECT, "turn_on", {"agent": agent}, by="operator", source="fixture", now=at)
    return agent


def main():
    configure()
    engines.start_project_agent_run = start_project_agent_run
    engines.owned_worker = owned_worker
    engines.stop_l2_worker = stop_l2_worker
    engines.observe_token_usage = lambda _engine, _session, _cursor, *, job_root: {"cursor": {}, "status": "observed",
                                                                                   "sessions": []}
    engines.worker_answer = lambda worker, *, job_root: workers[worker]["answer"]
    route.reserve = lambda _engine: None

    # The examples name the project's GitHub repository; the fixture's origin is a local folder runs fetch from.
    github_intake.project_repo = lambda _project: ("example", "harbor")
    with config.add_project(PROJECT, path=make_repo(config.PROJECT_ROOTS[0] / PROJECT)):
        pass
    l3.save_info(PROJECT, {"session_id": "fictional-harbor", "engine_last": config.ENGINES[0], "turns": 1})

    # Past runs end before this week's Monday, so on any weekday the budget counts only the run going now.
    monday = A.week_start(NOW)
    week = monday - timedelta(days=6)
    merges = make("Review each merge", "Read the pull request the event names and check that every behaviour it "
                  "changes has a test. Say which change lacks one and where the test would go.", at=week)
    burst = monday - timedelta(days=4, hours=2)
    send(merges, "PR #810 merged", "pr-810", burst)
    send(merges, "PR #811 merged", "pr-811", burst)
    send(merges, "PR #812 merged", "pr-812", burst + timedelta(minutes=2))
    finish(burst + timedelta(minutes=2), 7, "done", answer="Found: #811 changed the retry limit without a test.\n\n"
           "#811 raised the retry limit in `uploader.py`; the test would go in `tests/test_uploader.py` beside the "
           "other retry cases.")
    replay = monday - timedelta(days=3, hours=2)
    A.change(PROJECT, "run", {"agent": merges}, by="operator", source="fixture", now=replay)
    finish(replay, 3, "done", answer="Nothing: Every change in PR #812 has a test.")

    flaky = make("Flaky tests", "Rerun the end-to-end suite at the commit the event names and say which tests "
                 "failed and then passed.", at=week)
    for n in range(3):
        at = monday - timedelta(days=2, hours=3 - n)
        send(flaky, f"Nightly run failed on 4f2a9c{n}", f"nightly-{n}", at)
        finish(at, 1, "failed", detail="`make test-e2e` is not a target.")

    make("Issue triage", "Label the issue the event names and say which area it belongs to.", on=False, at=week)

    # Review each merge runs now on PR #813; three more merges wait for its next run.
    send(merges, "PR #813 merged", "pr-813", NOW - timedelta(minutes=4))
    A.start_next(PROJECT, now=NOW - timedelta(minutes=4))
    for n in range(3):
        send(merges, f"PR #{814 + n} merged", f"pr-{814 + n}", NOW - timedelta(minutes=3 - n))
    A.reconcile(PROJECT, now=NOW)
    serve()


if __name__ == "__main__":
    main()
