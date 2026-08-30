"""Self-improvement (ARCHITECTURE §8): incidents, right-sized rules, scopes, promotion, audit input."""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import config, rules, state as S, tasks as T

MECHANISMS = ("rule", "instruction", "skill", "incident-only")
SCOPES = ("project", "stack", "global")


def _index_append(row: dict) -> None:
    with open(config.INCIDENT_INDEX, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def index() -> list[dict]:
    if not config.INCIDENT_INDEX.exists():
        return []
    out = []
    for line in config.INCIDENT_INDEX.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def next_incident_id(project: str) -> str:
    n = sum(1 for r in index() if r.get("project") == project)
    repo_dir = config.project_path(project) / "docs" / "incidents"
    if repo_dir.is_dir():
        n = max(n, len(list(repo_dir.glob("I-*.md"))))
    return f"I-{n + 1:03d}"


def new_incident(project: str, *, title: str, task: str | None, what: str, evidence: str, cause: str,
                 tags: list[str], generalizable: str = "unknown", mechanism: str = "incident-only",
                 scope: str = "project", rule: str | None = None, actor: str = "l3") -> dict:
    """Write the incident into the project's Altitude folder (the repo copy lands via the apply S-task)."""
    iid = next_incident_id(project)
    body = (config.TEMPLATES / "incident.md").read_text().format(
        id=iid, title=title, date=S.now()[:10], task=task or "-", project=project, what=what.strip(), evidence=evidence.strip(),
        cause=cause.strip(), generalizable=generalizable, mechanism=mechanism, scope=scope, rule=rule or "-", status="open" if rule else "watch")
    d = config.project_dir(project) / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    S.atomic_write(d / f"{iid}.md", body)
    row = {"at": S.now(), "project": project, "id": iid, "title": title, "task": task, "tags": sorted(set(tags)),
           "scope": scope, "mechanism": mechanism, "rule": rule, "cause": cause.strip()[:200]}
    with open(config.project_dir(project) / "incidents.jsonl", "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    _index_append(row)
    if task:
        S.append_event(project, task, "incident", id=iid, tags=row["tags"], mechanism=mechanism, scope=scope, by=actor)
    S.project_log(project, "incident", id=iid, title=title, tags=row["tags"])
    S.regen_state_md(project)
    return {"id": iid, "path": str(d / f"{iid}.md"), "matches_elsewhere": matches_elsewhere(project, row["tags"])}


def matches_elsewhere(project: str, tags: list[str]) -> list[dict]:
    """Incidents in other projects sharing a root-cause tag — the evidence needed to promote (decision 32)."""
    tags = set(tags)
    return [r for r in index() if r.get("project") != project and tags & set(r.get("tags") or [])]


def propose_rule(project: str, *, incident: str, title: str, text: str, mechanism: str, scope: str = "project",
                 where: str = "", prevents: str = "", effect: str = "", stack: str | None = None,
                 actor: str = "l3") -> dict:
    """Draft the ledger entry and create the S-task that applies it (FYI-with-veto, decision 10)."""
    if mechanism not in MECHANISMS or scope not in SCOPES:
        raise ValueError(f"mechanism in {MECHANISMS}, scope in {SCOPES}")
    if scope != "project" and not matches_elsewhere(project, _tags_for(project, incident)):
        raise ValueError("stack/global scope needs a matching incident in another project (decision 32); file it as project scope")
    if len(text) > 400 and mechanism == "rule":
        raise ValueError("rule text over 400 chars — that is a skill, not a rule")
    target_project = project if scope == "project" else "altitude"
    ledger = {"project": config.project_path(project) / "docs" / "RULES.md",
              "stack": config.RULES / "stacks" / (stack or "unknown") / "RULES.md",
              "global": config.RULES / "global" / "RULES.md"}[scope]
    d = config.project_dir(project) / "rules-pending"
    rid = rules.next_id(ledger, "S" if mechanism == "skill" else "R", pending=d)
    entry = rules.render_entry(rid, title, scope=scope if scope == "project" else f"{scope}:{stack or ''}".rstrip(":"),
                               where=where or ("CLAUDE.md" if mechanism == "rule" else ".claude/skills/" if mechanism == "skill" else "the owning section"),
                               origin=f"{incident} ({project})", prevents=prevents, effect=effect, status="probation", text=text.strip())
    rules.write_pending(d, rid, entry)
    inc_path = config.project_dir(project) / "incidents" / f"{incident}.md"
    request = (f"Apply rule {rid} from incident {incident} (scope: {scope}, mechanism: {mechanism}).\n\n"
               f"1. Add the incident file `docs/incidents/{incident}.md` with this content:\n\n```\n{inc_path.read_text() if inc_path.exists() else '(missing)'}\n```\n\n"
               f"2. Append this entry to `{ledger.name if scope == 'project' else ledger}` (create the ledger with a one-line header if missing):\n\n```\n{entry}\n```\n\n"
               f"3. Apply the {mechanism}: " + {
                   "rule": f"add the rule text to `CLAUDE.md` in the most fitting section, tagged `[{rid}]`.",
                   "instruction": f"add the instruction to the section/skill that owns that step, tagged `[{rid}]`.",
                   "skill": f"create `.claude/skills/<name>/SKILL.md` implementing the procedure, tagged `[{rid}]`, and reference it from the step that needs it.",
                   "incident-only": "nothing else — the incident file is the record."}[mechanism]
               + "\n\n4. Open the PR titled `rules: " + rid + " from " + incident + "` and merge it if the project policy allows docs-only merges. No other changes.")
    task = T.new(target_project, f"apply {rid} ({incident})", "S", request, actor=actor, source="improve")
    T.auto_approve(target_project, task["slug"], f"rule application from {incident}; veto = revert the PR")
    T.fyi(project, None, f"{incident} → {rid} ({mechanism}, {scope}): {title}. Applying via task `{task['slug']}` on {target_project}; veto = revert the PR.", actor=actor)
    return {"rule": rid, "task": task["slug"], "target_project": target_project, "ledger": str(ledger)}


def _tags_for(project: str, incident: str) -> list[str]:
    for r in index():
        if r.get("project") == project and r.get("id") == incident:
            return r.get("tags") or []
    return []


def audit_input(project: str) -> dict:
    """What the weekly audit turn gets: every rule with its incidents and recurrence, plus promotion candidates."""
    proj = config.project(project)
    all_rules = rules.global_rules() + rules.stack_rules(proj.get("stacks", [])) + rules.project_rules(config.project_path(project))
    inc = [r for r in index() if r.get("project") == project]
    by_rule: dict[str, list] = {}
    for r in inc:
        if r.get("rule"):
            by_rule.setdefault(r["rule"], []).append(r["id"])
    tag_counts: dict[str, dict[str, int]] = {}
    for r in index():
        for t in r.get("tags") or []:
            tag_counts.setdefault(t, {})
            tag_counts[t][r["project"]] = tag_counts[t].get(r["project"], 0) + 1
    promotions = [{"tag": t, "projects": c} for t, c in tag_counts.items() if len(c) > 1]
    return {"rules": [{**r, "incidents": by_rule.get(r["id"], [])} for r in all_rules], "incidents": inc[-30:],
            "promotion_candidates": promotions}
