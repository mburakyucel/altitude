"""Real review, question and label storage for a held PR that closes without merging (#575)."""
import time

from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T

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
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass
    slug = T.new("atlas", "Runtime port", "Fictional work for the closed-PR walkthrough, never sent to a provider.",
                 hold_merge="Operator review of the runtime port")["slug"]
    T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
               worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    task = S.load_task("atlas", slug)
    task.update(prs=[41, 42], delivery={"number": 42, "head": "5f0c2e94b1d7a8c3e6f2019d4b7a5c8e1f3d6b20",
                                        "at": S.now()})
    S.save_task("atlas", task)
    S.append_event("atlas", slug, "delivery", **task["delivery"])
    T.message("atlas", slug, "l2", "PR #42 is green and held for your review.")
    T.block("atlas", slug, REGION["question"], actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            questions={"questions": [REGION]})
    time.sleep(1 - time.time() % 1)

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/close":
                # What `alt pr close 42` or the owner's next block records after GitHub reports PR #42 closed.
                return self._json({"recorded": T.record_pr_state("atlas", slug, 42, "CLOSED", by="operator")})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
