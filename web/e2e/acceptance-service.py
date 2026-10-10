"""Fictional populated project for all browser assertions and real L2 lifecycle requests."""
import json
import sys

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, l3, server, state as S, tasks as T, verify


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
    # An unrelated interactive session's statusline snapshot, as an earlier global wrapper left it.
    S.write_json(config.MONITOR_DIR / "statusline-unrelated.json", {
        "session_id": "unrelated", "cwd": "/home/someone/unrelated-folder", "context_window": {"used_percentage": 9}})

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

    def turn(prompt, reply, turn_id, *, trigger="chat", tasks=(), slug=None, **created):
        meta = {"slug": slug} if slug else {}
        l3.chat_log(project, "user", prompt, trigger=trigger, turn_id=turn_id, engine=config.ENGINES[0], **meta)
        l3.chat_log(project, "assistant", reply, trigger=trigger, turn_id=turn_id,
                    engine=config.ENGINES[0], tasks=list(tasks), **created, **meta)

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
        def do_POST(self):
            if self.path == "/fixture/reported":
                body = self._body()
                slug = running["slug"]
                row = S.load_task(project, slug)
                report = {"landed": {"prs": [{"number": 103, "merged": False}], "main_runs": [],
                                     "deploy": "not-applicable"}, "review": [], "blocked": ""}
                S.write_json(S.task_dir(project, slug) / "report.json", report)
                row.update(prs=[103], hold_merge="Operator review required", state="reported",
                           verified={"verdict": "ok", "owner": T.report_owner(row)})
                S.save_task(project, row)
                verify.gh = lambda *_args, **_kwargs: {"state": body.get("pr_state", "OPEN")}
                return self._json(row)
            if self.path == "/fixture/heads-up":
                if self._body().get("history"):
                    for index in range(15):
                        turn(f"Saved request {index}.", f"Saved answer {index}.", f"heads-up-history-{index}")
                T.fyi(project, running["slug"], "Activation pending: routine build details.")
                turn("Reconcile saved tasks.", "The saved tasks have been checked.",
                     f"heads-up-restart-{len(l3.chat_history(project))}", trigger="restart")
                row = T.fyi(project, running["slug"],
                            "Deliveries are waiting on the build.\n\nAn owner is investigating the failure.", actor="l3")
                T.fyi(project, running["slug"], "Automatic fault evidence stays here.", actor="altd")
                l3.chat_log(project, "system", "Historical automatic FYI with ambiguous authorship.",
                            trigger="fyi", by="l3")
                T.fyi(project, running["slug"], "Routine owner progress.", actor="l2")
                return self._json(row)
            if self.path == "/fixture/notable":
                # System turns as altd records them: routine ones around one that created a task and one that filed
                # an issue (the rows' `tasks` and `issues`, SPEC.md §4.1).
                made = T.new(project, "Serialize concurrent landings", "Fictional follow-up work.")
                turn("Altitude restarted.", "Nothing to resume.", "notable-restart-1", trigger="restart")
                turn("Altitude restarted.", "The saved context is current.", "notable-restart-0", trigger="restart")
                turn(f"Task: {running['slug']}\nThe owner reports a problem.", "Read the owner's evidence.\n\n"
                     "Prepare index migration found that two landings can race on a busy main; I created "
                     "Serialize concurrent landings to fix it.", "notable-task", trigger="owner-message",
                     slug=running["slug"], tasks=[made["slug"]])
                turn(f"Report landed for `{completed['slug']}`.", "The contract delivery is verified.",
                     "notable-report", trigger="report-landed", slug=completed["slug"])
                turn("System fault in atlas.", "A worker stopped twice for the same reason, so I filed an issue "
                     "for the fix.", "notable-issue", trigger="incident",
                     issues=[{"title": "Workers stop when their session file rotates",
                              "url": "https://github.com/example/atlas/issues/7"}])
                # A turn that failed after creating a task, and one that created several things.
                retry = T.new(project, "Retry the stalled backfill", "Fictional follow-up work.")
                l3.chat_log(project, "user", f"Task: {queued['slug']}\nThe owner is blocked.", trigger="block",
                            turn_id="notable-failed", slug=queued["slug"])
                l3.chat_log(project, "error", "The provider stopped.", trigger="block", turn_id="notable-failed",
                            slug=queued["slug"], tasks=[retry["slug"]])
                split = [T.new(project, title, "Fictional follow-up work.")["slug"]
                         for title in ("Split the search index", "Measure the split index")]
                turn("Report landed.", "The review found two separate follow-ups and a provider defect; I created "
                     "Split the search index and Measure the split index and filed an issue for the defect.",
                     "notable-several", trigger="report-landed", tasks=split,
                     issues=[{"title": "Search requests time out on large indexes",
                              "url": "https://github.com/example/atlas/issues/8"}])
                turn("Altitude restarted.", "The saved tasks have been checked.", "notable-restart-2", trigger="restart")
                turn("Altitude restarted.", "The running task continues.", "notable-restart-3", trigger="restart")
                return self._json({"slug": made["slug"]})
            if self.path == "/fixture/queued-notices":
                # Coordinator notices as altd writes them: a handled block in history, then a block, a report and a
                # fault still waiting in L3's queue. Their prompts carry ids and instructions the chat never shows.
                slug = decision["slug"]
                T.escalate(project, slug, "Use the bounded validation scope, without [PR #12](https://example.test/pull/12)? "
                           "It covers the saved migration paths; the wider scope can follow in the next patch.")
                prompt = T.block_question(S.load_task(project, slug))
                turn(prompt, "Asked for the validation scope; the owner keeps the bounded plan meanwhile.",
                     "queued-notices-handled", trigger="block", slug=slug)
                turn("Anything else waiting?", "Only the validation scope question.", "queued-notices-chat")
                with S.project_lock(project):
                    l3.queue_locked(project, prompt, trigger="block", slug=slug)
                    l3.queue_locked(project, server.report_fields(completed["slug"], {"verdict": "incomplete"}),
                                    trigger="report-landed")
                    l3.queue_locked(project, f"System fault [worker:{running['slug']}] in {project}/{running['slug']}: "
                                    "fixture worker stopped\n\nRead the incident and recover the task.", trigger="incident")
                return self._json({"slug": slug, "prompt": prompt})
            return super().do_POST()

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
