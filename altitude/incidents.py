"""Incident evidence and deduplicated system-fault reporting."""
from __future__ import annotations
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config, state as S, tasks as T

STATUSES = ("open", "watch", "closed")
# Every bullet templates/incident.md writes, in order. A value may run over many lines, so a field ends only at
# the NEXT one of these labels, at the amendment
# history, or at EOF — never at a stray `- ` line or a blank line inside the value.
INCIDENT_LABELS = ("date", "task", "project", "what happened", "evidence", "root cause", "status")
# Fields `alt incident amend` may rewrite, in template order → the bullet label each one owns in incident.md.
AMENDABLE = {"what": "what happened", "evidence": "evidence", "cause": "root cause", "status": "status"}
# ...and the incidents.jsonl column each one feeds, so a correction reaches `alt incident list`.
INDEXED = {"cause": "cause"}
_BULLET = re.compile(r"^(?:- (" + "|".join(re.escape(x) for x in INCIDENT_LABELS) + r"): |(amended): )", re.M)


def _index_append(row: dict) -> None:
    S.append_jsonl(config.INCIDENT_INDEX, row, key_field="projection_id")


FAULTS = config.ROOT / "monitor" / "faults.json"
FAULT_WINDOW_SECONDS = 24 * 3600


def fault_lock_path() -> Path:
    # The lock is a control-plane path and must follow the active runtime root;
    # FAULTS remains a legacy data constant patched by older callers/tests.
    return config.MONITOR_DIR / "faults.lock"


@contextmanager
def _fault_lock():
    """Serialize fault evidence RMW and dedupe, independently of project dispatch locks."""
    FAULTS.parent.mkdir(parents=True, exist_ok=True)
    with S.ordered_file_lock(fault_lock_path(), S.LockLevel.RECOVERY):
        yield


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
        target = "altitude" if "altitude" in config.load_projects() else project
        # The fuse and its one L3 wake are durable before incident rendering or FYI I/O. Those enrich the same
        # episode afterward; their failure must never leave a silent hold that nobody is asked to inspect.
        recovery.request_l3_attention(target, kind=kind, incident=rec.get("incident"))
        if recent:
            return None
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
        if inc:
            recovery.request_l3_attention(target, kind=kind, incident=inc["id"])
        return {"kind": kind, "incident": inc["id"] if inc else None, "count": rec["count"]}


def _fold_index(path: Path) -> list[dict]:
    """Fold append-only base/correction projections while reading legacy single rows."""
    folded: dict[str, dict] = {}
    for row in S.read_jsonl(path, key_field="projection_id", ignore_invalid=False):
        key = row.get("incident_key") or (
            f"{row.get('project')}/{row.get('id')}" if row.get("project") and row.get("id") else None
        )
        if not key:
            continue
        if row.get("projection_kind") == "amendment":
            if key in folded:
                folded[key].update(row.get("updates") or {})
            continue
        folded[key] = {k: v for k, v in row.items()
                       if k not in ("projection_id", "projection_kind", "updates")}
    return list(folded.values())


def index() -> list[dict]:
    return _fold_index(config.INCIDENT_INDEX)


_IID = re.compile(r"^I-(\d+)$")
INCIDENT_ID_ATTEMPTS = 20


@contextmanager
def _alloc_lock(directory: Path):
    """Use a dedicated leaf lock for incident-id allocation, never ``S.project_lock``.

    It is taken only around the allocate+reserve loop below, which acquires nothing else, so it can neither nest
    with itself nor invert an order against the project lock."""
    directory.mkdir(parents=True, exist_ok=True)
    with S.ordered_file_lock(directory / ".alloc.lock", S.LockLevel.OPERATION):
        yield


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
    # Legacy allocation deliberately skipped malformed historical rows. Keep that
    # compatibility only here; normal durable readers fail closed on corruption.
    for row in S.read_jsonl(ledger, key_field="projection_id", ignore_invalid=True):
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
    Safe to call while holding any project lock: allocation takes a leaf lock of its own."""
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
    created = S.now()
    row = {"incident_key": f"{project}/{iid}", "at": S.now(), "project": project,
           "id": iid, "title": title, "task": task, "tags": sorted(set(tags)),
           "cause": cause.strip()[:200]}
    meta = {"version": 1, **row, "at": created, "actor": actor}
    source = f"<!-- altitude-incident: {json.dumps(meta, sort_keys=True)} -->\n" + template.format(id=iid, **fields)
    S.atomic_write(path, source)
    reconcile_incident_file(project, path)
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


_SOURCE_META = re.compile(r"^<!-- altitude-(incident|amendment): (\{.*\}) -->$", re.M)


def _source_metadata(path: Path) -> list[tuple[str, dict]]:
    out = []
    for match in _SOURCE_META.finditer(path.read_text()):
        value = json.loads(match.group(2))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError(f"invalid Altitude incident metadata in {path}")
        out.append((match.group(1), value))
    return out


def reconcile_incident_file(project: str, path: Path) -> None:
    """Rebuild every derived index/event row from one canonical incident Markdown file."""
    metadata = _source_metadata(path)
    base = next((value for kind, value in metadata if kind == "incident"), None)
    if not base:
        return  # legacy Markdown remains readable but has no reconstructable metadata
    iid, task = base.get("id"), base.get("task")
    if base.get("project") != project or path.stem != iid:
        raise ValueError(f"incident metadata does not match {path}")
    key = f"{project}/{iid}"
    row = {k: base.get(k) for k in ("incident_key", "at", "project", "id", "title", "task", "tags", "cause")}
    row.update({"projection_id": f"incident:{key}:created", "projection_kind": "created"})
    for target in (config.project_dir(project) / "incidents.jsonl", config.INCIDENT_INDEX):
        S.append_jsonl(target, row, key_field="projection_id")
    if task and S.task_dir(project, task).is_dir():
        S.append_event(project, task, "incident", event_id=f"incident:{key}",
                       at=base.get("at"), id=iid, tags=base.get("tags") or [], by=base.get("actor"))
    S.project_log(project, "incident", event_id=f"incident:{key}",
                  at=base.get("at"), id=iid, title=base.get("title"), tags=base.get("tags") or [])
    for kind, amendment in metadata:
        if kind != "amendment":
            continue
        amendment_id = amendment.get("amendment_id")
        if not isinstance(amendment_id, str) or not amendment_id:
            raise ValueError(f"invalid amendment metadata in {path}")
        projection_id = f"incident:{key}:amend:{amendment_id}"
        correction = {"projection_id": projection_id, "projection_kind": "amendment",
                      "incident_key": key, "project": project, "id": iid,
                      "at": amendment.get("at"), "updates": amendment.get("updates") or {}}
        for target in (config.project_dir(project) / "incidents.jsonl", config.INCIDENT_INDEX):
            S.append_jsonl(target, correction, key_field="projection_id")
        event = f"incident-amended:{key}:{amendment_id}"
        payload = {"id": iid, "fields": amendment.get("fields") or [],
                   "reason": amendment.get("reason"), "by": amendment.get("actor")}
        if task and S.task_dir(project, task).is_dir():
            S.append_event(project, task, "incident-amended", event_id=event,
                           at=amendment.get("at"), **payload)
        S.project_log(project, "incident-amended", event_id=event,
                      at=amendment.get("at"), **payload)


def reconcile_startup() -> None:
    for project in config.load_projects():
        directory = config.project_dir(project) / "incidents"
        if directory.is_dir():
            for path in sorted(directory.glob("I-*.md")):
                reconcile_incident_file(project, path)


def amend_incident(project: str, incident: str, *, reason: str, actor: str = "l3", **fields) -> dict:
    """Correct filed evidence in place: rewrite only named fields and keep the replaced text beneath a dated
    `amended:` line. Corrections stack at the
    bottom of the file, oldest first:

        amended: 2026-08-30 by burak: root cause was wrong; corrected in chat
        - was root cause: the worker used stale repository state

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
    reconcile_incident_file(project, path)
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
    amended_at = S.now()
    amendment = {"version": 1, "amendment_id": uuid.uuid4().hex, "at": amended_at,
                 "actor": actor, "reason": reason, "fields": sorted(fields),
                 "updates": {INDEXED[k]: fields[k][:200] for k in fields if k in INDEXED}}
    body = (body.rstrip("\n") + f"\n\n<!-- altitude-amendment: {json.dumps(amendment, sort_keys=True)} -->"
            f"\namended: {amended_at[:10]} by {actor}: {reason}\n" + "\n".join(was) + "\n")
    S.atomic_write(path, body)
    reconcile_incident_file(project, path)
    if not task and named:
        print(f"alt: {incident} names task {named!r}, which has no folder — amended the incident, wrote no event",
              file=sys.stderr)
    S.regen_state_md(project)
    return {"id": incident, "path": str(path), "amended": sorted(fields), "task": task, "names_task": named,
            "by": actor}
