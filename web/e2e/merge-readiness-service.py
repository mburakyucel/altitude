"""Issue #902: real readiness/API storage with fictional PRs and engine execution."""
import time
from datetime import datetime, timezone

from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T, verify


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=repo)
    with config.add_project("atlas", path=repo):
        pass

    def held(title, number, finding=False):
        slug = T.new("atlas", title, "Fictional readiness journey; no provider call.")["slug"]
        worktree = add_worktree(repo, slug)
        T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
        T.set_hold_merge("atlas", slug, "Operator reviews the finished feature")
        task = S.load_task("atlas", slug)
        head = git("rev-parse", "HEAD", cwd=worktree).strip()
        at = S.now()
        identity = {"head": head, "base": head, "context_hash": "2" * 64, "tree": "1" * 40}
        review_id = f"{slug}-review"
        task.update(prs=[number], delivery={"number": number, "head": head, "at": at}, reviews=[{
            "id": review_id, "subject": "changes", "state": "completed", "requested_by": "l2",
            "requested_at": at, "engine_label": "Independent reviewer", "same_engine": True,
            "snapshot": identity, "reconciled": {**identity, "at": at, "reason": "Assessed the fictional API finding."},
            "dispositions": [{"finding_id": "F1", "disposition": "open" if finding else "fixed",
                              "reason": "The shared API is pending." if finding else "The shared API is verified."}],
            "result": {"text": "The shared API determines readiness.", "findings": [{"id": "F1",
                       "severity": "medium", "title": "Shared API remains pending", "body": "Verify the shared API."}],
                       "limitations": []},
            "message": {"id": review_id, "at": at, "role": "system", "by": "l2", "review_id": review_id,
                        "text": "Independent review completed."}}])
        S.save_task("atlas", task)
        S.write_json(S.task_dir("atlas", slug) / "report.json", {
            "landed": {"prs": [{"number": number, "merged": False}], "main_runs": [], "deploy": "not-applicable"},
            "review": [], "blocked": ""})
        return task

    blocked = held("Unfinished feature", 42, finding=True)
    T.block("atlas", blocked["slug"], "Waiting for the shared API delivery.", actor="l2", updates={"waiting_on": "l3"})
    reported = held("Unfinished report", 43, finding=True)
    T.report("atlas", reported["slug"], {"verdict": "ok", "owner": T.report_owner(reported),
             "delivery": reported["delivery"], "prs": [43]})
    independent = held("Independent choice", 44, finding=True)
    T.block("atlas", independent["slug"], "Waiting for the shared API delivery.", actor="l2", updates={"waiting_on": "l3"})
    T.escalate("atlas", independent["slug"], "Which preview region?", questions={"questions": [{
        "question": "Which preview region?", "review": "none", "options": [
            {"key": "west", "label": "West", "text": "Use the west preview region."},
            {"key": "east", "label": "East", "text": "Use the east preview region."}], "recommended_key": "west"}]})
    ready = held("Ready feature", 45)
    T.report("atlas", ready["slug"], {"verdict": "ok", "owner": T.report_owner(ready),
             "delivery": ready["delivery"], "prs": [45]})
    answered = held("Answered feature", 46)
    rebuilding = held("Rebuilding feature", 47)
    time.sleep(1 - time.time() % 1)  # approvals and answers follow the whole-second hold evidence

    def answer_merge(task, number):
        slug = task["slug"]
        T.block("atlas", slug, "Merge the feature?", actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
                questions={"questions": [{"question": f"Merge the feature (PR #{number})?", "review": "changes",
                    "recommended_key": "approve", "options": [
                        {"key": "approve", "label": "Approve merge", "text": "Merge the feature as shown."},
                        {"key": "changes", "label": "Request changes", "text": "Hold the feature for changes."}]}]})
        merge = S.load_task("atlas", slug)["questions"][-1]
        row = T.message("atlas", slug, T.OPERATOR_MESSAGE_ROLE, "Its menu and sheet feel off.",
                        question_id=merge["id"], revision=merge["revision"])
        T.resume("atlas", slug)
        T.take_inbox("atlas", slug)
        task = S.load_task("atlas", slug)  # the fictional owner received the answer
        task.setdefault("message_deliveries", {})[row["id"]] = {"state": "delivered", "at": S.now()}
        S.save_task("atlas", task)
        T.resolve_question("atlas", slug, merge["id"], merge["revision"], row["id"], disposition="answered",
                           reason="The operator turned the menu and sheet down.", expected_attempt=1)

    # Reported 2026-10-25: the owner's next question about the turned-down PR, marked "none".
    answer_merge(answered, 46)
    T.block("atlas", answered["slug"], "How should the feature start?", actor="l2",
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": [{
                "question": "How should the feature start?", "review": "none", "recommended_key": "chat", "options": [
                    {"key": "chat", "label": "Suggest in chat", "text": "Offer it in the conversation."},
                    {"key": "menu", "label": "Keep the menu", "text": "Keep the menu item."}]}]})
    # The owner stops without asking again at the same head.
    answer_merge(rebuilding, 47)
    rebuilt = S.load_task("atlas", rebuilding["slug"])
    T.report("atlas", rebuilt["slug"], {"verdict": "ok", "owner": T.report_owner(rebuilt),
             "delivery": rebuilt["delivery"], "prs": [47]})

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/block-ready":
                task = S.load_task("atlas", ready["slug"])
                task["state"] = "running"
                task["reviews"][0]["dispositions"][0]["disposition"] = "open"
                S.save_task("atlas", task)
                T.block("atlas", ready["slug"], "Waiting for the shared API delivery.", actor="l2",
                        updates={"waiting_on": "l3"})
                return self._json({"ok": True})
            if self.path == "/fixture/clear-ready":
                task = S.load_task("atlas", ready["slug"])
                for question in task.get("questions", []):
                    if question["status"] == "open":
                        T.resolve_question("atlas", ready["slug"], question["id"], question["revision"], None,
                            disposition="withdrawn", reason="The fictional API prerequisite is satisfied.", expected_attempt=1)
                task = S.load_task("atlas", ready["slug"])
                task["reviews"][0]["dispositions"][0]["disposition"] = "fixed"
                S.save_task("atlas", task)
                return self._json({"ok": True})
            if self.path == "/fixture/rebuild":
                task = S.load_task("atlas", rebuilding["slug"])
                task["delivery"] = {**task["delivery"], "head": "b" * 40, "at": datetime.now(timezone.utc).isoformat()}
                S.save_task("atlas", task)
                S.append_event("atlas", task["slug"], "delivery", **task["delivery"])
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
