"""Question responses use real storage and HTTP, with a deterministic owner at the engine seam."""
from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T


QUESTIONS = [
    {"question": "How long should we keep the old index?", "options": [
        {"key": "seven", "label": "7 days", "text": "Keep the index for seven days."},
        {"key": "fourteen", "label": "14 days", "text": "Keep the index for fourteen days."}],
     "recommended_key": "seven", "why": "Covers the first week of rollback."},
    {"question": "Where should the backup live?", "options": [
        {"key": "west", "label": "West", "text": "Use the west region."},
        {"key": "east", "label": "East", "text": "Use the east region."}],
     "recommended_key": "west", "why": "Keeps restores close to the service."},
    {"question": "Who should receive the rollout report?"},
]


class Owner(FakeL2):
    def checkpoint(self, slug):
        T.take_inbox("atlas", slug)
        task = S.load_task("atlas", slug)
        for question in T.question_group_view("atlas", task)["questions"]:
            response = question.get("response")
            if question["status"] != "open" or not response:
                continue
            if response["text"] == "Why seven or fourteen days?":
                T.message("atlas", slug, "l2", "Seven days covers the first week; fourteen keeps instant rollback longer. Any retention period is possible.")
                T.block("atlas", slug, "What retention period would you prefer?", actor="l2",
                        updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                        questions={"questions": [{**QUESTIONS[0], "id": question["id"],
                                                   "question": "What retention period would you prefer?"}]})
            else:
                assert response["text"] in ("21 days", "Use the east region.", "Use the west region.", "Release team")
                T.resolve_question("atlas", slug, question["id"], question["revision"], response["message_id"],
                                   disposition="answered", reason=response["text"], expected_attempt=1)
                T.message("atlas", slug, "l2", "I will use " + response["text"])


def main():
    configure()
    owner = Owner()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(owner, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    row = T.new("atlas", "Choose rollout settings", "Fictional settings for response acceptance.")
    slug = row["slug"]
    worktree = add_worktree(repo, slug)
    T.dispatch("atlas", slug, attempt=1, session_id="fixture-feedback", agent_id="fixture-feedback",
               worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    T.block("atlas", slug, "Three choices for the rollout.", actor="l2",
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": QUESTIONS})
    T.set_hold_merge("atlas", slug, "Operator review of the rollout")

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/checkpoint":
                owner.checkpoint(slug)
                return self._json({"ok": True})
            if self.path == "/fixture/revise":
                task = S.load_task("atlas", slug)
                question = T.question_group_view("atlas", task)["questions"][0]
                T.escalate("atlas", slug, "Retention needs a new choice.", questions={"questions": [
                            {**QUESTIONS[0], "id": question["id"], "question": "How long after migration should we keep the old index?"}]})
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
