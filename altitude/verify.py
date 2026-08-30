"""verify-report: the L2 says done; check it against GitHub and the files before L3 hears about it."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from . import config, engines, state as S


class VerifierFault(RuntimeError):
    """The verifier's own tooling failed (gh, network): not a verdict on the L2's work (decision 36)."""


def gh(args: list[str], cwd: Path) -> dict | list | None:
    try:
        p = subprocess.run(["gh"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=60, env=engines.clean_env())
    except (subprocess.SubprocessError, OSError) as e:
        raise VerifierFault(f"gh {' '.join(args[:3])}: {e}") from e
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        if "Could not resolve" in err or "no pull requests found" in err.lower() or "not found" in err.lower():
            return None  # a legitimately missing object, not a tooling failure
        raise VerifierFault(f"gh {' '.join(args[:3])} exit {p.returncode}: {err[-300:]}")
    try:
        return json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError as e:
        raise VerifierFault(f"gh {' '.join(args[:3])}: unparseable output") from e


def verify(project: str, slug: str) -> dict:
    try:
        return _verify(project, slug)
    except VerifierFault as e:
        from . import improve
        improve.system_fault("verifier", str(e), project=project, task=slug)
        return {"verdict": "fault", "problems": [f"verifier fault: {e}"], "signals": [], "spend": {}, "prs": [], "report": None, "fault": str(e)}


def _verify(project: str, slug: str) -> dict:
    d = S.task_dir(project, slug)
    repo = config.project_path(project)
    task = S.load_task(project, slug)
    out = {"verdict": "ok", "problems": [], "prs": [], "signals": [], "spend": {}}
    rep = S.read_json(d / "report.json")
    if not rep:
        out["problems"].append("report.json missing")
        out["verdict"] = "missing"
        if not (d / "report.md").exists():
            out["problems"].append("report.md missing")
        return _spend(out, task, d)
    for k in ("landed", "review", "deviations", "decisions", "fyi", "blocked", "follow_ups", "spend"):
        if k not in rep:
            out["problems"].append(f"report.json lacks `{k}`")
    landed = rep.get("landed") or {}
    for pr in landed.get("prs") or []:
        n = pr.get("number")
        info = gh(["pr", "view", str(n), "--json", "number,state,mergedAt,mergeCommit,headRefName"], repo) if n else None
        if info is None:
            out["problems"].append(f"PR #{n}: cannot read from GitHub")
            continue
        out["prs"].append(n)
        merged = info.get("state") == "MERGED"
        if pr.get("merged") and not merged:
            out["problems"].append(f"PR #{n} reported merged but GitHub says {info.get('state')}")
        if not merged and not rep.get("blocked") and pr.get("merged") is not False:
            out["problems"].append(f"PR #{n} not merged")
    for run in landed.get("main_runs") or []:
        rid = str(run.get("id", ""))
        info = gh(["run", "view", rid, "--json", "conclusion,status"], repo) if rid.isdigit() else None
        if info and info.get("conclusion") not in ("success", None) and run.get("conclusion") == "success":
            out["problems"].append(f"run {rid} reported success but is {info.get('conclusion')}")
    if not landed.get("prs") and not rep.get("blocked"):
        out["problems"].append("no PRs landed and not blocked")
    if rep.get("roadmap_complete") is False or not (d / "progress.md").exists():
        out["problems"].append("roadmap missing or not complete")
    # post-mortem signals (decision 30) — computed, not remembered
    if rep.get("deviations"):
        out["signals"].append(f"{len(rep['deviations'])} deviation(s)")
    if rep.get("blocked"):
        out["signals"].append(f"blocked: {rep['blocked'][:120]}")
    sp = rep.get("spend") or {}
    if sp.get("reverts"):
        out["signals"].append(f"{sp['reverts']} revert(s)")
    if int(sp.get("retries", 0) or 0) > 1:
        out["signals"].append(f"{sp['retries']} retries")
    env = task.get("envelope") or {}
    est = task.get("estimate") or {}
    if est.get("turns") and int(sp.get("turns", 0) or 0) > 2 * int(est["turns"]):
        out["signals"].append(f"turns {sp['turns']} > 2× estimate {est['turns']}")
    if env.get("subagent_launches") and int(sp.get("subagent_launches", 0) or 0) >= int(env["subagent_launches"]):
        out["signals"].append(f"subagent launches {sp['subagent_launches']} hit the cap {env['subagent_launches']}")
    tags = [r.get("tag") for r in rep.get("review") or [] if r.get("tag")]
    seen = _seen_tags(project, slug)
    rep_tags = sorted(set(t for t in tags if t in seen))
    if rep_tags:
        out["signals"].append(f"reviewer tags seen before in this project: {', '.join(rep_tags)}")
    if out["problems"]:
        out["verdict"] = "contradicted"
    if rep.get("blocked"):
        out["verdict"] = "blocked"
    out["report"] = {"blocked": rep.get("blocked"), "decisions": rep.get("decisions"), "fyi": rep.get("fyi"),
                     "follow_ups": rep.get("follow_ups"), "deviations": rep.get("deviations")}
    return _spend(out, task, d, sp)


def _spend(out: dict, task: dict, d: Path, sp: dict | None = None) -> dict:
    hook = S.read_json(config.MONITOR_DIR / f"counts-{task.get('session_id')}.json", {}) or {}
    out["spend"] = {"turns": (sp or {}).get("turns"), "subagent_launches_reported": (sp or {}).get("subagent_launches"),
                    "subagent_launches_hook": hook.get("subagent_launches"), "edits_hook": hook.get("edits"),
                    "retries": (sp or {}).get("retries"), "cap": (task.get("envelope") or {}).get("subagent_launches")}
    return out


def _seen_tags(project: str, current_slug: str) -> set[str]:
    seen = set()
    for t in S.list_tasks(project, include_archive=True):
        if t["slug"] == current_slug:
            continue
        rep = S.read_json(S.task_dir(project, t["slug"]) / "report.json")
        for r in (rep or {}).get("review") or []:
            if r.get("tag"):
                seen.add(r["tag"])
    return seen
