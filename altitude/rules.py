"""Rule ledgers (decision 14/32): parse RULES.md files, compile active rules into persona/brief sections."""
from __future__ import annotations
import os
import re
from pathlib import Path

from . import config

FIELDS = ("scope", "where", "origin", "prevents", "effect", "status", "text")
ACTIVE = ("probation", "active")


def parse_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rules, cur = [], None
    for line in path.read_text().splitlines():
        m = re.match(r"^## (R-\d+|S-\d+)\s*[—-]\s*(.*)$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(), "file": str(path)}
            rules.append(cur)
            continue
        m = re.match(r"^- (\w+):\s*(.*)$", line)
        if m and cur is not None and m.group(1) in FIELDS:
            cur[m.group(1)] = m.group(2).strip()
    return rules


def global_rules() -> list[dict]:
    return parse_ledger(config.RULES / "global" / "RULES.md")


def stack_rules(stacks: list[str]) -> list[dict]:
    out = []
    for s in stacks or []:
        out += parse_ledger(config.RULES / "stacks" / s / "RULES.md")
    return out


def project_rules(project_path: Path) -> list[dict]:
    return parse_ledger(Path(project_path) / "docs" / "RULES.md")


def compile_section(rules: list[dict], heading: str) -> str:
    live = [r for r in rules if r.get("status", "active") in ACTIVE and r.get("text")]
    if not live:
        return ""
    lines = [f"## {heading}", ""]
    for r in live:
        lines.append(f"- [{r['id']}] {r['text']}")
    return "\n".join(lines) + "\n"


def compiled_persona(level: str, project: str) -> Path:
    """personas/<level>.md + global rules + stack rules → ~/.altitude/<project>/persona-<level>.md"""
    from .state import atomic_write
    base = (config.PERSONAS / f"{level}.md").read_text()
    proj = config.project(project)
    parts = [base.rstrip() + "\n"]
    g = compile_section(global_rules(), "Global rules (every project; ids are auditable in altitude/rules/global/RULES.md)")
    if g:
        parts.append(g)
    s = compile_section(stack_rules(proj.get("stacks", [])), f"Stack rules ({', '.join(proj.get('stacks', []))})")
    if s:
        parts.append(s)
    out = config.project_dir(project) / f"persona-{level}.md"
    atomic_write(out, "\n".join(parts))
    return out


def next_id(path: Path, prefix: str = "R", pending: Path | None = None) -> str:
    ids = [int(r["id"].split("-")[1]) for r in parse_ledger(path) if r["id"].startswith(prefix)]
    if pending is not None and pending.is_dir():
        pat = re.compile(rf"^{re.escape(prefix)}-(\d+)\.md$")
        for f in pending.iterdir():
            m = pat.match(f.name)
            if m:
                ids.append(int(m.group(1)))
    return f"{prefix}-{(max(ids) + 1) if ids else 1:03d}"


def write_pending(directory: Path, rid: str, text: str) -> Path:
    """Reserve a pending draft without clobbering an existing one (I-003)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{rid}.md"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def render_entry(rid: str, title: str, **f) -> str:
    lines = [f"## {rid} — {title}"]
    for k in FIELDS:
        if k in f and f[k] is not None:
            lines.append(f"- {k}: {f[k]}")
    return "\n".join(lines) + "\n"
