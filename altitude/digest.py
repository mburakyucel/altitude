"""Cross-project user-input queue, WIP summary, and digest text."""
from __future__ import annotations

from . import config, state as S, tasks as T

def queue() -> list[dict]:
    items = []
    for p in config.load_projects():
        items += T.decisions(p)
    items.sort(key=lambda i: i.get("asked") or "")
    return items


def fyis(limit: int = 30) -> list[dict]:
    out = []
    for p in config.load_projects():
        out += T.inbox(p, limit)
    out.sort(key=lambda i: i["at"], reverse=True)
    return out[:limit]


def wip() -> dict:
    per = {p: sum(1 for t in S.list_tasks(p) if t["state"] == "running") for p in config.load_projects()}
    return {"per_project": per, "machine": sum(per.values()), "limit_project": config.WIP_PER_PROJECT, "limit_machine": config.WIP_PER_MACHINE,
            "waiting": [{"project": p, "slug": t["slug"], "why": "dispatch" if t["state"] == "queued" else "resume"}
                        for p in config.load_projects() for t in S.list_tasks(p)
                        if t["state"] == "queued" or (t["state"] == "blocked" and t.get("resume_after"))]}


def text() -> str:
    q = queue()
    lines = ["# Altitude digest", ""]
    if q:
        lines += [f"{len(q)} task(s) need input:"] + [f"- [{i['project']}] {i['slug']}: {i['question'][:200]}" for i in q]
    else:
        lines.append("No decisions waiting.")
    w = wip()
    lines.append(f"Running: {w['machine']} L2 task(s) — " + ", ".join(f"{p} {n}" for p, n in w["per_project"].items() if n) + ".")
    if w["waiting"]:
        lines.append("Waiting for a slot: " + ", ".join(f"{x['project']}/{x['slug']}" for x in w["waiting"]) + ".")
    f = fyis(8)
    if f:
        lines += ["", "Recent FYIs:"] + [f"- [{i['project']}] {i['text'][:200]}" for i in f]
    txt = "\n".join(lines) + "\n"
    S.atomic_write(config.DIGEST_FILE, txt)
    return txt

