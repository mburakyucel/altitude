"""verify-report: the L2 says done; check it against GitHub and the files before L3 hears about it."""
from __future__ import annotations
import json
import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import config, engines, state as S


_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
TRUSTED_REMOTE_PENDING = (
    "trusted remote landing integration is pending; local-suite and generic GitHub-actions proof "
    "cannot authorize landing closeout"
)


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


def report_fingerprint(report: object) -> str:
    """Bind clean-close evidence to the exact parsed report, independent of JSON layout."""
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _merge_records(project: str, slug: str) -> tuple[dict[int, dict], list[str]]:
    records: dict[int, dict] = {}
    problems: list[str] = []
    for path in S.task_dir(project, slug).glob("merge-request-*.json"):
        try:
            rec = S.read_json(path, None)
        except (OSError, ValueError) as exc:
            problems.append(f"durable merge record {path.name} is unreadable: {exc}")
            continue
        if not isinstance(rec, dict) or type(rec.get("pr")) is not int or rec["pr"] <= 0:
            problems.append(f"durable merge record {path.name} is malformed")
            continue
        if rec["pr"] in records:
            problems.append(f"PR #{rec['pr']} has duplicate durable merge records")
            continue
        records[rec["pr"]] = rec
    return records, problems


def _landing_provenance(project: str, slug: str, task: dict, report: dict, repo: Path) -> tuple[list[str], list[int], dict]:
    """Prove reported landings against task-owned coordinator state and live GitHub evidence."""
    # R-014 has defined the only future authoritative evidence shape, but the
    # landing consumer is not integrated yet.  Keep evaluating the old record
    # for useful diagnostics while making it impossible for either legacy
    # gate mode to produce clean-close authority.
    problems: list[str] = [TRUSTED_REMOTE_PENDING]
    landed = report.get("landed")
    if not isinstance(landed, dict):
        return (["report landed section is malformed"], [], {
            "version": 1, "valid": False, "project": project, "slug": slug,
            "dispatch_id": task.get("dispatch_id"),
            "task_attempt": task.get("attempt"), "report_fingerprint": report_fingerprint(report),
            "prs": [], "main_runs": [],
        })
    report_prs = landed.get("prs")
    if not isinstance(report_prs, list):
        report_prs = []
        problems.append("reported PRs are malformed")
    records, record_problems = _merge_records(project, slug)
    problems.extend(record_problems)
    dispatch_id = task.get("dispatch_id")
    task_attempt = task.get("attempt")
    if not isinstance(dispatch_id, str) or not dispatch_id:
        problems.append("task has no current dispatch generation for merge provenance")
    if type(task_attempt) is not int:
        problems.append("task has no current attempt for merge provenance")

    reported_numbers: list[int] = []
    proven_prs: list[dict] = []
    merge_to_pr: dict[str, int] = {}
    github_merge_shas: set[str] = set()
    for item in report_prs:
        if not isinstance(item, dict) or type(item.get("number")) is not int or item["number"] <= 0:
            problems.append("reported PR entry has no positive integer number")
            continue
        number = item["number"]
        reported_numbers.append(number)
        if item.get("merged") is not True:
            problems.append(f"PR #{number} is not reported merged")
        report_merge = str(item.get("merge_sha") or "")
        if not _SHA.fullmatch(report_merge):
            problems.append(f"PR #{number} has no exact reported merge SHA")
        rec = records.get(number)
        result = rec.get("result") if isinstance(rec, dict) and isinstance(rec.get("result"), dict) else {}
        rec_head = str((rec or {}).get("head_sha") or "")
        rec_merge = str(result.get("merge_sha") or "")
        gate_mode = result.get("gate_mode") or (rec or {}).get("gate_mode")
        candidate_gate = result.get("candidate_gate") or (rec or {}).get("candidate_gate")
        if not isinstance(rec, dict):
            problems.append(f"PR #{number} has no task-owned durable merge coordinator record")
        else:
            if (rec.get("project"), rec.get("slug"), rec.get("pr")) != (project, slug, number):
                problems.append(f"PR #{number} durable merge record is not owned by this task")
            if rec.get("state") != "merged" or result.get("merged") is not True:
                problems.append(f"PR #{number} durable merge coordinator record is not merged")
            if rec.get("dispatch_id") != dispatch_id or rec.get("task_attempt") != task_attempt:
                problems.append(f"PR #{number} durable merge record is not bound to the current dispatch")
            if not _SHA.fullmatch(rec_head) or result.get("head_sha") != rec_head:
                problems.append(f"PR #{number} durable merge record has no exact head binding")
            if not _SHA.fullmatch(rec_merge) or rec_merge != report_merge:
                problems.append(f"PR #{number} reported merge SHA differs from its durable merge record")
            if gate_mode == "github-actions":
                github_merge_shas.add(report_merge)
                if candidate_gate is not None:
                    problems.append(f"PR #{number} GitHub-actions merge carries unexpected local gate evidence")
            elif gate_mode == "local-suite":
                gate = candidate_gate if isinstance(candidate_gate, dict) else {}
                if (gate.get("sandboxed") is not True or gate.get("passed") is not True
                        or gate.get("base_sha") != rec.get("base_sha")
                        or gate.get("head_sha") != rec_head
                        or type(gate.get("tests")) is not int or gate["tests"] <= 0):
                    problems.append(f"PR #{number} has no exact passed trusted local candidate-gate proof")
            else:
                problems.append(f"PR #{number} durable merge record has no trusted gate mode")
        info = gh(["pr", "view", str(number), "--json",
                   "number,state,mergedAt,mergeCommit,headRefName,headRefOid"], repo)
        if not isinstance(info, dict):
            problems.append(f"PR #{number}: cannot read from GitHub")
            continue
        github_merge = info.get("mergeCommit") if isinstance(info.get("mergeCommit"), dict) else {}
        github_merge_sha = str(github_merge.get("oid") or "")
        github_head = str(info.get("headRefOid") or "")
        if info.get("number") != number:
            problems.append(f"PR #{number}: GitHub returned a different PR")
        if info.get("state") != "MERGED":
            problems.append(f"PR #{number} reported merged but GitHub says {info.get('state')}")
        if github_head != rec_head:
            problems.append(f"PR #{number} GitHub head differs from the current durable head binding")
        if github_merge_sha != report_merge or github_merge_sha != rec_merge:
            problems.append(f"PR #{number} merge SHA does not exactly match report, coordinator, and GitHub")
        if _SHA.fullmatch(report_merge):
            if report_merge in merge_to_pr:
                problems.append(f"PR #{number} reuses another reported merge SHA")
            merge_to_pr[report_merge] = number
        proven_prs.append({"number": number, "record_generation": (rec or {}).get("generation"),
                           "head_sha": rec_head, "merge_sha": report_merge,
                           "gate_mode": gate_mode, "candidate_gate": candidate_gate})

    if len(reported_numbers) != len(set(reported_numbers)):
        problems.append("report contains duplicate PR numbers")
    current_record_numbers = sorted(number for number, rec in records.items()
                                    if rec.get("dispatch_id") == dispatch_id and rec.get("state") == "merged")
    if sorted(set(reported_numbers)) != current_record_numbers:
        problems.append("reported PRs do not exactly match current task-owned merged coordinator records")
    if not report_prs:
        problems.append("no PRs landed and not blocked")

    report_runs = landed.get("main_runs")
    if not isinstance(report_runs, list):
        report_runs = []
        problems.append("reported main runs are malformed")
    proven_runs: list[dict] = []
    covered_merges: list[str] = []
    reported_run_ids: list[int] = []
    for run in report_runs:
        raw_id = run.get("id") if isinstance(run, dict) else None
        if not isinstance(raw_id, str) or not raw_id.isdigit() or int(raw_id) <= 0:
            problems.append("reported main run id is not numeric")
            continue
        run_id = int(raw_id)
        reported_run_ids.append(run_id)
        if run.get("conclusion") != "success":
            problems.append(f"main run {raw_id} is not reported successful")
        info = gh(["run", "view", raw_id, "--json", "databaseId,headSha,status,conclusion"], repo)
        if not isinstance(info, dict):
            problems.append(f"main run {raw_id} is unavailable on GitHub")
            continue
        head_sha = str(info.get("headSha") or "")
        if info.get("databaseId") != run_id:
            problems.append(f"main run {raw_id} GitHub id does not match")
        if info.get("status") != "completed":
            problems.append(f"main run {raw_id} is not completed")
        if info.get("conclusion") != "success":
            problems.append(f"main run {raw_id} did not conclude success")
        if head_sha not in github_merge_shas:
            problems.append(f"main run {raw_id} head does not exactly match a GitHub-actions merge SHA")
        else:
            covered_merges.append(head_sha)
        proven_runs.append({"id": run_id, "head_sha": head_sha,
                            "status": info.get("status"), "conclusion": info.get("conclusion")})
    if len(reported_run_ids) != len(set(reported_run_ids)):
        problems.append("report contains duplicate main run ids")
    if sorted(covered_merges) != sorted(github_merge_shas):
        problems.append("successful main runs do not cover each GitHub-actions merge SHA exactly once")
    if not github_merge_shas and report_runs:
        problems.append("local-suite landings must report zero GitHub main runs")

    evidence = {"version": 1, "valid": not problems, "project": project, "slug": slug,
                "dispatch_id": dispatch_id,
                "task_attempt": task_attempt, "report_fingerprint": report_fingerprint(report),
                "gate_modes": sorted({str(item.get("gate_mode") or "") for item in proven_prs}),
                "prs": proven_prs, "main_runs": proven_runs}
    return problems, reported_numbers, evidence


def clean_close_provenance_matches(project: str, slug: str, verdict: dict,
                                   task: dict, report: dict) -> bool:
    """Re-bind verifier evidence to live task/report/coordinator state before mechanical close."""
    # No current verdict carries the immutable base-attached R-014 artifact.
    # In particular, never grandfather generic checks or a local-suite proof.
    return False
    evidence = verdict.get("landing_provenance") if isinstance(verdict, dict) else None
    if (not isinstance(evidence, dict) or evidence.get("valid") is not True
            or evidence.get("project") != project or evidence.get("slug") != slug
            or evidence.get("dispatch_id") != task.get("dispatch_id")
            or evidence.get("task_attempt") != task.get("attempt")
            or evidence.get("report_fingerprint") != report_fingerprint(report)):
        return False
    records, record_problems = _merge_records(project, slug)
    if record_problems:
        return False
    report_prs = ((report.get("landed") or {}).get("prs")
                  if isinstance(report.get("landed"), dict) else None)
    report_runs = ((report.get("landed") or {}).get("main_runs")
                   if isinstance(report.get("landed"), dict) else None)
    if not isinstance(report_prs, list) or not isinstance(report_runs, list):
        return False
    evidence_prs = evidence.get("prs")
    evidence_runs = evidence.get("main_runs")
    if not isinstance(evidence_prs, list) or not isinstance(evidence_runs, list):
        return False
    if any(not isinstance(item, dict) or type(item.get("number")) is not int
           for item in evidence_prs):
        return False
    current_record_numbers = sorted(number for number, rec in records.items()
                                    if rec.get("dispatch_id") == task.get("dispatch_id")
                                    and rec.get("state") == "merged")
    if current_record_numbers != sorted(item["number"] for item in evidence_prs):
        return False
    expected_pairs = []
    for item in evidence_prs:
        if not isinstance(item, dict) or type(item.get("number")) is not int:
            return False
        rec = records.get(item["number"])
        result = rec.get("result") if isinstance(rec, dict) and isinstance(rec.get("result"), dict) else {}
        gate_mode = result.get("gate_mode") or (rec or {}).get("gate_mode")
        candidate_gate = result.get("candidate_gate") or (rec or {}).get("candidate_gate")
        if (not isinstance(rec, dict) or rec.get("generation") != item.get("record_generation")
                or rec.get("state") != "merged" or rec.get("dispatch_id") != task.get("dispatch_id")
                or rec.get("task_attempt") != task.get("attempt")
                or rec.get("head_sha") != item.get("head_sha")
                or result.get("head_sha") != item.get("head_sha")
                or result.get("merge_sha") != item.get("merge_sha")
                or gate_mode != item.get("gate_mode")
                or candidate_gate != item.get("candidate_gate")):
            return False
        if gate_mode == "github-actions":
            if candidate_gate is not None:
                return False
        elif gate_mode == "local-suite":
            gate = candidate_gate if isinstance(candidate_gate, dict) else {}
            if (gate.get("sandboxed") is not True or gate.get("passed") is not True
                    or gate.get("base_sha") != rec.get("base_sha")
                    or gate.get("head_sha") != rec.get("head_sha")
                    or type(gate.get("tests")) is not int or gate["tests"] <= 0):
                return False
        else:
            return False
        expected_pairs.append((item["number"], item.get("merge_sha")))
    report_pairs = [(item.get("number"), item.get("merge_sha")) for item in report_prs
                    if isinstance(item, dict)]
    if sorted(expected_pairs) != sorted(report_pairs) or len(report_pairs) != len(report_prs):
        return False
    evidence_run_pairs = [(str(item.get("id")), item.get("head_sha")) for item in evidence_runs
                          if isinstance(item, dict) and item.get("status") == "completed"
                          and item.get("conclusion") == "success"]
    report_run_ids = [str(item.get("id")) for item in report_runs
                      if isinstance(item, dict) and item.get("conclusion") == "success"]
    github_heads = [sha for item, (_, sha) in zip(evidence_prs, expected_pairs)
                    if item.get("gate_mode") == "github-actions"]
    return (len(evidence_run_pairs) == len(evidence_runs) == len(report_runs)
            and sorted(run_id for run_id, _ in evidence_run_pairs) == sorted(report_run_ids)
            and sorted(head for _, head in evidence_run_pairs) == sorted(github_heads))


def _verify(project: str, slug: str) -> dict:
    d = S.task_dir(project, slug)
    repo = config.project_path(project)
    task = S.load_task(project, slug)
    out = {"verdict": "ok", "problems": [], "prs": [], "signals": [], "spend": {}}
    report_paths = (d / "report.md", d / "report.json")
    missing = [path.name for path in report_paths if not path.exists()]
    if missing:
        out["problems"].extend(f"{name} missing" for name in missing)
        out["verdict"] = "missing"
        return _spend(out, project, task, d)
    boundary_ns = task.get("report_not_before_ns")
    boundary = task.get("report_not_before")
    if boundary_ns is not None:
        try:
            threshold_ns = int(boundary_ns)
            stale = ([path.name for path in report_paths if path.stat().st_mtime_ns < threshold_ns]
                     if threshold_ns > 0 else [path.name for path in report_paths])
        except (OSError, TypeError, ValueError):
            stale = [path.name for path in report_paths]
    elif boundary:
        try:
            threshold = datetime.fromisoformat(boundary).timestamp()
            stale = [path.name for path in report_paths if path.stat().st_mtime < threshold]
        except (OSError, TypeError, ValueError):
            stale = [path.name for path in report_paths]
    else:
        stale = []
    if boundary_ns is not None or boundary:
        if stale:
            out["problems"].extend(f"{name} predates the current report generation" for name in stale)
            out["verdict"] = "missing"
            return _spend(out, project, task, d)
    rep = S.read_json(d / "report.json")
    if not rep:
        out["problems"].append("report.json missing or invalid")
        out["verdict"] = "missing"
        return _spend(out, project, task, d)
    for k in ("landed", "review", "deviations", "decisions", "fyi", "blocked", "follow_ups", "spend"):
        if k not in rep:
            out["problems"].append(f"report.json lacks `{k}`")
    review = rep.get("review") or []
    open_findings = [finding for finding in review if finding.get("disposition") == "open"]
    if open_findings and not rep.get("blocked"):
        out["problems"].append("open finding on an unblocked report")
    landed = rep.get("landed") if isinstance(rep.get("landed"), dict) else {}
    if not rep.get("blocked"):
        landing_problems, reported_prs, provenance = _landing_provenance(
            project, slug, task, rep, repo)
        out["problems"].extend(landing_problems)
        out["prs"].extend(reported_prs)
        out["landing_provenance"] = provenance
    if rep.get("roadmap_complete") is False or not (d / "progress.md").exists():
        out["problems"].append("roadmap missing or not complete")
    # post-mortem signals (decision 30) — computed, not remembered
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
    env = task.get("envelope") or {}
    est = task.get("estimate") or {}
    if est.get("turns") and int(sp.get("turns", 0) or 0) > 2 * int(est["turns"]):
        out["signals"].append(f"turns {sp['turns']} > 2× estimate {est['turns']}")
    if env.get("subagent_launches") and int(sp.get("subagent_launches", 0) or 0) >= int(env["subagent_launches"]):
        out["signals"].append(f"subagent launches {sp['subagent_launches']} hit the cap {env['subagent_launches']}")
    tags = [r.get("tag") for r in review if r.get("tag")]
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
    return _spend(out, project, task, d, sp)


def _spend(out: dict, project: str, task: dict, d: Path, sp: dict | None = None) -> dict:
    dispatch_id = task.get("dispatch_id")
    counts_p = config.MONITOR_DIR / f"counts-{project}--{dispatch_id}.json" if dispatch_id else None
    if not counts_p or not counts_p.exists():
        counts_p = config.MONITOR_DIR / f"counts-{task.get('session_id')}.json"
    hook = S.read_json(counts_p, {}) or {}
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
