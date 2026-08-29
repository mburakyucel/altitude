"""Cross-project Decision queue, WIP, digest text, Kokoro rendering (ARCHITECTURE §6)."""
from __future__ import annotations
import subprocess
from pathlib import Path

from . import config, state as S, tasks as T

CLASS_RANK = {"L": 0, "M": 1, "S": 2}


def queue() -> list[dict]:
    items = []
    for p in config.load_projects():
        items += T.decisions(p)
    items.sort(key=lambda i: (0 if i["kind"] == "blocked" else 1, CLASS_RANK.get(i["class"], 3), i.get("asked") or ""))
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
            "waiting": [{"project": p, "slug": t["slug"]} for p in config.load_projects() for t in S.list_tasks(p) if t["state"] == "approved"]}


def text() -> str:
    q = queue()
    lines = ["# Altitude digest", ""]
    if q:
        lines += [f"{len(q)} decision(s) waiting:"] + [f"- [{i['project']}] {i['slug']} ({i['class']}): {i['question'][:200]}" for i in q]
    else:
        lines.append("No decisions waiting.")
    w = wip()
    lines.append(f"Running: {w['machine']} orchestrator(s) — " + ", ".join(f"{p} {n}" for p, n in w["per_project"].items() if n) + ".")
    if w["waiting"]:
        lines.append("Waiting for a slot: " + ", ".join(f"{x['project']}/{x['slug']}" for x in w["waiting"]) + ".")
    f = fyis(8)
    if f:
        lines += ["", "Recent FYIs:"] + [f"- [{i['project']}] {i['text'][:200]}" for i in f]
    txt = "\n".join(lines) + "\n"
    S.atomic_write(config.DIGEST_FILE, txt)
    return txt


def speak(txt: str) -> Path | None:
    """Render with Kokoro through voice-tutor's speak.py --stdin-text; returns the audio path if it worked."""
    speak_py = Path.home() / "Projects" / "voice-tutor" / "hooks" / "speak.py"
    if not speak_py.exists():
        return None
    out = config.ROOT / "digest.wav"
    try:
        subprocess.run(["python3", str(speak_py), "--stdin-text", "--out", str(out)], input=txt, text=True, timeout=300, capture_output=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return out if out.exists() else None
