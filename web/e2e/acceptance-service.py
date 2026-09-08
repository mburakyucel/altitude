"""Fictional populated project for all browser assertions and real L2 lifecycle requests."""
import json
import sys

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, l3, server, state as S, tasks as T


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    engines.claude_print = lambda _prompt, **options: {
        "text": "The fixture engine received the project message.", "session_id": "fixture-project-session"}
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    l3.save_info(project, {"session_id": "fixture-project-session", "engine_last": config.ENGINES[0],
                          "last_turn": S.now(), "turns": 4,
                          "sessions": {config.ENGINES[0]: {"session_id": "fixture-project-session", "turns": 4,
                                         "confinement_version": l3.L3_CONFINEMENT_VERSION}}})

    def task(title, state):
        row = T.new(project, title, "Fictional browser acceptance work, never sent to a live provider.")
        slug = row["slug"]
        if state != "queued":
            worktree = add_worktree(repo, slug)
            row = T.dispatch(project, slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                             worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
            T.message(project, slug, "l2", "The isolated task is ready for your message.")
            if state == "blocked":
                row = T.block(project, slug, "Waiting for clarification", actor="l2", updates={"waiting_on": "l3"})
            elif state == "rejected":
                row = T.reject(project, slug, "The fictional work is no longer needed.")
            elif state == "done":
                row["state"] = "reported"
                row["prs"] = [101]
                S.save_task(project, row)
                S.write_json(S.task_dir(project, slug) / "report.json", {
                    "landed": {"prs": [{"number": 101, "merged": True}], "main_runs": []},
                    "deploy": {"status": "not-applicable"}, "review": [],
                })
                row = T.done(project, slug, digest="Fictional completed delivery.")
            session = config.HOME / ".claude" / "projects" / "fixture" / f"fixture-{slug}.jsonl"
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_text(json.dumps({"type": "assistant", "timestamp": S.now(),
                "message": {"content": [{"type": "text", "text": "Fixture session: the task work is recorded."}]}}) + "\n")
        return row

    running = task("Prepare index migration", "running")
    queued = task("Check backfill recovery", "queued")
    completed = task("Document search contract", "done")
    task("Retire unused prototype", "rejected")
    task("Clarify retry policy", "blocked")
    decision = task("Choose validation scope", "blocked")
    if sys.argv[1:] == ["tasks"]:
        T.escalate(project, decision["slug"], "Which validation scope? Option A: Keep the bounded scope. "
                   "Option B: Expand the scope. I recommend A.")

    def turn(prompt, reply, turn_id, *, trigger="chat", tasks=()):
        l3.chat_log(project, "user", prompt, trigger=trigger, turn_id=turn_id, engine=config.ENGINES[0])
        l3.chat_log(project, "assistant", reply, trigger=trigger, turn_id=turn_id,
                    engine=config.ENGINES[0], tasks=list(tasks))

    turn("Keep the search contract stable.", "Two bounded tasks cover the migration.", "fixture-chat-1",
         tasks=(running["slug"], queued["slug"]))
    turn(f"Report landed for `{completed['slug']}`.", "The completed contract is archived.",
         "fixture-system-1", trigger="report-landed")
    turn("Check the saved retry decision.", "The recorded retry rule applies.",
         "fixture-system-2", trigger="task-blocked")
    turn("What remains?", "The migration is in progress.", "fixture-chat-2")
    turn("The fixture session was restored.", "The saved context is ready.",
         "fixture-system-3", trigger="system-recovery")
    turn("Keep the validation bounded.", "The fixture records the chosen scope.", "fixture-chat-3")
    S.regen_state_md(project)

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/workers":
                return self._json({"calls": [{key: row.get(key) for key in ("engine", "session_id", "prompt")}
                                             for row in fake.calls], "workers": fake.workers,
                                   "pending": {row["slug"]: T.pending(project, row["slug"])
                                               for row in S.list_tasks(project)}})
            return super().do_GET()

    serve(Handler)


if __name__ == "__main__":
    main()
