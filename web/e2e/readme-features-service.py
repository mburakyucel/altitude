"""The README's feature illustrations on the Atlas example of readme-service.py: a pull request waiting for the
operator's review after the other engine's review, a task the coordinator repairs and resumes, and improvement
suggestions in the Altitude project. Real handlers and storage; every message is authored example text."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

from service_support import serve
from tests.support import git, make_repo
from altitude import config, dispatch, engines, improve, improve_eval, l3, project_setup, route, server
from altitude import state as S, tasks as T, verify

_spec = importlib.util.spec_from_file_location("readme_service", Path(__file__).with_name("readme-service.py"))
readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readme)
PROJECT = readme.PROJECT
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def main():
    example = readme.seed()
    say = example["say"]
    slugs = {row["title"]: row["slug"] for row in S.list_tasks(PROJECT)}

    # Review before merge: the owner's report, the other engine's review and the operator's merge hold.
    limits = slugs["Add per-tenant limits to the public API"]
    git("remote", "set-url", "origin", "https://github.com/example/atlas.git", cwd=example["repo"])
    verify.gh = lambda *_args, **_kwargs: {"state": "OPEN"}  # the example PR stays open; no GitHub call is made
    say(limits, "l2", "Per-tenant limits are ready in PR #214. Each tenant gets its own token bucket, and a request "
        "over its limit receives 429 with Retry-After. Contract and load tests pass.", 14)
    T.set_hold_merge(PROJECT, limits, "Public API changes wait for your approval.")
    task = S.load_task(PROJECT, limits)
    head = git("rev-parse", "HEAD", cwd=task["worktree"]).strip()
    task.update(delivery={"number": 214, "head": head, "at": S.now()}, prs=[214])
    other = next((e for e in config.ENGINES if e != task.get("l2_engine")), config.ENGINES[0])
    snapshot = {"head": head, "base": head, "tree": "1" * 40, "context_hash": "2" * 64}
    finding = lambda key, title, body: {"id": key, "severity": "medium", "title": title, "body": body,
                                        "path": None, "line": None}
    with readme.minutes_ago(12):
        at = T._conversation_time()
    task.setdefault("reviews", []).append({
        "id": "readme-limits-changes", "requested_at": at, "requested_by": "l2", "subject": "changes",
        "state": "completed", "focus": "", "engine_label": config.ENGINE_LABELS[other], "same_engine": other == task.get("l2_engine"),
        "finished_at": at, "snapshot": snapshot,
        "result": {"text": "The limiter holds under concurrent requests from many tenants.", "limitations": [],
                   "findings": [finding("burst", "A burst at the window edge allows twice the limit",
                                        "Requests on both sides of a window boundary each see a full bucket."),
                                finding("retry", "Limit responses omit Retry-After",
                                        "A client over its limit cannot tell when to try again.")]},
        "dispositions": [
            {"finding_id": "burst", "disposition": "fixed", "reason": "Buckets refill continuously, not per window."},
            {"finding_id": "retry", "disposition": "fixed", "reason": "Every 429 carries Retry-After."}],
        "reconciled": {**snapshot, "reason": "Checked both fixes against the final version.", "at": at},
        "message": {"id": "readme-limits-changes", "at": at, "role": "system", "by": "l2",
                    "review_id": "readme-limits-changes", "text": "Adversarial changes review requested."}})
    S.save_task(PROJECT, task)
    S.write_json(S.task_dir(PROJECT, limits) / "report.json", {
        "landed": {"prs": [{"number": 214, "merged": False}], "main_runs": [], "deploy": "not-applicable"},
        "review": [], "blocked": ""})
    T.report(PROJECT, limits, {"verdict": "ok", "delivery": task["delivery"], "owner": T.report_owner(task), "prs": [214]})

    # A fault: a machine problem pauses the sign-on task until the coordinator repairs it.
    sso = slugs["Add SAML single sign-on for enterprise accounts"]
    T.block(PROJECT, sso, "system fault [environment]: the test identity provider stopped answering", actor="altd",
            expected_state="running", updates={"waiting_on": "l3", "fault": "environment"})

    # Improvement suggestions in the Altitude project, from five finished tasks.
    ended = NOW - timedelta(days=2)
    improve_eval.load({"project": "altitude", "now": NOW.isoformat(), "tasks": [
        {"slug": f"finished-{n}", "title": title, "state": "done", "verdict": "delivered", "tokens": 1000,
         "created": (ended - timedelta(hours=6 * n + 6)).isoformat(), "reported": (ended - timedelta(hours=6 * n + 1)).isoformat(),
         "finished": (ended - timedelta(hours=6 * n)).isoformat(),
         "messages": [{"id": f"m{n}", "at": (ended - timedelta(hours=6 * n + 1)).isoformat(), "role": "operator",
                       "text": "Asked and answered again."}]}
        for n, title in enumerate(["Show token usage per task", "Retry a failed activation", "Sort Needs you by age",
                                   "Fold system turns in chat", "Keep the terminal across reconnects"])]})
    with config.add_project("altitude", path=make_repo(example["repos"] / "altitude" / "altitude")):
        pass
    project_setup.request("altitude", "repair", actor="operator")
    project_setup.run("altitude")
    l3.save_info("altitude", {"session_id": "readme-altitude-l3", "engine_last": config.ENGINES[0], "turns": 1})
    # The review's lifecycle is real; the Altitude-project gate and Claude's reserve are unit-tested and fixed here.
    improve._altitude = lambda _project: True
    route.improvement_reserve = lambda: None
    server.request_l3_drain = lambda _project: False
    improve.set_weekly("altitude", True, agree=improve.terms("altitude")["version"], now=NOW - timedelta(days=9))
    suggestions = [
        ("agents", "Owners re-ask questions you already answered",
         "Three tasks asked again after your answer in task chat. Citing the earlier answer would end it."),
        ("agents", "Reports list checks that never ran",
         "Two reports named a check as passed without its log. Linking each run would catch it."),
        ("project", "Activation retries by hand after a slow restart",
         "Two tasks restarted an activation that a short retry would have finished.")]

    def review(text, instructions, schema, *, model, timeout):
        aliases = [a for group in improve.ALIASES.findall(text) for a in group.split(", ")]
        return {"model": model, "engine_model": model, "usage": {}, "cost": 0.0, "tokens": 900, "over_limit": False,
                "error": None, "answer": {"suggestions": [
                    {"target": target, "title": title, "explanation": explanation, "where": "the owner instructions",
                     "signal": "after_report", "anchors": (aliases[n:] + aliases[:n])[:2 + n % 2],
                     "uncertainty": "A small sample."}
                    for n, (target, title, explanation) in enumerate(suggestions)]}}
    engines.improvement_review = review
    asked = NOW - timedelta(minutes=40)
    l3.chat_log("altitude", "user", "Any suggestions from this week's work?", trigger="chat", turn_id="readme-improve",
                at=asked.isoformat())
    l3.chat_log("altitude", "assistant", "Started a review of this week's finished tasks.", trigger="chat",
                turn_id="readme-improve", at=(asked + timedelta(seconds=20)).isoformat())
    run = improve.start("altitude", "readme-improve", now=asked)["run"]
    improve.execute("altitude", run)
    improve.post("altitude", run, [1, 2, 3], [], now=asked + timedelta(minutes=8))
    S.regen_state_md(PROJECT)

    class Handler(server.Handler):
        def do_POST(self):
            if self.path == "/fixture/repair":
                # The coordinator's incident turn: it fixed the machine problem and resumed the task.
                with readme.minutes_ago(0.1):
                    l3.chat_log(PROJECT, "user", f"System fault [environment] in {PROJECT}/{sso}: the test identity "
                                "provider stopped answering", trigger="incident", turn_id="readme-repair", slug=sso)
                    l3.chat_log(PROJECT, "assistant", "The sign-on task's test identity provider stopped answering. I "
                                "restarted it, checked a login end to end and resumed the task.",
                                trigger="incident", turn_id="readme-repair", slug=sso)
                dispatch.request_task_operation(PROJECT, sso, "resume", "The test identity provider answers again; "
                                                "continue where you stopped.", actor="l3")
                dispatch.run_task_operation(PROJECT, sso)
                return self._json(S.load_task(PROJECT, sso))
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
