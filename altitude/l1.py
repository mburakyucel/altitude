"""L1 runs (decisions 45, 47): `alt l1 run` starts an implementer or a reviewer on either engine, detached, in its own
worktree; `alt l1 wait` collects the result. The L2 no longer spawns L1s through Claude Code's Agent tool, so the
engine is Altitude's choice (by quota), the in-flight cap is enforced here, and every run leaves a record. Raw stream
artifacts are local diagnostic evidence: cite their paths, never paste their contents into a PR, issue, or report."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, engines, git_policy, improve, route, state as S, tasks as T
from hooks import launch_counter as LC

RESULT_RE = re.compile(r"^RESULT:\s*(.+)$", re.M)
PR_RE = re.compile(r"(?:pull/|#)(\d+)")
# Single source of truth for the host patch constraint; the Codex L2 path must import this rather than define a copy.
CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: Do not call the custom `apply_patch` tool, because its filesystem verifier "
    "cannot create its bwrap namespace under this host's AppArmor policy. For every edit, call the shell command "
    "`apply_patch` through the exec tool and pass the patch on stdin; this stays inside the Codex workspace-write "
    "sandbox and its configured writable roots."
)
FOOTER = ("\n\n---\nWhen you are finished, print exactly one final line `RESULT: <PR number or URL, or 'no PR'> — <one sentence on what "
          "landed or why you stopped>`. Do not merge. Do not spawn agents or subagents.")
POLL = 5
# Raw engine artifacts are capped at 2 MiB per stream. Truncated files retain both ends and state the exact byte drop.
RAW_OUTPUT_CAP = engines.RAW_CAPTURE_CAP
# A launch is billed only once the engine process itself ran: an exec that never happened raises, and a wrapper that
# started and then failed to find the engine leaves 127 behind. Neither is a session, so neither is charged.
_EXIT_127 = re.compile(r"\bexit(?:\s+code)?\s+127\b")
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
    """The shape I-055 actually left behind. Codex does not surface the bwrap line to Altitude: on the real
    incident `error` was None and the returncode 0, and the only evidence was the worker's own account of why
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


def start(project: str, slug: str, brief: Path, *, role: str = "implementer", engine: str | None = None,
          model: str | None = None, name: str | None = None, cwd: str | None = None) -> dict:
    if role not in ("implementer", "reviewer"):
        raise T.TransitionError("role must be implementer or reviewer")
    if not Path(brief).exists():
        raise T.TransitionError(f"brief not found: {brief}")
    task = S.load_task(project, slug)
    launch_cap = int(task["envelope"]["subagent_launches"])
    # The cap check and the slot it takes are one atomic step under this task's own launch lock; routing,
    # `git worktree add`, the prompt write and the spawn all happen after it is released, so a launch never
    # serialises the project's writers. A run that has not started yet counts through its reservation, not a bill.
    with LC.launch_lock(config.ROOT, project, slug):
        ledger = LC.launch_ledger(config.ROOT, project, slug)
        if ledger is None:  # decision 36: an unreadable count is a fault, never "no launches yet"
            raise T.TransitionError(f"L1 launch count for {project}/{slug} cannot be read — refusing to launch "
                                    f"against an unknown envelope; fix the task folder or report blocked")
        launches, started = ledger
        held = LC.reservations(config.ROOT, project, slug)
        if held is None:
            raise T.TransitionError(f"L1 launch reservations for {project}/{slug} cannot be read — refusing to "
                                    f"launch against an unknown envelope; fix the task folder or report blocked")
        committed = launches + len(held)
        if committed >= launch_cap:
            raise T.TransitionError(f"L1 launch cap: {committed} run(s) already started (cap {launch_cap}) — raise the envelope or report blocked")
        runs = list_runs(project, slug)
        held_names = {str(r.get("name")) for r in held if r.get("name")}
        counted = started | held_names
        if role == "implementer":
            cap = int(task["envelope"]["l1_in_flight"])
            live = {r["name"] for r in runs
                    if r["name"] in counted and r["role"] == "implementer" and not r.get("done")}
            live.update(str(r.get("name")) for r in held
                        if r.get("name") and (r.get("role") == "implementer" or
                                              str(r.get("name")).startswith("implementer-")))
            if len(live) >= cap:
                raise T.TransitionError(f"L1 cap: {len(live)} implementer(s) in flight (cap {cap}) — `alt l1 wait` for one before starting another")
        author = next((r["engine"] for r in reversed(runs) if r["name"] in counted and r["role"] == "implementer"), None)
        used_names = {str(r["name"]) for r in runs} | held_names
        sequences = [int(r.get("n") or 0) for r in runs]
        sequences.extend(int(m.group(1)) for used in used_names if (m := re.search(r"-(\d+)$", used)))
        n = max(sequences, default=0) + 1
        name = name or f"{role}-{n}"
        if name in used_names:
            raise T.TransitionError(f"run {name!r} already exists")
        token = LC.reserve(config.ROOT, project, slug, name=name, role=role)
    try:
        return _spawn(project, slug, brief, task, role=role, engine=engine, model=model, name=name, cwd=cwd,
                      n=n, author=author, token=token, runs=runs)
    except BaseException:
        LC.release(config.ROOT, project, slug, token)  # refused, unroutable or unspawnable: no slot, no event, no bill
        raise


def _spawn(project: str, slug: str, brief: Path, task: dict, *, role: str, engine: str | None, model: str | None,
           name: str, cwd: str | None, n: int, author: str | None, token: str | None, runs: list[dict]) -> dict:
    """Everything after the reservation: route, make the worktree, write the prompt, start the detached wrapper."""
    choice = route.pick_engine("reviewer" if role == "reviewer" else "l1", forced=engine, task=task,
                               other_than=author if role == "reviewer" else None)
    base = Path(cwd) if cwd else Path(task.get("worktree") or config.project_path(project))
    if role == "implementer":
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
    if role == "implementer" and not cwd:
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
        workdir, branch = base, _git(base, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    persona = config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
    prompt = persona.read_text() + "\n\n# Sub-brief\n\n" + Path(brief).read_text()
    if choice["engine"] == "codex":
        prompt += "\n\n" + CODEX_PATCH_NOTE
    prompt += FOOTER
    (runs_dir(project, slug) / f"{name}.prompt.md").write_text(prompt)
    key = ("reviewer" if role == "reviewer" else "l1") + ("_codex" if choice["engine"] == "codex" else "")
    model = model or config.MODELS.get(key)
    if role == "implementer" and cwd:
        current = (_git(base, "rev-parse", "HEAD").stdout or "").strip()
        if current != parent_sha:
            raise T.TransitionError(
                f"--cwd HEAD moved during validation ({parent_sha[:12]} → {current[:12] or 'unreadable'}); retry"
            )
    rec = {"n": n, "name": name, "role": role, "engine": choice["engine"], "why": choice["why"], "model": model,
           "worktree": str(workdir), "branch": branch, "brief": str(brief), "started": S.now(), "pid": None,
           "done": None, "result": None, "reservation": token}
    save(project, slug, rec)
    # <name>.log remains the detached wrapper's own stdout/stderr; engine pipes are the separate raw artifacts.
    log = open(runs_dir(project, slug) / f"{name}.log", "ab")
    env = {**os.environ, "ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l1"}
    try:
        child = subprocess.Popen([sys.executable, str(config.REPO / "bin" / "alt"), "l1", "_exec", slug, name], cwd=str(workdir),
                                 stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    except OSError as e:  # the wrapper itself never ran: close the record and charge nothing
        rec.update({"done": S.now(), "result": {"error": f"L1 process failed to start: {type(e).__name__}: {e}",
                                                "pr": None, "summary": None}})
        save(project, slug, rec)
        raise T.TransitionError(f"L1 process failed to start: {e}") from e
    finally:
        log.close()
    rec["pid"] = child.pid
    save(project, slug, rec)
    # The slot moves to the wrapper: `alt l1 run` exits now, and the reservation must outlive it or die with the run.
    LC.hand_over(config.ROOT, project, slug, token, child.pid)
    return rec


def engine_started(rec: dict, res: dict, exc: BaseException | None, pid: int | None) -> bool:
    """Whether the engine process itself ran — the handshake a launch has to pass before it is billed.

    Claude hands back its child's pid the moment `Popen` returns, so there the evidence is that the callback fired
    at all: a closed subscription window short-circuits before it, and a failed exec raises instead. Codex exposes
    no such callback, so the evidence is the exit status it came back with. Common to both: an exec that never
    happened raises `FileNotFoundError`/`PermissionError`, and a wrapper that could not find the engine exits 127."""
    if res.get("limited"):
        return False  # a confirmed quota/engine refusal is not a started L1, even when the CLI emitted diagnostics
    if rec["engine"] == "claude":
        return pid is not None and not _EXIT_127.search(str(res.get("error") or exc or ""))
    if exc is not None:
        # TimeoutExpired is raised only after subprocess.run successfully spawned the engine. Every other
        # exception defaults to not-started: ENOEXEC and future pre-spawn OSErrors carry no positive handshake.
        return isinstance(exc, subprocess.TimeoutExpired)
    returncode = res.get("returncode")
    if not isinstance(returncode, int) or returncode == 127:
        return False
    return not _EXIT_127.search(str(res.get("error") or exc or ""))


def _settle_launch(project: str, slug: str, rec: dict, started: bool) -> None:
    """Turn the reservation into the authoritative bill, or give it back.

    The `l1-started` event is the bill and the run name is its identity, so this is idempotent: a re-run of the
    same name appends nothing and settles the same number. Nothing else writes `subagent_launches` for a task."""
    token = rec.get("reservation")
    if not started:
        LC.release(config.ROOT, project, slug, token)
        return
    cap = int((S.load_task(project, slug).get("envelope") or {}).get("subagent_launches", 0))
    with LC.launch_lock(config.ROOT, project, slug):
        ledger = LC.launch_ledger(config.ROOT, project, slug)
        if ledger is None:
            LC.note_fault(config.ROOT, f"{project}/{slug}: cannot read l1-started events, {rec['name']} not settled")
            return
        if rec["name"] not in ledger[1]:
            S.append_event(project, slug, "l1-started", name=rec["name"], role=rec["role"], engine=rec["engine"],
                           why=rec.get("why"), model=rec.get("model"), actor="l2")
        LC.release(config.ROOT, project, slug, token, locked=True)
        key = os.environ.get("ALTITUDE_SESSION_KEY")
        if key:
            LC.settle_counts(config.ROOT, LC.counts_path(config.ROOT, key), project, slug, cap)


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
    engine_pid: list[int] = []  # claude_print's `on_start`: the child's pid, the moment it exists
    failure: BaseException | None = None
    try:
        if rec["engine"] == "codex":
            common = _git(wt, "rev-parse", "--git-common-dir").stdout.strip()
            extra = ["sandbox_workspace_write.network_access=true"]
            if common:
                extra.append(f'sandbox_workspace_write.writable_roots=["{(wt / common).resolve()}"]')
            res = engines.codex_exec(prompt, cwd=wt, sandbox="read-only" if rec["role"] == "reviewer" else "workspace-write",
                                     model=rec["model"], timeout=config.L1_TIMEOUT, extra_config=extra, schema=schema,
                                     effort=config.CODEX_EFFORT.get(rec["role"]))
        else:
            res = engines.claude_print(prompt, cwd=wt, model=rec["model"], permission_mode="plan" if rec["role"] == "reviewer" else "auto",
                                       max_turns=config.L1_MAX_TURNS, timeout=config.L1_TIMEOUT, schema=schema,
                                       extra_env={"ALTITUDE_ACTOR": "l1", "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug},
                                       on_start=engine_pid.append)
        text, err = res.get("text") or "", res.get("error")
        raw_stdout, raw_stderr = res.get("raw_stdout") or "", res.get("raw_stderr") or ""
        raw_stdout_truncated = bool(res.get("raw_stdout_truncated"))
        raw_stderr_truncated = bool(res.get("raw_stderr_truncated"))
    except Exception as e:  # noqa: BLE001 — the record must close with the reason (decision 36)
        failure = e
        text, err = "", f"{type(e).__name__}: {e}"
        raw_stdout = getattr(e, "raw_stdout", getattr(e, "stdout", "")) or ""
        raw_stderr = getattr(e, "raw_stderr", getattr(e, "stderr", "")) or ""
        raw_stdout_truncated = bool(getattr(e, "raw_stdout_truncated", False))
        raw_stderr_truncated = bool(getattr(e, "raw_stderr_truncated", False))
    # The handshake: only now is it known whether an engine ran, so only now is the launch billed or given back.
    try:
        _settle_launch(project, slug, rec, engine_started(rec, res, failure, engine_pid[0] if engine_pid else None))
    except Exception as e:  # noqa: BLE001 — a settlement that breaks must not also lose the run's result
        LC.note_fault(config.ROOT, f"{project}/{slug}: settling {name} failed: {type(e).__name__}: {e}")
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
    if denial:
        try:
            improve.system_fault(kind="codex-sandbox", detail=denial, project=project, task=slug)
        except Exception as e:  # noqa: BLE001 — a fault raised about a broken run must not break the record too
            err = f"{err or ''}\nsystem_fault failed: {type(e).__name__}: {e}".strip()
        summary = "engine fault: codex-sandbox"
    pr = None
    if summary and "no pr" not in summary.lower():
        pm = PR_RE.search(summary)
        pr = int(pm.group(1)) if pm else None
    rec.update({"done": S.now(), "result": {"error": err, "pr": pr, "summary": summary, "usage": res.get("usage"),
                                            "structured": res.get("structured"), "returncode": res.get("returncode"),
                                            "text_tail": text[-1500:], "raw": raw_info}})
    save(project, slug, rec)
    S.append_event(project, slug, "l1-finished", name=name, engine=rec["engine"], pr=pr, error=(err or "")[:200], actor="l1")
    return rec


def _compact(r: dict) -> dict:
    """Return the L2 view; ``raw`` paths point to local-only diagnostic evidence."""
    res = r.get("result") or {}
    compact = {k: r.get(k) for k in ("name", "role", "engine", "why", "model", "branch", "worktree", "started", "done")} | {
        "pr": res.get("pr"), "summary": res.get("summary"), "error": res.get("error"), "usage": res.get("usage"),
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
