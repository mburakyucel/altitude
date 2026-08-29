"""Dispatch an L2 as `claude --bg` in a worktree; poll `claude agents`; notice done (ARCHITECTURE §5)."""
from __future__ import annotations
import json
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


def build_brief(project: str, slug: str) -> str:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    proposal = (d / "proposal.md").read_text() if (d / "proposal.md").exists() else (d / "request.md").read_text()
    env = task["envelope"]
    dec = task.get("decision") or {}
    approval_note = f" — Burak chose: {dec['chosen']}" if dec.get("chosen") else ""
    policy = proj.get("approval", "default")
    merge_policy = {"default": "S/M: merge when review is addressed and CI is green. L and any always-list class (money, infra, IAM, migrations, deploy workflow): open the PR and stop.",
                    "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when review is addressed and CI is green."}.get(policy, policy)
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, cls=task["class"], project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        task_dir=d, merge_policy=merge_policy, never_list=project_never_list(config.project_path(project)),
        l1_in_flight=env["l1_in_flight"], subagent_launches=env["subagent_launches"], max_turns=env["max_turns"],
        verification=env.get("verification", "reviewer"), approval_note=approval_note, repo=config.project_path(project),
        branch=slug, proposal=proposal, **{"class": task["class"]})
    stack = rules.compile_section(rules.stack_rules(proj.get("stacks", [])), "Stack rules")
    return text + ("\n" + stack if stack else "")


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-dispatch settings passed with --settings: hooks that enforce the envelope, nothing global."""
    hooks = config.HOOKS
    settings = {"hooks": {
        "PreToolUse": [{"matcher": "Agent|Task", "hooks": [{"type": "command", "command": f"python3 {hooks / 'subagent_cap.py'}", "timeout": 10}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"python3 {hooks / 'edit_count.py'}", "timeout": 10}]}],
    }, "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key}}
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


def run(project: str, slug: str, model: str | None = None) -> dict:
    task = S.load_task(project, slug)
    if task["state"] != "approved":
        raise T.TransitionError(f"{slug} is {task['state']}, not approved")
    held = wip_hold(project)
    if held:
        raise T.TransitionError(held)
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
                            model=model or proj.get("l2_model"), settings=settings,
                            extra_env={"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project,
                                       "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2", "ALTITUDE_SESSION_KEY": f"{project}--{dispatch_id}"})
    agent = res.get("agent") or {}
    if res["returncode"] != 0 and not agent:
        S.append_event(project, slug, "dispatch-failed", stdout=res["stdout"][:300], stderr=res["stderr"][:300])
        raise RuntimeError(f"claude --bg failed: {res['stderr'][:300] or res['stdout'][:300]}")
    worktree = str(config.project_path(project) / ".claude" / "worktrees" / slug)
    T.dispatch(project, slug, dispatch_id=dispatch_id, session_id=agent.get("sessionId"), agent_id=agent.get("id"),
               worktree=worktree, branch=agent.get("branch") or slug)
    if agent.get("sessionId"):
        S.write_json(config.MONITOR_DIR / f"session-{agent['sessionId']}.json",
                     {"project": project, "slug": slug, "dispatch_id": dispatch_id, "level": "l2"})
    return {"dispatch_id": dispatch_id, "agent": agent, "stdout": res["stdout"]}


def resume_blocked(project: str, slug: str, answer: str) -> dict:
    task = S.load_task(project, slug)
    if not task.get("session_id"):
        raise T.TransitionError("no session to resume; dispatch again")
    name = f"{project}/{task['dispatch_id']}"
    res = engines.claude_resume_bg(name, task["session_id"], f"Burak's answer: {answer}\nContinue from your progress file; finish to *done* and rewrite the report.",
                                   cwd=config.project_path(project), persona=rules.compiled_persona("l2", project),
                                   max_turns=task["envelope"]["max_turns"], settings=S.task_dir(project, slug) / "settings.json",
                                   extra_env={"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2"})
    T.resume(project, slug, answer=answer)
    return res


def wip_hold(project: str) -> str | None:
    running = [t for t in S.list_tasks(project) if t["state"] == "running"]
    proj = config.project(project)
    if len(running) >= int(proj.get("wip", config.WIP_PER_PROJECT)):
        return f"WIP limit: {len(running)} running in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p) if t["state"] == "running")
    if total >= config.WIP_PER_MACHINE:
        return f"WIP limit: {total} running on this machine"
    from .monitor import quota_hold
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
        if t["state"] != "running":
            continue
        a = agents.get(t.get("session_id")) or by_id.get(t.get("agent_id"))
        live = {"status": a.get("status"), "state": a.get("state")} if a else None
        if a is None or a.get("state") == "done" or a.get("status") == "exited":
            finished.append({"task": t, "agent": a})
        S.write_json(config.MONITOR_DIR / f"live-{project}--{t['slug']}.json", {"at": S.now(), "agent": live})
    return finished
