"""Incident evidence and deduplicated system-fault reporting."""
from __future__ import annotations
import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
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


def _block_faulting_task(project: str, slug: str, reason: str, kind: str,
                         expected_block_id: object = T._UNSET) -> tuple[bool, bool]:
    """Block the task a fault belongs to. Returns (newly blocked by this fault, is a repair task).

    The block is tagged with the fault kind and waits on L3, so the restart notice names it and Burak sees no
    card. A task that had already blocked itself is tagged the same way; a finished or missing task stays as it
    is, and the incident still records the fault.
    """
    try:
        task = S.load_task(project, slug)
    except (KeyError, OSError, ValueError):
        return False, False
    tag = {"waiting_on": "l3", "fault": kind}
    touched = False
    if task.get("state") in ("queued", "running", "reported"):
        try:
            T.block(project, slug, reason, actor="altd", expected_state=task["state"], updates=tag,
                    expected_block_id=expected_block_id)
            touched = True
        except T.TransitionError:
            pass
    elif task.get("state") == "blocked":
        with S.project_lock(project):
            task = S.load_task(project, slug)
            if (task.get("state") == "blocked"
                    and (expected_block_id is T._UNSET or task.get("block_id") == expected_block_id)):
                touched = task.get("fault") != kind
                task.update(tag)
                S.save_task(project, task)
    return touched, task.get("source") == "recovery"


def system_fault(kind: str, detail: str, *, project: str | None = None, task: str | None = None,
                 expected_block_id: object = T._UNSET) -> dict | None:
    """Block the faulting task; deduplicate incidents by source project and kind for 24 hours.

    Evidence, FYIs and L3 messages belong to the faulting project. Projectless machine faults go to
    registered `altitude`, or only the fault ledger if absent. Repair tasks never wake L3 again.
    A repeat notifies L3 only when it newly blocks a non-repair task; otherwise it returns None.
    """
    from . import l3
    from .dispatch import _seconds_since
    detail = (detail or "").strip()
    touched, repair = (_block_faulting_task(project, task, f"system fault [{kind}]: {detail[:300]}", kind, expected_block_id)
                       if project and task else (False, False))
    target = project or ("altitude" if "altitude" in config.load_projects() else None)
    key = json.dumps([project, kind])
    with _fault_lock():
        faults = S.read_json(FAULTS, {}) or {}
        # The 2026-09-07 project leak left unscoped records pointing to another project's evidence.
        # Preserve those records, but never reuse them for deduplication or incident references.
        rec = faults.get(key) or {}
        recent = (bool(rec.get("last")) and _seconds_since(rec["last"]) < FAULT_WINDOW_SECONDS
                  and rec.get("incident"))
        rec = {**rec, "first": rec.get("first") or S.now(), "last": S.now(),
               "count": int(rec.get("count", 0)) + 1, "incident": rec.get("incident"),
               "detail": detail[:500], "project": project, "task": task}
        faults[key] = rec
        S.write_json(FAULTS, faults)
        if recent or not target:
            if not (recent and touched and target and not repair):
                return None
            # 2026-09-03 08:10Z: a second task blocked by the day's main-unpushed fault sat waiting on L3, which
            # was never told. One incident per project/kind still holds; a newly blocked task is one more line.
            where = f"{project}/{task}"
            T.fyi(target, task, f"SYSTEM FAULT [{kind}] again — {detail[:300]} — blocking {where}; incident "
                  f"{target}/{rec['incident']} holds the evidence.", actor="altd")
            l3.queue_message(target, f"System fault [{kind}] again, now blocking {where}: {detail[:600]}\n\n"
                             f"Incident {target}/{rec['incident']} from earlier today already holds the evidence; amend it only if this "
                             "adds something, fix the cause if it is back, and resume the task with `alt task resume` once "
                             "the cause is gone. Answer in one or two plain sentences.\n\n"
                             + upstream_summary(target), trigger="incident")
            return {"kind": kind, "incident": rec["incident"], "count": rec["count"], "repeat": True}
        inc = new_incident(target, title=f"system fault: {kind}", task=task,
                           what=f"Altitude's own machinery failed ({kind}): {detail[:800]}",
                           evidence=f"monitor/faults.json key {key}; journalctl --user -u altitude", cause="not yet analysed — a system fault, not a task fault",
                           tags=["system-fault", kind], actor="altd", fault_key=key)
        rec["incident"] = inc["id"]
        faults[key] = rec
        S.write_json(FAULTS, faults)
    where = f"{project}/{task}" if project and task else project or target
    T.fyi(target, task, f"SYSTEM FAULT [{kind}] — {detail[:300]} — incident {target}/{inc['id']}."
          + (" Raised by a repair task, so L3 is not woken again." if repair else ""), actor="altd")
    if not repair:
        l3.queue_message(target, f"System fault [{kind}] in {where}: {detail[:800]}\n\n"
                         f"Its task is blocked and incident {target}/{inc['id']} holds the evidence. Read the evidence, record "
                         "what you learned with `alt incident amend`, then fix the cause directly if that is trivial or "
                         "create one ordinary task. Answer in two or three plain sentences.\n\n"
                         + upstream_summary(target), trigger="incident")
    return {"kind": kind, "incident": inc["id"], "count": rec["count"]}


def index(project: str | None = None) -> list[dict]:
    path = config.project_dir(project) / "incidents.jsonl" if project else config.INCIDENT_INDEX
    if not path.exists():
        return []
    faults = S.read_json(FAULTS, {}) or {}
    out = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "system-fault" in row.get("tags", []):
            row["upstream"] = (faults.get(row.get("fault_key"), {}).get("upstream") or
                               {"status": "missing", "url": None,
                                "reason": "No linked report. Check existing upstream issues; report only with authorization."})
        out.append(row)
    return out


def upstream_delivery(project: str, incident: str, *, outcome: dict | None = None,
                      expected: dict | None = None) -> dict:
    """Read or compare-and-save delivery on the incident's existing project/kind fault identity."""
    if not isinstance(incident, str) or not re.fullmatch(r"I-\d{8}-\d{6}(?:-\d+)?", incident):
        raise ValueError("alt issue upstream: invalid incident id")
    with _fault_lock():
        row = next((row for row in index(project) if row["id"] == incident), None)
        if not row or not row.get("fault_key"):
            raise ValueError("alt issue upstream: incident has no tracked system-fault identity in this project; "
                             "historical backfill requires separate authorization")
        faults = S.read_json(FAULTS, {}) or {}
        key = row["fault_key"]
        source = json.loads(key)[0]
        if key not in faults or not (source == project or source is None and project == "altitude"):
            raise ValueError("alt issue upstream: incident fault identity is unavailable in this project")
        current = row["upstream"]
        if outcome is None:
            return current
        if current != expected:
            raise ValueError("alt issue upstream: delivery changed; inspect `alt incident list` before trying again")
        faults[key]["upstream"] = outcome
        S.write_json(FAULTS, faults)
    S.project_log(project, "incident-upstream", id=incident, **outcome)
    S.regen_state_md(project)
    return outcome


def upstream_summary(project: str) -> str:
    """Bounded current fault-kind outcomes; the full incident list remains the audit surface."""
    rows = {row.get("fault_key") or row["id"]: row for row in index(project) if "upstream" in row}
    if not rows:
        return ""
    counts = {status: sum(row["upstream"]["status"] == status for row in rows.values())
              for status in ("missing", "failed", "uncertain", "confirmed")}
    lines = ["Upstream reports: " + ", ".join(f"{status}={count}" for status, count in counts.items())]
    ordered = sorted(reversed(list(rows.values())), key=lambda row: row["upstream"]["status"] == "confirmed")
    for row in ordered[:5]:
        outcome = row["upstream"]
        lines.append(f"- {row['id']}: {outcome['status']} — {outcome.get('url') or outcome['reason']}")
    lines.append("Inspect all links and gaps with `alt incident list`; reporting does not assign repair work.")
    return "\n".join(lines)


def _reserve_incident_file(directory: Path) -> tuple[str, Path]:
    """Reserve `I-<UTC stamp>.md` with an exclusive create; a same-second neighbour gets a numeric suffix."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for n in range(1, 100):
        iid = f"I-{stamp}" if n == 1 else f"I-{stamp}-{n}"
        path = directory / f"{iid}.md"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return iid, path
        except FileExistsError:
            continue
    raise RuntimeError(f"cannot file an incident in {directory}: every id for {stamp} is taken")


def new_incident(project: str, *, title: str, task: str | None, what: str, evidence: str, cause: str,
                 tags: list[str], actor: str = "l3", fault_key: str | None = None) -> dict:
    """Write incident evidence into the project's Altitude state.

    Filing an incident never creates a task or schedules a healing workflow.
    Safe to call while holding any project lock: reserving the file takes no lock."""
    template = (config.TEMPLATES / "incident.md").read_text()
    fields = dict(title=title, date=S.now()[:10], task=task or "-", project=project, what=what.strip(), evidence=evidence.strip(),
                  cause=cause.strip(), status="watch")
    d = config.project_dir(project) / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    iid, path = _reserve_incident_file(d)
    try:
        S.atomic_write(path, template.format(id=iid, **fields))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    row = {"at": S.now(), "project": project, "id": iid, "title": title, "task": task, "tags": sorted(set(tags)),
           "cause": cause.strip()[:200], **({"fault_key": fault_key} if fault_key else {})}
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
