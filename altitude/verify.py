"""verify-report: the L2 says done; check it against GitHub and the files before L3 hears about it."""
from __future__ import annotations
import json
import subprocess
from datetime import datetime
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
    delivery = task.get("delivery") or {}
    if delivery:
        out["delivery"] = delivery
        reported = {pr.get("number") for pr in landed.get("prs") or []}
        for number in task.get("prs") or []:
            if number not in reported:
                out["problems"].append(f"recorded PR #{number} missing from report")
        if not delivery.get("number") or not delivery.get("head"):
            out["problems"].append("current delivery has unpublished work; run alt land before completing")
        if (d / "report.json").stat().st_mtime < datetime.fromisoformat(delivery["at"]).timestamp():
            out["problems"].append("report predates the current delivery; refresh report.json")
    for pr in landed.get("prs") or []:
        n = pr.get("number")
        info = gh(["pr", "view", str(n), "--json", "number,state,mergedAt,mergeCommit,headRefName,headRefOid"], repo) if n else None
        if info is None:
            out["problems"].append(f"PR #{n}: cannot read from GitHub")
            continue
        out["prs"].append(n)
        merged = info.get("state") == "MERGED"
        if delivery:
            if n == delivery.get("number"):
                if info.get("headRefOid") != delivery.get("head"):
                    out["problems"].append(f"PR #{n}: GitHub head differs from the current delivery; run alt land again")
                out["problems"].extend(_worktree_problems(
                    task, delivery, (info.get("mergeCommit") or {}).get("oid") if merged else None))
            if merged and pr.get("merge_sha") != (info.get("mergeCommit") or {}).get("oid"):
                out["problems"].append(f"PR #{n}: reported merge SHA differs from GitHub")
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


def _worktree_problems(task: dict, delivery: dict, merge_sha: str | None) -> list[str]:
    worktree = task.get("worktree")
    if not worktree or not Path(worktree).is_dir():
        return ["task worktree unavailable; cannot verify current delivery"]

    def git(args):
        try:
            return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True,
                                  timeout=60, env=engines.clean_env())
        except (subprocess.SubprocessError, OSError) as e:
            raise VerifierFault(f"cannot inspect task worktree: {e}") from e

    problems = []
    for args, expected, problem in (
        (["rev-parse", "HEAD"], delivery["head"], "task HEAD differs from the current delivery; run alt land again"),
        (["status", "--porcelain", "--untracked-files=all"], "", "task worktree has uncommitted work"),
    ):
        result = git(args)
        if result.returncode:
            raise VerifierFault(f"cannot inspect task worktree: {result.stderr.strip()[-300:]}")
        if result.stdout.strip() != expected:
            if (args[0] == "rev-parse" and merge_sha
                    and git(["merge-base", "--is-ancestor", merge_sha, "HEAD"]).returncode == 0
                    and git(["merge-base", "--is-ancestor", "HEAD", "origin/main"]).returncode == 0):
                continue  # A clean branch reconciled to main has no unpublished delivery.
            problems.append(problem)
    return problems


def _spend(out: dict, project: str, task: dict, d: Path, sp: dict | None = None) -> dict:
    counts_p = S.counts_path(project, task)
    hook = (S.read_json(counts_p, {}) if counts_p else {}) or {}
    out["spend"] = {"turns": (sp or {}).get("turns"), "subagent_launches_reported": (sp or {}).get("subagent_launches"),
                    "edits_hook": hook.get("edits"),
                    "retries": (sp or {}).get("retries")}
    return out
