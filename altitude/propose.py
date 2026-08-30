"""Research → proposal → critique (ARCHITECTURE §4). Fresh sessions, schemas, other-engine critic for L."""
from __future__ import annotations
import json
from pathlib import Path

from . import config, engines, state as S, tasks as T


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
    res = engines.claude_print(prompt, cwd=config.project_path(project), persona=config.PERSONAS / "proposal.md",
                               permission_mode="plan", schema=config.SCHEMAS / "proposal.json", model=model or config.MODELS["proposal"],
                               max_turns=60, timeout=1200)
    if res["error"] and not res["structured"]:
        raise RuntimeError(res["error"])
    p = res["structured"] or {}
    S.write_json(d / "proposal.json", p)
    S.atomic_write(d / "proposal.md", render_proposal_md(p))
    S.append_event(project, slug, "proposal", turns=res["turns"], cost=res["cost"], cls=p.get("class"),
                   decision_needed=p.get("decision_needed"), hits=p.get("always_list_hits"))
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
    """Other-engine critique (decision 13): Codex reads the proposal read-only."""
    d = S.task_dir(project, slug)
    prompt = ((config.PERSONAS / "critic.md").read_text() + "\n\n## Request\n" + (d / "request.md").read_text()
              + "\n\n## Proposal\n" + (d / "proposal.md").read_text()
              + "\n\nAnswer as JSON per the output schema.")
    res = engines.codex_exec(prompt, cwd=config.project_path(project), schema=config.SCHEMAS / "critic.json")
    res["engine"] = "codex"
    if res["structured"] is None:
        # decision 36: no silent fallback to the same engine — the other-engine critique is the point. Raise it.
        from . import improve
        err = (res.get("error") or res.get("text") or "no structured output")[-600:]
        improve.system_fault("critic-engine", f"codex exec produced no critique: {err}", project=project, task=slug)
        c = {"verdict": "unavailable", "issues": [], "error": err}
    else:
        c = res["structured"]
    S.write_json(d / "critique.json", c)
    S.append_event(project, slug, "critique", verdict=c.get("verdict"), issues=len(c.get("issues") or []), engine=res.get("engine"))
    return c
