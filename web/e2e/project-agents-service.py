"""Project agents in the fictional project "harbor" through the real handlers and records: Dependency check turned on
from its built-in, waiting behind Flaky test watch's running run; Release notes check, the operator's own, with runs
that found something, found nothing, were skipped and couldn't finish; Test timing paused; Old lint sweep retired;
and L3's offer of a new agent under its chat reply. Only the engine seam is replaced: workers are fictional records,
so nothing launches a provider and the running run keeps running until Stop."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from service_support import FixtureSource, configure, serve
from tests.support import make_repo
from altitude import config, engines, l3, project_agents as A, route

PROJECT = "harbor"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
FOUND = """Found: The changelog misses two merged fixes.

Two fixes merged this week have no changelog entry:

- **Tide timer rounding**, merged on Tuesday.
- **Berth list export**, merged on Wednesday.

Add one line for each under Fixed."""
LONG = ("Read the release notes in `CHANGELOG.md` and compare them with what merged on main since the last release. "
        "List every merged change that has no note, and every note that names a change that never merged. "
        "Quote the pull request title for each.")

# The engine seam: each fictional worker's state, final answer, failure and usage, by worker id.
workers: dict[str, dict] = {}


def start_project_agent_run(engine, name, prompt, *, cwd, job_root, model, effort, runtime_max, extra_env):
    worker = "fixture-" + extra_env["ALTITUDE_AGENT_RUN"].replace("/", "-")
    workers[worker] = {"state": "working", "answer": "", "detail": "", "usage": {}}
    return {"returncode": 0, "agent": {"id": worker, "sessionId": f"session-{worker}",
                                             "engine_model": "claude-opus-5-5"}}


def owned_worker(worker, *, job_root):
    row = workers.get(worker)
    return row and {"id": worker, "state": row["state"], "detail": row["detail"], "detail_at": 0, "usage": row["usage"]}


def stop_l2_worker(_engine, worker, *, job_root):
    workers[worker]["state"] = "stopped"
    return "stopped"


def run(agent: str, at: datetime, minutes: int, state: str, *, answer: str = "", detail: str = "") -> None:
    """A run the operator started at `at` that ended `minutes` later as its fictional worker did."""
    receipt = A.change(PROJECT, "run", {"agent": agent}, by="operator", source="fixture", now=at)
    A.start_next(PROJECT, now=at)
    worker = f"fixture-{agent}-{receipt['n']}"
    workers[worker].update(state=state, answer=answer, detail=detail,
                           usage={"input_tokens": 380_000 + 9_000 * minutes, "output_tokens": 6_000})
    A.reconcile(PROJECT, now=at + timedelta(minutes=minutes))


def make(name: str, brief: str, *, tell: str = "found", schedules=None, on: bool = True, at: datetime) -> str:
    definition = {"name": name, "brief": brief, "tell": tell, "model": None, "limit": 20,
                  "schedules": [{"days": [0, 1, 2, 3, 4], "time": "07:00"}] if schedules is None else schedules}
    agent = A.change(PROJECT, "create", {"definition": definition}, by="operator", source="fixture", now=at)["agent"]
    if on:
        A.change(PROJECT, "turn_on", {"agent": agent}, by="operator", source="fixture", now=at)
    return agent


def main():
    configure()
    engines.start_project_agent_run = start_project_agent_run
    engines.owned_worker = owned_worker
    engines.stop_l2_worker = stop_l2_worker
    engines.worker_answer = lambda worker, *, job_root: workers[worker]["answer"]
    # The engine's allowance is read from its provider; Altitude's reserve is unit-tested and fixed here.
    route.reserve = lambda _engine: None

    # A run's session reads one fictional message from its worker's transcript.
    transcripts = {}

    @contextmanager
    def transcript_sources(_project, slug, _task, *, folder=None):
        path, record = transcripts.setdefault(slug, (config.ROOT / f"{slug}.jsonl", {"id": slug, "input_delivered": True}))
        if not path.exists():
            at = (NOW - timedelta(days=1)).isoformat()
            path.write_text(json.dumps({"timestamp": at, "row": {
                "source": config.ENGINES[0], "kind": "message", "role": "assistant", "type": "fixture", "at": at,
                "text": "Compared the changelog with the merged pull requests."}}) + "\n")
        yield [(FixtureSource(path, record), record)]

    @contextmanager
    def activity_source(project, slug, task):
        with transcript_sources(project, slug, task) as sources:
            yield sources[0][0]

    engines.transcript_sources = transcript_sources
    engines.activity_source = activity_source
    engines.session_context_source = lambda _project, _slug, _task: None
    engines.transcript_rows = lambda _engine, value, _worktree: [dict(value["row"])]
    engines.public_message_identity = lambda _engine, _value: None

    with config.add_project(PROJECT, path=make_repo(config.PROJECT_ROOTS[0] / PROJECT)):
        pass
    l3.save_info(PROJECT, {"session_id": "fictional-harbor", "engine_last": config.ENGINES[0], "turns": 1})

    # Past runs end before this week's Monday, so on any weekday the budget counts only the run going now.
    monday = A.week_start(NOW)
    week = monday - timedelta(days=6)
    dependency = A.change(PROJECT, "builtin", {"key": "dependency-check"}, by="operator", source="fixture",
                          now=week)["agent"]
    notes = make("Release notes check", LONG, tell="every", at=week)
    run(notes, monday - timedelta(days=4, hours=2), 6, "done", answer=FOUND)
    run(notes, monday - timedelta(days=3, hours=2), 4, "done", answer="Nothing: Every merged change has a note.")
    route.reserve = lambda _engine: "Its engine's weekly allowance is inside Altitude's reserve."
    A.change(PROJECT, "run", {"agent": notes}, by="operator", source="fixture", now=monday - timedelta(days=2))
    A.start_next(PROJECT, now=monday - timedelta(days=2))
    route.reserve = lambda _engine: None
    run(notes, monday - timedelta(days=1, hours=2), 3, "failed", detail="The checkout has no CHANGELOG.md")
    paused = make("Test timing", "Say which test files got slower since last week.", at=week)
    A.change(PROJECT, "pause", {"agent": paused}, by="operator", source="fixture", now=week)
    retired = make("Old lint sweep", "List lint warnings added this week.", at=week - timedelta(days=20))
    A.change(PROJECT, "retire", {"agent": retired}, by="operator", source="fixture", now=week)
    # Flaky test watch runs now, so Dependency check's Run now waits for it.
    flaky = make("Flaky test watch", "Find tests that failed and then passed on a retry this week.", schedules=[], at=week)
    A.change(PROJECT, "run", {"agent": flaky}, by="operator", source="fixture", now=NOW - timedelta(minutes=4))
    A.start_next(PROJECT, now=NOW - timedelta(minutes=4))
    A.change(PROJECT, "run", {"agent": dependency}, by="operator", source="fixture", now=NOW - timedelta(minutes=1))

    # L3 answers the operator's question with an offer of a new agent under its reply.
    l3.chat_log(PROJECT, "user", "Could something check the docs links every Friday?", trigger="chat", turn_id="human-1")
    l3.chat_log(PROJECT, "assistant", "An agent can do that. Here it is; create it if it reads right.", trigger="chat",
                turn_id="human-1", engine=config.ENGINES[0])
    A.offer(PROJECT, "human-1", {"name": "Docs link check", "brief": "Open every link in docs/ and list the ones that fail.",
                                  "schedules": [{"days": [4], "time": "16:00"}], "tell": "found", "model": None,
                                  "limit": 20})
    serve()


if __name__ == "__main__":
    main()
