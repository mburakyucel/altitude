"""Incident evidence and deduplicated system-fault reporting."""
from __future__ import annotations
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from . import config, engines, platform, state as S, tasks as T

STATUSES = ("open", "watch", "closed")
ISSUE_LABEL = "incident"
PENDING = "pending — "
# Every bullet templates/incident.md writes, in order. A value may run over many lines, so a field ends only at
# the NEXT one of these labels, at the amendment
# history, or at EOF — never at a stray `- ` line or a blank line inside the value.
INCIDENT_LABELS = ("date", "task", "project", "system", "what happened", "summary", "evidence", "root cause", "status",
                   "issue")
# Fields `alt incident amend` may rewrite, in template order → the bullet label each one owns in incident.md.
AMENDABLE = {"what": "what happened", "evidence": "evidence", "cause": "root cause", "status": "status", "issue": "issue"}
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
                         expected_block_id: object = T._UNSET,
                         expected_owner: dict | None = None, *, expected_task: dict | None = None) -> tuple[bool | None, bool]:
    """Return (changed blocker, is a repair task); None means no applicable task observation.

    The block is tagged with the fault kind and waits on L3, so the restart notice names it and the operator sees no
    card. A task that had already blocked itself is tagged the same way; a finished or missing task stays as it
    is, and the incident still records the fault.
    """
    try:
        task = S.load_task(project, slug)
    except (KeyError, OSError, ValueError):
        return None, False
    def matches(row):
        # #302: a newer accepted wake, worker, question or Stop owns the task. Retain fault
        # evidence without replacing that lifecycle, even when resume keeps the old block ID.
        return ((expected_block_id is T._UNSET or row.get("block_id") == expected_block_id)
                and (expected_task is None or all(row.get(key) == expected_task.get(key) for key in
                     ("state", "block_id", "resume_request", "agent_id", "session_id", "stop_id", "daemon_request"))))
    if not matches(task):
        return None, task.get("source") == "recovery"
    tag = {"waiting_on": "l3", "fault": kind}
    touched = None
    if task.get("state") in ("queued", "running", "reported"):
        try:
            T.block(project, slug, reason, actor="altd", expected_state=task["state"], updates=tag,
                    expected_owner=expected_owner,
                    expected_block_id=task.get("block_id") if expected_block_id is T._UNSET else expected_block_id,
                    expected_agent_id=task.get("agent_id"), expected_session_id=task.get("session_id"))
            touched = True
        except T.TransitionError:
            pass
    elif task.get("state") == "blocked":
        with S.project_lock(project):
            task = S.load_task(project, slug)
            if expected_owner is not None:
                try:
                    T._require_daemon_fence(task, slug)
                except T.TransitionError:
                    return None, task.get("source") == "recovery"
            if (task.get("state") == "blocked"
                    and (expected_owner is None or (T.report_owner(task) == expected_owner and not any(
                        row.get("wake", True) for row in T.pending(project, slug))))
                    and matches(task)):
                touched = task.get("fault") != kind or task.get("blocked_reason") != reason
                if touched:
                    T._supersede_resume(task)
                    task.update(tag, blocked_reason=reason)
                    S.save_task(project, task)
    return touched, task.get("source") == "recovery"


def system_fault(kind: str, detail: str, *, project: str | None = None, task: str | None = None,
                 expected_block_id: object = T._UNSET, expected_owner: dict | None = None,
                 expected_task: dict | None = None, step: str | None = None) -> dict | None:
    """Block the faulting task; deduplicate incidents by source project and kind for 24 hours.

    `step` names what was running when the fault happened when the kind alone does not say it.

    Evidence, FYIs and L3 messages belong to the faulting project. Projectless machine faults go to
    registered `altitude`, or only the fault ledger if absent. Repair tasks never wake L3 again.
    Saved unchanged blockers stay quiet; new tasks and changed observations notify their L3.
    """
    from . import l3
    from .dispatch import _seconds_since
    detail = (detail or "").strip()
    touched, repair = (_block_faulting_task(project, task, f"system fault [{kind}]: {detail}", kind,
                                          expected_block_id, expected_owner, expected_task=expected_task)
                       if project and task else (False, False))
    if expected_owner is not None and touched is None:
        return None  # #323: superseded verification creates neither a fault nor coordinator work.
    target = project or ("altitude" if "altitude" in config.load_projects() else None)
    key = json.dumps([project, kind])
    with _fault_lock():
        faults = S.read_json(FAULTS, {}) or {}
        # The 2026-09-07 project leak left unscoped records pointing to another project's evidence.
        # Preserve those records, but never reuse them for deduplication or incident references.
        rec = faults.get(key) or {}
        changed = touched if project and task else rec.get("detail") != detail
        recent = (bool(rec.get("last")) and _seconds_since(rec["last"]) < FAULT_WINDOW_SECONDS
                  and rec.get("incident"))
        rec = {**rec, "first": rec.get("first") or S.now(), "last": S.now(),
               "count": int(rec.get("count", 0)) + 1, "incident": rec.get("incident"),
               "detail": detail, "project": project, "task": task}
        faults[key] = rec
        S.write_json(FAULTS, faults)
        if recent or (rec.get("incident") and changed is False) or not target:
            if not (changed and target and not repair):
                return None
            # 2026-09-03 08:10Z: a second task blocked by the day's main-unpushed fault sat waiting on L3, which
            # was never told. One incident per project/kind still holds; a newly blocked task is one more line.
            where = f"{project}/{task}" if project and task else project or target
            T.fyi(target, task, f"SYSTEM FAULT [{kind}] changed — {detail[:300]} — in {where}; incident "
                  f"{target}/{rec['incident']} holds the evidence.", actor="altd")
            l3.queue_message(target, f"System fault [{kind}] changed in {where}: {detail[:600]}\n\n"
                             f"Incident {target}/{rec['incident']} holds the evidence. Inspect current task status for the full "
                             "blocker and amend the incident only if this "
                             "adds something, fix the cause if it is back, and resume the task with `alt task resume` once "
                             "the cause is gone. Keep recovery and prevention evidence/ownership separate; "
                             "unchanged follow-through stays quiet.", trigger="incident")
            return {"kind": kind, "incident": rec["incident"], "count": rec["count"], "repeat": True}
        inc = new_incident(target, title=f"system fault: {kind}", task=task,
                           what=f"Altitude's own machinery failed ({kind}): {detail[:800]}",
                           summary=failure_summary(kind, step, detail),
                           evidence=f"monitor/faults.json key {key}; {platform.job_logs_hint('altitude')}", cause="not yet analysed — a system fault, not a task fault",
                           tags=["system-fault", kind], actor="altd")
        rec["incident"] = inc["id"]
        faults[key] = rec
        S.write_json(FAULTS, faults)
    issue = publish_issue(target, inc["id"])   # GitHub waits outside the fault lock
    tracked = (f"issue {issue['issue']}" if issue.get("issue")
               else f"issue publication pending ({issue['pending']}); retry with `alt incident publish {inc['id']}`")
    where = f"{project}/{task}" if project and task else project or target
    T.fyi(target, task, f"SYSTEM FAULT [{kind}] — {detail[:300]} — incident {target}/{inc['id']}; {tracked}."
          + (" Raised by a repair task, so L3 is not woken again." if repair else ""), actor="altd")
    if not repair:
        l3.queue_message(target, f"System fault [{kind}] in {where}: {detail[:800]}\n\n"
                         f"{'Its task is blocked. ' if task and touched is not None else ''}"
                         f"Incident {target}/{inc['id']} holds the evidence and {tracked}. Read the evidence, record "
                         "verified recovery and prevention follow-through with `alt incident amend`. Inspect current "
                         "state before choosing supported recovery; if the cause matches an existing issue, attach it with "
                         "`alt incident amend <id> --issue <url>`; record prevention ownership on the issue and give one concise FYI.",
                         trigger="incident")
    return {"kind": kind, "incident": inc["id"], "count": rec["count"], "issue": issue.get("issue")}


def index(project: str | None = None) -> list[dict]:
    path = config.project_dir(project) / "incidents.jsonl" if project else config.INCIDENT_INDEX
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        try:
            body = (config.project_dir(row["project"]) / "incidents" / f"{row['id']}.md").read_text()
            spans = _field_spans(body, row["id"])
            row["issue"] = None
            for key, label in (("status", "status"), ("evidence", "evidence"), ("cause", "root cause"), ("issue", "issue"),
                               ("summary", "summary"), ("system", "system")):
                if label in spans:
                    start, end = spans[label]
                    row[key] = body[start:end].strip() or None if key == "issue" else body[start:end]
        except (OSError, ValueError):
            row.update(status="unavailable", evidence="Incident evidence unavailable; inspect the local record.", issue=None)
        out.append(row)
    return out


def open_summary(project: str) -> str:
    """Incidents not closed, one line each; full evidence and closed history stay with `alt incident list`."""
    pending = [row for row in reversed(index(project)) if row.get("status") != "closed"]
    lines = []
    for row in pending[:10]:
        issue = row.get("issue")
        link = "; no issue" if not issue else f"; issue {issue}"
        if row.get("status") == "unavailable":
            lines.append(f"- {row['id']}: evidence unavailable, inspect the local record")
            continue
        evidence = " ".join((row.get("evidence") or "").split())
        lines.append(f"- {row['id']}: {row['status']} — {row.get('title') or 'untitled'}{link}"
                     + (f"\n  {evidence[:300]}" + (" [truncated]" if len(evidence) > 300 else "") if evidence else ""))
    if len(pending) > 10:
        lines.append(f"- and {len(pending) - 10} more")
    if lines:
        lines = [f"{len(pending)} not closed; recovery is separate from prevention.", *lines,
                 "Full evidence and closed history: `alt incident list`."]
    return "\n".join(lines)


# ---- what an incident says about the failure and the machine -------------------------------------------------
_MESSAGE = re.compile(r"""["'](?:message|error)["']\s*:\s*["']((?:[^"'\\]|\\.)+)["']""")
_JSON_START = re.compile(r'\{"|\w*":')   # an event line, or a stream chunk cut mid-event


def _event_message(event: dict) -> str:
    for key in ("error", "message"):
        value = event.get(key)
        value = value.get("message") if isinstance(value, dict) else value
        if isinstance(value, str) and value.strip():
            return value
    return event["result"] if event.get("is_error") and isinstance(event.get("result"), str) else ""


def failure_line(text: str) -> str:
    """The last line that says what went wrong. An error inside a JSON event or a printed mapping counts; raw stream
    chunks and event lines without one never do (#658 published three `thinking_tokens` events instead)."""
    for line in reversed(text.splitlines()):
        start = line.find('{"')
        try:   # a whole event line, or one after a prefix such as the l2-died worker state
            event = json.JSONDecoder().raw_decode(line[start:])[0] if start >= 0 else None
        except ValueError:
            event = None
        found = (_event_message(event) if isinstance(event, dict) else "") or next(
            iter(reversed(_MESSAGE.findall(line))), "") or _JSON_START.split(line, 1)[0].strip(" ;:,")
        if found.strip():
            return " ".join(found.split())[:300]
    return ""


def failure_summary(kind: str, step: str | None, detail: str) -> str:
    line = failure_line(detail)
    return (f"Fault {kind}" + (f" during {step}" if step else "")
            + (f". Last error: {line}" if line else ". No error line was recorded."))


def _deployment() -> str:
    if platform.containerized():
        return "container image"
    return "installed release" if config.RELEASE is not None else "source checkout"


def _altitude_build() -> str:
    commit = (config.RELEASE or {}).get("commit")
    return f"{version()} ({commit[:12]})" if commit else version()


def system_context(project: str, task: str | None) -> str:
    """This machine, Altitude's build and the task's engine as `key: value` pairs. Only these facts are collected:
    never host or user names, home paths, addresses, serial numbers or hardware UUIDs."""
    from .dispatch import l2_engine
    facts = {**platform.host_facts(), "altitude": _altitude_build(), "deployment": _deployment()}
    if task:
        try:
            engine = l2_engine(S.load_task(project, task))
        except (KeyError, OSError, ValueError):
            engine = None
        if engine:
            facts["engine"] = f"{engine} {engines.cli_version(engine) or '(CLI version unknown)'}"
            facts["confinement"] = engines.worker_confinement(engine)
    return "; ".join(f"{key}: {' '.join(str(value).replace(';', ',').split())}" for key, value in facts.items())


def _system_section(system: str) -> str:
    """One bullet per fact; Altitude's own version and commit are public and kept, everything else is sanitized."""
    lines = []
    for pair in system.split("; "):
        key, _, value = pair.partition(": ")
        public = key == "altitude" and re.fullmatch(r"[\w.+-]+(?: \([0-9a-f]{12}\))?", value)
        lines.append(f"- {pair}" if public else f"- {sanitize(pair)}")
    return "\n".join(lines)


# ---- public issue: one sanitized GitHub issue per incident ---------------------------------------------------
_CREDENTIAL = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b"
    r"|-----BEGIN [\w ]*PRIVATE KEY-----(?:[\s\S]*?-----END [\w ]*PRIVATE KEY-----)?"
    r"|\b(?:authorization\s*[:=]\s*(?:bearer|basic)\s+|(?:[\w-]*(?:token|password|secret|api[_-]?key))[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[REDACTED\]|<REDACTED>)\S{4,}"
    r"|https?://[^\s/@:]+:[^\s/@]+@", re.I)
_PRIVATE = re.compile(r"(?:/home/|/Users/|~/|\$HOME/|[A-Z]:\\Users\\)|\bI-\d{8}-\d{6}(?:-\d+)?\.md\b|\bincidents(?:/|\.jsonl\b)|"
                      r"\b(?:conversation|inbox|faults|chat)\.jsonl?\b|\.altitude/", re.I)
# IPv4, MAC and IPv6 (eight groups or `::` shortened) addresses; a time such as 10:15:07 is none of these.
_ADDRESS = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
                      r"|\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"
                      r"|(?<![\w:])(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}(?![\w:])"
                      r"|(?<![\w:])(?=[0-9a-f:]*::)(?=[0-9a-f:]*[0-9a-f])[0-9a-f:]{3,39}(?![\w:])", re.I)


def _private_address(match: re.Match) -> bool:
    """Loopback and unspecified addresses (127.0.0.1, ::1, 0.0.0.0) name no machine; every other address does."""
    try:
        address = ipaddress.ip_address(match.group(0))
    except ValueError:
        return True   # a MAC address
    return not (address.is_loopback or address.is_unspecified)


# A serial number named as one ("serial PF4SERIAL9", "Serial Number: C02XK1ZZJGH5"); a value needs a digit, so
# "serial port" is prose.
_SERIAL = re.compile(r"((?i:\bserial(?:[ _-]?(?:number|no\.?))?)[\"']?\s*[:=#]?\s*[\"']?)"
                     r"(?!\[REDACTED\])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{5,}")
_PATH_TAIL = r"(?:[/\\][^\s`'\"<>\[\]{}()]*)?"
_REDACTIONS = (
    (_CREDENTIAL, "[REDACTED]"),
    (_SERIAL, r"\1[REDACTED]"),
    (re.compile(r"(?:~|\$HOME|/home/[^/\s]+|/Users/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)" + _PATH_TAIL), "[path]"),
    (re.compile(r"[^\s`'\"<>\[\]{}()]*(?:\.altitude/|\bincidents(?:/|\.jsonl\b)|\bI-\d{8}-\d{6}(?:-\d+)?\.md\b)[^\s`'\"<>\[\]{}()]*"), "[path]"),
    (re.compile(r"\b(?:conversation|inbox|faults|chat)\.jsonl?\b", re.I), "[file]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (_ADDRESS, lambda m: "[address]" if _private_address(m) else m.group(0)),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "[id]"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "[id]"),
)


def _names() -> str | None:
    """Managed projects other than the development project itself, longest first so a prefix never wins."""
    names = sorted((n for n in config.load_projects() if n != "altitude"), key=len, reverse=True)
    return "|".join(re.escape(n) for n in names) or None


def _standalone(name: str) -> str:
    """`name` not inside a longer word; unlike `\\b`, this also matches a name ending in punctuation (“Ada F.”)."""
    return rf"(?<!\w){re.escape(name)}(?!\w)"


def sanitize(text: str) -> str:
    """Plain words for a public issue: paths, ids, email and network addresses, credentials, task and project names,
    this machine's host and account names and the operator's name never leave. Encoded text is decoded first so
    `%2Fhome` cannot slip past."""
    text = unquote(text)
    home = str(Path.home())
    if home not in ("/", ""):
        text = re.sub(re.escape(home) + _PATH_TAIL, "[path]", text)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    if names := _names():
        text = re.sub(rf"\b(?:{names}|altitude)/[\w.-]+", "[task]", text)
        text = re.sub(rf"\b(?:{names})\b", "[project]", text)
    if operator := config.operator_name():
        text = re.sub(_standalone(operator), "the operator", text, flags=re.I)
    for label, names in platform.local_names().items():
        for name in sorted(names, key=len, reverse=True):
            text = re.sub(_standalone(name), f"[{label}]", text, flags=re.I)
    return text


def check_public(text: str) -> None:
    """The boundary every public issue field crosses; a miss in `sanitize` stays local instead of leaking."""
    text = unquote(text)
    home = str(Path.home()) + "/"
    absolute_paths = re.findall(r"/[^\s`'\"<>\[\]{}()]+", text)
    if (home in text
            or any((os.path.normpath("/" + path.lstrip("/")) + "/").startswith(home) for path in absolute_paths)
            or _PRIVATE.search(text)):
        raise ValueError("Private incident evidence boundary: an issue is public; home paths and private incident evidence must stay on this machine")
    if any(_private_address(m) for m in _ADDRESS.finditer(text)):
        raise ValueError("Private incident evidence boundary: an issue is public; network addresses stay on this machine")
    if _SERIAL.search(text):
        raise ValueError("Private incident evidence boundary: an issue is public; serial numbers stay on this machine")
    if _CREDENTIAL.search(text):
        raise ValueError("Private credential boundary: redact credentials and tokens before publishing an issue")
    if (operator := config.operator_name()) and re.search(_standalone(operator), text, re.I):
        raise ValueError("Private incident evidence boundary: an issue is public; the operator's name stays on this machine")


def _check_incident(text: str) -> None:
    """An incident issue also never names this machine. Project issues are exempt: an account name is often the
    GitHub owner their links name."""
    check_public(text)
    if any(re.search(_standalone(name), text, re.I) for names in platform.local_names().values() for name in names):
        raise ValueError("Private incident evidence boundary: an issue is public; this machine's host and account "
                         "names stay on this machine")


def version() -> str:
    if config.RELEASE is not None:
        return config.RELEASE.get("version") or "unknown"
    return config.SOURCE.name[:12] if config.SOURCE.parent.name == ".altitude-source" else "development"


def _gh(project: str, args: list[str], *, timeout: int, input: str | None = None) -> str:
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    try:
        result = subprocess.run(["gh", *args], input=input, cwd=config.project_path(project), env=env,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"GitHub request unavailable or timed out ({type(exc).__name__})") from exc
    if result.returncode:
        raise ValueError("GitHub refused: " + " ".join((result.stderr or "gh failed").split())[:200])
    return result.stdout


@contextmanager
def _incident_lock(project: str):
    """One publisher or amender per project across processes: a retry cannot race the daemon into a second
    issue, and an amendment never lands on a snapshot another writer has replaced."""
    directory = config.project_dir(project) / "incidents"
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read(project: str, incident: str) -> tuple[Path, str, dict]:
    path = config.project_dir(project) / "incidents" / f"{incident}.md"
    if not path.exists():
        raise ValueError(f"unknown incident {incident!r} in project {project!r} (no {path})")
    body = path.read_text()
    return path, body, _field_spans(body, incident)


def _field(body: str, spans: dict, label: str) -> str:
    start, end = spans.get(label, (0, 0))
    return body[start:end].strip()


def _set_issue(path: Path, body: str, spans: dict, value: str) -> None:
    """Rewrite the issue bullet, or add it after the status bullet for a record filed before issues existed."""
    if "issue" in spans:
        start, end = spans["issue"]
        body = body[:start] + value + body[end:]
    else:
        _, end = spans["status"]
        body = body[:end] + f"\n- issue: {value}" + body[end:]
    S.atomic_write(path, body)


def _issue_url(url: str, repository: str) -> str:
    if not isinstance(url, str) or not re.fullmatch(re.escape(repository) + r"/issues/[1-9]\d*", url.strip(), re.I):
        raise ValueError(f"an issue URL in {repository} is required")
    return url.strip()


def marker(project: str, incident: str) -> str:
    """The incident id plus an opaque project digest: ids are per project, and two projects can file in one second."""
    return f"Incident {incident} ({hashlib.sha256(project.encode()).hexdigest()[:8]})"


def _find_or_create(project: str, incident: str, body: str, spans: dict) -> tuple[str, bool]:
    """The marker in the body is the idempotency key: a retry after a timeout finds the issue it made."""
    from .server import issue_repository
    repository = issue_repository()
    key = marker(project, incident)
    listed = _gh(project, ["issue", "list", "--repo", repository, "--label", ISSUE_LABEL, "--state", "all",
                           "--limit", "200", "--json", "url,body"], timeout=30)
    for row in json.loads(listed or "[]"):
        if key in (row.get("body") or ""):
            return row["url"], False
    title = sanitize(body.split("\n", 1)[0].partition(" — ")[2].strip() or incident)[:200]
    text = "\n\n".join((
        "## Expected\nAltitude completes the step without this failure.",
        "## Actual\n" + sanitize(_field(body, spans, "summary") or _field(body, spans, "what happened")),
        "## Cause\n" + sanitize(_field(body, spans, "root cause")),
        "## Reproduction\nPending triage: the coordinator adds a fictional or redacted reproduction in a comment.",
        *(["## System\n" + _system_section(system)] if (system := _field(body, spans, "system")) else []),
        key)) + "\n"
    _check_incident(title + "\n" + text)
    url = _gh(project, ["issue", "create", "--repo", repository, f"--title={title}", f"--label={ISSUE_LABEL}",
                        "--body-file", "-"], input=text, timeout=60).strip()
    return _issue_url(url, repository), True


def publish_issue(project: str, incident: str) -> dict:
    """Create the incident's issue, or keep the failure on the record with the way back: `alt incident publish`."""
    with _incident_lock(project):
        path, body, spans = _read(project, incident)
        current = _field(body, spans, "issue")
        if current and not current.startswith(PENDING):
            return {"id": incident, "issue": current, "created": False}
        try:
            url, created = _find_or_create(project, incident, body, spans)
        except (ValueError, OSError) as exc:
            reason = " ".join(str(exc).split())[:300]
            _set_issue(path, body, spans, PENDING + reason)
            S.project_log(project, "incident-issue", id=incident, status="pending", reason=reason)
            S.regen_state_md(project)
            return {"id": incident, "issue": None, "pending": reason}
        _set_issue(path, body, spans, url)
    S.project_log(project, "incident-issue", id=incident, url=url, created=created)
    S.regen_state_md(project)
    _notify_development(project, incident, url)   # once per record: a link found by marker is as new to the coordinator
    return {"id": incident, "issue": url, "created": created}


def _notify_development(project: str, incident: str, url: str) -> None:
    """Another project's incident reaches the Altitude coordinator as one fixed public link, never as work."""
    from . import l3
    from .server import repository_url
    target = "altitude"
    if project == target or not config.is_managed(target):
        return
    try:
        with config.project_activity(target) as attached:
            if not attached or not config.is_managed(target):
                return
            checkout = config.project_path(target)
            origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=checkout,
                                    capture_output=True, text=True, timeout=10)
            repository = repository_url(origin.stdout) if origin.returncode == 0 else None
            if repository and repository.lower() == url.lower().rsplit("/issues/", 1)[0]:
                l3.queue_upstream_issue(target, url, checkout=checkout)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        S.project_log(project, "incident-issue", id=incident, url=url, notification="failed", reason=str(exc)[:200])


def _attach_issue(project: str, incident: str, body: str, spans: dict, url: str) -> str:
    """L3 judged the cause matches an existing issue: verify it, note the occurrence there and close the
    issue this incident created as its duplicate."""
    from .server import issue_repository
    repository = issue_repository()
    url = _issue_url(url, repository)
    previous = _field(body, spans, "issue")
    if previous.lower() == url.lower():
        return previous
    kept = json.loads(_gh(project, ["issue", "view", url, "--repo", repository, "--json", "url"], timeout=30)).get("url")
    if not isinstance(kept, str) or kept.lower() != url.lower():
        raise ValueError(f"cannot verify {url}; check GitHub authentication and issue access")
    _gh(project, ["issue", "comment", kept, "--repo", repository, "--body-file", "-"],
        input=f"Occurrence: incident {incident} on Altitude version {version()}.\n", timeout=60)
    if previous and not previous.startswith(PENDING):
        own = json.loads(_gh(project, ["issue", "view", previous, "--repo", repository, "--json", "body,state"], timeout=30))
        if marker(project, incident) in (own.get("body") or "") and own.get("state") == "OPEN":
            _gh(project, ["issue", "comment", previous, "--repo", repository, "--body-file", "-"],
                input=f"Duplicate of {kept}; tracking continues there.\n", timeout=60)
            _gh(project, ["issue", "close", previous, "--repo", repository, "--reason", "duplicate"], timeout=60)
    return kept


def _close_issue(project: str, incident: str, url: str, reason: str) -> None:
    """Closing the incident comments the sanitized reason on its issue and closes the issue this incident created;
    an issue it was attached to tracks other occurrences and stays with `alt issue close`."""
    from .server import issue_repository
    repository = issue_repository()
    note = sanitize(reason)
    _check_incident(note)
    own = json.loads(_gh(project, ["issue", "view", url, "--repo", repository, "--json", "body,state"], timeout=30))
    _gh(project, ["issue", "comment", url, "--repo", repository, "--body-file", "-"],
        input=f"Incident closed: {note}\n", timeout=60)
    if own.get("state") == "OPEN" and marker(project, incident) in (own.get("body") or ""):
        _gh(project, ["issue", "close", url, "--repo", repository, "--reason", "completed"], timeout=60)


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


def _one_field(value: str) -> str:
    """A value line that reads as a bullet would split the record on the next parse; indenting it keeps both."""
    return _BULLET.sub(lambda m: "  " + m.group(0), value.strip())


def new_incident(project: str, *, title: str, task: str | None, what: str, evidence: str, cause: str,
                 tags: list[str], actor: str = "l3", summary: str | None = None) -> dict:
    """Write incident evidence into the project's Altitude state; `publish_issue` gives it its public handle.

    `summary` is a system fault's clean failure line, which the issue reports as actual behavior; an incident
    filed without one reports `what`.
    Filing an incident never creates a task or schedules a healing workflow.
    Safe to call while holding any project lock: reserving the file and reading the system context take no lock
    and no network."""
    template = (config.TEMPLATES / "incident.md").read_text()
    title = " ".join(title.split())
    try:
        system = system_context(project, task)
    except Exception as exc:  # noqa: BLE001 — an unreadable machine fact never costs the fault its incident
        system = f"unavailable ({type(exc).__name__})"
    fields = dict(title=title, date=S.now()[:10], task=task or "-", project=project, system=system,
                  what=_one_field(what), summary=_one_field(summary or ""),
                  evidence=_one_field(evidence), cause=_one_field(cause), status="watch", issue="")
    d = config.project_dir(project) / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    iid, path = _reserve_incident_file(d)
    try:
        S.atomic_write(path, template.format(id=iid, **fields))
    except Exception:
        path.unlink(missing_ok=True)
        raise
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

        amended: 2026-08-30 by l3: root cause was wrong; corrected in chat
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
    with _incident_lock(project):
        return _amend(project, incident, reason=reason, actor=actor, fields=fields)


def _amend(project: str, incident: str, *, reason: str, actor: str, fields: dict) -> dict:
    path, body, spans = _read(project, incident)
    if "issue" in fields and "issue" not in spans:
        _set_issue(path, body, spans, "")
        path, body, spans = _read(project, incident)
    absent = [AMENDABLE[k] for k in AMENDABLE if k in fields and AMENDABLE[k] not in spans]
    if absent:
        raise ValueError(f"{incident} has no `- {absent[0]}:` line to amend")
    if "issue" in fields:
        fields["issue"] = _attach_issue(project, incident, body, spans, fields["issue"])
    if fields.get("status") == "closed":
        linked = fields.get("issue") or _field(body, spans, "issue")
        if linked and not linked.startswith(PENDING):
            _close_issue(project, incident, linked, reason)
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
