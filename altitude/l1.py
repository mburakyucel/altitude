"""L1 runs (decisions 45, 47): `alt l1 run` starts an implementer or a reviewer on either engine, detached, in its own
worktree; `alt l1 wait` collects the result. The L2 no longer spawns L1s through Claude Code's Agent tool, so the
engine is Altitude's choice (by quota), the in-flight cap is enforced here, and every run leaves a record."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, engines, route, state as S, tasks as T

RESULT_RE = re.compile(r"^RESULT:\s*(.+)$", re.M)
PR_RE = re.compile(r"(?:pull/|#)(\d+)")
FOOTER = ("\n\n---\nWhen you are finished, print exactly one final line `RESULT: <PR number or URL, or 'no PR'> — <one sentence on what "
          "landed or why you stopped>`. Do not merge. Do not spawn agents or subagents.")
POLL = 5


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
    runs = list_runs(project, slug)
    if role == "implementer":
        cap = int(task["envelope"]["l1_in_flight"])
        live = [r for r in runs if r["role"] == "implementer" and not r.get("done")]
        if len(live) >= cap:
            raise T.TransitionError(f"L1 cap: {len(live)} implementer(s) in flight (cap {cap}) — `alt l1 wait` for one before starting another")
    author = next((r["engine"] for r in reversed(runs) if r["role"] == "implementer"), None)
    choice = route.pick_engine("reviewer" if role == "reviewer" else "l1", forced=engine, task=task,
                               other_than=author if role == "reviewer" else None)
    n = len(runs) + 1
    name = name or f"{role}-{n}"
    if any(r["name"] == name for r in runs):
        raise T.TransitionError(f"run {name!r} already exists")
    base = Path(cwd) if cwd else Path(task.get("worktree") or config.project_path(project))
    if role == "implementer" and not cwd:
        common = _git(base, "rev-parse", "--git-common-dir").stdout.strip()
        if not common:
            raise T.TransitionError(f"{base} is not a git checkout")
        repo_root = (base / common).resolve().parent  # `.git` comes back relative to `base`
        short = slug[:30]
        wt, branch = repo_root / ".claude" / "worktrees" / f"{short}-{name}", f"l1/{short}-{name}"
        r = _git(base, "worktree", "add", "-b", branch, str(wt), "HEAD")
        if r.returncode != 0:
            raise T.TransitionError(f"git worktree add failed: {(r.stderr or r.stdout).strip()[:300]}")
        workdir = wt
    else:
        workdir, branch = base, _git(base, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    persona = config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
    prompt = persona.read_text() + "\n\n# Sub-brief\n\n" + Path(brief).read_text() + FOOTER
    (runs_dir(project, slug) / f"{name}.prompt.md").write_text(prompt)
    key = ("reviewer" if role == "reviewer" else "l1") + ("_codex" if choice["engine"] == "codex" else "")
    model = model or config.MODELS.get(key)
    rec = {"n": n, "name": name, "role": role, "engine": choice["engine"], "why": choice["why"], "model": model,
           "worktree": str(workdir), "branch": branch, "brief": str(brief), "started": S.now(), "pid": None, "done": None, "result": None}
    save(project, slug, rec)
    log = open(runs_dir(project, slug) / f"{name}.log", "ab")
    env = {**os.environ, "ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l1"}
    child = subprocess.Popen([sys.executable, str(config.REPO / "bin" / "alt"), "l1", "_exec", slug, name], cwd=str(workdir),
                             stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    log.close()
    rec["pid"] = child.pid
    save(project, slug, rec)
    S.append_event(project, slug, "l1-started", name=name, role=role, engine=rec["engine"], why=rec["why"], model=model, actor="l2")
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
    try:
        if rec["engine"] == "codex":
            common = _git(wt, "rev-parse", "--git-common-dir").stdout.strip()
            extra = ["sandbox_workspace_write.network_access=true"]
            if common:
                extra.append(f'sandbox_workspace_write.writable_roots=["{(wt / common).resolve()}"]')
            res = engines.codex_exec(prompt, cwd=wt, sandbox="read-only" if rec["role"] == "reviewer" else "workspace-write",
                                     model=rec["model"], timeout=config.L1_TIMEOUT, extra_config=extra, schema=schema)
        else:
            res = engines.claude_print(prompt, cwd=wt, model=rec["model"], permission_mode="plan" if rec["role"] == "reviewer" else "auto",
                                       max_turns=config.L1_MAX_TURNS, timeout=config.L1_TIMEOUT, schema=schema,
                                       extra_env={"ALTITUDE_ACTOR": "l1", "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug})
        text, err = res.get("text") or "", res.get("error")
    except Exception as e:  # noqa: BLE001 — the record must close with the reason (decision 36)
        text, err = "", f"{type(e).__name__}: {e}"
    m = RESULT_RE.search(text)
    summary = m.group(1).strip() if m else None
    pr = None
    if summary and "no pr" not in summary.lower():
        pm = PR_RE.search(summary)
        pr = int(pm.group(1)) if pm else None
    rec.update({"done": S.now(), "result": {"error": err, "pr": pr, "summary": summary, "usage": res.get("usage"),
                                            "structured": res.get("structured"), "returncode": res.get("returncode"),
                                            "text_tail": text[-1500:]}})
    save(project, slug, rec)
    S.append_event(project, slug, "l1-finished", name=name, engine=rec["engine"], pr=pr, error=(err or "")[:200], actor="l1")
    return rec


def _compact(r: dict) -> dict:
    res = r.get("result") or {}
    return {k: r.get(k) for k in ("name", "role", "engine", "why", "model", "branch", "worktree", "started", "done")} | {
        "pr": res.get("pr"), "summary": res.get("summary"), "error": res.get("error"), "usage": res.get("usage")}


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
