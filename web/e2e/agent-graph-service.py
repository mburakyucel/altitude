"""Work's graph over real task storage: running, waiting, planned, faulted and queued work; workers stay at the engine seam."""
from datetime import datetime, timedelta, timezone

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, l3, server, state as S, tasks as T

QUESTION = {
    "question": "Should a stale session cookie sign the user out?",
    "options": [
        {"key": "out", "label": "Sign out", "text": "Sign the user out and show the sign-in page."},
        {"key": "renew", "label": "Renew", "text": "Renew the session silently."},
    ],
    "recommended_key": "out", "why": "A stale cookie is the loop's trigger.",
}


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    project = "harbor"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    l3.save_info(project, {"session_id": "fixture-project-session", "engine_last": config.ENGINES[0], "last_turn": S.now(),
                          "turns": 1, "sessions": {config.ENGINES[0]: {"session_id": "fixture-project-session", "turns": 1,
                                                                      "confinement_version": l3.L3_CONFINEMENT_VERSION}}})

    def running(title):
        slug = T.new(project, title, "Fictional work for the graph walkthrough, never sent to a provider.")["slug"]
        T.dispatch(project, slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        return slug

    sync = running("Add offline sync for the field app")
    login = running("Fix the login redirect loop")
    T.block(project, login, QUESTION["question"], actor="l2",
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": [QUESTION]})
    export = running("Rework the export pipeline")
    T.new(project, "Ship the export dashboard", "Fictional work after the pipeline rework.", after=export)
    payment = running("Upgrade the payment SDK")
    T.block(project, payment, "Checkout unavailable.", actor="altd", updates={"fault": "checkout", "waiting_on": "l3"})
    T.new(project, "Update the onboarding copy", "Fictional queued work.")

    def stamp(slug, helpers, *, age=0):
        """The usage reading the service's collector writes: the worker's newest request and its helpers'."""
        now = datetime.now(timezone.utc)
        at = lambda seconds: (now - timedelta(seconds=seconds)).isoformat(timespec="seconds")
        owner = f"fixture-{slug}"
        rows = [{"engine": config.ENGINES[0], "session_id": f"{owner}-helper-{n}", "parent_session_id": owner,
                 "owner_session_id": owner, "role": "delegated", "parentage": "thread", "depth": 1, "attempt": 1,
                 "status": "observed", "total_tokens": 100, "observed_at": at(seconds)} for n, seconds in enumerate(helpers)]
        row = S.load_task(project, slug)
        row["token_usage"] = {
            "status": "partial", "total_tokens": 1000, "checked_at": at(age), "observed_at": at(5), "notes": [],
            "sessions": [{"engine": config.ENGINES[0], "session_id": owner, "role": "owner", "attempt": 1,
                          "status": "observed", "total_tokens": 900, "observed_at": at(5)}],
            "helpers": {"status": "partial", "observed_count": len(rows), "direct_count": len(rows), "descendant_count": 0,
                        "unclassified_count": 0, "total_tokens": 100 * len(rows), "sessions": rows}}
        S.save_task(project, row)

    class Handler(server.Handler):
        def do_POST(self):
            if self.path != "/fixture/activity":
                return super().do_POST()
            age = self._body().get("age", 0)
            # Two helpers made a request in the last minute and one ten minutes ago; one helper on the export rework.
            stamp(sync, [8, 20, 600], age=age)
            stamp(export, [12], age=age)
            return self._json({"ok": True})

    serve(Handler)


if __name__ == "__main__":
    main()
