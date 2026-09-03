"""Optional L1 runs: `alt l1 run` starts an implementer or reviewer on either engine, detached, in its own
worktree; `alt l1 wait` collects the result. The L2 decides when L1 help is useful and owns every result. Raw stream
artifacts are local diagnostic evidence: cite their paths, never paste their contents into a PR, issue, or report."""
from __future__ import annotations
import fcntl
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import config, dispatch, engines, git_policy, incidents, route, state as S, tasks as T

RESULT_RE = re.compile(r"^RESULT:\s*(.+)$", re.M)
PR_RE = re.compile(r"(?:pull/|#)(\d+)")
# One shared host constraint for Codex L1 and L2 prompts.
CODEX_PATCH_NOTE = engines.CODEX_PATCH_NOTE
IMPLEMENTER_FOOTER = ("\n\n---\nWhen finished, leave the bounded change uncommitted and print exactly one final line "
                      "`RESULT: patch ready — <tests and one-sentence handoff>`. Do not commit, open a PR, or merge; "
                      "Altitude captures a patch and the L2 decides what to integrate.")
REVIEWER_FOOTER = ("\n\n---\nWhen finished, print exactly one final line "
                   "`RESULT: no commit — <one-sentence review verdict>`. Do not edit, open a PR, or merge.")
POLL = 5
# Raw engine artifacts are capped at 2 MiB per stream. Truncated files retain both ends and state the exact byte drop.
RAW_OUTPUT_CAP = engines.RAW_CAPTURE_CAP
_CODEX_SANDBOX_MARKERS = ("uid map", "loopback", "RTM_NEWADDR", "Operation not permitted")
_SANDBOX_WORDS = ("bwrap", "bubblewrap", "sandbox", "landlock", "seccomp")
_DENIAL_WORDS = ("denied", "not permitted", "permission", "blocked", "refused", "could not create", "cannot create")


def _cap_raw_output(output: str | bytes | None) -> tuple[bytes, bool]:
    data = output if isinstance(output, bytes) else (output or "").encode("utf-8", errors="replace")
    return engines.cap_raw(data, RAW_OUTPUT_CAP)


def _codex_sandbox_denial(output: str | None) -> str | None:
    """The raw kernel line, on the paths where the engine surfaces one."""
    for line in (output or "").splitlines():
        if "bwrap:" in line and any(marker in line for marker in _CODEX_SANDBOX_MARKERS):
            return line.strip()[:300]
    return None


def _codex_sandbox_stop(text: str | None) -> str | None:
    """Detect the host sandbox denial that Codex may omit from its structured error. On affected runs,
    the structured `error` may be None with returncode 0, leaving only the worker's own account of why
    it stopped. Only consulted for a run that produced no PR, and a verbatim echo of CODEX_PATCH_NOTE is
    stripped first, so neither a landed run that merely discusses the sandbox nor the note itself can trigger it."""
    hay = (text or "").replace(CODEX_PATCH_NOTE, " ")
    low = hay.lower()
    if not any(w in low for w in _SANDBOX_WORDS):
        return None
    if not any(w in low for w in _DENIAL_WORDS):
        return None
    return hay.strip()[:300]


def runs_dir(project: str, slug: str) -> Path:
    d = S.task_dir(project, slug) / "l1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(project: str, slug: str, name: str) -> dict | None:
    return S.read_json(runs_dir(project, slug) / f"{name}.json", None)


def save(project: str, slug: str, rec: dict) -> None:
    S.write_json(runs_dir(project, slug) / f"{rec['name']}.json", rec)


def list_runs(project: str, slug: str) -> list[dict]:
    recs = [S.read_json(p, {}) or {} for p in runs_dir(project, slug).glob("*.json")]
    return sorted((r for r in recs if r.get("name")), key=lambda r: r.get("n", 0))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _require_current_l2(task: dict, slug: str, attempt: int | None) -> None:
    if task.get("state") != "running":
        raise T.TransitionError(f"{slug}: optional L1s may launch only for the current running L2")
    if not attempt or task.get("attempt") != attempt:
        raise T.TransitionError(f"{slug}: attempt {attempt} is no longer the current L2")
    if not task.get("session_id") or not task.get("agent_id"):
        raise T.TransitionError(f"{slug}: current L2 has no concrete session and agent")


@contextmanager
def _launch_permission(project: str, slug: str, attempt: int):
    """Fence the final L1 Popen against a concurrent L2 replacement."""
    with S.project_lock(project):
        _require_current_l2(S.load_task(project, slug), slug, attempt)
        yield


def start(project: str, slug: str, brief: Path, *, role: str = "implementer", engine: str | None = None,
          model: str | None = None, name: str | None = None, cwd: str | None = None,
          paths: list[str] | None = None, expected_attempt: int | None = None) -> dict:
    if role not in ("implementer", "reviewer"):
        raise T.TransitionError("role must be implementer or reviewer")
    if not Path(brief).exists():
        raise T.TransitionError(f"brief not found: {brief}")
    task = S.load_task(project, slug)
    expected_attempt = expected_attempt or int(os.environ.get("ALTITUDE_ATTEMPT") or 0)
    _require_current_l2(task, slug, expected_attempt)
    lock_path = runs_dir(project, slug) / ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        runs = list_runs(project, slug)
        author = next((r["engine"] for r in reversed(runs) if r["role"] == "implementer"), None)
        used_names = {str(r["name"]) for r in runs}
        sequences = [int(r.get("n") or 0) for r in runs]
        sequences.extend(int(m.group(1)) for used in used_names if (m := re.search(r"-(\d+)$", used)))
        n = max(sequences, default=0) + 1
        name = name or f"{role}-{n}"
        if name in used_names:
            raise T.TransitionError(f"run {name!r} already exists")
        return _spawn(project, slug, brief, task, role=role, engine=engine, model=model, name=name, cwd=cwd,
                      paths=paths, n=n, author=author, runs=runs, expected_attempt=expected_attempt)


def _spawn(project: str, slug: str, brief: Path, task: dict, *, role: str, engine: str | None, model: str | None,
           name: str, cwd: str | None, paths: list[str] | None, n: int, author: str | None, runs: list[dict],
           expected_attempt: int) -> dict:
    """Route, make the worktree, write the prompt, and start the detached wrapper."""
    choice = route.pick_engine("reviewer" if role == "reviewer" else "l1", forced=engine,
                               other_than=author if role == "reviewer" else None)
    if not choice.get("engine"):
        raise T.TransitionError(f"engine hold: {choice['why']}")
    base = Path(cwd) if cwd else Path(task.get("worktree") or config.project_path(project))
    if role == "implementer":
        task_lease = dispatch.task_paths(project, task)
        sublease = [dispatch._norm(path).lstrip("/") for path in (paths or task_lease) if str(path).strip()]
        if not sublease:
            raise T.TransitionError("write-capable L1 requires a non-empty sublease")
        outside = [path for path in sublease if not dispatch.inside_lease(path, task_lease)]
        if outside:
            raise T.TransitionError(f"L1 sublease is outside the task lease: {', '.join(outside)}")
        parent_branch = _git(base, "rev-parse", "--abbrev-ref", "HEAD")
        branch_name = (parent_branch.stdout or "").strip() if parent_branch.returncode == 0 else ""
        if not branch_name or branch_name == "HEAD":
            raise T.TransitionError(f"cannot launch a write-capable L1 from detached or unreadable HEAD in {base}")
        if branch_name in ("main", "master"):
            raise T.TransitionError(
                f"cannot launch a write-capable L1 directly on protected branch {branch_name!r}; use a task worktree"
            )
        if cwd:
            registered = {str(Path(p).resolve()) for p in (
                [task.get("worktree")] + [run.get("worktree") for run in runs]
            ) if p}
            if str(base.resolve()) not in registered:
                raise T.TransitionError(
                    f"--cwd {base} is not this task's registered L2 or L1 worktree"
                )
            project_repo = config.project_path(project)
            base_common = (_git(base, "rev-parse", "--git-common-dir").stdout or "").strip()
            repo_common = (_git(project_repo, "rev-parse", "--git-common-dir").stdout or "").strip()
            if not base_common or not repo_common or (base / base_common).resolve() != (project_repo / repo_common).resolve():
                raise T.TransitionError(f"--cwd {base} is not a worktree of the {project!r} repository")
        fetched = _git(base, "fetch", "-q", "origin", "main")
        if fetched.returncode != 0:
            raise T.TransitionError(
                f"cannot validate L1 parent against origin/main: {(fetched.stderr or fetched.stdout).strip()[:300]}"
            )
        try:
            origin_sha = git_policy.capture_origin_sha(base, "main")
            parent_sha = (_git(base, "rev-parse", "HEAD").stdout or "").strip()
            if not parent_sha:
                raise T.TransitionError(f"cannot capture the L1 parent commit in {base}")
            missing = git_policy.commits_missing_task_trailer(
                base, "main", f"{project}/{slug}", head=parent_sha, origin_sha=origin_sha
            )
        except git_policy.GitPolicyError as exc:
            raise T.TransitionError(f"cannot validate L1 parent provenance: {exc}") from exc
        if missing:
            sample = ", ".join(sha[:12] for sha in missing[:5])
            raise T.TransitionError(
                f"L1 parent has commit(s) without exact `Altitude-Task: {project}/{slug}` provenance: {sample}"
            )
    if role == "implementer":
        if not cwd:
            common = _git(base, "rev-parse", "--git-common-dir").stdout.strip()
            if not common:
                raise T.TransitionError(f"{base} is not a git checkout")
            repo_root = (base / common).resolve().parent  # `.git` comes back relative to `base`
            short = slug[:30]
            wt, branch = repo_root / ".claude" / "worktrees" / f"{short}-{name}", f"l1/{short}-{name}"
            r = _git(base, "worktree", "add", "-b", branch, str(wt), parent_sha)
            if r.returncode != 0:
                raise T.TransitionError(f"git worktree add failed: {(r.stderr or r.stdout).strip()[:300]}")
            workdir = wt
        else:
            workdir, branch = base, branch_name
    else:
        sublease = []
        workdir, branch = base, _git(base, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    persona = config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
    prompt = persona.read_text() + "\n\n# Sub-brief\n\n" + Path(brief).read_text()
    if choice["engine"] == "codex":
        prompt += "\n\n" + CODEX_PATCH_NOTE
    if role == "implementer":
        prompt += IMPLEMENTER_FOOTER + f" Your sublease is: {sublease}."
    else:
        prompt += REVIEWER_FOOTER
    (runs_dir(project, slug) / f"{name}.prompt.md").write_text(prompt)
    key = "reviewer" if role == "reviewer" else "l1"
    model = model or (None if choice["engine"] == "codex" else config.MODELS[key])
    if role == "implementer" and cwd:
        current = (_git(base, "rev-parse", "HEAD").stdout or "").strip()
        if current != parent_sha:
            raise T.TransitionError(
                f"--cwd HEAD moved during validation ({parent_sha[:12]} → {current[:12] or 'unreadable'}); retry"
            )
    rec = {"n": n, "name": name, "role": role, "engine": choice["engine"], "why": choice["why"], "model": model,
           "worktree": str(workdir), "branch": branch, "brief": str(brief), "started": S.now(), "pid": None,
           "done": None, "result": None, "paths": sublease,
           "parent_sha": parent_sha if role == "implementer" else None}
    save(project, slug, rec)
    # <name>.log remains the detached wrapper's own stdout/stderr; engine pipes are the separate raw artifacts.
    log = open(runs_dir(project, slug) / f"{name}.log", "ab")
    env = {**os.environ, "ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l1"}
    try:
        with _launch_permission(project, slug, expected_attempt):
            child = subprocess.Popen([sys.executable, str(config.REPO / "bin" / "alt"), "l1", "_exec", slug, name], cwd=str(workdir),
                                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    except T.TransitionError as e:
        rec.update({"done": S.now(), "result": {"error": str(e), "pr": None, "summary": None}})
        save(project, slug, rec)
        raise
    except OSError as e:  # the wrapper itself never ran: close the record and charge nothing
        rec.update({"done": S.now(), "result": {"error": f"L1 process failed to start: {type(e).__name__}: {e}",
                                                "pr": None, "summary": None}})
        save(project, slug, rec)
        raise T.TransitionError(f"L1 process failed to start: {e}") from e
    finally:
        log.close()
    rec["pid"] = child.pid
    save(project, slug, rec)
    S.append_event(project, slug, "l1-started", name=name, role=role, engine=choice["engine"],
                   why=choice["why"], model=model, actor="l2")
    return rec


def exec_run(project: str, slug: str, name: str) -> dict:
    """The detached child: run the engine to completion and close the record — always, whatever broke."""
    rec = load(project, slug, name)
    if not rec:
        raise T.TransitionError(f"no run {name!r}")
    prompt = (runs_dir(project, slug) / f"{name}.prompt.md").read_text()
    wt = Path(rec["worktree"])
    schema = config.SCHEMAS / "review.json" if rec["role"] == "reviewer" else None
    res: dict = {}
    raw_stdout: str | bytes | None = ""
    raw_stderr: str | bytes | None = ""
    raw_stdout_truncated = raw_stderr_truncated = False
    try:
        if rec["engine"] == "codex":
            if rec["role"] == "reviewer":
                runtime = runs_dir(project, slug) / f"{name}.codex-runtime"
                runtime.mkdir(parents=True, exist_ok=True)
                codex_prompt = (f"[altitude] Read-only review checkout: {wt}\n\n" + prompt)
                res = engines.codex_exec(
                    codex_prompt, cwd=runtime, sandbox="workspace-write", readable_roots=[wt],
                    model=rec["model"], timeout=config.L1_TIMEOUT, schema=schema,
                    effort=config.CODEX_EFFORT.get(rec["role"]),
                    fault_context={"project": project})
            else:
                res = engines.codex_exec(prompt, cwd=wt, sandbox="workspace-write",
                                         model=rec["model"], timeout=config.L1_TIMEOUT, schema=schema,
                                         effort=config.CODEX_EFFORT.get(rec["role"]),
                                         fault_context={"project": project})
        else:
            res = engines.claude_print(prompt, cwd=wt, model=rec["model"], permission_mode="plan" if rec["role"] == "reviewer" else "auto",
                                       max_turns=config.L1_MAX_TURNS, timeout=config.L1_TIMEOUT, schema=schema,
                                       extra_env={"ALTITUDE_ACTOR": "l1", "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug})
        text, err = res.get("text") or "", res.get("error")
        raw_stdout, raw_stderr = res.get("raw_stdout") or "", res.get("raw_stderr") or ""
        raw_stdout_truncated = bool(res.get("raw_stdout_truncated"))
        raw_stderr_truncated = bool(res.get("raw_stderr_truncated"))
    except Exception as e:  # noqa: BLE001 — the record must close with the reason
        text, err = "", f"{type(e).__name__}: {e}"
        raw_stdout = getattr(e, "raw_stdout", getattr(e, "stdout", "")) or ""
        raw_stderr = getattr(e, "raw_stderr", getattr(e, "stderr", "")) or ""
        raw_stdout_truncated = bool(getattr(e, "raw_stdout_truncated", False))
        raw_stderr_truncated = bool(getattr(e, "raw_stderr_truncated", False))
    stdout_data, stdout_capped = _cap_raw_output(raw_stdout)
    stderr_data, stderr_capped = _cap_raw_output(raw_stderr)
    run_dir = runs_dir(project, slug)
    raw_paths: dict[str, str | None] = {"stdout": None, "stderr": None}
    for stream, data in (("stdout", stdout_data), ("stderr", stderr_data)):
        path = run_dir / f"{name}.{stream}"
        try:
            path.write_bytes(data)
            raw_paths[stream] = str(path)
        except OSError as e:
            err = f"{err or ''}\nraw {stream} persistence failed: {type(e).__name__}: {e}".strip()
    # `raw` publishes local diagnostic evidence paths only: cite them, never paste their contents into external reports.
    raw_info = ({**raw_paths, "truncated": raw_stdout_truncated or raw_stderr_truncated or stdout_capped or stderr_capped}
                if any(raw_paths.values()) else None)
    persisted_stdout = stdout_data.decode("utf-8", errors="replace")
    persisted_stderr = stderr_data.decode("utf-8", errors="replace")
    m = RESULT_RE.search(text)
    summary = m.group(1).strip() if m else None
    denial = None
    if rec["engine"] == "codex":
        scan_raw_evidence = summary is None or "no pr" in summary.lower() or res.get("returncode") not in (None, 0)
        if scan_raw_evidence:
            denial = _codex_sandbox_denial(persisted_stderr) or _codex_sandbox_denial(persisted_stdout)
            if not denial:
                denial = _codex_sandbox_stop(summary or text[-1500:])
        if not denial:
            denial = _codex_sandbox_denial(err) or _codex_sandbox_denial(text)
        if not denial and res.get("engine_started") is False:
            denial = str(err or "Codex sandbox preflight failed")[:300]
    if denial:
        if res.get("fault_recorded") != "codex-sandbox":
            try:
                incidents.system_fault(kind="codex-sandbox", detail=f"helper of {slug}: {denial}", project=project)
            except Exception as e:  # noqa: BLE001 — a fault raised about a broken run must not break the record too
                err = f"{err or ''}\nsystem_fault failed: {type(e).__name__}: {e}".strip()
        summary = "engine fault: codex-sandbox"
    pr = None
    patch_path = None
    if rec["role"] == "implementer" and not err and not denial:
        from . import land
        try:
            final_head = (_git(wt, "rev-parse", "HEAD").stdout or "").strip()
            if not final_head or final_head != rec.get("parent_sha"):
                raise T.TransitionError(
                    f"L1 moved HEAD ({str(rec.get('parent_sha') or '')[:12]} → {final_head[:12] or 'unreadable'}); "
                    "preserving the worktree but refusing to synthesize a patch"
                )
            groups = land._changes(wt)
            changed = sorted({path for _xy, group in groups for path in group})
            outside = [path for path in changed if not dispatch.inside_lease(path, rec.get("paths") or [])]
            if outside:
                raise T.TransitionError(f"L1 changed files outside its sublease: {', '.join(outside)}")
            if changed:
                untracked = sorted({path for xy, group in groups if xy == "??" for path in group})
                if untracked:
                    add = _git(wt, "add", "-N", "--", *untracked)
                    if add.returncode != 0:
                        raise T.TransitionError(f"cannot prepare L1 patch: {(add.stderr or add.stdout).strip()[:300]}")
                diff = subprocess.run(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", *changed],
                                      cwd=str(wt), capture_output=True, text=True, timeout=60)
                if untracked:
                    _git(wt, "reset", "-q", "HEAD", "--", *untracked)
                if diff.returncode != 0:
                    raise T.TransitionError(f"cannot capture L1 patch: {(diff.stderr or diff.stdout).strip()[:300]}")
                patch_file = runs_dir(project, slug) / f"{name}.patch"
                patch_file.write_text(diff.stdout)
                patch_path = str(patch_file)
        except (OSError, subprocess.SubprocessError, T.TransitionError) as exc:
            err = str(exc)
    if summary and "no pr" not in summary.lower():
        pm = PR_RE.search(summary)
        pr = int(pm.group(1)) if pm else None
    rec.update({"done": S.now(), "result": {"error": err, "pr": pr, "patch": patch_path,
                                            "summary": summary, "usage": res.get("usage"),
                                            "structured": res.get("structured"), "returncode": res.get("returncode"),
                                            "text_tail": text[-1500:], "raw": raw_info}})
    save(project, slug, rec)
    S.append_event(project, slug, "l1-finished", name=name, engine=rec["engine"], pr=pr, error=(err or "")[:200], actor="l1")
    return rec


def _compact(r: dict) -> dict:
    """Return the L2 view; ``raw`` paths point to local-only diagnostic evidence."""
    res = r.get("result") or {}
    compact = {k: r.get(k) for k in ("name", "role", "engine", "why", "model", "branch", "worktree", "started", "done")} | {
        "pr": res.get("pr"), "patch": res.get("patch"), "summary": res.get("summary"),
        "error": res.get("error"), "usage": res.get("usage"),
        "raw": res.get("raw")}
    if r.get("role") != "reviewer":
        return compact
    structured = res.get("structured")
    findings = structured.get("findings") if isinstance(structured, dict) else None
    if not isinstance(findings, list):
        findings = None
    compact["findings"] = findings
    if findings is not None:
        if not findings:
            compact["summary"] = "no findings"
        else:
            counts = {severity: 0 for severity in ("blocking", "major", "minor")}
            other = 0
            for finding in findings:
                severity = finding.get("severity") if isinstance(finding, dict) else None
                if isinstance(severity, str) and severity in counts:
                    counts[severity] += 1
                else:
                    other += 1
            buckets = [f"{counts[severity]} {severity}" for severity in ("blocking", "major", "minor") if counts[severity]]
            if other:
                buckets.append(f"{other} other")
            compact["summary"] = f"{len(findings)} findings: {', '.join(buckets)}"
    elif r.get("done") and not (isinstance(compact["summary"], str) and compact["summary"].strip()) and compact["error"] is None:
        compact["error"] = "reviewer returned no findings and no summary"
    return compact


def status(project: str, slug: str) -> list[dict]:
    out = []
    for r in list_runs(project, slug):
        if not r.get("done") and not _alive(r.get("pid")):  # the child vanished without closing its record: say so
            r.update({"done": S.now(), "result": {"error": "L1 process died before finishing (no result)", "pr": None, "summary": None}})
            save(project, slug, r)
            S.append_event(project, slug, "l1-finished", name=r["name"], engine=r["engine"], pr=None, error="process died", actor="altd")
        out.append(_compact(r))
    return out


def wait(project: str, slug: str, name: str | None = None, timeout: int = 540) -> dict:
    """Block until `name` (or any unfinished run) finishes, or `timeout` — then return {waiting: true} so the caller
    calls again (an L2's Bash call is capped at 10 minutes)."""
    deadline = time.time() + timeout
    while True:
        runs = status(project, slug)
        pending = [r for r in runs if not r["done"] and (name is None or r["name"] == name)]
        finished = [r for r in runs if r["done"] and (name is None or r["name"] == name)]
        if name and not pending and not finished:
            raise T.TransitionError(f"no run {name!r}")
        if name and finished:
            return {"waiting": False, "run": finished[0], "runs": runs}
        if name is None and not pending:
            return {"waiting": False, "run": finished[-1] if finished else None, "runs": runs}
        if time.time() >= deadline:
            return {"waiting": True, "pending": [r["name"] for r in pending], "runs": runs}
        time.sleep(POLL)
