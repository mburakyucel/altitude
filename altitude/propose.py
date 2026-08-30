"""Research → proposal → critique (ARCHITECTURE §4). Fresh sessions, schemas, other-engine critic for L."""
from __future__ import annotations
import json
from pathlib import Path

from . import config, engines, route, state as S, tasks as T
from .dispatch import _expand_entry, _split_top_level


def _normalise_proposal_files(project: str, proposal: dict) -> None:
    """Store expanded bare paths, rejecting entries that cannot name files at the proposal base."""
    repo = config.project_path(project)
    normalised = []
    for raw_entry in proposal.get("files") or []:
        entry = str(raw_entry).strip()
        expanded = _expand_entry(entry)
        parts = _split_top_level(entry)
        if len(parts) > 1:  # _expand_entry's literal-path fast path intentionally preserves a plain comma list.
            expanded_with_new = [
                (path, part.strip().endswith("(new)")) for part in parts for path in _expand_entry(part)
            ]
        else:
            expanded_with_new = [(path, entry.endswith("(new)")) for path in expanded]
        if not expanded_with_new:
            raise RuntimeError(f"proposal files entry {entry!r} is invalid: it expands to no repo-relative paths")
        for path, is_new in expanded_with_new:
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts or path.startswith("./"):
                raise RuntimeError(f"proposal files entry {entry!r} is invalid: {path!r} is not a bare repo-relative path")
            if not is_new and not (repo / relative).exists():
                raise RuntimeError(
                    f"proposal files entry {entry!r} is invalid: {path!r} does not exist at proposal base {repo}"
                )
            normalised.append(path)
    proposal["files"] = normalised


def run_proposal(project: str, slug: str, model: str | None = None) -> dict:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    request = (d / "request.md").read_text()
    prior = ""
    prev = sorted(d.glob("proposal-v*.md"), key=lambda p: int(p.stem.split("-v")[1]))
    prev_md = (d / "proposal.md") if (d / "proposal.md").exists() else (prev[-1] if prev else None)
    if prev_md:
        prior = "\n\n## Previous proposal (being revised)\n" + prev_md.read_text()[:6000]
        ev = [e for e in S.read_events(project, slug) if e.get("answer", "").lower().startswith("revise")]
        if ev and ev[-1].get("note"):
            prior += f"\n\nBurak's revision note: {ev[-1]['note']}"
        crits = sorted(d.glob("critique-v*.json"), key=lambda p: int(p.stem.split("-v")[1]))
        if crits:  # the other engine's issues with the previous proposal — address each explicitly
            c = S.read_json(crits[-1], {}) or {}
            if c.get("issues"):
                prior += "\n\n## Critic's issues with the previous proposal (verdict: %s) — address every one, say how\n" % c.get("verdict") + "\n".join(
                    f"- [{i.get('severity')}] {i.get('claim')}" + (f" — fix: {i.get('fix')}" if i.get("fix") else "") for i in c["issues"])
        parks = [e for e in S.read_events(project, slug) if e.get("kind") == "state" and e.get("to") == "parked" and e.get("reason")]
        if parks:
            prior += f"\n\n## Why the L3 parked the previous proposal (this is the revision brief)\n{parks[-1]['reason']}"
        prior += ("\n\nThis is revision %d. Produce a corrected proposal, not a defence of the previous one. Burak's feedback (the "
                  "'## Burak's feedback' sections of the request and the revision note above) is binding: address every point and open "
                  "the summary with what changed because of it." % (task.get("revisions", 0) + task.get("feedback_rounds", 0) + 1))
    prompt = (f"Task `{slug}` (class {task['class']}) for project `{project}`.\n\n## Request\n{request}{prior}\n\n"
              "Research the repository and produce the proposal as JSON per the schema. Cite the docs you relied on.")
    choice = route.pick_engine("proposal", forced="claude" if model else None, task=task)
    if choice["engine"] == "codex":  # decision 56: the proposal is Codex work — persona inline, read-only sandbox, strict schema
        res = engines.codex_exec((config.PERSONAS / "proposal.md").read_text() + "\n\n" + prompt, cwd=config.project_path(project),
                                 schema=config.SCHEMAS / "proposal.json", timeout=1200, effort=config.CODEX_EFFORT.get("proposal"))
        res.setdefault("turns", 1); res.setdefault("cost", 0.0)
    else:
        res = engines.claude_print(prompt, cwd=config.project_path(project), persona=config.PERSONAS / "proposal.md",
                                   permission_mode="plan", schema=config.SCHEMAS / "proposal.json", model=model or config.MODELS["proposal"],
                                   max_turns=60, timeout=1200)
    if res["error"] and not res["structured"]:
        raise RuntimeError(res["error"])
    p = res["structured"] or {}
    _normalise_proposal_files(project, p)
    S.write_json(d / "proposal.json", p)
    S.atomic_write(d / "proposal.md", render_proposal_md(p))
    with S.project_lock(project):
        t2 = S.load_task(project, slug); t2["proposal_engine"] = choice["engine"]; S.save_task(project, t2)
    S.append_event(project, slug, "proposal", turns=res["turns"], cost=res["cost"], cls=p.get("class"), engine=choice["engine"],
                   why=choice["why"], decision_needed=p.get("decision_needed"), hits=p.get("always_list_hits"))
    return p


def render_proposal_md(p: dict) -> str:
    lines = [f"# Proposal — {p.get('class', '?')}", "", p.get("summary", ""), "", "## Approach", "", p.get("approach", ""), ""]
    if p.get("alternatives"):
        lines += ["## Alternatives", ""] + [f"- **{a.get('option')}** — {a.get('why_not')}" for a in p["alternatives"]] + [""]
    est = p.get("estimate") or {}
    lines += ["## Estimate and envelope", "",
              f"- turns ≈ {est.get('turns')}, subagent launches ≈ {est.get('subagent_launches')}, L1s {est.get('l1_count', '?')}, tiers: {est.get('model_tiers', '?')}", ""]
    if p.get("always_list_hits"):
        lines += ["## Always-list hits", ""] + [f"- {h}" for h in p["always_list_hits"]] + [""]
    if p.get("files"):
        lines += ["## Files", ""] + [f"- `{f}`" for f in p["files"]] + [""]
    lines += ["## Verification", "", p.get("verification", ""), ""]
    if p.get("risks"):
        lines += ["## Risks", ""] + [f"- {r}" for r in p["risks"]] + [""]
    if p.get("citations"):
        lines += ["## Citations", ""] + [f"- {c}" for c in p["citations"]] + [""]
    if p.get("decision_needed"):
        lines += ["## Decision needed", "", p.get("question", ""), ""] + [f"{i + 1}. {o}" for i, o in enumerate(p.get("options") or [])] + [""]
    return "\n".join(lines)


def run_critic(project: str, slug: str) -> dict:
    """Other-engine critique (decision 13): the engine that did not write the proposal reads it, read-only.
    With a Codex proposal that is Claude (research tier); when the Claude window is exhausted the critique still runs,
    on a second Codex session, labelled `degraded` in the critique and the event (decision 56 — never silently)."""
    d = S.task_dir(project, slug)
    task = S.load_task(project, slug)
    prompt = ((config.PERSONAS / "critic.md").read_text() + "\n\n## Request\n" + (d / "request.md").read_text()
              + "\n\n## Proposal\n" + (d / "proposal.md").read_text()
              + "\n\nAnswer as JSON per the output schema.")
    eng, degraded = route.other(task.get("proposal_engine") or "claude"), None
    if eng == "claude" and engines.usage_hold():
        eng, degraded = "codex", f"same-engine critique: the Claude window is exhausted until {engines.usage_hold()}"
    if eng == "codex":
        res = engines.codex_exec(prompt, cwd=config.project_path(project), schema=config.SCHEMAS / "critic.json", effort=config.CODEX_EFFORT.get("critic"))
    else:
        res = engines.claude_print(prompt, cwd=config.project_path(project), permission_mode="plan", schema=config.SCHEMAS / "critic.json",
                                   model=config.MODELS["research"], max_turns=30, timeout=900)
    res["engine"] = eng
    if res["structured"] is None:
        # decision 36: no silent fallback to the same engine — the other-engine critique is the point. Raise it.
        from . import improve
        err = (res.get("error") or res.get("text") or "no structured output")[-600:]
        improve.system_fault("critic-engine", f"{eng} produced no critique: {err}", project=project, task=slug)
        c = {"verdict": "unavailable", "issues": [], "error": err}
    else:
        c = res["structured"]
    if degraded:
        c["degraded"] = degraded
    S.write_json(d / "critique.json", c)
    S.append_event(project, slug, "critique", verdict=c.get("verdict"), issues=len(c.get("issues") or []), engine=eng, degraded=degraded)
    return c
