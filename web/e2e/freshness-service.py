"""Real change stream, storage and chat API with a held fixture L3 answer and a controllable stream outage."""
import os
import socket
import threading

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from altitude import config, engines, l3, server, state as S, tasks as T


def main():
    configure()
    answer = threading.Event()

    def held_turn(_prompt, *, on_text=None, on_start=None, **_options):
        on_start and on_start(os.getpid())
        on_text and on_text("Checking the saved migration notes.")
        assert answer.wait(60), "Fixture answer was not released"
        return {"text": "The saved migration notes are current.", "session_id": "fixture-project-session"}

    engines.claude_print = held_turn
    repos = {}
    for project, folder in (("atlas", "atlas"), ("beacon", "second-project/beacon")):  # one origin.git per parent
        repos[project] = make_repo(config.PROJECT_ROOTS[0] / folder)
        with config.add_project(project, path=repos[project]):
            pass
    l3.save_info("atlas", {"session_id": "fixture-project-session", "engine_last": config.ENGINES[0],
                           "last_turn": S.now(), "turns": 1,
                           "sessions": {config.ENGINES[0]: {"session_id": "fixture-project-session", "turns": 1,
                                        "confinement_version": l3.L3_CONFINEMENT_VERSION}}})

    def running(project, title):
        slug = T.new(project, title, "Fictional freshness work, never sent to a live provider.")["slug"]
        worktree = add_worktree(repos[project], slug)
        T.dispatch(project, slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        return slug

    migration = running("atlas", "Prepare index migration")
    running("atlas", "Check backfill recovery")
    streams = set()
    refused = threading.Event()

    class Handler(server.Handler):
        def do_GET(self):
            if self.path != "/api/changes":
                return super().do_GET()
            if refused.is_set():
                return self._json({"error": "Altitude is restarting."}, 503)
            streams.add(self)
            try:
                return super().do_GET()
            finally:
                streams.discard(self)

        def do_POST(self):
            if self.path == "/fixture/decision":
                title = self._body()["title"]
                slug = running("beacon", title)
                T.block("beacon", slug, f"{title}?", actor="l2", questions={"questions": [{"question": f"{title}?"}]})
                T.escalate("beacon", slug, f"{title}?", questions={"questions": [{"question": f"{title}?"}]})
                return self._json({"slug": slug})
            if self.path == "/fixture/transition":
                row = S.load_task("atlas", migration)
                row["state"] = "reported"
                S.save_task("atlas", row)
                S.write_json(S.task_dir("atlas", migration) / "report.json", {
                    "landed": {"prs": [], "main_runs": []}, "deploy": {"status": "not-applicable"}, "review": []})
                return self._json(T.done("atlas", migration, digest="Fictional completed migration."))
            if self.path == "/fixture/outage":
                refused.set()
                for stream in list(streams):
                    stream.connection.shutdown(socket.SHUT_RDWR)
                return self._json({"dropped": len(streams)})
            if self.path == "/fixture/recover":
                refused.clear()
                return self._json({"ok": True})
            if self.path == "/fixture/answer":
                answer.set()
                return self._json({"ok": True})
            if self.path == "/fixture/streams":
                return self._json({"open": len(streams)})
            return super().do_POST()

    serve(Handler, release=answer.set)


if __name__ == "__main__":
    main()
