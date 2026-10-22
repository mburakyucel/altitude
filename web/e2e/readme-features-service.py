"""The README's feature illustrations on the Atlas example of readme-service.py: a task the coordinator offers under
its reply and creates on the operator's press, an owner's design proposal with its independent review, an owner's
open question beside its live session, a pull request waiting for the operator's review after an independent review,
a command an owner hands to the operator's terminal, a task the coordinator repairs and resumes, improvement
suggestions in the Altitude project, and Harbor's routines with the tasks its dependency check suggested. Real
handlers, storage and terminal; the coordinator's engine, the routine's worker and the shell are fixtures, and every
message is authored example text."""
from datetime import datetime, timedelta, timezone
import importlib.util
import os
import pathlib

from service_support import serve
from tests.support import git, local_terminal_launch, local_terminal_stop, make_repo
from altitude import config, dispatch, engines, improve, improve_eval, l3, project_agents, project_setup, route, server
from altitude import suggestions as suggested, terminal
from altitude import state as S, tasks as T, verify

_spec = importlib.util.spec_from_file_location("readme_service", pathlib.Path(__file__).with_name("readme-service.py"))
readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readme)
PROJECT = readme.PROJECT
NOW = datetime.now(timezone.utc).replace(microsecond=0)
CREATED = "Hold cursors during the index switch"
SYNONYMS_SPEC = """# Synonyms editor

One page per tenant.

- **Draft.** Edits stay in a draft; live search is unchanged.
- **Preview.** Run real queries against the draft and compare the results.
- **Publish.** One button makes the draft live; the previous set stays one click away.
- **Empty.** A tenant without synonyms sees an example row and Add synonyms.
"""


def other_engine(task):
    return next((e for e in config.ENGINES if e != task.get("l2_engine")), config.ENGINES[0])


def finding(key, title, body):
    return {"id": key, "severity": "medium", "title": title, "body": body, "path": None, "line": None}


def main():
    example = readme.seed()
    say = example["say"]
    slugs = {row["title"]: row["slug"] for row in S.list_tasks(PROJECT)}

    # Create a task: the coordinator's latest reply offers one, and the operator's press runs its turn, scripted here.
    say(slugs["Preserve client compatibility"], "l2", "Contract tests pass across the switch in both directions.", 9)
    with readme.minutes_ago(8):
        l3.chat_log(PROJECT, "user", "A few searches started right at the v2 switch came back empty. Is that a bug?",
                    trigger="chat", turn_id="0ffe12a4b7c3")
    with readme.minutes_ago(7):
        l3.chat_log(PROJECT, "assistant", "Yes. A cursor issued in the second before the switch can point at the new "
                    "index before it serves reads. Holding new cursors until the switch completes fixes it.",
                    trigger="chat", turn_id="0ffe12a4b7c3", offer=CREATED)

    def coordinator(prompt, **options):
        if options.get("on_start"):
            options["on_start"](None)
        if l3.OFFER_PREFIX + CREATED in prompt:
            server.l3_verb_request(PROJECT, {"kind": "alt", "args": ["task", "new", "--title", CREATED, "-"],
                                             "stdin": "Hold cursors issued during the v2 switch until it completes."})
            return {"text": "Started it, with the empty searches at the switch in its brief.", "session_id": "readme-l3"}
        raise AssertionError(f"the example's coordinator has no scripted answer for: {prompt[:200]}")
    engines.claude_print = coordinator

    # Proposal review: the synonyms editor's owner shows its design and the other engine's review before building it.
    synonyms = slugs["Add a synonyms editor for search admins"]
    design = pathlib.Path(S.load_task(PROJECT, synonyms)["worktree"]) / "design" / "synonyms-editor.md"
    design.parent.mkdir(parents=True)
    design.write_text(SYNONYMS_SPEC)
    say(synonyms, "l2", "Here is the editor I propose before building it: one page per tenant, a draft you preview "
        "against real queries, and Publish to make it live. The review found two problems, both fixed, and offered "
        "inline editing from search results as an alternative.", 4)
    T.block(PROJECT, synonyms, "Build the synonyms editor as shown?", actor="l2", expected_attempt=1,
            updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            show=T.shown_files(PROJECT, S.load_task(PROJECT, synonyms), ["design/synonyms-editor.md"]),
            questions={"questions": [{"question": "Build the synonyms editor as shown?", "review": "proposal",
                                      "why": "Live search changes only on Publish.",
                                      "options": [
                {"key": "go", "label": "Approve proposal", "text": "Build it as shown."},
                {"key": "changes", "label": "Request changes", "text": "Hold it for changes."}],
                "recommended_key": "go"}]})
    task = S.load_task(PROJECT, synonyms)
    head = git("rev-parse", "HEAD", cwd=task["worktree"]).strip()
    snapshot = {"head": head, "base": head, "tree": "3" * 40, "context_hash": "4" * 64,
                "design": {"id": task["questions"][-1]["shown"]["id"]}}
    with readme.minutes_ago(7):
        at = T._conversation_time()
    task.setdefault("reviews", []).append({
        "id": "readme-synonyms-proposal", "requested_at": at, "requested_by": "l2", "subject": "proposal",
        "state": "completed", "focus": "", "engine_label": config.ENGINE_LABELS[other_engine(task)],
        "same_engine": False, "finished_at": at, "snapshot": snapshot,
        "result": {"text": "The draft-and-publish flow is safe to build.", "limitations": [], "findings": [
            finding("live", "Saving changes live search at once", "A typo would reach every tenant's search."),
            finding("empty", "No empty state for a new tenant", "A tenant without synonyms sees a blank table."),
            {**finding("inline", "Edit from search results",
                       "Quicker for one term at a time; bulk edits still need the page."), "severity": "alternative"}]},
        "dispositions": [
            {"finding_id": "live", "disposition": "fixed", "reason": "Edits stay in a draft until Publish."},
            {"finding_id": "empty", "disposition": "fixed", "reason": "A new tenant sees an example row."},
            {"finding_id": "inline", "disposition": "dismissed", "reason": "Offered to you as an alternative."}],
        "reconciled": {**snapshot, "reason": "Checked both fixes in the revised design.", "at": at},
        "message": {"id": "readme-synonyms-proposal", "at": at, "role": "system", "by": "l2",
                    "review_id": "readme-synonyms-proposal", "text": "Adversarial proposal review requested."}})
    S.save_task(PROJECT, task)
    example["live"](synonyms, [
        example["said"](30, "I am drafting the editor against the existing admin pages."),
        *example["ran"](26, "readme-routes", "rg -n \"synonym\" src/admin src/search",
                        "src/search/analyzer.ts:31: const synonyms = tenantSynonyms(tenant)\n"
                        "src/admin/routes.ts:12: tenants/:id/settings"),
        example["said"](22, "Synonyms load per tenant at query time, so a draft can be previewed without publishing."),
        *example["ran"](14, "readme-capture", "pnpm capture synonyms-editor",
                        "captured design/synonyms-editor-phone.png\ncaptured design/synonyms-editor-desktop.png"),
        example["said"](6, "The design is ready. The other engine's review is complete; both fixes are in."),
    ], 31)

    # The task view: the typo-tolerance owner's conversation, its open question and its live session.
    typos = slugs["Add typo tolerance to search"]
    say(typos, "l2", "Search now matches one typo per word, so \"serch\" finds \"search\". Exact matches still rank "
        "first.", 26)
    steer = say(typos, T.OPERATOR_MESSAGE_ROLE, "Product codes must stay exact. A code one character off is a different "
        "product.", 22)
    say(typos, "l2", "Codes stay exact: words with digits skip typo matching. Relevance tests pass on the sample "
        "tenants.", 12)
    T.block(PROJECT, typos, "Allow two typos in long words?", actor="l2", updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE},
            questions={"questions": [{"question": "Allow two typos in long words?",
                                      "why": "Two typos found 6% more results in the sample; 1 in 20 was off-topic.",
                                      "options": [
                {"key": "one", "label": "One typo", "text": "Keep one typo per word."},
                {"key": "two", "label": "Two in long words", "text": "Allow two in words of eight letters or more."}],
                "recommended_key": "one"}]})
    example["live"](typos, [
        example["said"](30, "I am adding typo matching to the query parser."),
        *example["ran"](27, "readme-fuzzy", "rg -n \"exact|fuzzy\" src/search",
                        "src/search/parser.ts:40: const exact = isQuoted(term)\n"
                        "src/search/match.ts:18: export function fuzzy(term, edits)"),
        steer,
        example["said"](20, "Words with digits skip typo matching, so product codes stay exact."),
        *example["ran"](14, "readme-relevance", "pnpm test -- relevance", "41 relevance tests passed\n0 failed"),
        example["said"](11, "One choice is yours before I finish: how many typos long words allow."),
    ], 31)

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
    other = other_engine(task)
    snapshot = {"head": head, "base": head, "tree": "1" * 40, "context_hash": "2" * 64}
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

    # A step only the operator can take, handed to the task's terminal and run only on their Enter.
    os.environ.update({"PS1": "atlas $ ", "PROMPT_COMMAND": ""})
    terminal.shell_command = lambda: ["bash", "--noprofile", "--norc"]
    terminal.launch, terminal.stop = local_terminal_launch, local_terminal_stop
    billing = slugs["Sign billing webhook payloads"]
    say(billing, "l2", "Each payload carries an HMAC-SHA256 signature and a timestamp; receivers reject anything older "
        "than five minutes.", 20)
    say(billing, "l2", "Key rotation keeps both keys signing, so receivers accept either until the old one retires.", 12)
    say(billing, "l2", "Signing works end to end in tests. Deploying to staging uses "
        "your SSH key, so this step is yours. Nothing runs until you press Enter:\n\n```run\nmake deploy ENV=staging\n```", 6)

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
    improve._altitude = lambda project: project == "altitude"
    route.improvement_reserve = lambda: None
    drain = server.request_l3_drain
    server.request_l3_drain = lambda project: project == PROJECT and drain(project)  # the Altitude project's L3 stays idle
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
    # Harbor's routines: the built-in dependency check found two security fixes two hours ago and suggested a task for
    # each, which the coordinator checked and kept; a routine of the operator's own reads each merge.
    answers: dict[str, str] = {}
    def start(_engine, _name, _prompt, *, extra_env, **_options):
        worker = "readme-" + extra_env["ALTITUDE_AGENT_RUN"].replace("/", "-")
        return {"returncode": 0, "agent": {"id": worker, "sessionId": f"session-{worker}"}}
    engines.start_project_agent_run = start
    engines.owned_worker = lambda worker, *, job_root: worker in answers and {
        "id": worker, "state": "done", "detail": "", "detail_at": 0, "usage": {"input_tokens": 300_000, "output_tokens": 4_000}}
    engines.worker_answer = lambda worker, *, job_root: answers[worker]
    engines.observe_token_usage = lambda _engine, _session, _cursor, *, job_root: {"cursor": {}, "status": "observed",
                                                                                   "sessions": []}
    # While seeding: the engines' allowance is unread, Atlas's tasks in flight hold every task slot, and the runs'
    # Git commands skip the guards' hooks, as the seed's own do.
    reserve, route.reserve = route.reserve, lambda _engine: None
    slot, project_agents._slot = project_agents._slot, lambda: None
    unhooked = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": os.devnull}
    os.environ.update(unhooked)

    def ran(agent, at, answer):
        project_agents.change("harbor", "run", {"agent": agent}, by="operator", source="fixture", now=at)
        project_agents.start_next("harbor", now=at)
        run = project_agents._load("harbor")["runs"][-1]
        answers[run["worker"]] = answer
        project_agents.reconcile("harbor", now=at + timedelta(minutes=6))
        return run["n"]

    day = NOW - timedelta(hours=2)
    project_agents.change("harbor", "builtin", {"key": "dependency-check"}, by="operator", source="fixture",
                          now=day - timedelta(days=6))
    merges = project_agents.change("harbor", "create", {"definition": {
        "name": "Review each merge", "brief": "Read the newest merged pull request and say which change lacks a test.",
        "schedules": [], "events": True, "tell": "found", "model": None, "limit": 20}},
        by="operator", source="fixture", now=day - timedelta(days=6))["agent"]
    project_agents.change("harbor", "turn_on", {"agent": merges}, by="operator", source="fixture",
                          now=day - timedelta(days=6))
    deps = next(a["id"] for a in project_agents._load("harbor")["agents"] if a.get("builtin") == "dependency-check")
    ran(merges, day - timedelta(days=1), "Nothing: #212 changes only the docs.")
    n = ran(deps, day, """Found: two packages Harbor uses have security fixes.

The lock file pins yaml 2.3.1 and sharp 0.32.5; both have releases that close published advisories.

Suggest: Update yaml for its security fix
Version 2.3.4 closes an advisory about deeply nested input. The upload tests pass with it.

Suggest: Update sharp for its security fix
Version 0.32.6 closes an advisory in its bundled image decoder. The thumbnail tests pass with it.""")
    suggested.post("harbor", f"agent:{deps}/{n}", [1, 2], [], now=day + timedelta(minutes=20))
    route.reserve, project_agents._slot = reserve, slot
    for key in unhooked:
        del os.environ[key]
    S.regen_state_md(PROJECT)

    class Handler(server.Handler):
        # Fixture controls answer PUT alone, so they never pass through the application's own routes.
        def do_PUT(self):
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
            return self._json({"error": "unknown fixture"}, 404)

    serve(Handler, release=terminal.close_all)


if __name__ == "__main__":
    main()
