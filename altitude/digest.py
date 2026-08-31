"""Cross-project Decision queue, WIP, digest text, Kokoro rendering (ARCHITECTURE §6)."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

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
            "waiting": [{"project": p, "slug": t["slug"], "why": "dispatch" if t["state"] == "approved" else "resume"}
                        for p in config.load_projects() for t in S.list_tasks(p)
                        if t["state"] == "approved" or (t["state"] == "blocked" and t.get("resume_after"))]}


def text() -> str:
    q = queue()
    lines = ["# Altitude digest", ""]
    if q:
        lines += [f"{len(q)} task(s) need input:"] + [f"- [{i['project']}] {i['slug']}: {i['question'][:200]}" for i in q]
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
    """Render the digest with the local Kokoro server (the same OpenAI-shaped endpoint voice-tutor's speak.py streams
    from — that script has no file output, so altd calls the server itself). Returns the wav path; faults are raised."""
    import json as _json
    import urllib.request
    from . import improve
    url = os.environ.get("TTS_URL", "http://127.0.0.1:8880/v1/audio/speech")
    voice = os.environ.get("TTS_VOICE", "af_heart")
    out = config.ROOT / "digest.wav"
    body = _json.dumps({"model": "kokoro", "input": txt, "voice": voice, "response_format": "wav"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001 — network/HTTP/timeout: one fault, no fallback (decision 36)
        improve.system_fault("tts", f"{url}: {e}")
        return None
    if not data.startswith(b"RIFF"):
        improve.system_fault("tts", f"{url}: response is not a WAV ({len(data)} bytes, starts {data[:12]!r})")
        return None
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return out
