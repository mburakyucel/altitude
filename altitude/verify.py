"""verify-report: the L2 says done; check it against GitHub and the files before L3 hears about it."""
from __future__ import annotations
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import config, engines, git_policy, state as S


class VerifierFault(RuntimeError):
    """The verifier's own tooling failed (gh, network), not the L2's work."""


class RateLimited(VerifierFault):
    """GitHub refused the read for the account's rate limit: a wait, not a fault (#814)."""


class Unreachable(VerifierFault):
    """gh could not reach GitHub (a timeout, a connection, DNS or TLS failure, a gateway error). The caller retries
    on a later tick and files the fault only once it persists (I-20261018-220735)."""


#: gh's wording when GitHub refuses a request for a primary or secondary rate limit.
_RATE_LIMIT = re.compile(r"rate limit (already )?exceeded|exceeded a secondary rate limit", re.I)
#: GitHub's REST pull request projected onto the GraphQL field names the readers share. REST has its own
#: quota, separate from the GraphQL budget that landings and workers share (#814). An open PR's
#: merge_commit_sha is GitHub's test merge, so it is a merge commit only once the PR merged.
PULL_JQ = ('{number, url: .html_url, state: (if .merged_at then "MERGED" elif .state == "closed" then "CLOSED" '
           'else "OPEN" end), baseRefName: .base.ref, headRefName: .head.ref, headRefOid: .head.sha, '
           'mergedAt: .merged_at, mergeCommit: (if .merged_at then {oid: .merge_commit_sha} else null end)}')


#: gh's wording when the request never got an answer from GitHub: Go's network errors and GitHub's gateway errors.
_UNREACHABLE = re.compile(r"error connecting to|connection (reset|refused)|i/o timeout|no such host|dial tcp|"
                          r"TLS handshake|tls: |context deadline exceeded|timeout awaiting|HTTP 50[234]", re.I)


def rate_limited(output: str) -> bool:
    """Whether a failed gh command's output is GitHub refusing it for a rate limit."""
    return bool(_RATE_LIMIT.search(output or ""))


OPEN_FINDINGS = "open finding on an unblocked report"


def open_findings(report: dict | None) -> list[dict]:
    """Review findings the owner handed back instead of fixing or dismissing."""
    return [finding for finding in (report or {}).get("review") or [] if finding.get("disposition") == "open"]


def report_sections(report: dict, errors: list[str]) -> dict:
    """The report's sections in their schema shapes. A section of another shape names its field in `errors` and
    reads as empty, so readers show the rest and the verifier hands the report back (I-20261019-110932)."""
    def objects(section: dict, key: str, field: str) -> list:
        rows = section.get(key) or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            errors.append(f"report.{field}: expected a list of objects")
            return []
        return rows

    def items(key: str) -> list:
        value = report.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(f"report.{key}: expected a list")
            return []
        return value

    landed = report.get("landed") or {}
    if not isinstance(landed, dict):
        errors.append("report.landed: expected an object"); landed = {}
    prs = objects(landed, "prs", "landed.prs")
    objects(landed, "main_runs", "landed.main_runs")
    objects(report, "review", "review")
    blocked = report.get("blocked")
    if blocked is not None and not isinstance(blocked, str):
        errors.append("report.blocked: expected text"); blocked = None
    spend = report.get("spend") or {}
    if not isinstance(spend, dict):
        errors.append("report.spend: expected an object"); spend = {}
    return {"prs": prs, "blocked": blocked, "spend": spend,
            **{key: items(key) for key in ("decisions", "fyi", "follow_ups", "deviations")}}


def gh(args: list[str], cwd: Path) -> dict | list | None:
    try:
        p = subprocess.run(["gh"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=60, env=engines.clean_env())
    except subprocess.TimeoutExpired as e:
        raise Unreachable(f"gh {' '.join(args[:3])}: {e}") from e
    except (subprocess.SubprocessError, OSError) as e:
        raise VerifierFault(f"gh {' '.join(args[:3])}: {e}") from e
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        if rate_limited(err):
            raise RateLimited(f"gh {' '.join(args[:3])}: {err[-300:]}")
        if _UNREACHABLE.search(err):
            raise Unreachable(f"gh {' '.join(args[:3])}: {err[-300:]}")
        if "Could not resolve" in err or "no pull requests found" in err.lower() or "not found" in err.lower():
            return None  # a legitimately missing object, not a tooling failure
        raise VerifierFault(f"gh {' '.join(args[:3])} exit {p.returncode}: {err[-300:]}")
    try:
        return json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError as e:
        raise VerifierFault(f"gh {' '.join(args[:3])}: unparseable output") from e


def verify(project: str, slug: str) -> dict:
    """The verdict on the task's report. Unreachable propagates: the caller judges whether GitHub stays unreachable."""
    from . import tasks as T
    owner = T.report_owner(S.load_task(project, slug))
    try:
        return {**_verify(project, slug), "owner": owner}
    except RateLimited as e:  # the next tick verifies again
        return {"verdict": "rate-limited", "problems": [str(e)], "signals": [], "spend": {}, "prs": [], "report": None}
    except Unreachable:
        raise
    except VerifierFault as e:
        return fault(project, slug, e, owner)


def fault(project: str, slug: str, e: VerifierFault, owner: dict) -> dict:
    """File the verifier's own failure as a system fault for the report `owner` and return its verdict."""
    from . import incidents
    incidents.system_fault("verifier", str(e), project=project, task=slug, expected_owner=owner)
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
    from . import tasks as T
    if task.get("report_after") and not T.report_current(task, d / "report.json"):
        out["problems"].append("report predates follow-up work; refresh report.json")
        out["verdict"] = "contradicted"
        return _spend(out, project, task, d)
    report_sections(rep, out["problems"])
    if out["problems"]:  # the owner corrects its report's shape before the rest can be read
        out["verdict"] = "contradicted"
        return _spend(out, project, task, d)
    for k in ("landed", "review", "blocked"):
        if k not in rep:
            out["problems"].append(f"report.json lacks `{k}`")
    unresolved = open_findings(rep)
    if unresolved and not rep.get("blocked"):
        out["problems"].append(OPEN_FINDINGS)
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
        info = gh(["api", f"repos/{{owner}}/{{repo}}/pulls/{n}", "--jq", PULL_JQ], repo) if n else None
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
                    project, task, delivery, (info.get("mergeCommit") or {}).get("oid") if merged else None))
            if merged and not _same_commit(pr.get("merge_sha"), (info.get("mergeCommit") or {}).get("oid")):
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
    if unresolved:
        out["signals"].append(f"{len(unresolved)} open review findings")
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


def _same_commit(reported: str | None, actual: str | None) -> bool:
    """The report names GitHub's merge commit in full or by a prefix of at least seven characters (#456)."""
    return bool(reported and actual and len(reported) >= 7 and actual.startswith(reported))


def _worktree_problems(project: str, task: dict, delivery: dict, merge_sha: str | None) -> list[str]:
    worktree = config.task_worktree(project, task["slug"])
    if not task.get("worktree") or not worktree.is_dir():
        return ["task worktree unavailable; cannot verify current delivery"]

    def git(args):
        try:
            return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True,
                                  timeout=60, env=git_policy.task_git(worktree, engines.clean_env()))
        except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
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
