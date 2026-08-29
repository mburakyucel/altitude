"""Ideas and the GitHub backlog (decisions 27–28)."""
from __future__ import annotations
import json
import subprocess

from . import config, engines, l3, state as S


def idea(project: str, text: str, on_text=None) -> dict:
    S.project_log(project, "idea", text=text[:500])
    prompt = ("Burak has an idea. Evaluate it against the project's docs and current tasks: file it as a task with a class "
              "(`alt task new`), or park it with a one-line reason, or say it is already covered. Answer in ≤5 sentences.\n\n"
              f"Idea: {text}")
    return l3.turn(project, prompt, trigger="idea", on_text=on_text)


def backlog_issues(project: str, limit: int = 60) -> list[dict]:
    try:
        p = subprocess.run(["gh", "issue", "list", "--state", "open", "--limit", str(limit), "--json", "number,title,labels,body,updatedAt"],
                           cwd=str(config.project_path(project)), capture_output=True, text=True, timeout=60, env=engines.clean_env())
        return json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError):
        return []


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
