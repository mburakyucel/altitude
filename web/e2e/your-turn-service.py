"""Real turn, review and label storage for the "your turn" walkthrough; only the L2 engine is a fixture."""
import time

from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, server, state as S, tasks as T, verify


RETENTION = {
    "question": "How long should we keep the old index?",
    "options": [
        {"key": "seven", "label": "7 days", "text": "Keep the old index for seven days."},
        {"key": "fourteen", "label": "14 days", "text": "Keep the old index for fourteen days."},
    ],
    "recommended_key": "seven", "why": "One week covers the rollout.",
}
HEAD = "5f0c2e94b1d7a8c3e6f2019d4b7a5c8e1f3d6b20"   # fictional delivery head
INTEGRATED = "8a41c07d2e9b5f3a6c1d0e8b7f4a2c9d5e3b1a06"   # the same content integrated onto current main


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    # The reported review task continues only while its fictional PR is open; no GitHub call is made.
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    # A fictional GitHub origin gives the review its View PR link; nothing in these flows fetches it.
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass

    def running(title):
        slug = T.new("atlas", title, "Fictional work for the your-turn walkthrough, never sent to a provider.")["slug"]
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        return slug

    question = running("Index rollout")
    T.message("atlas", question, "l2", "The new index passes the fixture checks.")
    for index in range(14):
        T.message("atlas", question, "l2", f"Read-only verification {index + 1}: the fixture migration remains healthy.")
    T.message("atlas", question, "l2", "Keeping the old index preserves instant rollback; the window is your call.")
    T.block("atlas", question, RETENTION["question"], actor="l2",
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": [RETENTION]})

    review = running("Release notes")
    T.message("atlas", review, "l2", "The release notes are delivered and checked; the PR waits for your review.")
    T.set_hold_merge("atlas", review, "Operator review of the published release notes")
    row = S.load_task("atlas", review)
    row.update(delivery={"number": 42, "head": HEAD, "at": S.now()}, prs=[42])
    S.save_task("atlas", row)
    S.write_json(S.task_dir("atlas", review) / "report.json", {
        "landed": {"prs": [{"number": 42, "merged": False}], "main_runs": [], "deploy": "not-applicable"},
        "review": [], "blocked": ""})
    T.report("atlas", review, {"verdict": "ok", "delivery": row["delivery"], "owner": T.report_owner(row), "prs": [42]})

    running("Cache warmup")
    replying = running("Retry policy")
    T.message("atlas", replying, T.OPERATOR_MESSAGE_ROLE, "Keep three retries for the fixture client.")
    fault = running("Repair checkout")
    T.block("atlas", fault, "Checkout unavailable.", actor="altd", updates={"fault": "checkout", "waiting_on": "l3"})
    dispatch.stop("atlas", running("Stopped validation"), by=T.OPERATOR_MESSAGE_ROLE)
    # Records keep whole seconds and an approval counts only in a later second than the hold, which an
    # operator never beats; serving from the next second keeps a fast walkthrough from beating it either.
    time.sleep(1 - time.time() % 1)

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/park":
                # The owner reads the reply and parks its saved group again; open questions return as asked again.
                with server._bg_guard:
                    resumed = server._bg.get(f"resume:atlas:{question}")
                if resumed:
                    resumed.join(10)
                    assert not resumed.is_alive(), "The fixture resume did not finish"
                task = S.load_task("atlas", question)
                if task["state"] == "blocked":
                    T.resume("atlas", question)
                T.block("atlas", question, T._groups(task)[-1]["reason"], actor="l2", expected_attempt=1)
                return self._json(T.question_group_view("atlas", S.load_task("atlas", question)))
            if self.path == "/fixture/integrate":
                # After approval the owner integrates current main (a new head, same content) and waits on L3.
                with server._bg_guard:
                    resumed = server._bg.get(f"resume:atlas:{review}")
                if resumed:
                    resumed.join(10)
                    assert not resumed.is_alive(), "The fixture resume did not finish"
                task = S.load_task("atlas", review)
                if task["state"] in ("blocked", "reported"):
                    T.resume("atlas", review)
                T.take_inbox("atlas", review)
                task = S.load_task("atlas", review)
                task["delivery"] = {**task["delivery"], "head": INTEGRATED, "at": S.now()}
                S.save_task("atlas", task)
                T.block("atlas", review, "Waiting for the release checkout to recover.", actor="l2",
                        updates={"waiting_on": "l3"})
                return self._json({"head": INTEGRATED})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
