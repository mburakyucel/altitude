"""Real task, step and notice storage for a recovery command L3 hands the operator for the project."""
import threading
import time
from types import SimpleNamespace

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, l3, server, tasks as T, terminal

COMMAND = "git -C ~/Projects/atlas restore README.md"
STEP = ("Restore the service checkout so merged changes can activate again.\n\n"
        "Altitude stopped updating itself because one file there differs from main. This puts it back; "
        f"nothing else changes.\n\n```run\n{COMMAND}\n```")


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    slugs = {}
    for title, reason in (("VM harness", "The service checkout must be restored before this can activate."),
                          ("Export access", "CI export access is missing.")):
        slug = T.new("atlas", title, "Fictional work for the project-step walkthrough, never sent to a provider.")["slug"]
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.block("atlas", slug, reason, actor="l2", updates={"waiting_on": "l3"})
        slugs[title] = slug
    # What the coordinator's socket runs for `alt project step - --task <slug>`.
    l3.step_verb("atlas", ["-", "--task", slugs["VM harness"]], STEP)
    time.sleep(1 - time.time() % 1)

    class Handler(server.Handler):
        # The fixture's one control reads nothing from the request, so it adds no request source to code scanning.
        def do_PUT(self):
            # The project terminal's notice once the handed command looks finished, through the real notice path.
            notice = SimpleNamespace(cond=threading.Condition(), command={"text": COMMAND, "attempt": None},
                                     place="project", project="atlas", slug=None)
            terminal._notice(notice, "the command you handed the operator looks finished in the project terminal", finished=True)
            deadline = time.monotonic() + 5
            while T.operator_steps("atlas") and time.monotonic() < deadline:
                time.sleep(.05)
            return self._json({"steps": len(T.operator_steps("atlas")),
                               "told": [row["trigger"] for row in l3.queued("atlas")]})

    serve(Handler)


if __name__ == "__main__":
    main()
