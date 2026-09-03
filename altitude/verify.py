"""verify-report: the L2 says done; check it against GitHub and the files before L3 hears about it."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from . import config, engines, state as S


class VerifierFault(RuntimeError):
    """The verifier's own tooling failed (gh, network), not the L2's work."""


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
        from . import incidents
        incidents.system_fault("verifier", str(e), project=project, task=slug)
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
        return _spend(out, project, task, d)
    for k in ("landed", "review", "blocked"):
        if k not in rep:
            out["problems"].append(f"report.json lacks `{k}`")
    review = rep.get("review") or []
    open_findings = [finding for finding in review if finding.get("disposition") == "open"]
    if open_findings and not rep.get("blocked"):
        out["problems"].append("open finding on an unblocked report")
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
    # Post-task signals are computed from the report, not remembered by a model.
    if rep.get("deviations"):
        out["signals"].append(f"{len(rep['deviations'])} deviation(s)")
    if rep.get("blocked"):
        out["signals"].append(f"blocked: {rep['blocked'][:120]}")
    if open_findings:
        out["signals"].append(f"{len(open_findings)} open review findings")
    sp = rep.get("spend") or {}
    if sp.get("reverts"):
        out["signals"].append(f"{sp['reverts']} revert(s)")
    if int(sp.get("retries", 0) or 0) > 1:
        out["signals"].append(f"{sp['retries']} retries")
    if out["problems"]:
        out["verdict"] = "contradicted"
    if rep.get("blocked"):
        out["verdict"] = "blocked"
    out["report"] = {"blocked": rep.get("blocked"), "decisions": rep.get("decisions"), "fyi": rep.get("fyi"),
                     "follow_ups": rep.get("follow_ups"), "deviations": rep.get("deviations")}
    return _spend(out, project, task, d, sp)


def _spend(out: dict, project: str, task: dict, d: Path, sp: dict | None = None) -> dict:
    counts_p = S.counts_path(project, task)
    hook = (S.read_json(counts_p, {}) if counts_p else {}) or {}
    out["spend"] = {"turns": (sp or {}).get("turns"), "subagent_launches_reported": (sp or {}).get("subagent_launches"),
                    "edits_hook": hook.get("edits"),
                    "retries": (sp or {}).get("retries")}
    return out
