"""Issue #902: real readiness/API storage with fictional PRs and engine execution."""
import time

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
    time.sleep(1 - time.time() % 1)  # approvals follow the whole-second hold evidence

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
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
