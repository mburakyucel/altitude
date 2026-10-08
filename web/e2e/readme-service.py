"""The README's example project: a search-index migration with three owners and one operator decision.

Real handlers and storage; only the engine processes are fixtures. Every message is authored example text.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from service_support import configure, serve
from tests.support import SUITE, add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, l3, project_setup, route, state as S, tasks as T

PROJECT = "atlas"
RETENTION = {
    "question": "How long should we keep the old index for rollback?",
    "options": [
        {"key": "seven", "label": "7 days", "text": "Keep the old index for seven days."},
        {"key": "thirty", "label": "30 days", "text": "Keep the old index for thirty days."},
    ],
    "recommended_key": "seven",
    "why": "Covers the pilot and a full traffic cycle. Thirty days gives a longer rollback window but keeps "
           "both indexes on disk.",
}


@contextmanager
def minutes_ago(minutes: float):
    """Seed records at an earlier moment so the conversation reads as it happened."""
    real = S.now, T._conversation_time
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    S.now = lambda: moment.replace(microsecond=0).isoformat()
    T._conversation_time = lambda: moment.isoformat(timespec="microseconds")
    try:
        yield moment.isoformat()
    finally:
        S.now, T._conversation_time = real


def main():
    configure()
    settings = S.read_json(config.ROOT / "settings.json", {})
    S.write_json(config.ROOT / "settings.json", {**settings, "operator_name": "Alex"})
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    engine = config.ENGINES[0]
    repos = SUITE / "readme-repositories"  # beside the projects folder: one origin.git per parent
    repo = make_repo(repos / PROJECT / PROJECT)
    with config.add_project(PROJECT, path=repo):
        pass
    with config.add_project("harbor", path=make_repo(repos / "harbor" / "harbor")):
        pass

    def turn(minutes, prompt, reply, turn_id):
        with minutes_ago(minutes):
            l3.chat_log(PROJECT, "user", prompt, trigger="chat", turn_id=turn_id, engine=engine)
        with minutes_ago(minutes - 1):
            l3.chat_log(PROJECT, "assistant", reply, trigger="chat", turn_id=turn_id, engine=engine)

    turn(65, "Move Atlas to a versioned search index. Keep the v1 API stable and p95 below 200 ms. "
         "Agree the rollout constraints before starting.",
         "Keep the response contract unchanged; bind cursors to an index generation. Backfills use document ID "
         "and source revision so retries are safe. Establish latency on the current index before comparing v2.",
         "readme-1")
    turn(35, "Agreed. Start compatibility, backfill and the performance baseline independently. "
         "Hold the rollout until we have the results.",
         "Three L2 owners are working in separate worktrees.\n\n**Compatibility** covers the v1 contract and "
         "cursors. **Backfill** implements resumable batches. **Performance** measures the existing index while "
         "those changes are built.\n\nI will use their reports to coordinate rollout readiness. You can steer "
         "each owner directly from its task.", "readme-2")
    l3.save_info(PROJECT, {"session_id": "readme-l3", "engine_last": engine, "last_turn": S.now(), "turns": 14,
                           "sessions": {engine: {"session_id": "readme-l3", "turns": 14,
                                                 "confinement_version": l3.L3_CONFINEMENT_VERSION}}})

    def owner(title, request, minutes):
        with minutes_ago(minutes):
            slug = T.new(PROJECT, title, request)["slug"]
            T.dispatch(PROJECT, slug, attempt=1, session_id=f"readme-{slug}", agent_id=f"readme-{slug}",
                       worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=engine)
        fake.workers[f"readme-{slug}"] = {"id": f"readme-{slug}", "sessionId": f"readme-{slug}",
                                          "state": "working", "status": "busy", "input_delivered": True}
        return slug

    delivered = []  # the owner's session records each message as its inbox hook delivered it

    def say(slug, role, text, minutes):
        with minutes_ago(minutes) as at:
            row = T.message(PROJECT, slug, role, text)
        T.take_inbox(PROJECT, slug)
        if role == T.OPERATOR_MESSAGE_ROLE:
            delivered.append({"type": "attachment", "timestamp": at, "attachment": {
                "type": "hook_additional_context", "content": [T.render_inbox([row])]}})

    compatibility = owner("Preserve client compatibility",
                          "Keep the v1 response contract and in-flight cursors across the index cutover.", 34)
    backfill = owner("Build resumable index backfill",
                     "Backfill the versioned index in resumable, revision-aware batches.", 32)
    owner("Measure search latency baseline", "Measure p95 latency on the current index with a replay harness.", 30)

    say(compatibility, "l2", "I am covering the v1 response contract and the index-switch boundary. Existing "
        "clients will keep the same request and response shape.", 22)
    say(compatibility, T.OPERATOR_MESSAGE_ROLE, "Keep pagination tokens valid across the cutover. Clients must "
        "not restart an in-flight search.", 18)
    say(compatibility, "l2", "I will bind each token to its index generation. New searches can move to v2 while "
        "existing cursors finish on v1. The tests will cross the switch in both directions.", 17)
    say(backfill, "l2", "Can a retried batch rewrite a document that already reached the new index?", 20)
    say(backfill, "l3", "Use the recorded rule: upsert by document ID and source revision. A replay must not "
        "replace a newer revision. Resume with that contract.", 19)

    # The compatibility owner's live session: replies and commands with their folded output.
    def at(minutes):
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    def said(minutes, text):
        return {"type": "assistant", "timestamp": at(minutes), "message": {"content": [{"type": "text", "text": text}]}}

    def ran(minutes, ident, command, output):
        return [{"type": "assistant", "timestamp": at(minutes), "message": {"content": [
                    {"type": "tool_use", "id": ident, "name": "Bash", "input": {"command": command}}]}},
                {"type": "user", "timestamp": at(minutes - 0.5), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": ident, "content": output}]}}]

    session = config.HOME / ".claude" / "projects" / "readme" / f"readme-{compatibility}.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    records = [
        *delivered,
        said(18, "The cursor currently holds an offset. I am checking where the index generation can travel with it."),
        *ran(16, "readme-search", 'rg -n "cursor|generation" src/search',
             "src/search/cursor.ts:12: export function decodeCursor(token)\n"
             "src/search/index.ts:48: const generation = activeIndex()"),
        said(15, "The compatibility boundary is small: decode the token, select its generation, preserve the response "
                 "shape. I will cover old cursors, new searches and rollback in the contract suite."),
        *ran(12, "readme-tests", "pnpm test -- search-contract", "18 contract tests passed"),
        said(0.25, "Contract tests pass. I am checking rollback across index generations."),
    ]
    session.write_text("".join(json.dumps(record) + "\n" for record in records))
    # The current invocation's output supplies the task's latest-activity preview.
    job = S.task_dir(PROJECT, compatibility) / "l2-engine"
    job.mkdir(parents=True, exist_ok=True)
    S.write_json(job / f"readme-{compatibility}.json", {"id": f"readme-{compatibility}", "engine": engine,
                                                         "session_id": f"readme-{compatibility}", "started_at": at(34)})
    (job / f"readme-{compatibility}.stdout.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records if record["type"] != "attachment"))

    # Later the backfill owner needs a product decision; L3 brings it to the operator.
    T.block(PROJECT, backfill, RETENTION["question"], actor="l2", questions={"questions": [RETENTION]})
    T.escalate(PROJECT, backfill, RETENTION["question"], questions={"questions": [RETENTION]})
    for name in (PROJECT, "harbor"):  # Setup is complete, as on a configured machine
        project_setup.request(name, "repair", actor="operator")
        project_setup.run(name)
    # Weekly allowance readings are illustrative; no provider is asked.
    readings = {"claude": 42, "codex": 36}
    route.engine_readouts = lambda: [{"engine": name, "label": config.ENGINE_LABELS[name], "week": readings[name],
                                      "known": True, "stale": False, "at": S.now()} for name in config.ENGINES]
    S.regen_state_md(PROJECT)
    serve()


if __name__ == "__main__":
    main()
