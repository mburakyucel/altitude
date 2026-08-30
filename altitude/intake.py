"""Ideas and the GitHub backlog (decisions 27–28)."""
from __future__ import annotations
import json
import subprocess

from . import config, engines, l3, state as S


def idea(project: str, text: str, on_text=None) -> dict:
    S.project_log(project, "idea", text=text[:500])
    # [R-007] Keep the server-triggered closing text conversational.
    prompt = ("Burak has an idea. Evaluate it against the project's docs and current tasks: file it as a task with a class "
              "(`alt task new`), or park it with a one-line reason, or say it is already covered. Close with at most two plain "
              "sentences saying what happened and whether anything waits on Burak, with no ids, slugs, paths, rule or decision "
              "numbers, or spend figures in that closing text.\n\n"
              f"Idea: {text}")
    return l3.turn(project, prompt, trigger="idea", on_text=on_text)


def size(project: str, slug: str) -> dict:
    """Decision 53: a task filed with `--class auto` gets its class from a read-only sizer session (research tier), then
    follows the class table: S is approved at once (decision 13), M/L go to the proposal flow on the next tick.
    A sizer that fails is a system fault (decision 36): the task stays requested with `size_error`, never a default class."""
    from . import improve, tasks as T
    task = S.load_task(project, slug)
    if task["state"] != "requested" or task.get("class"):
        return {"skipped": f"state {task['state']}, class {task.get('class')}"}
    request = (S.task_dir(project, slug) / "request.md").read_text()
    prompt = f"Request `{slug}` for project `{project}`:\n\n{request}\n\nPick the class and the paths as JSON per the schema."
    try:
        res = engines.claude_print(prompt, cwd=config.project_path(project), persona=config.PERSONAS / "size.md",
                                   permission_mode="plan", schema=config.SCHEMAS / "size.json", model=config.MODELS["research"],
                                   max_turns=20, timeout=600)
        out = res.get("structured") or {}
        if res.get("error") and not out:
            raise RuntimeError(res["error"])
        if out.get("class") not in S.CLASSES:
            raise RuntimeError(f"sizer returned no class: {json.dumps(out)[:200]}")
    except Exception as e:  # noqa: BLE001 — every failure is one fault line + a stuck-visible task, not a guessed class
        with S.project_lock(project):
            t = S.load_task(project, slug); t["size_error"] = str(e)[:300]; S.save_task(project, t)
        improve.system_fault("sizer", f"intake sizer failed for {project}/{slug}: {str(e)[:200]}", project=project, task=slug)
        raise
    T.set_class(project, slug, out["class"], out["why"], paths=out.get("paths") or [], actor="sizer")
    S.append_event(project, slug, "size-run", turns=res.get("turns"), cost=res.get("cost"))
    if out["class"] == "S":
        T.auto_approve(project, slug, f"sized S by the intake sizer: {out['why']}")
    return {"class": out["class"], "why": out["why"], "paths": out.get("paths") or []}


def backlog_issues(project: str, limit: int = 60) -> list[dict]:
    try:
        p = subprocess.run(["gh", "issue", "list", "--state", "open", "--limit", str(limit), "--json", "number,title,labels,body,updatedAt"],
                           cwd=str(config.project_path(project)), capture_output=True, text=True, timeout=60, env=engines.clean_env())
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"gh issue list failed: {e}") from e
    if p.returncode != 0:
        raise RuntimeError(f"gh issue list exit {p.returncode}: {(p.stderr or '')[-300:]}")
    return json.loads(p.stdout or "[]")


def backlog(project: str, on_text=None) -> dict:
    issues = backlog_issues(project)
    S.project_log(project, "backlog", count=len(issues))
    if not issues:
        return {"text": "No open issues (or `gh` cannot read this repo).", "issues": 0}
    lines = [f"- #{i['number']} {i['title']} [{', '.join(l['name'] for l in i.get('labels') or [])}]\n  {(i.get('body') or '')[:300].replace(chr(10), ' ')}" for i in issues]
    prompt = ("Triage this GitHub backlog into dispatchable batches: group by area, propose at most 3 tasks with classes "
              "(create them with `alt task new --source backlog`, referencing issue numbers in the request), and list what you "
              "are parking with one line each. Decisions only where the rule says so.\n\n" + "\n".join(lines))
    res = l3.turn(project, prompt, trigger="backlog", on_text=on_text)
    res["issues"] = len(issues)
    return res
