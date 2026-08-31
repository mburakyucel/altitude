"""Self-improvement (ARCHITECTURE §8): incidents, right-sized rules, scopes, promotion, audit input."""
from __future__ import annotations
import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from . import config, dispatch, rules, state as S, tasks as T

MECHANISMS = ("rule", "instruction", "skill", "incident-only")
SCOPES = ("project", "stack", "global")
STATUSES = ("open", "watch", "closed")
# Every bullet templates/incident.md writes, in order. A value may run over many lines (ARCHITECTURE §8 shows
# multi-line `what happened`/`evidence`), so a field ends only at the NEXT one of these labels, at the amendment
# history, or at EOF — never at a stray `- ` line or a blank line inside the value.
INCIDENT_LABELS = ("date", "task", "project", "what happened", "evidence", "root cause",
                   "generalizable", "mechanism", "scope", "rule", "status")
# Fields `alt incident amend` may rewrite, in template order → the bullet label each one owns in incident.md.
AMENDABLE = {"what": "what happened", "evidence": "evidence", "cause": "root cause", "status": "status"}
# ...and the incidents.jsonl column each one feeds, so a correction reaches `alt incident list` / the weekly audit.
INDEXED = {"cause": "cause"}
_BULLET = re.compile(r"^(?:- (" + "|".join(re.escape(x) for x in INCIDENT_LABELS) + r"): |(amended): )", re.M)
_PATH_PART = r"(?:[A-Za-z0-9_.*?\[\]-]+|\{[A-Za-z0-9_.*?\[\]-]+(?:,[A-Za-z0-9_.*?\[\]-]+)+\})+"
_WHERE_PATH = re.compile(
    rf"(?<![\w./{{}},-])(?:{_PATH_PART}/)*{_PATH_PART}\.[A-Za-z0-9]+\.?(?![\w./{{}}-])"
    r"|(?<![\w./{},-])\.claude/skills/\.?(?![\w./{}-])"
)


def _index_append(row: dict) -> None:
    with open(config.INCIDENT_INDEX, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


FAULTS = config.ROOT / "monitor" / "faults.json"
FAULT_WINDOW_SECONDS = 24 * 3600


def system_fault(kind: str, detail: str, *, project: str | None = None, task: str | None = None) -> dict | None:
    """Decision 36: Altitude's own machinery failed. Raise it where it gets fixed — an incident on the `altitude`
    project plus an inbox line — never paper over it with a fallback. One incident per kind per 24 h; repeats are
    counted in monitor/faults.json."""
    from . import tasks as T
    from .dispatch import _seconds_since
    detail = (detail or "").strip()
    FAULTS.parent.mkdir(parents=True, exist_ok=True)
    faults = S.read_json(FAULTS, {}) or {}
    rec = faults.get(kind) or {}
    recent = bool(rec.get("last")) and _seconds_since(rec["last"]) < FAULT_WINDOW_SECONDS and rec.get("incident")
    rec = {"first": rec.get("first") or S.now(), "last": S.now(), "count": int(rec.get("count", 0)) + 1,
           "incident": rec.get("incident"), "detail": detail[:500], "project": project, "task": task}
    faults[kind] = rec
    S.write_json(FAULTS, faults)
    if recent:
        return None
    target = "altitude" if "altitude" in config.load_projects() else project
    inc = None
    if target:
        inc = new_incident(target, title=f"system fault: {kind}", task=task,
                           what=f"Altitude's own machinery failed ({kind})" + (f" while serving project `{project}`" if project and project != target else "") + f": {detail[:800]}",
                           evidence=f"monitor/faults.json[{kind}]; journalctl --user -u altitude", cause="not yet analysed — a system fault, not a task fault",
                           tags=["system-fault", kind], generalizable="unknown", mechanism="incident-only", scope="project", actor="altd")
        rec["incident"] = inc["id"]; faults[kind] = rec; S.write_json(FAULTS, faults)
        T.fyi(target, task, f"SYSTEM FAULT [{kind}] — {detail[:300]} — incident {inc['id']}. Altitude itself needs the fix; nothing falls back silently (decision 36).", actor="altd")
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
    """A lock of its own for incident-id allocation, deliberately NOT `S.project_lock` (I-013). This is a leaf:
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
    """One past the highest id ever issued — never a count (I-013): two `l2-died` faults in the same second both
    counted the same 8 rows and both got `I-009`, and the blind overwrite behind it kept only the second. A gap
    left by a lost or renumbered record is never handed out again either. Same shape as `rules.next_id`."""
    nums = _issued_incident_numbers(project)
    return f"I-{(max(nums) + 1) if nums else 1:03d}"


def new_incident(project: str, *, title: str, task: str | None, what: str, evidence: str, cause: str,
                 tags: list[str], generalizable: str = "unknown", mechanism: str = "incident-only",
                 scope: str = "project", rule: str | None = None, actor: str = "l3") -> dict:
    """Write the incident into the project's Altitude folder (the repo copy lands via the apply S-task).
    Safe to call while holding any project lock — allocation takes a leaf lock of its own (I-013), precisely so a
    caller that already holds one (`dispatch.run` does, around the `wip_hold` that files `quota-unknown`) cannot
    deadlock on it."""
    template = (config.TEMPLATES / "incident.md").read_text()
    fields = dict(title=title, date=S.now()[:10], task=task or "-", project=project, what=what.strip(), evidence=evidence.strip(),
                  cause=cause.strip(), generalizable=generalizable, mechanism=mechanism, scope=scope, rule=rule or "-",
                  status="open" if rule else "watch")
    d = config.project_dir(project) / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    # Render once before anything is reserved, with a placeholder id: a template placeholder this function does not
    # pass — or a stray brace in incident.md — must blow up here, not after O_EXCL has burned an id and left a
    # zero-byte file behind. The real id is not known until the reservation succeeds, and only that value differs,
    # so if this pass renders the one below cannot fail.
    template.format(id="I-000", **fields)
    # (I-013) Reserve the id with an exclusive create, then fill the file atomically. The empty file IS the
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
           "scope": scope, "mechanism": mechanism, "rule": rule, "cause": cause.strip()[:200]}
    with open(config.project_dir(project) / "incidents.jsonl", "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    _index_append(row)
    if task:
        S.append_event(project, task, "incident", id=iid, tags=row["tags"], mechanism=mechanism, scope=scope, by=actor)
    S.project_log(project, "incident", id=iid, title=title, tags=row["tags"])
    S.regen_state_md(project)
    return {"id": iid, "path": str(path), "matches_elsewhere": matches_elsewhere(project, row["tags"])}


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
    """Correct a filed incident in place: rewrite only the named fields, keep the replaced text beneath a dated
    `amended:` line. Nothing is ever deleted — an incident is the provenance of a rule (decision 14), so a
    correction that lost the original would break the audit trail it exists for. Corrections stack at the
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


def matches_elsewhere(project: str, tags: list[str]) -> list[dict]:
    """Incidents in other projects sharing a root-cause tag — the evidence needed to promote (decision 32)."""
    tags = set(tags)
    return [r for r in index() if r.get("project") != project and tags & set(r.get("tags") or [])]


def _where_paths(target_project: str, where: str) -> list[str]:
    """Paths named by prose, normalized into entries the dispatcher and land can resolve."""
    repo = config.project_path(target_project).resolve()
    paths = []
    for match in _WHERE_PATH.finditer(where):
        entry = match.group(0)
        if entry.endswith("."):
            entry = entry[:-1]
        expanded = dispatch._expand_entry(entry)
        if not any(any(char in path for char in "*?[") for path in expanded):
            paths.append(entry)  # keep a brace group intact; task_paths expands it for dispatch and land
            continue
        for path in expanded:
            if not any(char in path for char in "*?["):
                paths.append(path)
                continue
            try:
                matches = sorted(repo.glob(path))
            except (OSError, ValueError):
                continue
            paths.extend(str(found.relative_to(repo)) for found in matches)
    return list(dict.fromkeys(paths))


def _application_paths(target_project: str, ledger: Path, incident: str, where: str,
                       mechanism: str) -> list[str]:
    """Repo-relative files named by a rule application, in the order the apply brief presents them."""
    ledger_path = None
    for root in (config.project_path(target_project), config.REPO):
        try:
            ledger_path = str(ledger.resolve().relative_to(root.resolve()))
            break
        except ValueError:
            continue
    where_paths = _where_paths(target_project, where)
    paths = ([ledger_path] if ledger_path else []) + [f"docs/incidents/{incident}.md"]
    if mechanism == "rule":
        paths.append("CLAUDE.md")
    elif mechanism == "skill":
        paths.append(".claude/skills/")
    elif mechanism == "instruction" and not where_paths:
        paths.append("CLAUDE.md")
    paths.extend(where_paths)
    return list(dict.fromkeys(paths))


def propose_rule(project: str, *, incident: str, title: str, text: str, mechanism: str, scope: str = "project",
                 where: str = "", prevents: str = "", effect: str = "", stack: str | None = None,
                 actor: str = "l3") -> dict:
    """Draft the ledger entry and create the S-task that applies it (FYI-with-veto, decision 10)."""
    if mechanism not in MECHANISMS or scope not in SCOPES:
        raise ValueError(f"mechanism in {MECHANISMS}, scope in {SCOPES}")
    if scope != "project" and not matches_elsewhere(project, _tags_for(project, incident)):
        raise ValueError("stack/global scope needs a matching incident in another project (decision 32); file it as project scope")
    if len(text) > 400 and mechanism == "rule":
        raise ValueError("rule text over 400 chars — that is a skill, not a rule")
    target_project = project if scope == "project" else "altitude"
    ledger = {"project": config.project_path(project) / "docs" / "RULES.md",
              "stack": config.RULES / "stacks" / (stack or "unknown") / "RULES.md",
              "global": config.RULES / "global" / "RULES.md"}[scope]
    d = config.project_dir(project) / "rules-pending"
    rid = rules.next_id(ledger, "S" if mechanism == "skill" else "R", pending=d)
    applies_where = where or {"rule": "CLAUDE.md", "skill": ".claude/skills/",
                              "instruction": "CLAUDE.md", "incident-only": "the owning section"}[mechanism]
    if mechanism == "instruction" and not _where_paths(target_project, applies_where):
        applies_where = "CLAUDE.md"
    entry = rules.render_entry(rid, title, scope=scope if scope == "project" else f"{scope}:{stack or ''}".rstrip(":"),
                               where=applies_where,
                               origin=f"{incident} ({project})", prevents=prevents, effect=effect, status="probation", text=text.strip())
    rules.write_pending(d, rid, entry)
    inc_path = config.project_dir(project) / "incidents" / f"{incident}.md"
    request = (f"Apply rule {rid} from incident {incident} (scope: {scope}, mechanism: {mechanism}).\n\n"
               f"1. Add the incident file `docs/incidents/{incident}.md` with this content:\n\n```\n{inc_path.read_text() if inc_path.exists() else '(missing)'}\n```\n\n"
               f"2. Append this entry to `{'docs/RULES.md' if scope == 'project' else ledger}` (create the ledger with a one-line header if missing):\n\n```\n{entry}\n```\n\n"
               f"3. Apply the {mechanism}: " + {
                   "rule": f"add the rule text to `CLAUDE.md` in the most fitting section, tagged `[{rid}]`.",
                   "instruction": f"add the instruction to the section/skill that owns that step, tagged `[{rid}]`.",
                   "skill": f"create `.claude/skills/<name>/SKILL.md` implementing the procedure, tagged `[{rid}]`, and reference it from the step that needs it.",
                   "incident-only": "nothing else — the incident file is the record."}[mechanism]
               + "\n\n4. Open the PR titled `rules: " + rid + " from " + incident + "`, bind a clean reviewer to its exact head, "
                 "then submit the daemon merge request if project policy allows docs-only merges. No other changes.")
    task = T.new(target_project, f"apply {rid} ({incident})", "S", request, actor=actor, source="improve",
                 paths=_application_paths(target_project, ledger, incident, applies_where, mechanism))
    T.auto_approve(target_project, task["slug"], f"rule application from {incident}; veto = revert the PR")
    T.fyi(project, None, f"{incident} → {rid} ({mechanism}, {scope}): {title}. Applying via task `{task['slug']}` on {target_project}; veto = revert the PR.", actor=actor)
    return {"rule": rid, "task": task["slug"], "target_project": target_project, "ledger": str(ledger)}


def _tags_for(project: str, incident: str) -> list[str]:
    for r in index():
        if r.get("project") == project and r.get("id") == incident:
            return r.get("tags") or []
    return []


def _scope_family(scope) -> str:
    """`stack:python` and `stack:web` are one ledger family; a missing scope is the empty family."""
    return (scope or "").split(":", 1)[0].strip()


def audit_input(project: str) -> dict:
    """What the weekly audit turn gets: every rule with its incidents and recurrence, plus promotion candidates."""
    proj = config.project(project)
    all_rules = rules.global_rules() + rules.stack_rules(proj.get("stacks", [])) + rules.project_rules(config.project_path(project))
    inc = [r for r in index() if r.get("project") == project]
    by_rule: dict[str, list] = {}
    by_scope_rule: dict[tuple[str, str], list] = {}
    for r in inc:
        if r.get("rule"):
            by_rule.setdefault(r["rule"], []).append(r["id"])
            by_scope_rule.setdefault((_scope_family(r.get("scope")), r["rule"]), []).append(r["id"])
    # An id two ledgers both carry (project R-003 and global R-003) must not share one incident list, so there the
    # incident's own scope decides. An id only one ledger carries keeps the plain match: an incident is filed under
    # the scope of the moment, and a rule later promoted to global or a stack would otherwise lose its origin.
    families: dict[str, set] = {}
    for r in all_rules:
        families.setdefault(r["id"], set()).add(_scope_family(r.get("scope")))
    contested = {rid for rid, fams in families.items() if len(fams) > 1}

    def incidents_for(r: dict) -> list:
        if r["id"] in contested:
            return by_scope_rule.get((_scope_family(r.get("scope")), r["id"]), [])
        return by_rule.get(r["id"], [])
    tag_counts: dict[str, dict[str, int]] = {}
    for r in index():
        for t in r.get("tags") or []:
            tag_counts.setdefault(t, {})
            tag_counts[t][r["project"]] = tag_counts[t].get(r["project"], 0) + 1
    promotions = [{"tag": t, "projects": c} for t, c in tag_counts.items() if len(c) > 1]
    return {"rules": [{**r, "incidents": incidents_for(r)} for r in all_rules], "incidents": inc[-30:],
            "promotion_candidates": promotions}
