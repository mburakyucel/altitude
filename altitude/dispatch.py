"""Dispatch an L2 as `claude --bg` in a worktree; poll `claude agents`; notice done (ARCHITECTURE §5)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import re
from pathlib import Path

from . import config, engines, rules, state as S, tasks as T


def project_never_list(repo: Path) -> str:
    """Best effort: the 'Never' bullets from the repo's CLAUDE.md, else a generic line."""
    md = repo / "CLAUDE.md"
    if md.exists():
        lines = [l.strip("- ").strip() for l in md.read_text().splitlines() if re.match(r"^\s*-\s*\*\*?never", l, re.I) or "never" in l.lower()[:40]]
        if lines:
            return "; ".join(l[:160] for l in lines[:8])
    return "no changes outside the brief; no weakened guardrails; high-impact classes: open the PR and stop"


JOBS_DIR = config.HOME / ".claude" / "jobs"   # the harness's background-job state, keyed by agent id


def _git_branch(worktree: str | Path) -> str | None:
    """The branch git reports for `worktree`, or None if it is absent, detached or git failed."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError):
        return None
    b = (r.stdout or "").strip()
    return b if r.returncode == 0 and b and b != "HEAD" else None


def worktree_branch(slug: str, worktree: str | Path | None = None, agent_id: str | None = None) -> str:
    """The branch `claude --bg -w <slug>` really checks out — `worktree-<slug>`, never the bare slug.

    The harness records the real name in `~/.claude/jobs/<agent_id>/state.json` (`worktreeBranch`); before an agent id
    exists the name is derived and confirmed against the worktree itself. Every failure degrades to the derived name:
    a wrong branch in the brief is bad, a dispatch that dies reading a state file is worse."""
    derived = f"worktree-{slug}"
    if agent_id:
        try:
            st = json.loads((JOBS_DIR / str(agent_id) / "state.json").read_text())
            b = (st.get("worktreeBranch") or "").strip() if isinstance(st, dict) else ""
            if b:
                return b
        except (OSError, ValueError, AttributeError):
            pass
    return (_git_branch(worktree) or derived) if worktree else derived


def build_brief(project: str, slug: str) -> str:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    proposal = (d / "proposal.md").read_text() if (d / "proposal.md").exists() else (d / "request.md").read_text()
    env = task["envelope"]
    dec = task.get("decision") or {}
    approval_note = f" — Burak chose: {dec['chosen']}" if dec.get("chosen") else ""
    if dec.get("chosen") and dec.get("detail"):  # decision 46: the card is short; the conditions behind it travel with the brief
        approval_note += f"\n\nBehind the card (the L3's reasoning and conditions — binding where they say so):\n{dec['detail']}"
    if dec.get("chosen") and dec.get("note"):  # decision 50: Burak's own words with the answer are binding too
        approval_note += f"\n\nBurak's note with that answer (binding): {dec['note']}"
    policy = proj.get("approval", "default")
    if task.get("hold_merge"):  # decision 48: the hold is the exception, and it says why
        merge_policy = f"**Held for Burak** — open the PR, get it review-clean and CI-green, and stop; Burak merges it himself. Why: {task['hold_merge']}"
    else:
        merge_policy = {"default": "Merge when the review is addressed and CI is green — every class, L included (decision 48). Only a brief marked *held* stops at the open PR.",
                        "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when the review is addressed and CI is green."}.get(policy, policy)
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, cls=task["class"], project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        model=task.get("model") or config.MODELS["l2"],
        engine_line=(f"the engine is forced to **{task['engine']}** for this task." if task.get("engine") else "the engine is Altitude's choice."),
        leases=("; ".join(f"`{l['slug']}` on {', '.join(l['paths']) or '(undeclared paths)'}" for l in leases(project, exclude=slug)) or "none"),
        paths=", ".join(task_paths(project, task)) or "(not declared — stay inside the proposal's file list)",
        task_dir=d, merge_policy=merge_policy, never_list=project_never_list(config.project_path(project)),
        l1_in_flight=env["l1_in_flight"], subagent_launches=env["subagent_launches"], max_turns=env["max_turns"],
        verification=env.get("verification", "reviewer"), approval_note=approval_note, repo=config.project_path(project),
        branch=worktree_branch(slug, config.project_path(project) / ".claude" / "worktrees" / slug),
        proposal=proposal, **{"class": task["class"]})
    stack = rules.compile_section(rules.stack_rules(proj.get("stacks", [])), "Stack rules")
    return text + ("\n" + stack if stack else "")


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-dispatch settings passed with --settings: hooks that enforce the envelope, nothing global."""
    hooks = config.HOOKS
    settings = {"hooks": {
        "PreToolUse": [{"matcher": "Agent|Task|Bash", "hooks": [{"type": "command", "command": f"python3 {hooks / 'subagent_cap.py'}", "timeout": 10}]},
                       {"matcher": "Bash", "hooks": [{"type": "command", "command": f"python3 {hooks / 'guard.py'}", "timeout": 10}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"python3 {hooks / 'edit_count.py'}", "timeout": 10}]}],
    }, "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key},
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW}  # decision 49: the 300k umbrella, also for resumed sessions
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


def run(project: str, slug: str, model: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "approved":
            raise T.TransitionError(f"{slug} is {task['state']}, not approved")
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
        task["dispatching"] = S.now()
        S.save_task(project, task)
    attempt = task.get("attempt", 0) + 1
    dispatch_id = f"{slug}-{attempt}"
    name = f"{project}/{dispatch_id}"
    d = S.task_dir(project, slug)
    brief_md = build_brief(project, slug)
    T.brief(project, slug, brief_md, actor="altd")
    # envelope file the hooks read (keyed by dispatch id; session id is learned after launch)
    env_file = config.MONITOR_DIR / f"envelope-{project}--{dispatch_id}.json"
    S.write_json(env_file, {"project": project, "slug": slug, "dispatch_id": dispatch_id, **task["envelope"]})
    settings = session_settings(project, slug, f"{project}--{dispatch_id}")
    persona = rules.compiled_persona("l2", project)
    proj = config.project(project)
    res = engines.claude_bg(name, brief_md, cwd=config.project_path(project), worktree=slug, persona=persona,
                            permission_mode="auto", max_turns=task["envelope"]["max_turns"],
                            model=model or task.get("model") or proj.get("l2_model") or config.MODELS["l2"], settings=settings,
                            extra_env=l2_env(project, {"slug": slug, "dispatch_id": dispatch_id}))
    agent = res.get("agent") or {}
    if res["returncode"] != 0 and not agent:
        with S.project_lock(project):
            t2 = S.load_task(project, slug); t2["dispatching"] = None; S.save_task(project, t2)
        S.append_event(project, slug, "dispatch-failed", stdout=res["stdout"][:300], stderr=res["stderr"][:300])
        raise RuntimeError(f"claude --bg failed: {res['stderr'][:300] or res['stdout'][:300]}")
    worktree = str(config.project_path(project) / ".claude" / "worktrees" / slug)
    T.dispatch(project, slug, dispatch_id=dispatch_id, session_id=agent.get("sessionId"), agent_id=agent.get("id"),
               worktree=worktree, branch=worktree_branch(slug, worktree, agent.get("id")))
    if agent.get("sessionId"):
        S.write_json(config.MONITOR_DIR / f"session-{agent['sessionId']}.json",
                     {"project": project, "slug": slug, "dispatch_id": dispatch_id, "level": "l2"})
    return {"dispatch_id": dispatch_id, "agent": agent, "stdout": res["stdout"]}


def l2_env(project: str, task: dict) -> dict:
    """The env every L2 session (fresh or resumed) needs: the hooks read the session key to find their envelope."""
    return {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": task["slug"],
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_SESSION_KEY": f"{project}--{task['dispatch_id']}"}


def resume_session(project: str, slug: str, text: str, session_id: str | None = None) -> dict:
    """Re-attach the task's L2 transcript in a new --bg worker (in the task's worktree) and hand it `text`.

    A resumed session gets a new session id and agent id: the task is rebound to the live row, so `poll()` follows the
    new worker instead of re-reading the old one's `failed`/`done` row. No fallback to the main checkout: a missing
    worktree is a dispatch-again situation, not a place to run an L2 that thinks it is on its own branch."""
    task = S.load_task(project, slug)
    sid = session_id or task.get("session_id")
    if not sid:
        raise T.TransitionError("no session to resume; dispatch again")
    cwd = Path(task.get("worktree") or "")
    if not task.get("worktree") or not cwd.is_dir():
        raise T.TransitionError(f"worktree missing for {slug} ({task.get('worktree')}); dispatch again")
    name = f"{project}/{task['dispatch_id']}"
    res = engines.claude_resume_bg(name, sid, text, cwd=cwd, persona=rules.compiled_persona("l2", project),
                                   max_turns=task["envelope"]["max_turns"], settings=S.task_dir(project, slug) / "settings.json",
                                   extra_env=l2_env(project, task))
    live = [a for a in engines.claude_agents() if a.get("name") == name and a.get("state") not in ("failed", "done", "stopped")]
    if not live:
        raise RuntimeError(f"resume of {name} produced no live worker: {res['stderr'][:200] or res['stdout'][:200]}")
    new = max(live, key=lambda a: a.get("startedAt") or 0)
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["agent_id"], t["session_id"] = new.get("id"), new.get("sessionId")
        S.save_task(project, t)
    S.append_event(project, slug, "resumed", agent_id=new.get("id"), session_id=new.get("sessionId"), previous=sid)
    res["agent"] = new
    return res


def resume_blocked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ") -> dict:
    task = S.load_task(project, slug)
    if task["state"] == "blocked":
        hold = wip_hold(project, task)
        if hold:
            waiting = (f"waiting for lease: {hold.removeprefix('file lease: ')}"
                       if hold.startswith("file lease: ") else f"waiting: {hold}")
            with S.project_lock(project):
                task = S.load_task(project, slug)
                if "blocked_question" not in task:
                    task["blocked_question"] = task.get("blocked_reason")
                task["resume_answer"] = answer
                task["resume_prefix"] = prefix
                task["resume_after"] = S.now()
                task["blocked_reason"] = waiting
                S.save_task(project, task)
                S.append_event(project, slug, "resume-deferred", hold=hold, reason=waiting)
            return {"deferred": True, "hold": hold, "waiting": waiting}
    if task.get("agent_id"):  # the idle worker that stopped at the block keeps nothing the transcript does not
        engines.claude_stop(task["agent_id"])
    res = resume_session(project, slug, f"{prefix}{answer}\nContinue from your progress file; finish to *done* and rewrite the report.")
    T.resume(project, slug, answer=answer)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.pop("resume_after", None)
        task.pop("resume_answer", None)
        task.pop("resume_prefix", None)
        S.save_task(project, task)
    res["deferred"] = False
    return res


def resume_due(project: str) -> list[str]:
    """Tasks blocked by an exhausted window come back by themselves once it reopens — oldest first, WIP-throttled."""
    if engines.usage_hold():
        return []
    now, back = S.now(), []
    due = [t for t in S.list_tasks(project) if t["state"] == "blocked" and t.get("resume_after") and t["resume_after"] <= now]
    for t in sorted(due, key=_resume_order):
        if wip_hold(project, t):
            continue  # a lease or the improve-serialization rule holds this one; a younger unrelated task may still go
        if "resume_answer" in t:
            answer = t["resume_answer"]
            prefix = t.get("resume_prefix", "")
        else:
            answer = "The usage window has reopened; Altitude held you, nothing is wrong with the task."
            prefix = ""
        res = resume_blocked(project, t["slug"], answer, prefix=prefix)
        if res and res.get("deferred"):
            continue
        with S.project_lock(project):
            t2 = S.load_task(project, t["slug"])
            t2.pop("resume_after", None)
            t2.pop("resume_answer", None)
            t2.pop("resume_prefix", None)
            S.save_task(project, t2)
        back.append(t["slug"])
    return back


def _resume_order(task: dict) -> tuple[str, str]:
    """The deterministic oldest-first order shared by due resumes and pending-resume leases."""
    return (task.get("created") or "", task.get("slug") or "")


def _norm(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def paths_overlap(a: list[str], b: list[str]) -> list[str]:
    """Paths collide when equal or when one is a directory prefix of the other (decision 39)."""
    out = []
    for x in map(_norm, a):
        for y in map(_norm, b):
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                out.append(x if len(x) >= len(y) else y)
    return sorted(set(out))


BROAD_CLAIMS = ("tests", "docs", "altitude", "web", "hooks", "bin", "personas", "schemas", "templates", "src", "lib", "app")


def narrow(paths: list[str]) -> list[str]:
    """Decision 51 addendum: a claim on a whole top-level directory (`tests/`, `docs/`) is not a lease — it would hold every
    task in the project behind one (2026-08-30: 23 approved tasks waited on a single `tests/` claim). Files and deeper
    directories lease; top-level directory claims are dropped here, so briefs still show them but nothing waits on them."""
    return [p for p in paths if p.strip("/").split("/")[0] != p.strip("/") or p.strip("/") not in BROAD_CLAIMS]


def hold_conflict(mine: list[str], others: list[dict]) -> str | None:
    """Return the first narrowed file-lease conflict with ``others``, if any."""
    mine = narrow(mine)
    for other in others:
        hit = paths_overlap(mine, narrow(other.get("paths", [])))
        if hit:
            activity = other.get("activity", "running")
            return f"file lease: `{other['slug']}` is {activity} on {', '.join(hit[:4])}"
    return None


def _split_top_level(value: str) -> list[str]:
    """Split commas outside brace groups and parenthesized annotations."""
    parts, start, brace_depth, paren_depth = [], 0, 0, 0
    for i, char in enumerate(value):
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "," and not brace_depth and not paren_depth:
            parts.append(value[start:i])
            start = i + 1
    parts.append(value[start:])
    return parts


def _annotation_start(value: str) -> int | None:
    """The start of a trailing parenthesized annotation, not parentheses within a filename."""
    start = value.find(" (")
    while start >= 0:
        depth = 0
        for i in range(start + 1, len(value)):
            if value[i] == "(":
                depth += 1
            elif value[i] == ")":
                depth -= 1
                if depth == 0:
                    if not value[i + 1:].strip():
                        return start
                    break
        start = value.find(" (", start + 2)
    return None


def _expand_braces(path: str) -> list[str]:
    """Expand balanced brace groups, including subsequent and nested groups."""
    candidates = [path]
    while any("{" in candidate for candidate in candidates):
        expanded = []
        for candidate in candidates:
            start = candidate.find("{")
            if start < 0:
                expanded.append(candidate)
                continue
            depth, end = 0, None
            for i in range(start, len(candidate)):
                if candidate[i] == "{":
                    depth += 1
                elif candidate[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end is None:
                continue
            members = _split_top_level(candidate[start + 1:end])
            expanded.extend(candidate[:start] + member.strip() + candidate[end + 1:]
                            for member in members if member.strip())
        candidates = expanded
    return [candidate for candidate in candidates if "{" not in candidate and "}" not in candidate]


def _expand_entry(entry: str) -> list[str]:
    """Expand one declared file entry into unannotated paths."""
    entry = entry.strip()
    if not entry or entry.startswith("("):
        return []
    if "{" not in entry and "}" not in entry and " (" not in entry:
        return [entry]
    out = []
    for part in _split_top_level(entry):
        path = part.strip()
        annotation = _annotation_start(path)
        if annotation is not None:
            path = path[:annotation].strip()
        if not path or path.startswith("(") or ("/" not in path and "." not in path):
            continue
        out.extend(_expand_braces(path))
    return out


def task_paths(project: str, task: dict) -> list[str]:
    """The paths a task has declared: `--paths` on the task, else the proposal's `files`. This is the *staging* lease
    (`alt land` refuses changes outside it); the *hold* lease is `narrow()` of it — see wip_hold."""
    entries = task.get("paths")
    if not entries:
        p = S.read_json(S.task_dir(project, task["slug"]) / "proposal.json", {}) or {}
        entries = p.get("files") or []
    return [path for entry in entries for path in _expand_entry(str(entry))]


def _lease_tasks(project: str, exclude: str | None = None) -> list[dict]:
    """Tasks that currently hold file leases, including blocked tasks queued to resume."""
    return [t for t in S.list_tasks(project)
            if t["slug"] != exclude
            and (t["state"] == "running" or (t["state"] == "blocked" and t.get("resume_after")))]


def leases(project: str, exclude: str | None = None) -> list[dict]:
    """Running and pending-resume tasks and the paths they hold, for status and briefs."""
    out = []
    for task in _lease_tasks(project, exclude):
        lease = {"slug": task["slug"], "paths": task_paths(project, task)}
        if task["state"] == "blocked":
            lease["pending_resume"] = True
        out.append(lease)
    return out


def job_detail(agent_id: str | None) -> tuple[str, datetime | None]:
    """What the worker last said about itself (`~/.claude/jobs/<id>/state.json` detail) and when — the limit message
    lands here, and "resets 8pm" only means something relative to the moment it was written."""
    if not agent_id:
        return "", None
    p = JOBS_DIR / str(agent_id) / "state.json"
    try:
        st = json.loads(p.read_text())
        return (str(st.get("detail") or "") if isinstance(st, dict) else ""), datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    except (OSError, ValueError):
        return "", None


def rule_application(task: dict) -> bool:
    """A task that edits the rules ledger (docs/RULES.md, docs/incidents) — those serialize; every other improve task
    relies on leases like anyone else (decision 51: the broad "one improve task at a time" held 11 tasks for hours)."""
    paths = task.get("paths") or []
    return str(task.get("slug", "")).startswith("apply-r-") or any(str(p).strip().lstrip("./").startswith(("docs/RULES.md", "docs/incidents")) for p in paths)


PER_TASK_HOLDS = ("file lease", "one rule-application")  # holds that belong to one task; the rest of the queue is still dispatchable


def per_task_hold(hold: str | None) -> bool:
    return bool(hold) and str(hold).startswith(PER_TASK_HOLDS)


def wip_hold(project: str, task: dict | None = None) -> str | None:
    held = engines.usage_hold()
    if held:
        return f"usage limit: subscription window exhausted, resets {held}"
    running = [t for t in S.list_tasks(project) if t["state"] == "running"]
    proj = config.project(project)
    if task and rule_application(task) and any(rule_application(t) for t in running):
        return "one rule-application task at a time (they edit the same ledger)"
    if task:
        mine = task_paths(project, task)
        mine_pending = task.get("state") == "blocked" and bool(task.get("resume_after"))
        for other in _lease_tasks(project, exclude=task["slug"]):
            pending_resume = other["state"] == "blocked"
            if pending_resume and mine_pending and _resume_order(other) >= _resume_order(task):
                continue  # among overlapping queued resumes, the deterministic oldest task proceeds first
            activity = "blocked with a pending resume" if pending_resume else "running"
            held = hold_conflict(mine, [{"slug": other["slug"], "paths": task_paths(project, other),
                                         "activity": activity}])
            if held:
                return held
    live = [a for a in engines.claude_agents() if a.get("kind") == "background" and a.get("state") not in ("done", "failed", "stopped")]  # stopped = no process
    if len(live) >= config.SESSIONS_PER_MACHINE:
        return f"session ceiling: {len(live)} live Claude sessions on this machine (cap {config.SESSIONS_PER_MACHINE})"
    if len(running) >= int(proj.get("wip", config.WIP_PER_PROJECT)):
        return f"WIP limit: {len(running)} running in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p) if t["state"] == "running")
    if total >= config.WIP_PER_MACHINE:
        return f"WIP limit: {total} running on this machine"
    from .monitor import quota_hold, quota
    if not (quota() or {}).get("known"):
        from . import improve  # decision 36: the reserve line cannot be enforced — say so, once a day
        improve.system_fault("quota-unknown", "no statusline snapshot: the quota reserve line (decision 31) is not being enforced; run `alt install-statusline` or fix the monitor")
    q = quota_hold()
    if q:
        return q
    return None


def poll(project: str) -> list[dict]:
    """Compare running tasks with `claude agents`; return the tasks whose L2 finished (state done / gone)."""
    agents = {a.get("sessionId"): a for a in engines.claude_agents()}
    by_id = {a.get("id"): a for a in agents.values()}
    finished = []
    for t in S.list_tasks(project):
        has_report = (S.task_dir(project, t["slug"]) / "report.json").exists()
        if t["state"] == "blocked" and has_report and "idle without a report" in (t.get("blocked_reason") or ""):
            finished.append({"task": t, "agent": None})  # report landed after the idle check: hand it to the verifier
            continue
        if t["state"] != "running":
            continue
        a = agents.get(t.get("session_id")) or by_id.get(t.get("agent_id"))
        live_p = config.MONITOR_DIR / f"live-{project}--{t['slug']}.json"
        prev = S.read_json(live_p, {}) or {}
        live = {"status": a.get("status"), "state": a.get("state")} if a else None
        idle_since = None
        if a and a.get("status") == "idle" and a.get("state") != "done" and not has_report:
            detail, at = job_detail(a.get("id"))
            lim = engines.usage_limit_in(detail, now=at)
            if lim:  # decision 44: the worker is waiting for the window, not for a human
                finished.append({"task": t, "agent": a, "limited": lim})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None, "limited": lim})
                continue
            idle_since = prev.get("idle_since") or S.now()
        died = a is not None and a.get("state") == "failed" and not has_report
        if died:  # worker gone before a report: raised as a system fault by the server, never read as "still running"
            finished.append({"task": t, "agent": a, "died": True})
        elif a is None or a.get("state") in ("done", "failed") or a.get("status") == "exited" or (has_report and a.get("status") == "idle"):
            finished.append({"task": t, "agent": a})
        elif idle_since and _seconds_since(idle_since) > IDLE_NEEDS_INPUT_SECONDS:
            finished.append({"task": t, "agent": a, "needs_input": True})
            idle_since = None
        S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": idle_since})
    return finished


IDLE_NEEDS_INPUT_SECONDS = 150


def _seconds_since(iso: str) -> float:
    from datetime import datetime
    import time
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


RESTART_PENDING = "restart-pending.json"
DEPLOY_DIRS = ("altitude/", "bin/", "systemd/")   # code the running altd loaded at start; everything else is read per use


def pull_after_done(project: str, task: dict) -> list[str]:
    """Self-deploy (decision 43): when a project's checkout *is* the deployment — Altitude's own repo — fast-forward it to
    origin/main after a task lands, so merged hooks, personas and templates are what the next session runs. Python
    changes need a restart: those are announced with an FYI and `monitor/restart-pending.json`, never restarted from here."""
    import subprocess
    proj = config.project(project)
    if not proj.get("self_deploy", project == "altitude"):
        return []
    repo = config.project_path(project)
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        if br != "main":
            return [f"self-deploy skipped: checkout on {br!r}, not main"]
        pull = subprocess.run(["git", "pull", "-q", "--ff-only", "origin", "main"], cwd=str(repo), capture_output=True, text=True, timeout=120)
        if pull.returncode != 0:
            T.fyi(project, task.get("slug"), f"self-deploy: `git pull --ff-only` failed in {repo}: {(pull.stderr or pull.stdout).strip()[:200]}")
            return [f"self-deploy: pull failed: {(pull.stderr or pull.stdout).strip()[:120]}"]
        new = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        if new == head:
            return []
        files = subprocess.run(["git", "diff", "--name-only", head, new], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout.split()
    except (subprocess.SubprocessError, OSError) as e:
        from . import improve
        improve.system_fault("self-deploy", f"{project}: {e}", project=project, task=task.get("slug"))
        return [f"self-deploy: git error: {e}"]
    code = [f for f in files if f.startswith(DEPLOY_DIRS)]
    notes = [f"self-deploy: main {head[:7]} → {new[:7]} ({len(files)} files)"]
    if code:
        pend_p = config.MONITOR_DIR / RESTART_PENDING
        pend = S.read_json(pend_p, {}) or {}
        pend = {"since": pend.get("since") or S.now(), "head": new, "files": sorted(set(pend.get("files", [])) | set(code))}
        S.write_json(pend_p, pend)
        T.fyi(project, task.get("slug"), f"restart pending: altd runs code older than main ({len(pend['files'])} file(s) under "
                                        f"{'/'.join(d.rstrip('/') for d in DEPLOY_DIRS)} changed since {pend['since'][:16]}Z) — "
                                        f"`systemctl --user restart altitude` when convenient; L2 workers survive it (decision 42).")
        notes.append(f"restart pending ({len(code)} code files)")
    return notes


def cleanup_after_done(project: str, task: dict) -> list[str]:
    """After `done`: drop the L2 background session and the merged worktrees nobody owns any more.

    A worktree is never removed while a not-done task lists it, a live `claude agents` row runs in it, or git has it
    locked — "fully merged into origin/main" is also true of a branch with no commits yet, and on 2026-08-30 the old rule
    removed two running L2s' worktrees out from under them. If the live-session list cannot be read, nothing is removed."""
    import subprocess
    from . import improve
    repo = config.project_path(project)
    notes = []
    if task.get("agent_id"):
        notes.append("claude rm: " + engines.claude_rm(task["agent_id"])[:120])
    protected = {str(Path(t["worktree"])) for t in S.list_tasks(project)
                 if t.get("worktree") and t.get("slug") != task.get("slug") and t.get("state") not in ("done", "rejected")}
    try:
        protected |= {str(Path(a["cwd"])) for a in engines.claude_agents()
                      if a.get("cwd") and a.get("state") not in ("failed", "done", "stopped")}
    except RuntimeError as e:
        improve.system_fault("cleanup-agents", f"{project}: cannot list live sessions, removing nothing: {e}", project=project, task=task.get("slug"))
        return notes + [f"skipped worktree cleanup: {e}"]
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=str(repo), capture_output=True, text=True, timeout=60)
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo), capture_output=True, text=True, timeout=30)
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError) as e:
        improve.system_fault("cleanup-git", f"{project}: {e}", project=project, task=task.get("slug"))
        return notes + [f"git: {e}"]

    def owned(wt: str) -> bool:
        return any(wt == q or wt.startswith(q + "/") or q.startswith(wt + "/") for q in protected)

    wt, branch, locked = None, None, False
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            wt = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            branch = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif line == "locked" or line.startswith("locked "):
            locked = True
        elif line == "":
            if wt and branch and "/.claude/worktrees/" in wt and not locked and not owned(wt):
                merged = subprocess.run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=str(repo), capture_output=True).returncode == 0
                if merged:
                    rm = subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=str(repo), capture_output=True, text=True, timeout=60)
                    if rm.returncode == 0:
                        subprocess.run(["git", "branch", "-D", branch], cwd=str(repo), capture_output=True, text=True, timeout=30)
                        notes.append(f"removed merged worktree {Path(wt).name}")
                    else:
                        notes.append(f"could not remove {Path(wt).name}: {(rm.stderr or rm.stdout).strip()[:120]}")
            wt, branch, locked = None, None, False
    notes += pull_after_done(project, task)
    return notes
