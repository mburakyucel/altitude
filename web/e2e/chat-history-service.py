"""Real chat history, stranded-report retry and routing with a fixture L3 that is unavailable until recovered."""
from datetime import datetime, timedelta, timezone

from service_support import configure, serve
from tests.support import make_repo
from altitude import config, engines, l3, route, server, state as S


def main():
    configure()
    project = "atlas"
    with config.add_project(project, path=make_repo(config.PROJECT_ROOTS[0] / project)):
        pass
    l3.save_info(project, {"session_id": "fixture-session", "engine_last": config.ENGINES[0],
                           "last_turn": S.now(), "turns": 1,
                           "sessions": {config.ENGINES[0]: {"session_id": "fixture-session", "turns": 1,
                                        "confinement_version": l3.L3_CONFINEMENT_VERSION}}})

    def answer(_prompt, **_options):
        return {"text": "Fictional report reviewed; nothing waits on you.", "session_id": "fixture-session",
                "reported_session_id": "fixture-session", "usage": {}, "error": None, "tools": []}

    engines.claude_print = engines.codex_exec = answer
    for index, (question, reply) in enumerate((("Is the fictional backup plan ready?", "Yes, it is written down."),
                                               ("Which fictional mirror goes first?", "The north mirror."))):
        l3.chat_log(project, "user", question, trigger="chat", turn_id=f"human-{index}")
        l3.chat_log(project, "assistant", reply, trigger="chat", turn_id=f"human-{index}")
    for index in range(150):  # an earlier burst of failed report deliveries
        l3.chat_log(project, "user", "Report landed for fictional-mirror.", trigger="report-landed",
                    turn_id=f"burst-{index}", slug="fictional-mirror")
        l3.chat_log(project, "error", "engine hold: fixture allowance exhausted", trigger="report-landed",
                    turn_id=f"burst-{index}", slug="fictional-mirror")
    slug = "fictional-mirror"
    S.task_dir(project, slug).mkdir(parents=True, exist_ok=True)
    S.write_json(S.task_dir(project, slug) / "report.json", {
        "landed": {"prs": []}, "blocked": "Needs a fictional mirror choice", "fyi": [], "decisions": [],
        "follow_ups": [], "review": []})
    S.save_task(project, {"slug": slug, "title": "Fictional mirror", "state": "blocked", "created": S.now(),
                          "updated": S.now(), "attempt": 1, "l3_handled": None,
                          "blocked_reason": "Needs a fictional mirror choice",
                          "verified": {"verdict": "blocked", "attempt": 1, "problems": [], "signals": [],
                                       "spend": {}, "prs": [], "report": {"blocked": "Needs a fictional mirror choice"}}})

    def availability(delta):
        route.note_rejection({"engine": config.ENGINES[0]}, {
            "scope": "engine", "why": "fixture allowance exhausted",
            "until": (datetime.now(timezone.utc) + delta).isoformat()})

    availability(timedelta(hours=1))

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/ticks":
                for _ in range(20):
                    server.resume_stranded_reports(project)
                    with server._bg_guard:
                        workers = list(server._bg.values())
                    for worker in workers:
                        worker.join(10)
                return self._json({"rows": len(l3.chat_history(project, None)),
                                   "handled": S.load_task(project, slug).get("l3_handled")})
            if self.path == "/fixture/recover":
                availability(timedelta(hours=-1))
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
