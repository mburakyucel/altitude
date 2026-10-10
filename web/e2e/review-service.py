"""Real review API, task storage and Git snapshots; only engine calls are fixtures."""
import threading
import uuid

from service_support import configure, serve
from tests.support import add_worktree, git, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, reviews, route, server, state as S, tasks as T


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    row = T.new(project, "Keep pagination stable", "Preserve pagination and expire invalid cursors explicitly.",
                hold_merge="Operator review of the finished feature.")
    slug = row["slug"]
    worktree = add_worktree(repo, slug)
    T.dispatch(project, slug, attempt=1, session_id="fixture-owner", agent_id="fixture-owner",
               worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    (S.task_dir(project, slug) / "brief.md").write_text("Acceptance: expired cursors return an explicit error.\n")
    count = 0

    def commit():
        nonlocal count
        count += 1
        (worktree / "pagination.py").write_text(f"REVISION = {count}\n")
        git("add", "pagination.py", cwd=worktree)
        git("commit", "-q", "-m", "Update pagination", cwd=worktree)

    commit()
    proposal = T.message(project, slug, "l2", "Filter deleted records before applying the page limit. Keep the cursor format.")
    source = T.message(project, slug, T.OPERATOR_MESSAGE_ROLE, "Keep the pagination contract stable.")
    mode = {"available": True, "fail": False, "active": False, "calls": 0}
    gate = threading.Event()
    gate.set()
    choice = {"engine": config.ENGINES[1], "label": "Second engine", "model": "Fixture model",
              "allowance_known": True}
    route.pick_review = lambda *_args, **_kwargs: choice if mode["available"] else {"engine": None, "why": "No reviewer is available."}

    def engine_review(_prompt, **kwargs):
        mode["calls"] += 1
        mode["active"] = True
        try:
            if not kwargs["on_start"]({"unit": "fixture-review", "pid": 12345, "started_ticks": "1"}):
                return {"error": "Cancelled", "termination_confirmed": True}
            if not gate.wait(15):
                return {"error": "Fixture review gate timed out", "termination_confirmed": True}
            if mode["fail"]:
                return {"error": "The review engine exited before returning findings.", "termination_confirmed": True}
            return {"text": "Expired cursors restart pagination instead of returning the agreed error.", "findings": [{"id": "expiry", "severity": "high",
                    "title": "Expired cursors restart pagination", "body": "Return the explicit expiration error.",
                    "path": "pagination.py", "line": 1}], "limitations": ["Captured files only; no tests executed."],
                    "termination_confirmed": True, "error": None}
        finally:
            mode["active"] = False

    engines.review = engine_review
    engines.review_active = lambda _worker: mode["active"]

    def stop(_worker):
        gate.set()
        return True

    engines.review_stop = stop

    def latest():
        return S.load_task(project, slug)["reviews"][-1]

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"project": project, "slug": slug, "calls": mode["calls"],
                                   "hold_merge": S.load_task(project, slug).get("hold_merge"),
                                   "pending": T.pending(project, slug), "review": reviews.view(project, slug),
                                   "proposal": proposal, "questions": [q for q in S.load_task(project, slug).get("questions", []) if q["status"] == "open"]})
            return super().do_GET()

        def do_POST(self):
            nonlocal proposal
            if not self.path.startswith("/fixture/"):
                return super().do_POST()
            body = self._body()
            try:
                if self.path == "/fixture/l2-request":
                    result = reviews.request(project, slug, actor="l2", expected_attempt=1,
                                             request_id=uuid.uuid4().hex, focus="Check expiration boundaries.", subject=body.get("subject", "changes"))
                elif self.path == "/fixture/run":
                    if body.get("hold"):
                        gate.clear()
                    result = reviews.run(project, slug, latest()["id"], actor="l2", expected_attempt=1,
                                         context_ids=[source["id"]], proposal_id=proposal["id"] if latest().get("subject") == "proposal" else None)
                elif self.path == "/fixture/release":
                    gate.set()
                    result = {"ok": True}
                elif self.path == "/fixture/assess":
                    T.message(project, slug, "l2", "The review caught an expired-cursor bug. I fixed it and checked the later edits. Your merge hold still applies.")
                    result = reviews.assess(project, slug, latest()["id"], actor="l2", expected_attempt=1,
                        dispositions=[{"finding_id": "expiry", "disposition": "fixed", "reason": "Added an explicit expiration response and regression test."}],
                        reason="Checked the pagination fix and all changes since the captured checkpoint.",
                        proposal_id=proposal["id"] if latest().get("subject") == "proposal" else None)
                elif self.path == "/fixture/proposal-question":
                    result = T.block(project, slug, "Use this pagination approach?", actor="l2", expected_attempt=1,
                                     questions={"questions": [{"question": "Use this pagination approach?"}]})
                elif self.path == "/fixture/revise-proposal":
                    proposal = T.message(project, slug, "l2", "Filter deleted records before applying the page limit. Preserve the last examined record in the cursor.")
                    result = proposal
                elif self.path == "/fixture/approve-proposal":
                    question = next(q for q in S.load_task(project, slug)["questions"] if q["status"] == "open")
                    decision = T.message(project, slug, T.OPERATOR_MESSAGE_ROLE, "Use this pagination approach.")
                    result = T.resolve_question(project, slug, question["id"], question["revision"], decision["id"],
                                                disposition="answered", reason="The operator approved this approach.", expected_attempt=1)
                elif self.path == "/fixture/edit":
                    commit()
                    result = {"ok": True}
                elif self.path == "/fixture/mode":
                    mode.update({key: body[key] for key in ("available", "fail") if key in body})
                    if "same_engine" in body:
                        same = body["same_engine"]
                        choice.update(engine=config.ENGINES[0 if same else 1], label="Owner engine" if same else "Second engine",
                                      same_engine=same, fallback_reason="No alternate engine is available." if same else "")
                    result = {"ok": True}
                elif self.path == "/fixture/l2-withdraw":
                    result = reviews.withdraw(project, slug, latest()["id"], actor="l2", expected_attempt=1,
                                              reason="Owner attempts to skip the operator request")
                else:
                    return self._json({"error": "Unknown fixture action"}, 404)
                return self._json(result)
            except T.TransitionError as exc:
                return self._json({"error": str(exc)}, 409)

    serve(Handler, release=gate.set)


if __name__ == "__main__":
    main()
