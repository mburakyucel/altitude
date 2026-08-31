"""Incident evidence and deduplicated system-fault reporting."""
from __future__ import annotations
import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from . import config, state as S, tasks as T

STATUSES = ("open", "watch", "closed")
# Every bullet templates/incident.md writes, in order. A value may run over many lines (ARCHITECTURE §8 shows
# multi-line `what happened`/`evidence`), so a field ends only at the NEXT one of these labels, at the amendment
# history, or at EOF — never at a stray `- ` line or a blank line inside the value.
INCIDENT_LABELS = ("date", "task", "project", "what happened", "evidence", "root cause", "status")
# Fields `alt incident amend` may rewrite, in template order → the bullet label each one owns in incident.md.
AMENDABLE = {"what": "what happened", "evidence": "evidence", "cause": "root cause", "status": "status"}
# ...and the incidents.jsonl column each one feeds, so a correction reaches `alt incident list`.
INDEXED = {"cause": "cause"}
_BULLET = re.compile(r"^(?:- (" + "|".join(re.escape(x) for x in INCIDENT_LABELS) + r"): |(amended): )", re.M)


def _index_append(row: dict) -> None:
    with open(config.INCIDENT_INDEX, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


FAULTS = config.ROOT / "monitor" / "faults.json"
FAULT_WINDOW_SECONDS = 24 * 3600


def fault_lock_path() -> Path:
    return FAULTS.with_suffix(".lock")


@contextmanager
def _fault_lock():
    """Serialize fault evidence RMW and dedupe, independently of project dispatch locks."""
    FAULTS.parent.mkdir(parents=True, exist_ok=True)
    with open(fault_lock_path(), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def system_fault(kind: str, detail: str, *, project: str | None = None, task: str | None = None) -> dict | None:
    """Record a fault as evidence and an FYI without creating repair work.

    One incident per kind per 24 hours keeps a persistent fault from flooding the evidence store. Explicit
    operational recovery remains L3's responsibility; this function never dispatches or creates a task.
    """
    from . import tasks as T
    from .dispatch import _seconds_since
    detail = (detail or "").strip()
    with _fault_lock():
        faults = S.read_json(FAULTS, {}) or {}
        rec = faults.get(kind) or {}
        recent = (bool(rec.get("last")) and _seconds_since(rec["last"]) < FAULT_WINDOW_SECONDS
                  and rec.get("incident"))
        rec = {"first": rec.get("first") or S.now(), "last": S.now(),
               "count": int(rec.get("count", 0)) + 1, "incident": rec.get("incident"),
               "detail": detail[:500], "project": project, "task": task}
        faults[kind] = rec
        S.write_json(FAULTS, faults)
        from . import recovery
        recovery.hold(detail or kind, kind=kind, incident=rec.get("incident"), actor="altd")
        if recent:
            return None
        target = "altitude" if "altitude" in config.load_projects() else project
        inc = None
        if target:
            inc = new_incident(target, title=f"system fault: {kind}", task=task,
                               what=f"Altitude's own machinery failed ({kind})" + (f" while serving project `{project}`" if project and project != target else "") + f": {detail[:800]}",
                               evidence=f"monitor/faults.json[{kind}]; journalctl --user -u altitude", cause="not yet analysed — a system fault, not a task fault",
                               tags=["system-fault", kind], actor="altd")
            rec["incident"] = inc["id"]
            faults[kind] = rec
            S.write_json(FAULTS, faults)
            recovery.attach_incident(kind, inc["id"])
            T.fyi(target, task, f"SYSTEM FAULT [{kind}] — {detail[:300]} — incident {inc['id']}. Evidence recorded; no recovery work was created automatically.", actor="altd")
        return {"kind": kind, "incident": inc["id"] if inc else None, "count": rec["count"]}


def index() -> list[dict]:
    if not config.INCIDENT_INDEX.exists():
        return []
    out = []
    for line in config.INCIDENT_INDEX.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


_IID = re.compile(r"^I-(\d+)$")
INCIDENT_ID_ATTEMPTS = 20


@contextmanager
def _alloc_lock(directory: Path):
    """Use a dedicated leaf lock for incident-id allocation, never ``S.project_lock``.

    it is taken only around the allocate+reserve loop below, which acquires nothing else, so it can neither nest
    with itself nor invert an order against the project lock — and `dispatch.run` does hold a project lock while
    `wip_hold` files a `quota-unknown` fault, so reusing that lock here would deadlock altd outright."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".alloc.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _issued_incident_numbers(project: str) -> list[int]:
    """Every incident number this project has already issued, wherever it was recorded: the global index, the
    per-project ledger, the Altitude-side folder `new_incident` writes, and the repo copy. Anything that is not a
    strict `I-NNN` is skipped rather than crashed on."""
    nums: list[int] = []

    def take(value: object) -> None:
        m = _IID.match(str(value))
        if m:
            nums.append(int(m.group(1)))

    for r in index():
        if r.get("project") == project:
            take(r.get("id"))
    ledger = config.project_dir(project) / "incidents.jsonl"
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                take(row.get("id"))
    for d in (config.project_dir(project) / "incidents", config.project_path(project) / "docs" / "incidents"):
        if d.is_dir():
            for f in d.glob("I-*.md"):
                take(f.stem)
    return nums


def next_incident_id(project: str) -> str:
    """Return one past the highest id ever issued, never a row count.

    This preserves uniqueness across gaps and duplicate historical rows.
    """
    nums = _issued_incident_numbers(project)
    return f"I-{(max(nums) + 1) if nums else 1:03d}"


def new_incident(project: str, *, title: str, task: str | None, what: str, evidence: str, cause: str,
                 tags: list[str], actor: str = "l3") -> dict:
    """Write incident evidence into the project's Altitude state.

    Filing an incident never creates a task or schedules a healing workflow.
    Safe to call while holding any project lock: allocation takes a leaf lock of its own, so a
    caller that already holds one (`dispatch.run` does, around the `wip_hold` that files `quota-unknown`) cannot
    deadlock on it."""
    template = (config.TEMPLATES / "incident.md").read_text()
    fields = dict(title=title, date=S.now()[:10], task=task or "-", project=project, what=what.strip(), evidence=evidence.strip(),
                  cause=cause.strip(), status="watch")
    d = config.project_dir(project) / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    # Render once before anything is reserved, with a placeholder id: a template placeholder this function does not
    # pass — or a stray brace in incident.md — must blow up here, not after O_EXCL has burned an id and left a
    # zero-byte file behind. The real id is not known until the reservation succeeds, and only that value differs,
    # so if this pass renders the one below cannot fail.
    template.format(id="I-000", **fields)
    # Reserve the id with an exclusive create, then fill the file atomically. The empty file is the
    # reservation and holds the id against every other racer — process or thread — while `atomic_write` replaces
    # it whole, so a crash mid-write can never leave a half-parsed incident. An existing incidents/I-NNN.md is
    # never overwritten: we take the next free id, or give up loudly. The lock only stops racers spinning here.
    iid, path = "", d
    with _alloc_lock(d):
        for _ in range(INCIDENT_ID_ATTEMPTS):
            iid = next_incident_id(project)
            path = d / f"{iid}.md"
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                continue                    # someone took it between the max and the open — recompute and retry
            break
        else:
            raise RuntimeError(f"cannot file an incident on {project}: {iid or '(none allocated)'} already exists at {path} "
                               f"and so did every id tried before it ({INCIDENT_ID_ATTEMPTS} attempts); an existing incident "
                               "file is never overwritten")
    S.atomic_write(path, template.format(id=iid, **fields))
    row = {"at": S.now(), "project": project, "id": iid, "title": title, "task": task, "tags": sorted(set(tags)),
           "cause": cause.strip()[:200]}
    with open(config.project_dir(project) / "incidents.jsonl", "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    _index_append(row)
    if task:
        S.append_event(project, task, "incident", id=iid, tags=row["tags"], by=actor)
    S.project_log(project, "incident", id=iid, title=title, tags=row["tags"])
    S.regen_state_md(project)
    return {"id": iid, "path": str(path)}


def _field_spans(body: str, incident: str) -> dict[str, tuple[int, int]]:
    """Where each bullet's value starts and ends. A value runs to the next known label, to the amendment
    history, or to EOF, so multi-line values (and the `- ` lines inside them) survive a rewrite intact."""
    marks = list(_BULLET.finditer(body))
    spans: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(marks):
        if m.group(2):            # `amended:` — everything below is the record of past corrections, not a field
            break
        label = m.group(1)
        if label in spans:
            raise ValueError(f"{incident} has two `- {label}:` bullets, so its fields cannot be told apart; "
                             "amending it would corrupt one of them — fix the file by hand first")
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        spans[label] = (m.end(), len(body[:end].rstrip("\n")))
    return spans


def _breaks_parse(value: str) -> str | None:
    """A new value that carries its own `- <label>:` or `amended:` line would read as a different field the next
    time the file is parsed. Refuse it rather than write something we could not round-trip."""
    m = _BULLET.search(value, 1)
    return m.group(0).strip() if m else None


def _incident_task(body: str, spans: dict[str, tuple[int, int]]) -> str | None:
    s, e = spans.get("task", (0, 0))
    task = body[s:e].strip()
    return task if task and task != "-" else None


def _index_correct(path: Path, project: str, incident: str, updates: dict) -> bool:
    """Rewrite one incident's existing index row. Never appends: a second row for the same id would leave the
    audit reading the old cause beside the new one."""
    if not updates or not path.exists():
        return False
    lines, hit = [], False
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            lines.append(line)
            continue
        if row.get("project") == project and row.get("id") == incident:
            row.update(updates)
            line, hit = json.dumps(row, sort_keys=True), True
        lines.append(line)
    if hit:
        S.atomic_write(path, "\n".join(lines) + "\n")
    return hit


def amend_incident(project: str, incident: str, *, reason: str, actor: str = "l3", **fields) -> dict:
    """Correct filed evidence in place: rewrite only named fields and keep the replaced text beneath a dated
    `amended:` line. Corrections stack at the
    bottom of the file, oldest first:

        amended: 2026-08-30 by burak: root cause was wrong; corrected in chat
        - was root cause: the envelope had no slack

    Everything is validated before the file is touched — a refusal writes nothing, anywhere."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("an amendment needs a --reason; an unexplained correction is not auditable")
    unknown = sorted(set(fields) - set(AMENDABLE))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}; amendable fields are {', '.join(AMENDABLE)}")
    fields = {k: str(v).strip() for k, v in fields.items() if v is not None}
    if not fields:
        raise ValueError(f"nothing to amend: give at least one of {', '.join('--' + k for k in AMENDABLE)}")
    for key, value in fields.items():
        if not value:
            raise ValueError(f"--{key} is empty; an amendment replaces a field, it cannot blank one")
        bad = _breaks_parse(value)
        if bad:
            raise ValueError(f"--{key} has a line starting `{bad}`, which would read as another field next time; rephrase it")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status in {STATUSES}")
    path = config.project_dir(project) / "incidents" / f"{incident}.md"
    if not path.exists():
        raise ValueError(f"unknown incident {incident!r} in project {project!r} (no {path})")
    body = path.read_text()
    spans = _field_spans(body, incident)
    absent = [AMENDABLE[k] for k in AMENDABLE if k in fields and AMENDABLE[k] not in spans]
    if absent:
        raise ValueError(f"{incident} has no `- {absent[0]}:` line to amend")
    named = _incident_task(body, spans)
    # Decided before the write, so the event can never fail after the file has changed — and so a task name that
    # has no folder is reported rather than conjured into one by append_event's mkdir.
    task = named if named and S.task_dir(project, named).is_dir() else None
    edits = sorted(((spans[AMENDABLE[k]], k) for k in fields), key=lambda e: e[0])
    was = [f"- was {AMENDABLE[k]}: {body[s:e]}" for (s, e), k in edits]      # file order, so it reads like the file
    for (s, e), key in reversed(edits):                                      # right to left: earlier spans keep their offsets
        body = body[:s] + fields[key] + body[e:]
    body = body.rstrip("\n") + f"\n\namended: {S.now()[:10]} by {actor}: {reason}\n" + "\n".join(was) + "\n"
    S.atomic_write(path, body)
    row = {INDEXED[k]: fields[k][:200] for k in fields if k in INDEXED}
    _index_correct(config.project_dir(project) / "incidents.jsonl", project, incident, row)
    _index_correct(config.INCIDENT_INDEX, project, incident, row)
    if task:
        S.append_event(project, task, "incident-amended", id=incident, fields=sorted(fields), reason=reason, by=actor)
    elif named:
        print(f"alt: {incident} names task {named!r}, which has no folder — amended the incident, wrote no event",
              file=sys.stderr)
    S.project_log(project, "incident-amended", id=incident, fields=sorted(fields), reason=reason, by=actor)
    S.regen_state_md(project)
    return {"id": incident, "path": str(path), "amended": sorted(fields), "task": task, "names_task": named,
            "by": actor}
