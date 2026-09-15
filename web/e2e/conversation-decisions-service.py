"""Real decision API/storage with a deliberately stepped, deterministic L2 engine."""
from pathlib import Path
from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T


RETENTION = {
    "question": "How long should we keep the old index?",
    "options": [
        {"key": "seven", "label": "7 days", "text": "Keep the old index for seven days."},
        {"key": "fourteen", "label": "14 days", "text": "Keep the old index for fourteen days."},
        {"key": "thirty", "label": "30 days", "text": "Keep the old index for thirty days."},
    ],
    "recommended_key": "seven", "why": "One week covers the rollout.",
}
REGION = {
    "question": "Which backup region should we use?",
    "options": [
        {"key": "west", "label": "West", "text": "Use the west region for backups."},
        {"key": "east", "label": "East", "text": "Use the east region for backups."},
    ],
    "recommended_key": "west", "why": "West is nearest to the service.",
}
OWNER = {"question": "Who should receive the rollout report?"}
REVIEW = {
    "question": "Merge the reviewed rollout?",
    "options": [{"key": "merge", "label": "Merge rollout", "text": "Merge the reviewed rollout."}],
    "recommended_key": "merge", "why": "The presented rollout has passed its checks and review.",
}
WITHDRAWAL = "I withdrew the merge question while I assess the requested audit. Your rollback choice remains useful."


class ConversationOwner(FakeL2):
    """Only the provider's semantic response is scripted; question writes use production verbs."""

    def checkpoint(self, slug):
        T.take_inbox("atlas", slug)
        task = S.load_task("atlas", slug)
        message = next(row for row in reversed(T.task_messages("atlas", slug))
                       if row["role"] == T.OPERATOR_MESSAGE_ROLE)
        text = message["text"]
        submitted = [q for q in T.question_group_view("atlas", task)["questions"]
                     if q["status"] == "open" and q.get("response")]
        if submitted:
            # This explicit fixture checkpoint models interpretation of known quick answers.
            # HTTP submission alone never invokes resolution or grants authorization.
            for question in submitted:
                response = question["response"]
                assert response["text"] in {o["text"] for o in question["options"]}
                T.resolve_question("atlas", slug, question["id"], question["revision"],
                                   response["message_id"], disposition="answered", reason=response["text"],
                                   expected_attempt=task["attempt"])
            answer = "The submitted choices are recorded. I will continue within that direction."
        elif slug == "review-revised-rollout":
            if text == "What does the rollback choice cover?":
                answer = "Only the rollback window. The reviewed rollout and merge choice are unchanged."
            elif text == "Audit the rollout wording before merge.":
                question = next(q for q in reversed(task["questions"]) if q["status"] == "open" and q["question"].startswith("Merge"))
                T.resolve_question("atlas", slug, question["id"], question["revision"], None,
                                   disposition="withdrawn", reason=WITHDRAWAL, expected_attempt=task["attempt"])
                Path(task["worktree"], "rollout-notes.md").write_text("Requested wording audit remains required.\n")
                answer = WITHDRAWAL
            else:
                raise AssertionError(f"No deterministic freshness response for {text!r}")
        elif slug == "rollout-decisions" and text != "Could we roll back after day seven?":
            # The fixture model interprets the sentence, then invokes the same cited-message verb
            # as a real owner. One source can answer several independent questions.
            cases = {
                "Keep 14 days; use snapshots so the backup region no longer matters.": {
                    RETENTION["question"]: ("answered", "Keep the old index for fourteen days."),
                    REGION["question"]: ("superseded", "Snapshots make the backup region unnecessary."),
                },
                "Keep 14 days, use West, and send the report to the release team.": {
                    RETENTION["question"]: ("answered", "Keep the old index for fourteen days."),
                    REGION["question"]: ("answered", "Use the west region for backups."),
                    OWNER["question"]: ("answered", "Send the rollout report to the release team."),
                },
                "Release team": {
                    OWNER["question"]: ("answered", "Send the rollout report to the release team."),
                },
            }
            if text not in cases:
                raise AssertionError(f"No deterministic group response for {text!r}")
            for member in T.question_group_view("atlas", task)["questions"]:
                if member["question"] not in cases[text]:
                    continue
                disposition, reason = cases[text][member["question"]]
                T.resolve_question("atlas", slug, member["id"], member["revision"], message["id"],
                                   disposition=disposition, reason=reason, expected_attempt=task["attempt"])
            answer = ("Fourteen days is recorded. Snapshots remove the region choice. Who should receive the rollout report?"
                      if "snapshots" in text else "The answers are recorded. I will continue with the rollout.")
        elif text == "Could we roll back after day seven?":
            answer = "After day seven we would rebuild the old index. Fourteen days keeps instant rollback available longer."
        elif text == "Maybe two weeks, but I am unsure about cost.":
            answer = "Fourteen days doubles the temporary storage. Should I use fourteen days, or keep seven?"
        else:
            question = next(q for q in reversed(task["questions"]) if q["status"] == "open")
            cases = {
                "14 days": ("answered", "Keep the old index for fourteen days.", None),
                "Use snapshots instead; the old index is no longer needed.":
                    ("superseded", "Use snapshots instead; old-index retention is no longer relevant.", None),
                "Keep it for fourteen days. I still need to choose the backup region.":
                    ("answered", "Keep the old index for fourteen days.", "Which backup region should we use?"),
                "Use the west region.": ("answered", "Use the west region for backups.", None),
            }
            if text not in cases:
                raise AssertionError(f"No deterministic L2 response for {text!r}")
            disposition, reason, remaining = cases[text]
            T.resolve_question("atlas", slug, question["id"], question["revision"], message["id"],
                               disposition=disposition, reason=reason, remaining=remaining,
                               expected_attempt=task["attempt"])
            answer = reason + (" The backup region is still open." if remaining else " I will continue with that direction.")
        T.message("atlas", slug, "l2", answer)
        return {"message_id": message["id"], "answer": answer}


def main():
    configure()
    fake = ConversationOwner()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass

    def task(title, question, *, questions=None, paths=None):
        row = T.new("atlas", title, "Fictional index migration for deterministic browser verification.", paths=paths)
        slug = row["slug"]
        worktree = add_worktree(repo, slug)
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.message("atlas", slug, "l2", "The new index passes the fixture checks. Keeping the old one preserves instant rollback.")
        T.block("atlas", slug, question if questions else "The brief leaves the retention choice open.",
                actor="l2", updates={"waiting_on": "l3"}, questions=questions)
        T.escalate("atlas", slug, question, questions=questions)
        T.message("atlas", slug, "l3", "The brief sets no retention limit; the operator should choose the rollback window.", wake_blocked=False)
        # Plenty of later human and technical output makes last-event anchoring visibly wrong.
        for index in range(9):
            T.message("atlas", slug, "l2", f"Read-only verification {index + 1}: fixture migration remains healthy.")
        S.append_event("atlas", slug, "fixture-evidence", summary="A later technical checkpoint is not the unresolved question.")
        return slug

    task("Index rollout", RETENTION["question"], questions={"questions": [RETENTION]})
    task("Retention and backup", "How long should we keep the old index, and which backup region should we use?")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/workers":
                return self._json({"calls": [{key: row.get(key) for key in ("engine", "session_id", "prompt")}
                                             for row in fake.calls], "workers": fake.workers,
                                   "pending": {row["slug"]: T.pending("atlas", row["slug"])
                                               for row in S.list_tasks("atlas")}})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/freshness":
                slug = task("Review revised rollout", "Review the rollout and choose its rollback window.",
                            questions={"questions": [REVIEW, RETENTION]})
                T.set_hold_merge("atlas", slug, "Operator review of the completed rollout")
                return self._json({"slug": slug})
            if self.path == "/fixture/freshness-ready":
                body = self._body()
                slug = body["slug"]
                row = S.load_task("atlas", slug)
                notes = Path(row["worktree"], "rollout-notes.md")
                assert notes.read_text() == "Requested wording audit remains required.\n"
                changed = body.get("changed", False)
                notes.write_text("Wording revised; requested audit and fixture checks complete.\n" if changed else
                                 "Requested audit complete; the reviewed wording remains valid.\n")
                T.message("atlas", slug, "l2", notes.read_text().strip())
                question = {**REVIEW, "question": "Merge the revised rollout?"} if changed else REVIEW
                T.block("atlas", slug, "The rollout is ready for your decision.", actor="l2",
                        updates={"waiting_on": "burak"}, questions={"questions": [question]})
                return self._json({"notes": notes.read_text()})
            if self.path == "/fixture/freshness-work":
                row = S.load_task("atlas", self._body()["slug"])
                return self._json({"notes": Path(row["worktree"], "rollout-notes.md").read_text()})
            if self.path == "/fixture/long-context":
                detail = ("Should we keep the old index for fourteen days? "
                          "Option A: Keep fourteen days. Option B: Keep seven days. "
                          "I recommend A.\n\n"
                          "Migration evidence: the candidate index contains every fixture record and all validation checks pass.\n\n"
                          "Rollback procedure: retain the old alias until the agreed window expires, then remove it in the existing cleanup job.\n\n"
                          "Storage review: keeping both index generations uses twice the temporary disk space. Capacity is reserved for the full window.\n\n"
                          "Recovery rehearsal: restoring the previous alias preserves reads; writes continue through the current ingestion path.\n\n"
                          "Final verification: cleanup waits for the last retained backup and records its result in the task conversation.")
                slug = task("Choose retention window", detail)
                T.escalate("atlas", slug, detail, recommendation="Keep the old index for fourteen days.",
                           recommendation_label="Keep 14 days", recommendation_why="Longer instant rollback uses twice the temporary storage.")
                return self._json({"slug": slug, "detail": detail})
            if self.path == "/fixture/second-project":
                other_repo = make_repo(config.PROJECT_ROOTS[0] / "second-project" / "beacon")
                assert git("remote", "get-url", "origin", cwd=other_repo) != git("remote", "get-url", "origin", cwd=repo)
                with config.add_project("beacon", path=other_repo):
                    pass
                row = T.new("beacon", "Choose backup retention", "Fictional second-project question.")
                slug = row["slug"]
                worktree = add_worktree(other_repo, slug)
                T.dispatch("beacon", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                           worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
                T.block("beacon", slug, RETENTION["question"], actor="l2", updates={"waiting_on": "l3"},
                        questions={"questions": [RETENTION]})
                T.escalate("beacon", slug, RETENTION["question"], questions={"questions": [RETENTION]})
                return self._json({"slug": slug})
            if self.path == "/fixture/delegated-questions":
                slug = task("Lease and policy", "Two independent implementation questions.", paths=["tests/"],
                            questions={"questions": [{"question": "May I edit the tests?"},
                                                     {"question": "May we change the security policy?"}]})
                T.set_hold_merge("atlas", slug, "Operator security review")
                return self._json({"slug": slug})
            if self.path in ("/fixture/l3-followup", "/fixture/l3-settle"):
                slug = self._body()["slug"]
                settle = self.path.endswith("settle")
                message = T.message("atlas", slug, "l3", "The recorded lease includes tests/; test edits are authorized."
                                    if settle else "I recommend checking the lease before asking for more scope.",
                                    wake_blocked=False)
                if settle:
                    row = S.load_task("atlas", slug)
                    member = T.question_group_view("atlas", row)["questions"][0]
                    assert row["paths"] == ["tests/"]
                    T.resolve_question("atlas", slug, member["id"], member["revision"], message["id"],
                                       disposition="answered", reason="The existing lease authorizes test edits.",
                                       expected_attempt=row["attempt"],
                                       l3_authority="Recorded task lease includes tests/. L3 clarifies existing scope; no operator policy decision is settled.")
                return self._json({"message_id": message["id"]})
            if self.path == "/fixture/group":
                slug = task("Rollout decisions", "Three choices for the rollout.",
                            questions={"questions": [RETENTION, REGION, OWNER]})
                return self._json({"slug": slug})
            if self.path == "/fixture/checkpoint":
                return self._json(fake.checkpoint(self._body()["slug"]))
            if self.path == "/fixture/revise":
                slug = self._body()["slug"]
                T.escalate("atlas", slug, "Should we retain the old index for fourteen days?",
                           recommendation="Keep the old index for fourteen days.", recommendation_label="Use 14 days & resume")
                return self._json({"ok": True})
            if self.path == "/fixture/revise-group":
                slug = self._body()["slug"]
                group = T.question_group_view("atlas", S.load_task("atlas", slug))
                region = next(q for q in group["questions"] if q["question"] == REGION["question"])
                T.escalate("atlas", slug, "The region recommendation changed after a fixture latency check.",
                           questions={"questions": [{**REGION, "id": region["id"], "recommended_key": "east",
                                                     "why": "The new latency check favors East."}]})
                return self._json({"ok": True})
            if self.path == "/fixture/requeue":
                slug = self._body()["slug"]
                T.requeue("atlas", slug, clear_worker=True,
                          reason="The fixture provider capacity requires a fresh attempt.")
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
