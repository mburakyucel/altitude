"""Real task, question and label storage for a wait on the operator's step beside a coordinator wait."""
import time

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T

STEP = "Run the sign-in command in the task terminal.\n\n```run\nexample-tool sign-in\n```"
REGION = {"question": "Which region should the installer default to?",
          "options": [{"key": "west", "label": "West", "text": "Default the installer to west."},
                      {"key": "east", "label": "East", "text": "Default the installer to east."}],
          "recommended_key": "west", "why": "West is nearest to the fixture users."}


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    slugs = {}
    for title, reason, waiting, questions in (
            ("Sign in", STEP, T.OPERATOR_MESSAGE_ROLE, None),
            ("Export access", "CI export access is missing.", "l3", None),
            ("Installer region", REGION["question"], T.OPERATOR_MESSAGE_ROLE, {"questions": [REGION]})):
        slug = T.new("atlas", title, "Fictional work for the operator-step walkthrough, never sent to a provider.")["slug"]
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.block("atlas", slug, reason, actor="l2", updates={"waiting_on": waiting}, questions=questions)
        slugs[title] = slug
    time.sleep(1 - time.time() % 1)

    notices = iter((True, False))

    class Handler(server.Handler):
        # The fixture's one control reads nothing from the request, so it adds no request source to code scanning.
        def do_PUT(self):
            # What the task terminal sends for the handed command: first that it looks finished, a wake for the owner
            # that marks the question; then, handed again, that the terminal ended before it finished.
            finished = next(notices)
            T.notify("atlas", slugs["Sign in"], "Terminal: the command looks finished." if finished else
                     "Terminal: the task terminal ended (closed) before the command finished.", by="terminal",
                     attempt=1, ran="example-tool sign-in", finished=finished)
            return self._json({"resume_after": bool(S.load_task("atlas", slugs["Sign in"]).get("resume_after"))})

    serve(Handler)


if __name__ == "__main__":
    main()
