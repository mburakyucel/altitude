"""Real question, review and delivery records for the decision selections walkthrough; only the L2 engine is a fixture."""
import time

from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T, verify


QUESTIONS = [
    {"question": "How long should we keep the old index?", "options": [
        {"key": "seven", "label": "7 days", "text": "Keep the old index for seven days."},
        {"key": "fourteen", "label": "14 days", "text": "Keep the old index for fourteen days."}],
     "recommended_key": "seven", "why": "One week covers the rollout."},
    {"question": "Where should the backup live?", "options": [
        {"key": "west", "label": "West", "text": "Use the west region."},
        {"key": "east", "label": "East", "text": "Use the east region."}],
     "recommended_key": "west", "why": "Keeps restores close to the service."},
]
HEAD = "5f0c2e94b1d7a8c3e6f2019d4b7a5c8e1f3d6b20"   # fictional delivery head


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    # The reported review task continues only while its fictional PR is open; no GitHub call is made.
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass

    def running(title):
        slug = T.new("atlas", title, "Fictional work for the decision selections walkthrough, never sent to a provider.")["slug"]
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(add_worktree(repo, slug)), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        return slug

    rollout = running("Index rollout")
    T.message("atlas", rollout, "l2", "The new index passes the fixture checks; two settings are your call.")
    T.block("atlas", rollout, "Two rollout settings.", actor="l2",
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": QUESTIONS})

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

    warmup = running("Cache warmup")
    taken = []
    # An approval counts only in a later second than the hold; serving from the next second keeps a fast run honest.
    time.sleep(1 - time.time() % 1)

    def settled(slug):
        with server._bg_guard:
            resumed = server._bg.get(f"resume:atlas:{slug}")
        if resumed:
            resumed.join(10)
            assert not resumed.is_alive(), "The fixture resume did not finish"

    class Handler(server.Handler):
        # Fixture controls answer PUT alone, so they never pass through the application's own routes.
        def do_PUT(self):
            if self.path == "/fixture/record":
                # The owner reads the answers and records each one as decided, citing the message that carried it.
                settled(rollout)
                task = S.load_task("atlas", rollout)
                for question in T.question_group_view("atlas", task)["questions"]:
                    response = question["response"]
                    T.resolve_question("atlas", rollout, question["id"], question["revision"], response["message_id"],
                                       disposition="answered", reason=response["text"], expected_attempt=1)
                return self._json({"ok": True})
            if self.path == "/fixture/take":
                # The running owner takes its inbox; no receipt says the session read it yet.
                taken.extend(row["id"] for row in T.take_inbox("atlas", warmup))
                return self._json({"taken": taken})
            if self.path == "/fixture/receipt":
                # The session's receipt arrives for every message it took.
                with S.project_lock("atlas"):
                    task = S.load_task("atlas", warmup)
                    for message_id in taken:
                        task.setdefault("message_deliveries", {})[message_id] = {"at": S.now()}
                    S.save_task("atlas", task)
                return self._json({"ok": True})
            return self._json({"error": "unknown fixture"}, 404)

    serve(Handler)


if __name__ == "__main__":
    main()
