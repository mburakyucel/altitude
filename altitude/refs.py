"""Resolve the ids a card or a report mentions — decision rows, rule sections, incidents — so the UI can show them on tap
(decision 46: the card stays plain; the ledger is one tap away, not in Burak's head)."""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import config, improve

DECISION = re.compile(r"^(?:decisions?\s*)?#?\s*(\d+)$", re.I)
HEADERS = ("Status", "Decision", "Why", "Cost / trade-off")


def _section(path: Path, heading: str) -> str:
    if not path.exists():
        return ""
    out: list[str] = []
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            if out:
                break
            if line.lower().startswith(heading.lower()):
                out.append(line)
        elif out:
            out.append(line)
    return "\n".join(out).strip()


def _row(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    return next((l for l in path.read_text().splitlines() if l.startswith(f"| {n} |")), "")


def resolve(project: str, ref: str) -> dict:
    """{ref, kind, title, text} for an incident (I-007), a rule (R-003) or a decision (31 / decision 31); KeyError if unknown."""
    ref = ref.strip()
    root = config.project_path(project)
    if re.fullmatch(r"I-\d{3}", ref, re.I):
        ref = ref.upper()
        rec = next((r for r in improve.index() if r.get("project") == project and r.get("id") == ref), None)
        md = root / "docs" / "incidents" / f"{ref}.md"
        text = md.read_text() if md.exists() else (json.dumps(rec, indent=1) if rec else "")
        if not text:
            raise KeyError(ref)
        return {"ref": ref, "kind": "incident", "title": f"{ref} — {(rec or {}).get('title') or ''}".rstrip(" —"), "text": text[:8000]}
    if re.fullmatch(r"R-\d{3}", ref, re.I):
        ref = ref.upper()
        text = _section(root / "docs" / "RULES.md", f"## {ref}")
        if not text:
            raise KeyError(ref)
        return {"ref": ref, "kind": "rule", "title": text.splitlines()[0].lstrip("# ").strip(), "text": text}
    m = DECISION.match(ref)
    if m:
        n = int(m.group(1))
        row = _row(root / "docs" / "DECISIONS.md", n)
        if not row:
            raise KeyError(ref)
        cells = [c.strip() for c in row.strip().strip("|").split("|")][1:]
        text = "\n\n".join(f"{h}: {c}" for h, c in zip(HEADERS, cells))
        return {"ref": f"decision {n}", "kind": "decision", "title": f"Decision {n}", "text": text}
    raise KeyError(ref)
