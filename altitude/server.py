"""altd — the Altitude server: web app + JSON API + timers. Stdlib http.server, the pocketbook's shape (decision 29)."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import mimetypes
import os
import secrets
import ssl
import subprocess
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import config, digest, dispatch, engines, git_policy, improve, intake, l3, mechanize, merge_coordinator, monitor, propose, quota_claude, quota_codex, recovery_breaker, refs, route, rules, state as S, status as task_status, tasks as T, verify

LOG = config.ROOT / "altd.log"
_bg: dict[str, threading.Thread] = {}
_bg_guard = threading.Lock()
PROPOSAL_MAX_ATTEMPTS = 3
STALE_PROPOSAL_FAILURE = (
    "proposal attempt ended before validation completed (the proposal engine or server process stopped)"
)
_CONTROL_COOKIE = "altitude-control"
_CONTROL_TOKEN = secrets.token_urlsafe(32)


def log(msg: str) -> None:
    line = f"{S.now()} {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", flush=True)


def spawn(key: str, fn, *a) -> bool:
    """Run fn in a named background thread unless one with that key is already running."""
    with _bg_guard:
        t = _bg.get(key)
        if t and t.is_alive():
            return False

        def run():
            try:
                fn(*a)
            except Exception as e:  # noqa: BLE001
                log(f"[{key}] failed: {e}\n{traceback.format_exc()}")
                parts = key.split(":")
                improve.system_fault(f"workflow:{parts[0]}", f"{key}: {e}", project=parts[1] if len(parts) > 1 else None,
                                     task=parts[2] if len(parts) > 2 else None)
        t = threading.Thread(target=run, name=key, daemon=True)
        _bg[key] = t
        t.start()
        return True


def _pid_alive(pid) -> bool:
    """Is a recorded subprocess still running? An unknown or unsignalable pid counts as alive.

    Fails towards waiting (I-011): a false "alive" costs one tick of patience, a false "dead" costs a second
    L3 turn on a task that already has one.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _turn_in_flight(project: str, task: dict) -> bool:
    """Scheduling hint for a proposal flow; the project L3 lease is the authority.

    The legacy task-local marker is retained for a one-release migration and old flow diagnostics.
    It may delay a proposal thread, but can no longer authorize or duplicate an engine turn.
    """
    if l3.busy(project):
        return True
    rec = task.get("proposal_turn") or {}
    if not _pid_alive(rec.get("pid")):
        return False
    return dispatch._seconds_since(rec.get("started") or "") <= config.L3_TURN_TIMEOUT


def _record_l3_turn(project: str, slug: str, pid: int | None, generation: str | None = None) -> None:
    """Note (pid) or clear (None) the proposal-ready L3 turn's subprocess in the task's status.json.

    Same idea as the session_id/agent_id dispatch records for L2 workers (ce856bb): the child survives altd,
    so the record on disk — not a thread in this process — is what says whether it is still running.
    """
    with S.project_lock(project):
        t = S.load_task(project, slug)
        if pid:
            t["proposal_turn"] = {"pid": int(pid), "pid_start": l3._proc_start_time(pid),
                                  "generation": generation, "started": S.now()}
        elif generation is None or (t.get("proposal_turn") or {}).get("generation") == generation:
            t["proposal_turn"] = None
        S.save_task(project, t)


def _current_proposal_error(task: dict) -> str | None:
    """Return the failure recorded by this proposal attempt, not one carried from an earlier retry."""
    failure = task.get("proposal_error") or {}
    message = str(failure.get("message") or "").strip()
    started = str(task.get("proposal_started") or "")
    recorded = str(failure.get("at") or "")
    return message if message and started and recorded >= started else None


def _begin_proposal_attempt(project: str, slug: str) -> int:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["proposal_attempts"] = int(task.get("proposal_attempts", 0)) + 1
        S.save_task(project, task)
        return task["proposal_attempts"]


def _park_exhausted_proposal(project: str, slug: str) -> bool:
    task = S.load_task(project, slug)
    if int(task.get("proposal_attempts", 0)) < PROPOSAL_MAX_ATTEMPTS or not _current_proposal_error(task):
        return False
    parked = T.park_failed_proposal(project, slug, PROPOSAL_MAX_ATTEMPTS, actor="altd")
    if parked:
        log(f"[{project}/{slug}] proposal failed after {parked['proposal_attempts']} attempts → parked")
    return bool(parked)


def _resume_stale_proposal(project: str, slug: str, key: str) -> bool:
    """Resume one dead proposal flow, unless its third failed attempt is terminal."""
    has_proposal = (S.task_dir(project, slug) / "proposal.json").exists()
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "requested":
            return False
        # A dead engine/process still spent the attempt. Give the retry (or terminal park) useful context instead of
        # letting an attempt with no validation record disappear from the cap. A completed on-disk proposal is reused.
        if not has_proposal and not _current_proposal_error(task):
            task["proposal_error"] = {"message": STALE_PROPOSAL_FAILURE, "at": S.now()}
        exhausted = (int(task.get("proposal_attempts", 0)) >= PROPOSAL_MAX_ATTEMPTS
                     and bool(_current_proposal_error(task)))
        if not exhausted:
            task["proposal_started"] = None
        task["proposal_turn"] = None  # its turn is dead; the resume below is the only one
        S.save_task(project, task)
    if exhausted:
        _park_exhausted_proposal(project, slug)
        return False
    log(f"[{project}/{slug}] proposal flow resumed (previous run did not finish)")
    return spawn(key, run_proposal_flow, project, slug)


# ---- workflows the timers and buttons trigger --------------------------------

def start_l3(project: str) -> None:
    # [R-007] The start reply is a conversation with Burak, not a turn log.
    l3.turn(project, "You have just been started for this project. Read the state file and the repo's README/CLAUDE.md (skim), "
                     "then answer in a few plain sentences: what this project is, what is in flight, and what you would need from Burak. "
                     "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
                     "the digest, the FYI, or the task folder — not in the reply text. Run no other commands.",
            trigger="start")


def _l3_parked_during_turn(project: str, slug: str, turn_start: str) -> bool:
    """Did the L3 park this task during the turn that started at `turn_start` (incident I-008)?

    The last `state → parked` event in the task's log decides it: a park by Burak (or any actor
    other than the L3), or an L3 park that predates the turn, must never queue an auto-revision.
    """
    last = None
    for ev in S.read_events(project, slug):
        if ev.get("kind") == "state" and ev.get("to") == "parked":
            last = ev
    return bool(last and last.get("by") == "l3" and str(last.get("at") or "") >= turn_start)


def _skip_proposal_ready_turn(project: str, slug: str, task: dict, *, keep_fyi_proposed: bool = False) -> None:
    """Clear the in-flight marker and log a proposal-ready turn skipped after a state change."""
    with S.project_lock(project):
        latest = S.load_task(project, slug)
        latest["proposal_started"] = None
        S.save_task(project, latest)
    if task["state"] == "proposed" and keep_fyi_proposed:
        log(f"[{project}/{slug}] proposed outside this flow — skipping the proposal-ready L3 turn; still checked for FYI-only auto-approve")
        return
    log(f"[{project}/{slug}] no longer requested (state={task['state']}) — skipping the proposal-ready L3 turn; proposal kept on disk")


def _requeue_invalid_proposal(project: str, slug: str, p: dict) -> None:
    """Bound and surface a proposal that reached `proposed` without its required card/disposition."""
    fault = False
    with S.project_lock(project):
        current = S.load_task(project, slug)
        if current.get("state") != "proposed" or current.get("decision"):
            return
        failures = int(current.get("proposal_reconcile_failures") or 0) + 1
        delay = (60, 300, 900)[min(failures - 1, 2)]
        current["proposal_reconcile_failures"] = failures
        current["proposal_reconcile_after"] = (
            datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
        current["proposal_reconcile_reason"] = (
            "proposal has always-list hits and requires an L3 disposition"
            if p.get("always_list_hits")
            else "proposal requires an L3 disposition: decision_needed is not explicitly false")
        current["proposal_started"] = None
        if failures >= 2 and not current.get("proposal_reconcile_faulted"):
            current["proposal_reconcile_faulted"] = S.now()
            fault = True
        T._move(project, current, "requested", "altd-recovery",
                reason=current["proposal_reconcile_reason"], retry_after=current["proposal_reconcile_after"],
                failures=failures)
    if fault:
        improve.system_fault(
            "proposal-reconcile",
            f"{project}/{slug}: L3 twice left a proposal without its required decision/disposition",
            project=project, task=slug)


def _finish_proposal(project: str, slug: str, p: dict, task: dict) -> None:
    """Apply proposal-state policy after either a completed or skipped proposal-ready turn."""
    if task["state"] != "proposed":
        return
    persisted = S.read_json(S.task_dir(project, slug) / "proposal.json", {}) or {}
    if isinstance(persisted, dict):
        p = persisted
    hits = p.get("always_list_hits")
    if hits and not task.get("hold_merge"):  # always-list remains an additional blocker under R-014
        hits = hits if isinstance(hits, list) else [hits]
        T.set_hold_merge(project, slug, "always-list: " + ", ".join(str(h) for h in hits)[:160], actor="altd")
    # an FYI-only proposal (no question) is auto-approved by the class table (M, no always-list hits)
    if not task.get("decision") and task["class"] in ("S", "M") and p.get("decision_needed") is False and not p.get("always_list_hits"):
        T.approve(project, slug, None, actor="burak", note="auto: FYI-class proposal (decision 13)")  # recorded as auto in event note
        T.fyi(project, slug, f"{slug} ({task['class']}): proposal needs no decision — dispatching. Summary: {p.get('summary', '')[:300]}")
    elif not task.get("decision"):
        _requeue_invalid_proposal(project, slug, p)


def size_task(project: str, slug: str) -> None:
    task = S.load_task(project, slug)
    try:
        res = _automatic_call("size", project, slug, _task_evidence(project, task, "created", "source"),
                              intake.size, project, slug)
        if res is _BREAKER_BLOCKED:
            return
        log(f"[{project}/{slug}] sized: {res}")
    except Exception as e:  # noqa: BLE001 — the fault is already filed by intake.size
        log(f"[{project}/{slug}] sizer failed: {e}")


def run_proposal_flow(project: str, slug: str) -> None:
    task = S.load_task(project, slug)
    return _automatic_call("proposal-flow", project, slug,
                           _task_evidence(project, task, "created", "proposal_attempts", "proposal_started"),
                           _run_proposal_flow, project, slug)


def _run_proposal_flow(project: str, slug: str) -> None:
    task = S.load_task(project, slug)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("proposal_started"):
            return
        task["proposal_started"] = S.now()
        S.save_task(project, task)
    tdir = S.task_dir(project, slug)
    if (tdir / "proposal.json").exists():  # resuming after a server restart: reuse what is already on disk
        p = S.read_json(tdir / "proposal.json")
        log(f"[{project}/{slug}] proposal reused from disk")
    else:
        _begin_proposal_attempt(project, slug)
        log(f"[{project}/{slug}] proposal agent")
        try:
            p = propose.run_proposal(project, slug)
        except Exception:
            _park_exhausted_proposal(project, slug)
            raise
    T.clear_proposal_failure(project, slug)
    crit = None
    if task["class"] == "L" or (p.get("always_list_hits") and task["class"] == "M"):
        cj, pj = tdir / "critique.json", tdir / "proposal.json"
        if cj.exists() and pj.exists() and cj.stat().st_mtime >= pj.stat().st_mtime:  # a critique of *this* proposal
            crit = S.read_json(cj)
        else:
            tcrit = S.load_task(project, slug)
            if tcrit["state"] != "requested":
                _skip_proposal_ready_turn(project, slug, tcrit)
                _finish_proposal(project, slug, p, tcrit)
                return
            log(f"[{project}/{slug}] critic")
            crit = propose.run_critic(project, slug)
    # [R-007] The proposal-ready output belongs in the card or task record, not a turn-log reply.
    header = (f"Proposal ready for `{slug}` ({task['class']}). Files: proposal.md / proposal.json / critique.json in the task folder. "
              f"Proposal says decision_needed={p.get('decision_needed')}, always-list hits={p.get('always_list_hits')}, "
              f"estimate={p.get('estimate')}."
              + ((f" CRITIC UNAVAILABLE — Altitude fault raised (see inbox/incidents); this task needs the other-engine critique, so park it with that reason until the fault is fixed."
                  if crit.get("verdict") == "unavailable" else
                  f" Critic verdict: {crit.get('verdict')} with {len(crit.get('issues') or [])} issue(s) — read critique.json.") if crit else "")
              + "\n\nApply the Decision rule. Then run exactly one of: "
              f"`alt task propose {slug} --existing --question \"…\" --option \"…\" --option \"…\" --context \"…\" --detail \"…\"` "
              "(card for Burak — decision 46: --context is the situation in ≤ 2 plain sentences (what is wrong, what the proposal does), --question is "
              "the one question in plain words, options ≤ 8 words each with the recommended first, and everything else — reasoning, the critic's conditions, ids, file names, spend — goes in --detail; the CLI rejects the rest), "
              f"`alt task propose {slug} --existing` followed by nothing (FYI-only M task — the server dispatches when a slot is free) "
              f"or `alt task auto-approve {slug} --reason \"…\"` (S only), or `alt task park {slug} --reason \"…\"`. "
              "If the critic says revise and you agree, `alt task park` with the reason and say what should change. "
              "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    # Burak (or the L3 in a chat turn) may have parked, approved, rejected or proposed the task while the proposal and
    # the critic ran (incident I-008): re-read the state and skip the turn rather than talk to the L3 about a task that
    # has already been decided. proposal.json stays on disk; clearing proposal_started lets tick() re-run the flow
    # (requested only) if it comes back to requested later.
    t1 = S.load_task(project, slug)
    if t1["state"] != "requested":
        _skip_proposal_ready_turn(project, slug, t1, keep_fyi_proposed=True)
        _finish_proposal(project, slug, p, t1)
        return
    else:
        task_at_turn = None

        def still_requested() -> bool:
            nonlocal task_at_turn
            task_at_turn = S.load_task(project, slug)
            return task_at_turn["state"] == "requested"

        turn_generation = [None]

        def proposal_engine_started(pid: int) -> None:
            lease = S.read_json(l3.lease_path(project), {}) or {}
            turn_generation[0] = lease.get("generation")
            _record_l3_turn(project, slug, pid, turn_generation[0])

        try:
            res = l3.turn(project, header, trigger="proposal-ready",
                          on_start=proposal_engine_started, precheck=still_requested,
                          evidence={"proposal_slug": slug})
        finally:  # the turn is over — unless this altd died first, and then the record is exactly the point
            _record_l3_turn(project, slug, None, turn_generation[0])
        if res.get("busy"):
            # Another project-level L3 turn owns the engine. Leave the proposal reusable and
            # re-arm this flow; the next tick will retry after the durable lease clears.
            with S.project_lock(project):
                latest = S.load_task(project, slug)
                if latest.get("state") == "requested":
                    latest["proposal_started"] = None
                    S.save_task(project, latest)
            log(f"[{project}/{slug}] proposal-ready turn deferred behind active L3 {res.get('busy_trigger')}")
            return
        if res.get("skipped"):
            skipped_task = task_at_turn or S.load_task(project, slug)
            _skip_proposal_ready_turn(project, slug, skipped_task)
            _finish_proposal(project, slug, p, skipped_task)
            return
        turn_start = res.get("_turn_started_at")
        if turn_start is None:  # fails closed (decision 36): an unknown turn start must never override a park
            log(f"[{project}/{slug}] l3.turn returned no start timestamp — cannot date the turn, so any park stands")
        t2 = S.load_task(project, slug)
        # critic said revise and the L3 parked with a revision brief *in this turn*: re-propose, at most twice, then it
        # waits for Burak. A park by Burak, or one the L3 made for him in an earlier turn (I-008), must stand.
        if (t2["state"] == "parked" and crit and crit.get("verdict") == "revise" and turn_start is not None
                and _l3_parked_during_turn(project, slug, turn_start)):
            n = int(t2.get("revisions", 0))
            if n < 2:
                with S.project_lock(project):
                    t3 = S.load_task(project, slug); t3["revisions"] = n + 1; t3["proposal_started"] = None; S.save_task(project, t3)
                T.archive_proposal(S.task_dir(project, slug))  # history as -vN; the reviser reads the latest critique-vN
                T.unpark(project, slug, actor="altd")
                log(f"[{project}/{slug}] critic revise → revision {n + 1} queued (bounded at 2)")
                return
            T.fyi(project, slug, f"{slug}: parked after {n} revisions — the proposal and critic keep disagreeing; needs your read (task folder has proposal-v*.md / critique-v*.json).")
            return
    _finish_proposal(project, slug, p, t2)


def on_l2_finished(project: str, item: dict) -> None:
    t = item["task"]
    slug = t["slug"]
    if item.get("limited"):  # decision 44: hold, remember when to come back, say it once
        until, a = item["limited"], item.get("agent") or {}
        news = engines.note_usage_limit(until, f"L2 {a.get('id', '')} of {slug}")
        with S.project_lock(project):
            t0 = S.load_task(project, slug); t0["resume_after"] = until; S.save_task(project, t0)
        T.block(project, slug, f"usage limit: the subscription window is exhausted, resets {until} — Altitude resumes this L2 itself after that")
        if news:
            T.fyi(project, slug, f"Usage limit hit (5-hour window). Dispatch, proposals and L3 turns are held until {until}; "
                                 f"blocked L2s resume automatically, WIP-throttled, oldest first.", actor="altd")
        log(f"[{project}/{slug}] L2 hit the usage limit → blocked until {until}")
        return
    with S.project_lock(project):  # a new report: whatever L3 did with the previous one no longer counts
        t0 = S.load_task(project, slug); t0["l3_handled"] = None; S.save_task(project, t0)
    if item.get("needs_input"):
        a = item.get("agent") or {}
        reason = f"L2 is idle without a report — probably waiting for a permission or a question. Attach: `claude attach {a.get('id', '')}`; or answer via the card (Resume sends your note into the session)."
        T.block(project, slug, reason)
        T.fyi(project, slug, f"{slug}: L2 idle {dispatch.IDLE_NEEDS_INPUT_SECONDS}s without finishing — needs input? attach {a.get('id', '')}")
        log(f"[{project}/{slug}] L2 idle → blocked (needs input)")
        return
    if item.get("died"):
        a = item.get("agent") or {}
        engine = dispatch.task_l2_engine(project, t)
        if engine == "codex":
            detail = item.get("error") or f"Codex wrapper {a.get('id', '')} exited"
            recovery = ("Resume restarts the durable Codex session, or starts fresh from progress.md if no thread id "
                        "was emitted")
        else:
            detail = f"`claude agents` state=failed (agent {a.get('id', '')})"
            recovery = f"Resume re-attaches its transcript (agent {a.get('id', '')})"
        improve.system_fault("l2-died", f"{engine} L2 worker ({t.get('dispatch_id')}) died without a report: "
                             f"{detail}", project=project, task=slug)
        T.block(project, slug, f"L2 session died before reporting (Altitude fault, not the L2 worker) — {recovery}")
        log(f"[{project}/{slug}] L2 died → blocked; fault raised")
        return
    v = verify.verify(project, slug)
    log(f"[{project}/{slug}] L2 finished; verdict {v['verdict']}; problems {v['problems']}")
    T.set_spend(project, slug, **{k: val for k, val in v.get("spend", {}).items() if val is not None})
    if v["verdict"] == "fault":
        T.block(project, slug, f"verifier fault (Altitude, not the L2): {v.get('fault')}")
        log(f"[{project}/{slug}] verifier fault → blocked; fault raised")
        return
    if v["verdict"] == "missing":
        T.block(project, slug, "L2 session ended without a report (report.json missing)")
        return
    elif v["verdict"] == "blocked":
        T.report(project, slug, v)
        T.block(project, slug, (v.get("report") or {}).get("blocked") or "blocked (see report)")
        return  # blocked reports belong to the evidence coordinator, never a second report-landed turn
    else:
        T.report(project, slug, v)
    report_turn(project, t, v)


def _report_evidence(project: str, slug: str, verdict: dict) -> str:
    """Stable evidence key: report bytes plus the verifier result that prompted judgement."""
    report = S.task_dir(project, slug) / "report.json"
    try:
        report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    except OSError:
        report_sha = "missing"
    raw = json.dumps({"report": report_sha, "verified": verdict}, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _report_claim_live(claim: dict, lease: dict) -> bool:
    if not claim.get("generation"):
        return False
    age = dispatch._seconds_since(claim.get("started") or "")
    owner_live = (age <= config.L3_TURN_TIMEOUT
                  and l3.process_live(claim.get("owner_pid"), claim.get("owner_pid_start")))
    lease_matches = (lease.get("evidence") or {}).get("report_generation") == claim.get("generation")
    return owner_live or lease_matches


def _report_fail(rec: dict, detail: str) -> bool:
    """Charge one unchanged-evidence failure; return True when its one fault is due."""
    attempts = int(rec.get("attempts") or 0) + 1
    rec["attempts"] = attempts
    rec["last_error"] = detail[:500]
    rec["last_attempt"] = S.now()
    delay = config.REPORT_RETRY_SECONDS[min(attempts - 1, len(config.REPORT_RETRY_SECONDS) - 1)]
    rec["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
    rec["claim"] = None
    if attempts >= config.REPORT_MAX_FAILURES and not rec.get("faulted"):
        rec["faulted"] = S.now()
        return True
    return False


def _report_ready(project: str, task: dict, verdict: dict) -> bool:
    """Read-only scheduling check; ``report_turn`` owns the atomic claim."""
    fingerprint = _report_evidence(project, task["slug"], verdict)
    rec = task.get("report_recovery") or {}
    if rec.get("fingerprint") != fingerprint:
        return True
    if rec.get("breaker_deferred") and _breaker_open():
        return False
    lease = l3.lease_info(project)
    if _report_claim_live(rec.get("claim") or {}, lease):
        return False
    if int(rec.get("attempts") or 0) >= config.REPORT_MAX_FAILURES:
        return False
    return not rec.get("retry_after") or str(rec["retry_after"]) <= S.now()


def _claim_report_turn(project: str, slug: str, verdict: dict) -> tuple[str, str] | None:
    """Claim one evidence generation, settling a dead prior claim before any retry."""
    fingerprint = _report_evidence(project, slug, verdict)
    lease = l3.lease_info(project)
    fault = False
    claimed = None
    with S.project_lock(project):
        live = S.load_task(project, slug)
        # A newly blocked report normally belongs to the evidence coordinator, but an already
        # queued report-landed caller is still valid. The generic L3 lease serializes the two;
        # only `reported` tasks are programmatically requeued below.
        if live.get("state") not in ("reported", "blocked") or live.get("l3_handled"):
            return None
        rec = live.get("report_recovery") or {}
        if rec.get("fingerprint") != fingerprint:
            rec = {"version": 1, "fingerprint": fingerprint, "attempts": 0,
                   "verdict": verdict, "retry_after": None, "last_error": None,
                   "faulted": None, "claim": None}
        prior = rec.get("claim") or {}
        if _report_claim_live(prior, lease):
            return None
        if prior.get("generation"):
            fault = _report_fail(rec, "stale report-turn claim after daemon/engine exit")
        if (int(rec.get("attempts") or 0) < config.REPORT_MAX_FAILURES
                and (not rec.get("retry_after") or str(rec["retry_after"]) <= S.now())):
            generation = secrets.token_hex(16)
            rec.pop("breaker_deferred", None)
            rec.pop("breaker_deferred_at", None)
            rec["claim"] = {"generation": generation, "owner_pid": os.getpid(),
                            "owner_pid_start": l3._proc_start_time(os.getpid()), "started": S.now()}
            claimed = (generation, fingerprint)
        live["report_recovery"] = rec
        S.save_task(project, live)
    if fault:
        improve.system_fault("report-recovery",
                             f"{project}/{slug}: L3 failed twice to disposition unchanged report evidence",
                             project=project, task=slug)
    return claimed


def _settle_report_turn(project: str, slug: str, generation: str, fingerprint: str,
                        before_state: str, result: dict) -> bool:
    """Settle only this report generation. Return whether the task was dispositioned."""
    fault = False
    disposed = False
    with S.project_lock(project):
        live = S.load_task(project, slug)
        rec = live.get("report_recovery") or {}
        claim = rec.get("claim") or {}
        if rec.get("fingerprint") != fingerprint or claim.get("generation") != generation:
            return live.get("state") != before_state
        if live.get("state") != before_state:
            live["l3_handled"] = S.now()
            live.pop("report_recovery", None)
            disposed = True
        elif result.get("busy") or result.get("limited"):
            # Another generic turn or quota pressure is not a judgement failure.
            rec["claim"] = None
            rec["retry_after"] = (datetime.now(timezone.utc)
                                  + timedelta(seconds=config.REPORT_RETRY_SECONDS[0])).replace(microsecond=0).isoformat()
            rec["last_error"] = "L3 busy" if result.get("busy") else "L3 engine quota limited"
            live["report_recovery"] = rec
        else:
            detail = (f"L3 error: {result.get('error')}" if result.get("error")
                      else "L3 made no task disposition")
            fault = _report_fail(rec, detail)
            live["report_recovery"] = rec
        S.save_task(project, live)
    if fault:
        improve.system_fault("report-recovery",
                             f"{project}/{slug}: L3 failed twice to disposition unchanged report evidence",
                             project=project, task=slug)
    return disposed


def _defer_report_for_breaker(project: str, slug: str, verdict: dict) -> None:
    """Persist a no-cost hold after mechanical closeout fails under an open breaker."""
    fingerprint = _report_evidence(project, slug, verdict)
    with S.project_lock(project):
        live = S.load_task(project, slug)
        if live.get("state") not in ("reported", "blocked") or live.get("l3_handled"):
            return
        rec = live.get("report_recovery") or {}
        if rec.get("fingerprint") != fingerprint:
            rec = {"version": 1, "fingerprint": fingerprint, "attempts": 0,
                   "verdict": verdict, "retry_after": None, "last_error": None,
                   "faulted": None, "claim": None}
        rec["breaker_deferred"] = True
        rec["breaker_deferred_at"] = S.now()
        rec["last_error"] = "global recovery breaker open"
        live["report_recovery"] = rec
        S.save_task(project, live)


def report_turn(project: str, t: dict, v: dict) -> None:
    """Close a mechanically clean S/M report, otherwise run the L3's report-landed turn.

    The clean-close gate uses the on-disk report and live task state. It requires an ok verifier with no problems or
    signals, no merge hold, exact task-owned coordinator merge provenance, at least one numeric GitHub main run bound
    to each merge SHA with completed/success status, a healthy or not-applicable
    deploy, only fixed or dismissed review findings, and no decisions, blocks, FYIs, follow-ups, or post-mortem work.
    Any malformed, corrupt, stale, or raced state fails closed to L3; corrupt JSON also raises a decision-36 system
    fault. The durable evidence claim is settled only after a task-state disposition, so a killed, failed, or no-op
    turn is retried by `resume_stranded_reports` instead of leaving the task waiting for nobody.
    """
    slug = t["slug"]
    if (v.get("verdict") == "ok" and not v.get("problems") and not v.get("signals")
            and t.get("class") != "L"):
        report_error = task_error = None
        try:
            with S.project_lock(project):
                try:
                    live = S.load_task(project, slug)
                except ValueError as exc:
                    task_error = exc
                    live, report = {}, None
                else:
                    try:
                        report = S.read_json(S.task_dir(project, slug) / "report.json", {})
                    except ValueError as exc:
                        report_error = exc
                        report = None
        except (KeyError, OSError):
            live, report = {}, None
        if task_error is not None:
            improve.system_fault("task-json", f"{project}/{slug}: {task_error}", project=project, task=slug)
        if report_error is not None:
            improve.system_fault("report-json", f"{project}/{slug}: {report_error}", project=project, task=slug)
        if isinstance(report, dict):
            landed = report.get("landed") or {}
            if not isinstance(landed, dict):
                landed = {}
            deploy = str(landed.get("deploy") or "")
            deploy_status = (deploy.split(maxsplit=1) or [""])[0].rstrip(":")
            prs = landed.get("prs") or []
            runs = landed.get("main_runs") or []
            review = report.get("review") or []
            live_hold_merge = live.get("hold_merge")
            provenance_ok = verify.clean_close_provenance_matches(project, slug, v, live, report)
            if (not report.get("decisions") and not report.get("blocked") and not report.get("fyi")
                    and not report.get("follow_ups") and deploy_status in ("healthy", "not-applicable")
                    and isinstance(prs, list) and all(isinstance(pr, dict) and pr.get("merged") is True for pr in prs)
                    and isinstance(runs, list)
                    and all(isinstance(run, dict) and isinstance(run.get("id"), str) and run.get("id", "").isdigit()
                            and run.get("conclusion") == "success" for run in runs)
                    and isinstance(review, list)
                    and all(isinstance(item, dict) and item.get("disposition") in ("fixed", "dismissed")
                            for item in review)
                    and live.get("state") == "reported" and not live_hold_merge and provenance_ok):
                pr_text = ", ".join("PR #{} ({})".format(pr.get("number"), pr.get("title") or "untitled") for pr in prs) or "No PRs recorded"
                run_text = ", ".join("{}: {}".format(run.get("id"), run.get("conclusion")) for run in runs) or "none recorded"
                fixed = sum(item.get("disposition") == "fixed" for item in review)
                dismissed = sum(item.get("disposition") == "dismissed" for item in review)
                clean_digest = (f"No decisions. {pr_text} merged. Main runs: {run_text}. Deploy: {deploy}. "
                                f"Review findings: {fixed} fixed, {dismissed} dismissed.")
                try:
                    T.done(project, slug, actor="altd", digest=clean_digest)
                except T.TransitionError:
                    log(f"[{project}/{slug}] clean close lost the state race → L3 turn")
                else:
                    T.fyi(project, slug, f"{slug}: closed by altd without an L3 turn — nothing to judge: verifier verdict ok; "
                          f"task class {t.get('class')}; hold_merge unset; PRs merged: {pr_text}; "
                          f"main runs: {run_text}; deploy: {deploy}; no decisions, blocked items, FYIs, follow-ups, or "
                          "post-mortem signals.", actor="altd")
                    with S.project_lock(project):
                        t2 = S.load_task(project, slug); t2["l3_handled"] = S.now()
                        t2.pop("report_recovery", None); S.save_task(project, t2)
                    log(f"[{project}/{slug}] clean report closed by altd; no L3 turn")
                    return
    if _breaker_open():
        _defer_report_for_breaker(project, slug, v)
        log(f"[{project}/{slug}] report closeout retained; L3 recovery held by global breaker")
        return
    claim_error = None
    try:
        claim = _claim_report_turn(project, slug, v)
    except (KeyError, OSError, ValueError) as exc:
        # A missing/corrupt live status already failed the clean-close gate. The generic project L3 lease
        # still serializes this fault-path turn, but there is no valid task record in which to persist its
        # evidence claim. Preserve the upstream fail-closed judgement path and make the durability loss visible.
        claim_error = exc
        claim = ("", "")
    if claim is None:
        return
    report_generation, report_fingerprint = claim
    inc = improve.index()
    # [R-007] The report-landed substance belongs in the task record, not a turn-log reply.
    header = (f"Report landed for `{slug}` ({t['class']}): verdict **{v['verdict']}**. Problems: {v['problems'] or 'none'}. "
              f"Post-mortem signals: {v['signals'] or 'none'}. Spend: {v.get('spend')}. PRs: {v.get('prs')}. "
              f"Report excerpt: {json.dumps(v.get('report') or {})[:1500]}\n"
              f"Read <task_dir>/report.md if you need more. Incidents in other projects (for scope decisions): "
              f"{json.dumps([{k: r.get(k) for k in ('project', 'id', 'tags')} for r in inc[-20:]])}\n\n"
              "Do the report-landed procedure from your instructions: preserve a digest/FYI and block or resume with the exact gap; "
              "R-014 forbids `alt task done` and every automated landing claim until trusted remote landing integration exists. "
              "then the post-mortem pass (incident + right-sized rule, or one line saying nothing went wrong). "
              "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    if claim_error is not None:
        try:
            _automatic_call("l3-report", project, slug, {"claim_error": str(claim_error)},
                            lambda: l3.turn(project, header, trigger="report-landed"))
        finally:
            log(f"[{project}/{slug}] L3 turn completed but l3_handled could not be stamped: {claim_error}")
        return
    before_state = S.load_task(project, slug).get("state")
    try:
        res = _automatic_call(
            "l3-report", project, slug, {"report_generation": report_generation, "fingerprint": report_fingerprint},
            lambda: l3.turn(project, header, trigger="report-landed",
                            evidence={"report_slug": slug, "report_generation": report_generation,
                                      "report_fingerprint": report_fingerprint}))
        if res is _BREAKER_BLOCKED:
            res = {"busy": True, "error": "global recovery breaker open"}
    except Exception as exc:  # the retry record, not the thread table, must survive an engine/daemon failure
        res = {"text": "", "error": f"{type(exc).__name__}: {exc}"}
    disposed = _settle_report_turn(project, slug, report_generation, report_fingerprint,
                                   before_state, res or {})
    if not disposed:
        detail = f"L3 error: {(res or {}).get('error')}" if (res or {}).get("error") else "L3 made no task disposition"
        log(f"[{project}/{slug}] report turn not handled ({detail}); evidence-keyed retry policy retained it")


def resume_stranded_reports(project: str) -> None:
    """Retry reported work; atomically promote only a provenance-matched clean blocked report."""
    for t in S.list_tasks(project):
        if t.get("state") not in ("reported", "blocked") or t.get("l3_handled"):
            continue
        report_path = S.task_dir(project, t["slug"]) / "report.json"
        if not report_path.exists():
            continue
        key = f"finished:{project}:{t['slug']}"
        with _bg_guard:
            if (_bg.get(key) or threading.Thread()).is_alive():
                continue
        v = t.get("verified") or (t.get("report_recovery") or {}).get("verdict") or {
                                  "verdict": "missing", "problems": ["no verified report on the task"], "signals": [],
                                  "spend": {}, "prs": t.get("prs", []), "report": {}}
        if t.get("state") == "blocked":
            try:
                report = S.read_json(report_path)
            except (OSError, ValueError) as exc:
                log(f"[{project}/{t['slug']}] cannot read stranded report: {exc}")
                continue
            last_block = next((event for event in reversed(S.read_events(project, t["slug"]))
                               if event.get("kind") == "state" and event.get("to") == "blocked"), None)
            if not (v.get("verdict") == "ok" and isinstance(report, dict) and not report.get("blocked")
                    and "attempt" in v and v.get("attempt") == t.get("attempt")
                    and last_block and last_block.get("frm") == "running"):
                continue
            try:
                t = T.report(project, t["slug"], v, expected_state="blocked",
                             expected_attempt=t.get("attempt"), expected_block_from="running")
            except (T.TransitionError, KeyError) as exc:
                log(f"[{project}/{t['slug']}] stranded report promotion skipped after a concurrent change: {exc}")
                continue
        if not _report_ready(project, t, v):
            continue
        log(f"[{project}/{t['slug']}] report turn resumed (previous run did not finish)")
        spawn(key, report_turn, project, t, v)


def _blocked_recovery_path(project: str) -> Path:
    return config.project_dir(project) / "blocked-recovery.json"


def _blocked_record(project: str) -> dict:
    return S.read_json(_blocked_recovery_path(project), {}) or {"version": 1, "tasks": {}}


def _blocked_input_signature(project: str, tasks: list[dict]) -> str:
    local = []
    for task in tasks:
        report = S.task_dir(project, task["slug"]) / "report.json"
        try:
            report_signal = [report.stat().st_mtime_ns, report.stat().st_size,
                             hashlib.sha256(report.read_bytes()).hexdigest()]
        except OSError:
            report_signal = None
        local.append({key: task.get(key) for key in ("slug", "state", "blocked_reason", "attempt", "dispatch_id",
                                                      "session_id", "agent_id", "hold_merge", "worktree", "branch")})
        local[-1]["envelope"] = task.get("envelope")
        local[-1]["report"] = report_signal
    running = []
    for task in S.list_tasks(project):
        if task.get("state") == "running":
            try:
                paths = dispatch.task_paths(project, task)
            except Exception as exc:  # input signal is fault-tolerant; full status records the error
                paths = [f"error:{type(exc).__name__}"]
            running.append([task["slug"], sorted(paths)])
    raw = json.dumps({"blocked": local, "running": sorted(running)}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _blocked_evidence(raw: dict) -> dict:
    def selected(source, names):
        source = source if isinstance(source, dict) else {}
        return {name: source.get(name) for name in names}
    prs = []
    for pr in raw.get("prs") or []:
        checks = selected(pr.get("checks"), ("total", "passed", "failed", "rejected", "pending"))
        checks["failing"] = sorted((pr.get("checks") or {}).get("failing") or [])
        checks["rejecting"] = sorted((pr.get("checks") or {}).get("rejecting") or [])
        prs.append({**selected(pr, (
            "number", "state", "merged", "head_sha", "base_sha", "merge_sha", "mergeable",
            "merge_state_status", "review_decision",
        )), "checks": checks})
    runs = []
    for run in ((raw.get("l1_runs") or {}).get("runs") or []):
        runs.append(selected(run, (
            "name", "role", "engine", "done", "stale", "pr", "review_pr",
            "review_head_at_start", "reviewed_head_sha",
        )))
    merge_requests = []
    for rec in (raw.get("merge_requests") or []):
        item = selected(rec, (
            "pr", "state", "generation", "dispatch_id", "task_attempt", "branch", "base",
            "base_sha", "head_sha", "candidate_tree", "gate_mode", "candidate_gate", "result",
        ))
        # Claim/poll phases are one pending lifecycle state. Attempts, retry
        # timestamps, claims, and transient error text are intentionally absent:
        # unchanged external CI must not manufacture new evidence for L3.
        if item.get("state") in {"requested", "processing", "waiting"}:
            item["state"] = "pending"
        merge_requests.append(item)
    return {
        "v": 2,
        "gate": raw.get("gate"),
        "repository": selected(raw.get("repository"), ("branch", "dirty", "head", "origin_sha", "ahead", "behind", "determinate", "error")),
        "task": selected(raw, ("blocked_reason", "attempt", "dispatch_id", "session_id", "agent_id",
                               "hold_merge", "worktree", "branch")),
        "wip_hold": raw.get("wip_hold"),
        "report": selected(raw.get("report_json"), ("exists", "sha256")),
        "envelope": selected(raw.get("envelope"), ("l1_in_flight", "subagent_launches", "max_turns", "verification")),
        "envelope_file": selected(raw.get("envelope_file"), ("l1_in_flight", "subagent_launches", "max_turns", "verification")),
        "counts": selected(raw.get("counts"), ("subagent_launches", "edits")),
        "l1": {"in_flight": (raw.get("l1_runs") or {}).get("in_flight"),
               "runs": sorted(runs, key=lambda row: json.dumps(row, sort_keys=True, default=str))},
        "prs": sorted(prs, key=lambda pr: int(pr.get("number") or 0)),
        "merge_requests": sorted(merge_requests, key=lambda rec: int(rec.get("pr") or 0)),
        "main_run": selected(raw.get("main_run"), ("id", "head_sha", "status", "conclusion")),
        "error_sources": sorted({str(error).split(":", 1)[0] for error in (raw.get("errors") or [])}),
    }


def _blocked_fingerprint(evidence: dict) -> str:
    raw = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _blocked_batch_precheck(project: str, batch_id: str, items: list[dict]) -> bool:
    record = _blocked_record(project)
    if (record.get("batch") or {}).get("id") != batch_id:
        return False
    for item in items:
        try:
            live = S.load_task(project, item["slug"])
        except KeyError:
            return False
        if (live.get("state") != "blocked" or live.get("needs_user")
                or str(live.get("blocked_reason") or "") != item["blocked_reason"]
                or live.get("dispatch_id") != item["dispatch_id"]):
            return False
    return True


def _blocked_item_disposed(project: str, batch_id: str, item: dict, batch_started: str) -> bool:
    """Whether durable task/event state proves this exact recovery item was dispositioned.

    The same predicate settles both a normally returning turn and an orphaned batch found after restart:
    an ack written before altd died must not be charged as a no-op merely because its caller never returned.
    """
    try:
        live = S.load_task(project, item["slug"])
    except KeyError:
        return True
    explicit = str((live.get("needs_user") or {}).get("asked") or "") >= batch_started
    acknowledged = False
    if live.get("state") == "blocked":
        acknowledged = any(
            ev.get("kind") == "blocked-ack" and ev.get("by") == "l3"
            and ev.get("recovery_batch") == batch_id
            and ev.get("blocked_reason") == item["blocked_reason"]
            and ev.get("dispatch_id") == item["dispatch_id"]
            for ev in reversed(S.read_events(project, item["slug"]))
        )
    return live.get("state") != "blocked" or explicit or acknowledged


def _blocked_fail(entry: dict, fingerprint: str, error: str) -> bool:
    """Record one failure for this evidence. Return True only when a new system fault is due."""
    if entry.get("fingerprint") != fingerprint:
        return False
    failures = int(entry.get("failures") or 0) + 1
    entry["failures"] = failures
    entry["last_error"] = error[:500]
    delay = config.BLOCKED_RETRY_SECONDS[min(failures - 1, len(config.BLOCKED_RETRY_SECONDS) - 1)]
    entry["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    if failures >= config.BLOCKED_MAX_FAILURES and not entry.get("faulted"):
        entry["faulted"] = S.now()
        return True
    return False


def reconcile_blockers(project: str) -> None:
    """Programmatically scan blocked evidence, then send one bounded changed-evidence batch to L3."""
    snapshots = [t for t in S.list_tasks(project) if t.get("state") == "blocked" and not t.get("resume_after")]
    signature = _blocked_input_signature(project, snapshots)
    path = _blocked_recovery_path(project)
    now = S.now()
    stale_faults = []

    with S.project_lock(project):
        record = _blocked_record(project)
        record.setdefault("version", 1); record.setdefault("tasks", {})
        record_dirty = False
        batch = record.get("batch") or {}
        if batch:
            age = dispatch._seconds_since(batch.get("started") or "")
            if age <= config.BLOCKED_CLAIM_TIMEOUT and (_pid_alive(batch.get("owner_pid")) or _pid_alive(batch.get("engine_pid"))):
                return
            for item in batch.get("items") or []:
                entry = record["tasks"].get(item["slug"]) or {}
                if (_blocked_item_disposed(project, batch.get("id") or "", item, batch.get("started") or "")
                        and entry.get("fingerprint") == item["fingerprint"]):
                    entry["handled_fingerprint"] = item["fingerprint"]
                    entry["failures"] = 0; entry["retry_after"] = None; entry["last_error"] = None
                elif _blocked_fail(entry, item["fingerprint"],
                                   "stale blocked-recovery claim after daemon/engine exit"):
                    stale_faults.append(item["slug"])
            record["batch"] = None
            record_dirty = True
        scan = record.get("scan") or {}
        if scan.get("token") and not scan.get("completed"):
            age = dispatch._seconds_since(scan.get("started") or "")
            if age < config.BLOCKED_SCAN_CLAIM_TIMEOUT and _pid_alive(scan.get("owner_pid")):
                if record_dirty:
                    S.write_json(path, record)
                return
            scan = {"token": None, "owner_pid": None, "started": None, "completed": None,
                    "input_signature": None, "last_error": "stale/incomplete scan claim cleared"}
            record["scan"] = scan
            record_dirty = True
        scan_age = dispatch._seconds_since(scan.get("completed") or "")
        scan_due = scan.get("input_signature") != signature or scan_age >= config.BLOCKED_SCAN_SECONDS
        if scan_due:
            token = f"{os.getpid()}:{datetime.now(timezone.utc).timestamp()}"
            record["scan"] = {"token": token, "owner_pid": os.getpid(), "started": now,
                              "completed": None, "input_signature": signature}
            S.write_json(path, record)
        else:
            token = None
            if record_dirty:
                S.write_json(path, record)

    for slug in stale_faults:
        improve.system_fault("blocked-recovery", f"{project}/{slug}: two stale L3 recovery claims for unchanged evidence",
                             project=project, task=slug)

    if token:
        try:
            scanned = {}
            for snapshot in snapshots:
                if snapshot.get("needs_user"):
                    continue
                raw = task_status.status(project, snapshot["slug"])
                evidence = _blocked_evidence(raw)
                scanned[snapshot["slug"]] = {"fingerprint": _blocked_fingerprint(evidence), "evidence": evidence,
                                              "created": snapshot.get("created") or ""}
        except Exception as exc:  # a failed scan is closed and retried on schedule, never left claimed
            with S.project_lock(project):
                record = _blocked_record(project)
                if (record.get("scan") or {}).get("token") == token:
                    record["scan"] = {"token": None, "owner_pid": None, "started": None, "completed": S.now(),
                                      "input_signature": signature,
                                      "last_error": f"{type(exc).__name__}: {exc}"[:500]}
                    S.write_json(path, record)
            return
        with S.project_lock(project):
            record = _blocked_record(project)
            if (record.get("scan") or {}).get("token") != token:
                return
            previous = record.setdefault("tasks", {})
            merged = {}
            for slug, fresh in scanned.items():
                old = previous.get(slug) or {}
                if old.get("fingerprint") == fresh["fingerprint"]:
                    fresh.update({key: old.get(key) for key in ("handled_fingerprint", "failures", "retry_after",
                                                                "last_error", "faulted") if old.get(key) is not None})
                else:
                    fresh.update({"failures": 0, "retry_after": None, "last_error": None, "faulted": None})
                merged[slug] = fresh
            record["tasks"] = merged
            record["scan"] = {"token": None, "owner_pid": None, "started": None, "completed": S.now(),
                              "input_signature": signature}
            S.write_json(path, record)

    if _breaker_open():
        return

    with S.project_lock(project):
        record = _blocked_record(project)
        if record.get("batch"):
            return
        live_by_slug = {t["slug"]: t for t in S.list_tasks(project) if t.get("state") == "blocked"}
        candidates = []
        for slug, entry in record.get("tasks", {}).items():
            task = live_by_slug.get(slug)
            if not task or task.get("resume_after") or task.get("needs_user") or dispatch.recovery_pending(project, task):
                continue
            if entry.get("fingerprint") == entry.get("handled_fingerprint"):
                continue
            if int(entry.get("failures") or 0) >= config.BLOCKED_MAX_FAILURES:
                continue
            if entry.get("retry_after") and str(entry["retry_after"]) > now:
                continue
            candidates.append((0 if not task.get("l3_handled") else 1, entry.get("created") or "", slug, task, entry))
        candidates.sort(key=lambda row: row[:3])
        chosen = candidates[:config.BLOCKED_BATCH_LIMIT]
        if not chosen:
            return
        batch_id = hashlib.sha256(f"{project}:{now}:{os.getpid()}".encode()).hexdigest()[:16]
        items = [{"slug": slug, "fingerprint": entry["fingerprint"],
                  "blocked_reason": str(task.get("blocked_reason") or ""), "dispatch_id": task.get("dispatch_id")}
                 for _, _, slug, task, entry in chosen]
        record["batch"] = {"id": batch_id, "owner_pid": os.getpid(), "engine_pid": None,
                           "started": now, "items": items}
        S.write_json(path, record)

    def on_start(pid):
        with S.project_lock(project):
            record = _blocked_record(project)
            if (record.get("batch") or {}).get("id") == batch_id:
                record["batch"]["engine_pid"] = pid
                S.write_json(path, record)

    prompt_items = [{"slug": item["slug"], "status": record["tasks"][item["slug"]]["evidence"]} for item in items]
    prompt = ("Altitude found blocked tasks whose programmatic lease/worker/report/PR/check/gate/repository evidence changed. "
              "Process every item: run `alt task status <slug>`, then acknowledge/keep blocked, resume, mark needs-user, reject, or repair stale state. "
              "R-014 forbids finish/close and landing claims until trusted remote landing integration exists. "
              "A temporary operational prerequisite (lease, worker, sandbox repair, PR/check/main/repository state) must stay blocked: "
              f"acknowledge this batch with `alt task block <slug> --recovery-batch {batch_id} "
              "--reason \"the current concrete prerequisite\"`; never park it, "
              "because blocking keeps it under automatic evidence watch. Never park from a reconciliation turn. Do not merely "
              "restate the blocker without the command. Open/conflicting PRs are operational work, "
              "but obey hold_merge and never merge a held PR. Only a genuine always-list or non-obvious executive choice may "
              "reach Burak; mark it with `alt task needs-user <slug> --reason \"the concise decision needed\"`.\n\n"
              + json.dumps(prompt_items, indent=2, sort_keys=True, default=str))
    try:
        res = _automatic_call(
            "l3-reconcile", project, None, {"batch": batch_id, "items": items},
            lambda: l3.turn(project, prompt, trigger="reconcile", on_start=on_start,
                            precheck=lambda: _blocked_batch_precheck(project, batch_id, items),
                            evidence={"recovery_batch": batch_id,
                                      "blocked_fingerprints": [item["fingerprint"] for item in items]}))
        if res is _BREAKER_BLOCKED:
            res = {"busy": True, "error": "global recovery breaker open"}
    except Exception as exc:  # settle the durable claim immediately; do not wait 21 minutes on the live daemon PID
        res = {"error": f"{type(exc).__name__}: {exc}", "text": ""}
    busy = bool((res or {}).get("busy"))
    skipped = bool((res or {}).get("skipped")) and not busy
    limited = bool((res or {}).get("limited"))
    engine_error = (res or {}).get("error")
    faults = []
    with S.project_lock(project):
        record = _blocked_record(project)
        if (record.get("batch") or {}).get("id") != batch_id:
            return
        for item in items:
            entry = record.get("tasks", {}).get(item["slug"]) or {}
            disposed = _blocked_item_disposed(project, batch_id, item, now)
            if skipped:
                pass
            elif disposed:
                entry["handled_fingerprint"] = item["fingerprint"]
                entry["failures"] = 0; entry["retry_after"] = None; entry["last_error"] = None
            elif limited or busy:
                entry["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=config.BLOCKED_RETRY_SECONDS[-1])).isoformat()
                entry["last_error"] = "L3 busy" if busy else "L3 engine quota limited"
            elif engine_error:
                if _blocked_fail(entry, item["fingerprint"], f"L3 engine error: {engine_error}"):
                    faults.append(item["slug"])
            elif _blocked_fail(entry, item["fingerprint"], "L3 returned without changing task state or explicitly escalating it"):
                faults.append(item["slug"])
        record["batch"] = None
        if skipped:  # local identity changed after the scan: rescan on the very next tick, not after claim timeout
            record["scan"] = {"token": None, "owner_pid": None, "started": None, "completed": None,
                              "input_signature": None, "last_error": "batch precheck changed"}
        S.write_json(path, record)
    for slug in faults:
        improve.system_fault("blocked-recovery", f"{project}/{slug}: L3 failed twice to disposition unchanged blocked evidence",
                             project=project, task=slug)


def resume_stranded_blockers(project: str) -> None:
    """Start one project sweep only when blocked work or durable recovery cleanup exists."""
    relevant = any(task.get("state") == "blocked" and not task.get("resume_after")
                   for task in S.list_tasks(project))
    path = _blocked_recovery_path(project)
    durable_cleanup = False
    if path.exists():
        record = _blocked_record(project)
        scan = record.get("scan") or {}
        durable_cleanup = bool(record.get("batch") or record.get("tasks")
                               or (scan.get("token") and not scan.get("completed")))
    if relevant or durable_cleanup:
        spawn(f"blocked-sweep:{project}", reconcile_blockers, project)


def dispatch_waiting(project: str) -> None:
    for t in S.list_tasks(project):
        if t["state"] != "approved":
            continue
        hold = dispatch.wip_hold(project, t)
        if hold and dispatch.per_task_hold(hold):  # decision 51: a leased task must not block the queue behind it
            continue
        if hold:
            S.write_json(config.project_dir(project) / "hold.json", {"at": S.now(), "reason": hold})
            return
        try:
            res = _automatic_call("dispatch", project, t["slug"],
                                  _task_evidence(project, t, "created", "attempt", "approved_at"),
                                  dispatch.run, project, t["slug"])
            if res is _BREAKER_BLOCKED:
                return
            log(f"[{project}/{t['slug']}] dispatched {res['dispatch_id']} agent={res['agent'].get('id') if res.get('agent') else None}")
        except Exception as e:  # noqa: BLE001
            log(f"[{project}/{t['slug']}] dispatch failed: {e}")
            current = S.load_task(project, t["slug"])
            if current.get("state") == "approved" and current.get("pending_dispatch"):
                # The launcher may have spawned before agent discovery or the
                # lifecycle transition failed. Keep the exact durable claim
                # approved, leased, and eligible for restart adoption.
                S.append_event(project, t["slug"], "dispatch-awaiting-adoption",
                               error=f"{type(e).__name__}: {e}"[:500])
                continue
            T.block(project, t["slug"], f"dispatch failed: {e}"[:300])
    hp = config.project_dir(project) / "hold.json"
    if hp.exists():
        hp.unlink()


def process_merge_request(project: str, slug: str, pr: int) -> None:
    """Keep durable merge intent pending until R-014 remote-evidence integration lands."""
    result = merge_coordinator.process(project, slug, pr)
    log(f"[{project}/{slug}] merge request PR #{pr}: {result.get('state')}")


def drain_hook_faults() -> None:
    """Hooks run inside L2 sessions and cannot reach the server: they append to monitor/hook-faults.log; the tick raises them."""
    p = config.MONITOR_DIR / "hook-faults.log"
    if not p.exists():
        return
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    p.unlink()
    for ln in lines[-20:]:
        improve.system_fault("hook", ln[:400])


DONE_CLEANUP_RETRY_SECONDS = 15 * 60


def cleanup_done_task(project: str, task: dict) -> bool:
    """Run one due done-cleanup attempt and durably record success or its retry claim."""
    retry = task.get("cleanup_retry") or {}
    try:
        if retry.get("after") and datetime.fromisoformat(str(retry["after"])) > datetime.now(timezone.utc):
            return False
    except (TypeError, ValueError):
        pass

    notes = dispatch.cleanup_after_done(project, task)
    complete = bool(getattr(notes, "complete", True))
    pending = list(getattr(notes, "pending", []))
    branches = list(getattr(notes, "branches", []))
    retry_after = None
    with S.project_lock(project):
        live = S.load_task(project, task["slug"])
        if live.get("state") != "done" or live.get("cleaned"):
            return bool(live.get("cleaned"))
        if complete:
            live["cleaned"] = S.now()
            live.pop("cleanup_retry", None)
        else:
            attempt = int((live.get("cleanup_retry") or {}).get("attempt") or 0) + 1
            retry_after = (datetime.now(timezone.utc) + timedelta(seconds=DONE_CLEANUP_RETRY_SECONDS)).replace(
                microsecond=0).isoformat()
            live["cleanup_retry"] = {"after": retry_after, "attempt": attempt,
                                     "pending": pending, "branches": branches, "last_attempt": S.now()}
        S.save_task(project, live)
    S.append_event(project, task["slug"], "cleanup", complete=complete, retry_after=retry_after,
                   pending=pending, branches=branches, notes=list(notes))
    if complete:
        log(f"[{project}/{task['slug']}] cleanup complete: {list(notes)}")
    else:
        log(f"[{project}/{task['slug']}] cleanup deferred until {retry_after}: {pending}")
    return complete


def tick() -> None:
    try:
        quota_codex.refresh_if_due()
    except Exception as e:  # noqa: BLE001
        log(f"[quota-codex] refresh failed: {e}")
    try:
        quota_claude.refresh_if_due()
    except Exception as e:  # noqa: BLE001
        log(f"[quota-claude] refresh failed: {e}")
    drain_hook_faults()
    for project in list(config.load_projects()):
        try:
            # Recover/fence an orphaned project-level turn before any workflow-specific
            # claim inspects its own evidence. Codex loses its in-daemon broker on restart.
            l3.lease_info(project)
            for slug in T.migrate_operational_parks(project):
                log(f"[{project}/{slug}] restored from a stale operational park")
            for item in dispatch.poll(project):
                spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
            for slug, pr in merge_coordinator.pending(project):
                spawn(f"merge:{project}:{slug}:{pr}", process_merge_request, project, slug, pr)
            resume_stranded_reports(project)
            if not _breaker_open():
                for slug in dispatch.resume_due(project, launch=lambda task, category, fn: _resume_launch(project, task, category, fn)):
                    log(f"[{project}/{slug}] resumed: the usage window reopened")
                for slug in dispatch.resume_recoverable(project, launch=lambda task, category, fn: _resume_launch(project, task, category, fn)):
                    log(f"[{project}/{slug}] resumed: routine blocker cleared")
            resume_stranded_blockers(project)
            for t in S.list_tasks(project):
                if t["state"] == "requested" and t.get("class") == "S":
                    # Crash recovery for the narrow window after set_class() persisted S but before
                    # intake.size() could auto-approve it. This is class-table policy, not L3 inference.
                    try:
                        T.auto_approve(project, t["slug"], "recovered sized S task after interrupted intake (decision 13)")
                    except T.TransitionError:
                        if S.load_task(project, t["slug"]).get("state") == "requested":
                            raise
                    continue
                if t["state"] == "proposed" and t.get("class") == "M" and not t.get("decision"):
                    # Crash recovery for the window after an FYI-only proposal was persisted but before
                    # run_proposal_flow() applied its deterministic class-table disposition.
                    proposal_path = S.task_dir(project, t["slug"]) / "proposal.json"
                    proposal = {}
                    artifact_error = None
                    if proposal_path.is_file():
                        try:
                            proposal = S.read_json(proposal_path)
                            if not isinstance(proposal, dict):
                                raise ValueError("proposal must be a JSON object")
                        except (OSError, ValueError) as exc:
                            artifact_error = f"corrupt proposal evidence archived: {type(exc).__name__}: {exc}"
                            with S.project_lock(project):
                                current = S.load_task(project, t["slug"])
                                if current.get("state") == "proposed" and not current.get("decision"):
                                    T.archive_proposal(S.task_dir(project, t["slug"]))
                    else:
                        artifact_error = "proposed task has no proposal.json evidence"
                    if artifact_error:
                        improve.system_fault("proposal-artifact", f"{project}/{t['slug']}: {artifact_error}",
                                             project=project, task=t["slug"])
                    try:
                        _finish_proposal(project, t["slug"], proposal, S.load_task(project, t["slug"]))
                    except T.TransitionError:
                        if S.load_task(project, t["slug"]).get("state") == "proposed":
                            raise
                    continue
                if _breaker_open():
                    continue
                if t["state"] == "requested" and not t.get("class") and not t.get("size_error"):  # sizer is Codex (decision 56)
                    spawn(f"size:{project}:{t['slug']}", size_task, project, t["slug"])  # decision 53
                    continue
                if t["state"] == "requested" and t["class"] in ("M", "L"):  # proposal is Codex, L3 turn falls to Codex when held (decision 56)
                    if t.get("proposal_reconcile_after") and str(t["proposal_reconcile_after"]) > S.now():
                        continue
                    started = t.get("proposal_started")
                    key = f"propose:{project}:{t['slug']}"
                    alive = (_bg.get(key) or threading.Thread()).is_alive()
                    # the L3 turn a previous altd started outlives it, so "no thread in this process" is not proof
                    # the flow is over: while its recorded pid is alive the turn is still in flight, and resuming
                    # would run a second one on the same task (incident I-011)
                    if not alive and _turn_in_flight(project, t):
                        continue
                    has_proposal = (S.task_dir(project, t["slug"]) / "proposal.json").exists()
                    # a flow that is not running in this process died with the previous server: resume at once if the
                    # proposal is on disk, otherwise wait 30 min in case an orphaned proposal agent is still writing it
                    stale = started and not alive and (has_proposal or dispatch._seconds_since(started) > 1800)
                    if stale:
                        _resume_stale_proposal(project, t["slug"], key)
                    elif not started:
                        spawn(key, run_proposal_flow, project, t["slug"])
            if not _breaker_open():
                dispatch_waiting(project)
            for t in S.list_tasks(project, include_archive=True):
                if t["state"] == "done" and not t.get("cleaned"):
                    cleanup_done_task(project, t)
            if not _breaker_open():
                try:
                    mechanize.run_due(project)
                except Exception as e:  # noqa: BLE001
                    log(f"[{project}] mechanize failed: {e}\n{traceback.format_exc()}")
                    improve.system_fault(f"mechanize:{project}", str(e), project=project)
                weekly_audit(project)
        except Exception as e:  # noqa: BLE001
            log(f"[{project}] tick failed: {e}\n{traceback.format_exc()}")
            improve.system_fault("tick", f"{project}: {e}", project=project)
    morning_digest()


def weekly_audit(project: str) -> None:
    if _breaker_open():
        return
    inf = l3.info(project)
    last = inf.get("last_audit")
    if last and time.time() - datetime.fromisoformat(last).timestamp() < 7 * 86400:
        return
    if inf.get("audit_retry_after") and str(inf["audit_retry_after"]) > S.now():
        return
    if not (inf.get("session_id") or inf.get("codex_session_id")):
        return
    data = improve.audit_input(project)
    # [R-007] The audit substance belongs in rule records and FYIs, not a turn-log reply.
    prompt = ("Weekly rule audit. Input (rules with their incidents, recent incidents, cross-project promotion candidates):\n"
              + json.dumps(data)[:12000] + "\n\nFor each probation/active rule: recurred? exercised? origin still true? Retire, tighten, or keep — "
              "each retirement/tightening via `alt rule propose` (FYI-with-veto). Propose promotions only where two projects share a tag. "
              "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    spawn(f"audit:{project}", _weekly_audit_turn, project, prompt,
          hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest())


def _weekly_audit_turn(project: str, prompt: str, evidence: str) -> None:
    """Stamp the week only after a real turn; a durable busy result remains retryable."""
    try:
        res = _automatic_call(
            "l3-audit", project, None, {"audit_input": evidence},
            lambda: l3.turn(project, prompt, trigger="audit", evidence={"audit_input": evidence}))
        if res is _BREAKER_BLOCKED:
            res = {"busy": True, "error": "global recovery breaker open"}
    except Exception:
        inf = l3.info(project)
        inf["audit_retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=300)).replace(microsecond=0).isoformat()
        l3.save_info(project, inf)
        raise
    inf = l3.info(project)
    if (res or {}).get("busy") or (res or {}).get("limited"):
        inf["audit_retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).replace(microsecond=0).isoformat()
    elif (res or {}).get("error"):
        inf["audit_retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=300)).replace(microsecond=0).isoformat()
    else:
        inf["last_audit"] = S.now()
        inf.pop("audit_retry_after", None)
    l3.save_info(project, inf)


_last_digest_day = [None]


def morning_digest() -> None:
    now = datetime.now()
    if now.hour >= 8 and _last_digest_day[0] != now.date():
        _last_digest_day[0] = now.date()
        txt = digest.text()
        spawn("digest-speak", digest.speak, txt)


def timer_loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log(f"tick: {e}\n{traceback.format_exc()}")
            try:
                improve.system_fault("tick", str(e))
            except Exception as e2:  # noqa: BLE001 — the fault channel itself is broken: the journal is the last resort
                log(f"tick: could not record fault: {e2}")
        time.sleep(config.AGENT_POLL_SECONDS)


# ---- HTTP -------------------------------------------------------------------

class _HeadWriter:
    """Pass GET's headers through while dropping its entity body."""

    def __init__(self, wfile):
        self._wfile = wfile
        self.drop = False

    def __getattr__(self, name):
        return getattr(self._wfile, name)

    def write(self, data):
        return len(data) if self.drop else self._wfile.write(data)

    def flush(self):
        return self._wfile.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "altd/0.1"

    _seen_clients: set = set()

    def log_message(self, fmt, *args):  # quieter: one line per new client address, nothing per request
        ip = self.client_address[0]
        if ip not in self._seen_clients:
            self._seen_clients.add(ip)
            log(f"first request from {ip}: {self.command} {self.path}")

    def end_headers(self) -> None:
        super().end_headers()
        if self.command == "HEAD" and getattr(self, "_head", False):
            self.wfile.drop = True

    def do_HEAD(self) -> None:
        wfile = self.wfile
        self.wfile = _HeadWriter(wfile)
        self._head = True
        try:
            self.do_GET()
        finally:
            self._head = False
            self.wfile = wfile

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str | None = None) -> None:
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, raw_path: str) -> None:
        """The built SPA (web/dist): hashed /assets/* immutable, index.html no-store, and any
        other GET falls back to index.html so client-side routes deep-link. A missing build is
        an explicit 503 naming `make web` (decision 36), never a silent fallback."""
        dist = config.WEB_DIST.resolve()
        index = dist / "index.html"
        if not index.is_file():
            return self._json({"error": "web UI not built: web/dist is missing — run `make web` first"}, 503)
        rel = unquote(raw_path).lstrip("/")
        try:
            resolved = (dist / rel).resolve() if rel else index
        except (OSError, ValueError):  # embedded NUL and friends
            return self._json({"error": "not found"}, 404)
        if not resolved.is_relative_to(dist):  # traversal (incl. percent-encoded) and symlink escapes
            return self._json({"error": "not found"}, 404)
        if not resolved.is_file():
            if resolved.relative_to(dist).parts[:1] == ("assets",):
                # a miss under the hashed build output is a stale index, not a client route:
                # serving index.html there hands JS/CSS a text/html body (MIME parse error)
                return self._json({"error": "not found"}, 404)
            resolved = index  # SPA fallback: /projects/x, /chat/y, ... render client-side
        data = resolved.read_bytes()
        immutable = resolved != index and resolved.relative_to(dist).parts[:1] == ("assets",)
        ctype = "text/html; charset=utf-8" if resolved.suffix == ".html" else (
            mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-store")
        if resolved == index:
            self.send_header(
                "Set-Cookie",
                f"{_CONTROL_COOKIE}={_CONTROL_TOKEN}; Path=/; HttpOnly; Secure; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(data)

    def _remote_operator(self) -> bool:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
            local = ipaddress.ip_address(self.connection.getsockname()[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            return False
        # Altitude's UI is served on the WireGuard address. Engine processes run
        # locally and must not be able to mint their own operator reset by
        # fetching the SPA and replaying its cookie.
        return not peer.is_loopback and peer != local

    def _control_authorized(self) -> bool:
        """Authenticate a remote browser mutation with a host-bound opaque cookie."""
        if not self._remote_operator():
            return False
        host = (self.headers.get("Host") or "").strip()
        origin = urlparse((self.headers.get("Origin") or "").strip())
        if not host or origin.scheme != "https" or origin.netloc != host:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
            supplied = cookie[_CONTROL_COOKIE].value
        except (CookieError, KeyError):
            return False
        return secrets.compare_digest(supplied, _CONTROL_TOKEN)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _stream_open(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _stream_send(self, obj: dict) -> None:
        data = (json.dumps(obj, default=str) + "\n").encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream_close(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self) -> None:
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        try:
            if parts and parts[0] == "digest.wav":
                return self._file(config.ROOT / "digest.wav", "audio/wav")
            if parts and parts[0] == "ca.crt":  # the local CA, for installing on a phone once
                return self._file(config.TLS_DIR / "ca.crt", "application/x-x509-ca-cert")
            if not parts or parts[0] != "api":
                return self._static(u.path)
            api = parts[1] if len(parts) > 1 else ""
            if api == "overview":
                return self._json(overview())
            if api == "project" and len(parts) > 2:
                return self._json(project_view(parts[2]))
            if api == "task" and len(parts) > 3:
                return self._json(task_view(parts[2], parts[3]))
            if api == "monitor":
                return self._json(monitor_view())
            if api == "digest":
                return self._json({"text": digest.text(), "audio": (config.ROOT / "digest.wav").exists()})
            if api == "chat" and len(parts) > 2:
                return self._json({"history": l3.chat_history(parts[2], int(q.get("limit", ["60"])[0])), "busy": l3.busy(parts[2]), "l3": l3.public_info(parts[2])})
            if api == "ref" and len(parts) > 3:
                try:
                    return self._json(refs.resolve(parts[2], unquote(parts[3])))
                except KeyError as e:
                    return self._json({"error": f"unknown reference {e}"}, 404)
            if api == "rules" and len(parts) > 2:
                proj = config.project(parts[2])
                return self._json({"global": rules.global_rules(), "stack": rules.stack_rules(proj.get("stacks", [])),
                                   "project": rules.project_rules(config.project_path(parts[2])),
                                   "incidents": [r for r in improve.index() if r["project"] == parts[2]]})
            return self._json({"error": "unknown api"}, 404)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"GET {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"GET {self.path}: {e}\n{traceback.format_exc()}")
            return self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        o = self._body()
        try:
            api = parts[1] if len(parts) > 1 and parts[0] == "api" else ""
            if api == "recovery-breaker" and len(parts) > 2 and parts[2] == "reset":
                if not self._control_authorized():
                    return self._json({"error": "browser control authorization failed"}, 403)
                try:
                    generation = int(o.get("generation"))
                    result = recovery_breaker.reset(generation, str(o.get("reason") or ""))
                except (TypeError, ValueError, recovery_breaker.BreakerOpen) as exc:
                    return self._json({"error": str(exc), "breaker": recovery_breaker.public_state()}, 409)
                return self._json({"ok": True, "breaker": result})
            if api == "project" and len(parts) > 2 and parts[2] == "add":
                name = o["name"]
                P = config.load_projects()
                path = Path(o.get("path") or (config.PROJECT_ROOTS[0] / name))
                if not path.is_dir():
                    return self._json({"error": f"{path} is not a directory"}, 400)
                P[name] = {"path": str(path), "stacks": [s.strip() for s in (o.get("stacks") or "").split(",") if s.strip()],
                           "approval": o.get("approval") or "default", "wip": int(o.get("wip") or config.WIP_PER_PROJECT)}
                config.save_projects(P)
                config.project_dir(name).mkdir(parents=True, exist_ok=True)
                S.regen_state_md(name)
                spawn(f"start:{name}", _start_l3_breakered, name)
                return self._json({"ok": True, "project": P[name]})
            if api == "project" and len(parts) > 2 and parts[2] == "remove":
                P = config.load_projects(); P.pop(o["name"], None); config.save_projects(P)
                return self._json({"ok": True})
            if api == "decide":
                project, slug, opt = o["project"], o["slug"], o.get("option")
                t = S.load_task(project, slug)
                if t["state"] == "blocked":
                    choice = ["Resume", "Park", "Reject"][int(opt)]
                    if choice == "Resume":
                        queued = spawn(f"resume:{project}:{slug}", _resume_requested,
                                       project, slug, o.get("note") or "continue")
                        return self._json({"ok": True, "state": S.load_task(project, slug)["state"],
                                           "resume_queued": queued})
                    elif choice == "Park":
                        T.park(project, slug, o.get("note") or "parked by Burak", actor="burak")
                    else:
                        T.reject(project, slug, o.get("note") or "rejected by Burak", actor="burak")
                    return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
                if o.get("revise"):  # decision 50: feedback on the card → the proposal is redone around it
                    t = T.revise(project, slug, o.get("note") or "", actor="burak")
                    return self._json({"ok": True, "state": t["state"]})
                t = T.approve(project, slug, int(opt) if opt is not None else None, actor="burak", note=o.get("note") or "")
                if t["state"] == "approved":
                    spawn(f"dispatch:{project}", dispatch_waiting, project)
                return self._json({"ok": True, "state": t["state"]})
            if api == "task" and len(parts) > 2 and parts[2] == "action":
                project, slug, action = o["project"], o["slug"], o["action"]
                reason = o.get("reason") or f"{action} by Burak"
                if action == "park":
                    T.park(project, slug, reason, actor="burak")
                elif action == "build":  # executive override (Burak): approve as requested and dispatch now, skipping proposal/critic
                    t = S.load_task(project, slug)
                    if t["state"] == "parked":
                        T.unpark(project, slug, actor="burak")
                    t = S.load_task(project, slug)
                    opt = 0 if (t.get("decision") or {}).get("options") else None
                    res = T.approve(project, slug, opt, actor="burak", note="build now: Burak skipped the proposal loop (executive override)")
                    log(f"[{project}/{slug}] build now (override) → {res['state']}")
                    spawn(f"dispatch:{project}", dispatch_waiting, project)
                elif action == "unpark":
                    T.unpark(project, slug, actor="burak")
                elif action == "reject":
                    T.reject(project, slug, reason, actor="burak")
                elif action == "done":
                    T.done(project, slug, actor="burak")
                elif action == "dispatch":
                    spawn(f"dispatch:{project}", dispatch_waiting, project)
                elif action == "propose":
                    with S.project_lock(project):
                        t = S.load_task(project, slug)
                        t["proposal_started"] = None
                        t.pop("proposal_error", None)
                        t["proposal_attempts"] = 0
                        S.save_task(project, t)
                    spawn(f"propose:{project}:{slug}", run_proposal_flow, project, slug)
                elif action == "verify":
                    return self._json(verify.verify(project, slug))
                elif action == "new":
                    t = T.new(project, o["title"], o.get("class") or "auto", o.get("request") or o["title"], actor="burak")
                    return self._json({"ok": True, "slug": t["slug"]})
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "l2" and len(parts) > 2 and parts[2] == "message":
                project, slug, text = o["project"], o["slug"], o["text"]
                with S.project_lock(project):
                    t = S.load_task(project, slug)
                    if t.get("state") not in ("running", "blocked"):
                        return self._json({"error": f"{slug}: cannot message L2 in {t.get('state')} state"}, 409)
                    if not t.get("dispatch_id") or not t.get("worktree"):
                        return self._json({"error": f"{slug}: no current L2 dispatch generation"}, 409)
                    qa = S.task_dir(project, slug) / "qa.md"
                    with open(qa, "a") as f:
                        f.write(f"\n## Burak → L2 ({S.now()})\n{text}\n")
                try:
                    resume_fn = ((lambda: dispatch.resume_blocked(project, slug, text)) if t["state"] == "blocked"
                                 else (lambda: dispatch.resume_session(project, slug, text)))
                    res = _automatic_call("resume-user", project, slug,
                                          _task_evidence(project, t, "dispatch_id", "attempt", "blocked_reason"),
                                          resume_fn)
                    if res is _BREAKER_BLOCKED:
                        return self._json({"error": "global recovery breaker is open"}, 409)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                return self._json({"ok": True, "stdout": res.get("stdout"), "stderr": res.get("stderr")})
            if api == "l3" and len(parts) > 2 and parts[2] == "reset":
                l3.reset(o["project"], "reset from the page"); return self._json({"ok": True})
            if api == "chat":
                project, text = o["project"], (o.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty"}, 400)
                if l3.busy(project):
                    return self._json({"error": "L3 is busy; try again in a moment"}, 409)
                self._stream_open()
                send = lambda t: self._stream_send({"t": t})  # noqa: E731
                if text.startswith("/idea"):
                    res = intake.idea(project, text[5:].strip(), on_text=send)
                elif text.startswith("/backlog"):
                    res = intake.backlog(project, on_text=send)
                else:
                    res = l3.turn(project, text, trigger="chat", on_text=send)
                self._stream_send({"done": {k: res.get(k) for k in ("session_id", "context_percent", "turns", "cost", "error")}})
                self._stream_close()
                return
            if api == "digest" and len(parts) > 2 and parts[2] == "speak":
                spawn("digest-speak", digest.speak, digest.text()); return self._json({"ok": True})
            if api == "install-statusline":
                return self._json(install_statusline())
            return self._json({"error": "unknown api"}, 404)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"POST {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"POST {self.path}: {e}\n{traceback.format_exc()}")
            try:
                self._json({"error": str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass


def monitor_view() -> dict:
    """Monitor payload with each engine seat named separately for routing observability."""
    tool_shapes = {}
    for project in config.load_projects():
        path = config.MONITOR_DIR / f"tool-shapes-{project}.json"
        if path.exists():
            try:
                histogram = S.read_json(path, {})
                if not isinstance(histogram, dict):
                    raise TypeError(f"expected object in {path}")
                shapes = histogram.get("shapes", [])
                tool_shapes[project] = (shapes if isinstance(shapes, list) else [])[:10]
            except (ValueError, TypeError) as exc:
                log(f"[{project}] warning: cannot display tool-shape histogram: {exc}")
    return {"quota": monitor.quota(), "codex_quota": route.quota_codex(),
            "sessions": monitor.sessions(), "agents": engines.claude_agents(), "tool_shapes": tool_shapes,
            "recovery_breaker": recovery_breaker.public_state()}


def overview() -> dict:
    projects = config.discover_projects()
    for p in projects:
        if p["managed"]:
            ts = S.list_tasks(p["name"])
            p["counts"] = {s: sum(1 for t in ts if t["state"] == s) for s in S.STATES}
            p["l3"] = l3.public_info(p["name"])
            p["hold"] = S.read_json(config.project_dir(p["name"]) / "hold.json")
    return {"projects": projects, "queue": digest.queue(), "fyis": digest.fyis(30), "wip": digest.wip(), "quota": monitor.quota(),
            "recovery_breaker": recovery_breaker.public_state(),
            "now": S.now()}


def project_view(name: str) -> dict:
    proj = config.project(name)
    live = {s["slug"]: s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("project") == name}
    tasks = []
    for t in S.list_tasks(name):
        d = S.task_dir(name, t["slug"])
        prog = (d / "progress.md").read_text()[-1500:] if (d / "progress.md").exists() else ""
        tasks.append({**t, "live": live.get(t["slug"]), "progress_tail": prog, "has": {f: (d / f"{f}.md").exists() for f in ("request", "proposal", "brief", "report", "digest", "progress")}})
    order = {"blocked": 0, "running": 1, "reported": 2, "proposed": 3, "approved": 4, "requested": 5, "parked": 6}
    tasks.sort(key=lambda t: (order.get(t["state"], 9), t["updated"]))
    return {"name": name, "config": proj, "l3": l3.public_info(name), "busy": l3.busy(name), "tasks": tasks,
            "archive": [{k: t.get(k) for k in ("slug", "class", "state", "title", "updated")} for t in S.list_tasks(name, True) if t["state"] in ("done", "rejected")][-20:],
            "inbox": T.inbox(name, 30), "decisions": T.decisions(name), "log": S.read_project_log(name, 40),
            "incidents": [r for r in improve.index() if r["project"] == name][-10:], "hold": S.read_json(config.project_dir(name) / "hold.json"),
            "state_md": (config.project_dir(name) / "STATE.md").read_text() if (config.project_dir(name) / "STATE.md").exists() else ""}


def task_view(project: str, slug: str) -> dict:
    t = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    files = {f: (d / f"{f}.md").read_text() for f in ("request", "proposal", "brief", "report", "digest", "progress", "qa") if (d / f"{f}.md").exists()}
    return {**t, "files": files, "events": S.read_events(project, slug), "critique": S.read_json(d / "critique.json"),
            "report_json": S.read_json(d / "report.json"), "live": next((s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("slug") == slug and s.get("project") == project), None)}


def install_statusline() -> dict:
    """Wrap the global statusline so interactive sessions feed the quota monitor. Edits ~/.claude/settings.json."""
    settings = Path.home() / ".claude" / "settings.json"
    cur = S.read_json(settings, {}) or {}
    sl = cur.get("statusLine") or {}
    wrapper = str(config.HOOKS / "statusline-monitor.sh")
    if sl.get("command") == wrapper:
        return {"ok": True, "already": True}
    orig = sl.get("command")
    cur["statusLine"] = {"type": "command", "command": wrapper}
    if orig:
        cur.setdefault("env", {})["ALTITUDE_ORIG_STATUSLINE"] = orig
    S.write_json(settings, cur)
    return {"ok": True, "wrapped": orig}


def main(host: str | None = None, port: int | None = None) -> None:
    config.ensure_root()
    recovery_breaker.acquire_reset_authority()
    if os.environ.get("ALTITUDE_SERVICE"):  # only the systemd instance runs "current main"; a smoke/test altd must not clear the flag (I-013)
        try:
            git_policy.service_preflight(config.REPO)
            git_policy.require_hooks_installed(config.REPO)
        except git_policy.GitPolicyError as e:
            log(f"service startup refused: {e}")
            raise SystemExit(1) from e
        (config.MONITOR_DIR / dispatch.RESTART_PENDING).unlink(missing_ok=True)
    host = host or config.HOST
    port = port or config.PORT
    if os.environ.get("ALTITUDE_TIMERS", "1") != "0":
        threading.Thread(target=timer_loop, name="timers", daemon=True).start()
    else:
        log("timers disabled (ALTITUDE_TIMERS=0): serve-only instance, no polling/dispatch — for smoke tests against a shared ALTITUDE_HOME")
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # decision 36: no silent fallback to loopback — exit non-zero and let systemd retry (wg0 may not be up yet)
        log(f"cannot bind {host}:{port} ({e}); exiting so the unit restarts (RestartSec)")
        raise SystemExit(1)
    srv.daemon_threads = True
    scheme = "http"
    crt, key = config.TLS_DIR / "server.crt", config.TLS_DIR / "server.key"
    if config.TLS and crt.is_file() and key.is_file():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    elif config.TLS:
        log(f"no certificate in {config.TLS_DIR} — serving plain http (run `alt tls-init` for https)")
    log(f"altd listening on {scheme}://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def tls_init(ip: str | None = None) -> dict:
    """Self-signed local CA + server certificate for the WireGuard address (same recipe as the pocketbook's make-certs.sh:
    EC P-256, CA 10 years, server cert 397 days because iOS rejects longer). Idempotent for the CA."""
    d = config.ROOT / "tls" if config.TLS_DIR == config._POCKETBOOK_TLS and not (config._POCKETBOOK_TLS / "ca.key").exists() else config.TLS_DIR
    d.mkdir(parents=True, exist_ok=True)
    ip = ip or config.HOST
    run = lambda *a: subprocess.run(list(a), cwd=str(d), check=True, capture_output=True, text=True)  # noqa: E731
    if not (d / "ca.crt").exists():
        run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
        run("openssl", "req", "-x509", "-new", "-key", "ca.key", "-sha256", "-days", "3650", "-out", "ca.crt",
            "-subj", "/CN=Altitude local CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "server.key")
    run("openssl", "req", "-new", "-key", "server.key", "-subj", "/CN=altitude", "-out", "server.csr")
    ext = d / "server.ext"
    ext.write_text(f"basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost\n")
    run("openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "397", "-sha256",
        "-out", "server.crt", "-extfile", str(ext))
    (d / "server.csr").unlink(missing_ok=True); ext.unlink(missing_ok=True)
    for f in ("ca.key", "server.key"):
        (d / f).chmod(0o600)
    end = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", str(d / "server.crt")], capture_output=True, text=True).stdout.strip()
    return {"dir": str(d), "ip": ip, "server_cert": end, "phone": f"open http://{ip}:{config.PORT}/ca.crt once (with ALTITUDE_TLS=0) or install ca.crt by other means, then trust it in the phone's certificate settings"}

_BREAKER_BLOCKED = object()


def _recovery_evidence(kind: str, project: str, slug: str | None, value) -> str:
    raw = json.dumps({"kind": kind, "project": project, "slug": slug, "value": value},
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _automatic_call(kind: str, project: str, slug: str | None, evidence, fn, *args):
    """Reserve and settle one exact automatic action; OPEN never calls ``fn``."""
    generation = _recovery_evidence(kind, project, slug, evidence)
    try:
        permit = recovery_breaker.reserve(kind, project=project, slug=slug,
                                          evidence_generation=generation)
    except recovery_breaker.BreakerOpen as exc:
        log(f"[{project}{'/' + slug if slug else ''}] automatic {kind} held by recovery breaker: {exc}")
        return _BREAKER_BLOCKED
    try:
        with recovery_breaker.launch_gate(permit):
            pass
        result = fn(*args)
    except recovery_breaker.BreakerOpen as exc:
        recovery_breaker.settle(permit, outcome="held")
        log(f"[{project}{'/' + slug if slug else ''}] automatic {kind} lost its launch generation: {exc}")
        return _BREAKER_BLOCKED
    except Exception as exc:
        recovery_breaker.settle(permit, outcome="error",
                                error_family=f"{kind}:{type(exc).__name__}", detail=str(exc))
        raise
    outcome = "deferred" if isinstance(result, dict) and (
        result.get("busy") or result.get("limited") or result.get("deferred")) else "success"
    recovery_breaker.settle(permit, outcome=outcome)
    return result


def _resume_requested(project: str, slug: str, answer: str):
    task = S.load_task(project, slug)
    return _automatic_call("resume-user", project, slug,
                           _task_evidence(project, task, "dispatch_id", "attempt", "blocked_reason"),
                           dispatch.resume_or_retry_blocked, project, slug, answer)


def _start_l3_breakered(project: str):
    return _automatic_call("l3-start", project, None, {"project": config.project(project)},
                           start_l3, project)


def _resume_launch(project: str, task: dict, category: str, fn) -> tuple[bool, object]:
    evidence = _task_evidence(project, task, "dispatch_id", "attempt", "blocked_reason",
                              "resume_after", "pending_resume", "auto_recovery")
    result = _automatic_call(f"resume-{category}", project, task.get("slug"), evidence, fn)
    return result is not _BREAKER_BLOCKED, (None if result is _BREAKER_BLOCKED else result)


def _breaker_open() -> bool:
    """Fail closed on state/lock errors as well as a durable OPEN state."""
    try:
        return recovery_breaker.public_state().get("mode") != recovery_breaker.CLOSED
    except Exception as exc:  # noqa: BLE001 — inability to prove CLOSED must suppress launches
        log(f"[recovery-breaker] state unavailable; automatic launches held: {exc}")
        return True


def _task_evidence(project: str, task: dict, *keys: str) -> dict:
    evidence = {key: task.get(key) for key in keys}
    evidence["state"] = task.get("state")
    evidence["slug"] = task.get("slug")
    try:
        request = S.task_dir(project, task["slug"]) / "request.md"
        evidence["request_sha256"] = hashlib.sha256(request.read_bytes()).hexdigest()
    except OSError:
        evidence["request_sha256"] = "missing"
    return evidence
