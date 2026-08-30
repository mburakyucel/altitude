"""Compact, fault-tolerant, read-only task orientation (decision 47, tier 0).

``wip_hold`` reports only the file-lease reason: it is a read-only subset of
``dispatch.wip_hold``, which is the dispatcher's state-advancing check.
"""
from __future__ import annotations

from . import config, dispatch, l1, state as S, verify


_TASK_FIELDS = (
    "state", "class", "title", "attempt", "dispatch_id", "session_id", "agent_id", "source",
    "hold_merge", "blocked_reason", "updated", "worktree", "branch",
)
_PR_FIELDS = "number,state,mergedAt,mergeCommit,headRefName,headRefOid,statusCheckRollup"
_RUN_FIELDS = "databaseId,headSha,conclusion,status,workflowName"
_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}


def _error(errors: list[str], source: str, exc: object) -> None:
    errors.append(f"{source}: {str(exc)[:240]}")


def _check_summary(rollup: object) -> dict:
    checks = rollup if isinstance(rollup, list) else []
    summary = {"total": len(checks), "passed": 0, "failed": 0, "pending": 0, "failing": []}
    for check in checks:
        check = check if isinstance(check, dict) else {}
        result = str(check.get("conclusion") or check.get("state") or "").upper()
        if result in _PASSED:
            summary["passed"] += 1
        elif result in _FAILED:
            summary["failed"] += 1
            summary["failing"].append(str(check.get("name") or check.get("context") or "unknown"))
        else:
            summary["pending"] += 1
    return summary


def _pr_numbers(task: dict, runs: list[dict], report: object, errors: list[str]) -> list[int]:
    task_prs = task.get("prs") or []
    if not isinstance(task_prs, list):
        _error(errors, "prs", "task PR list is not a list")
        task_prs = []
    numbers = set()
    for value in task_prs + [run.get("pr") for run in runs]:
        if type(value) is int or (isinstance(value, str) and value.isdigit()):
            numbers.add(int(value))
        elif value is not None:
            _error(errors, "prs", f"invalid PR number {value!r}")
    landed = report.get("landed") if isinstance(report, dict) else None
    report_prs = landed.get("prs") if isinstance(landed, dict) else []
    report_prs = report_prs if isinstance(report_prs, list) else [report_prs]
    for entry in report_prs:
        value = entry.get("number") if isinstance(entry, dict) else entry
        if type(value) is int or (isinstance(value, str) and value.isdigit()):
            numbers.add(int(value))
    return sorted(numbers)


def status(project: str, slug: str) -> dict:
    """Return read-only orientation signals; faults never escape.

    ``wip_hold`` is only the file-lease reason, a read-only subset of the
    dispatcher's state-advancing ``dispatch.wip_hold`` check.
    ``paths`` is a raw staging lease; ``hold_paths`` is its narrowed hold lease.
    """
    errors: list[str] = []
    out = {
        "project": project, "slug": slug,
        **{field: None for field in _TASK_FIELDS},
        "envelope": None, "counts": None, "envelope_file": None, "l1_runs": None,
        "lease": [], "other_leases": [], "hold": None, "wip_hold": None,
        "report_json": None, "prs": [], "main_run": None, "errors": errors,
    }

    try:
        task = S.load_task(project, slug)
        if not isinstance(task, dict):
            raise TypeError("task record is not an object")
    except Exception as e:  # each source boundary reports its own fault and leaves the shape intact
        _error(errors, "task", e)
        task = {}
    if task:
        out["slug"] = task.get("slug") or slug
        for field in _TASK_FIELDS:
            out[field] = task.get(field)
        out["envelope"] = task.get("envelope")

    dispatch_id = task.get("dispatch_id")
    session_id = task.get("session_id")
    counts_path = config.MONITOR_DIR / f"counts-{project}--{dispatch_id}.json" if dispatch_id else None
    if (counts_path is None or not counts_path.exists()) and session_id:
        counts_path = config.MONITOR_DIR / f"counts-{session_id}.json"  # legacy session-keyed counter
    if counts_path is not None:
        try:
            if not counts_path.exists():
                raise FileNotFoundError(counts_path)
            counts = S.read_json(counts_path, {})
            if not isinstance(counts, dict):
                raise TypeError("counter file is not an object")
            out["counts"] = {"subagent_launches": counts.get("subagent_launches", 0),
                             "edits": counts.get("edits", 0)}
        except Exception as e:
            _error(errors, "counts", e)

    if dispatch_id:
        envelope_path = config.MONITOR_DIR / f"envelope-{project}--{dispatch_id}.json"
        try:
            if not envelope_path.exists():
                raise FileNotFoundError(envelope_path)
            envelope_file = S.read_json(envelope_path, None)
            if not isinstance(envelope_file, dict):
                raise TypeError("envelope file is not an object")
            out["envelope_file"] = envelope_file
        except Exception as e:
            _error(errors, "envelope_file", e)

    runs: list[dict] = []
    if task:
        try:
            l1_dir = S.task_dir(project, slug) / "l1"
            raw_runs = l1.list_runs(project, slug) if l1_dir.is_dir() else []
            for run in raw_runs:
                result = run.get("result") or {}
                compact = {key: run.get(key) for key in ("name", "role", "engine", "done")}
                compact["pr"] = result.get("pr") if isinstance(result, dict) else None
                if not compact["done"] and not l1._alive(run.get("pid")):
                    compact["stale"] = True
                runs.append(compact)
            out["l1_runs"] = {
                "in_flight": sum(not run.get("done") and not run.get("stale") for run in runs),
                "runs": runs,
            }
        except Exception as e:
            _error(errors, "l1_runs", e)

        try:
            out["lease"] = dispatch.task_paths(project, task)
        except Exception as e:
            _error(errors, "lease", e)

    try:
        other_leases = []
        for other in dispatch.leases(project, exclude=out["slug"]):
            other["hold_paths"] = dispatch.narrow(other["paths"])
            other_leases.append(other)
        out["other_leases"] = other_leases
    except Exception as e:
        _error(errors, "other_leases", e)

    try:
        out["hold"] = S.read_json(config.project_dir(project) / "hold.json", None)
    except Exception as e:
        _error(errors, "hold", e)

    if task:
        try:
            mine = dispatch.narrow(out["lease"])
            for other in out["other_leases"]:
                hit = dispatch.paths_overlap(mine, other.get("hold_paths", []))
                if hit:
                    out["wip_hold"] = f"file lease: `{other['slug']}` is running on {', '.join(hit[:4])}"
                    break
        except Exception as e:
            _error(errors, "wip_hold", e)

    report = None
    try:
        report_path = S.task_dir(project, slug) / "report.json"
        out["report_json"] = {"exists": report_path.exists(), "path": str(report_path)}
        report = S.read_json(report_path, None)
    except Exception as e:
        _error(errors, "report_json", e)

    numbers = _pr_numbers(task, runs, report, errors)
    branch = task.get("branch")
    if not numbers and not branch:
        return out
    try:
        repo = config.project_path(project)
    except Exception as e:
        _error(errors, "prs", e)
        return out
    if not numbers:
        try:
            listed = verify.gh(["pr", "list", "--head", str(branch), "--json", "number"], repo)
            if listed is None:
                _error(errors, "prs", f"no PR list result for branch {branch}")
                listed = []
            if not isinstance(listed, list):
                raise verify.VerifierFault("gh pr list returned a non-list result")
            numbers = _pr_numbers({"prs": [entry.get("number") for entry in listed
                                             if isinstance(entry, dict)]}, [], None, errors)
        except verify.VerifierFault as e:
            _error(errors, "prs", e)
    if not numbers:
        return out

    merge_candidates: list[tuple[str, int, str]] = []
    prs = []
    for index, number in enumerate(numbers):
        try:
            info = verify.gh(["pr", "view", str(number), "--json", _PR_FIELDS], repo)
        except verify.VerifierFault as e:
            _error(errors, "prs", e)
            continue
        if not isinstance(info, dict):
            _error(errors, "prs", f"PR #{number} not found")
            continue
        merge = info.get("mergeCommit") or {}
        merge_sha = merge.get("oid") if isinstance(merge, dict) else None
        merged = info.get("state") == "MERGED"
        prs.append({"number": info.get("number", number), "state": info.get("state"),
                    "head_ref": info.get("headRefName"), "head_sha": info.get("headRefOid"),
                    "merged": merged, "merge_sha": merge_sha,
                    "checks": _check_summary(info.get("statusCheckRollup"))})
        if merged and merge_sha:
            merge_candidates.append((str(info.get("mergedAt") or ""), index, str(merge_sha)))
    out["prs"] = prs

    if not merge_candidates:
        return out
    newest_sha = max(merge_candidates)[2]
    try:
        run_list = verify.gh(["run", "list", "--branch", "main", "--limit", "100",
                              "--json", _RUN_FIELDS], repo)
        if run_list is not None and not isinstance(run_list, list):
            raise verify.VerifierFault("gh run list returned a non-list result")
        match = next((run for run in (run_list or [])
                      if isinstance(run, dict) and run.get("headSha") == newest_sha), None)
        if match:
            out["main_run"] = {"id": match.get("databaseId"), "workflow": match.get("workflowName"),
                               "status": match.get("status"), "conclusion": match.get("conclusion"),
                               "head_sha": match.get("headSha")}
        else:
            errors.append(f"no main run found for {newest_sha}")
    except verify.VerifierFault as e:
        _error(errors, "main_run", e)
    return out
