"""altd — the Altitude server: web app + JSON API + timers. Stdlib http.server, the pocketbook's shape (decision 29)."""
from __future__ import annotations
import fcntl
import json
import mimetypes
import os
import ssl
import ssl
import subprocess
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import config, digest, dispatch, engines, git_policy, improve, intake, l3, mechanize, monitor, propose, quota_codex, refs, rules, state as S, tasks as T, verify

LOG = config.ROOT / "altd.log"
_bg: dict[str, threading.Thread] = {}
_bg_guard = threading.Lock()


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


def _turn_in_flight(task: dict) -> bool:
    """Is the L3 turn recorded on this task still running (possibly from a previous altd)?

    Bounded by the engine's own turn timeout: the kill timer lives in the process that started the turn, so
    after a restart nothing else bounds it, and a recycled pid must never strand the task for good.
    """
    rec = task.get("proposal_turn") or {}
    if not _pid_alive(rec.get("pid")):
        return False
    return dispatch._seconds_since(rec.get("started") or "") <= config.L3_TURN_TIMEOUT


def _record_l3_turn(project: str, slug: str, pid: int | None) -> None:
    """Note (pid) or clear (None) the proposal-ready L3 turn's subprocess in the task's status.json.

    Same idea as the session_id/agent_id dispatch records for L2 workers (ce856bb): the child survives altd,
    so the record on disk — not a thread in this process — is what says whether it is still running.
    """
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["proposal_turn"] = {"pid": int(pid), "started": S.now()} if pid else None
        S.save_task(project, t)


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


def _finish_proposal(project: str, slug: str, p: dict, task: dict) -> None:
    """Apply proposal-state policy after either a completed or skipped proposal-ready turn."""
    if task["state"] != "proposed":
        return
    hits = p.get("always_list_hits")
    if hits and not task.get("hold_merge"):  # decision 48: always-list → Burak merges
        hits = hits if isinstance(hits, list) else [hits]
        T.set_hold_merge(project, slug, "always-list: " + ", ".join(str(h) for h in hits)[:160], actor="altd")
    # an FYI-only proposal (no question) is auto-approved by the class table (M, no always-list hits)
    if not task.get("decision") and task["class"] in ("S", "M") and not p.get("always_list_hits"):
        T.approve(project, slug, None, actor="burak", note="auto: FYI-class proposal (decision 13)")  # recorded as auto in event note
        T.fyi(project, slug, f"{slug} ({task['class']}): proposal needs no decision — dispatching. Summary: {p.get('summary', '')[:300]}")


def size_task(project: str, slug: str) -> None:
    try:
        res = intake.size(project, slug)
        log(f"[{project}/{slug}] sized: {res}")
    except Exception as e:  # noqa: BLE001 — the fault is already filed by intake.size
        log(f"[{project}/{slug}] sizer failed: {e}")


def run_proposal_flow(project: str, slug: str) -> None:
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
        log(f"[{project}/{slug}] proposal agent")
        p = propose.run_proposal(project, slug)
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
              f"`alt task propose {slug} --file <task_dir>/proposal.md --question \"…\" --option \"…\" --option \"…\" --context \"…\" --detail \"…\"` "
              "(card for Burak — decision 46: --context is the situation in ≤ 2 plain sentences (what is wrong, what the proposal does), --question is "
              "the one question in plain words, options ≤ 8 words each with the recommended first, and everything else — reasoning, the critic's conditions, ids, file names, spend — goes in --detail; the CLI rejects the rest), "
              f"`alt task propose {slug} --file <task_dir>/proposal.md` followed by nothing (FYI-only M task — the server dispatches when a slot is free) "
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

        try:
            res = l3.turn(project, header, trigger="proposal-ready",
                          on_start=lambda pid: _record_l3_turn(project, slug, pid), precheck=still_requested)
        finally:  # the turn is over — unless this altd died first, and then the record is exactly the point
            _record_l3_turn(project, slug, None)
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
        improve.system_fault("l2-died", f"L2 worker {a.get('id', '')} ({t.get('dispatch_id')}) died without a report: "
                             f"`claude agents` state=failed", project=project, task=slug)
        T.block(project, slug, f"L2 session died before reporting (Altitude fault, not the L2's) — Resume from the card "
                               f"re-attaches its transcript (agent {a.get('id', '')})")
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
    elif v["verdict"] == "blocked":
        T.report(project, slug, v)
        T.block(project, slug, (v.get("report") or {}).get("blocked") or "blocked (see report)")
    else:
        T.report(project, slug, v)
    report_turn(project, t, v)


def report_turn(project: str, t: dict, v: dict) -> None:
    """The L3's report-landed turn. `l3_handled` is stamped only when the turn returns, so a turn that altd's restart
    cut short is re-run by `resume_stranded_reports` instead of leaving the task waiting for nobody."""
    slug = t["slug"]
    if (v.get("verdict") == "ok" and not v.get("problems") and not v.get("signals")
            and t.get("class") != "L" and not t.get("hold_merge")):
        try:
            with S.project_lock(project):
                live = S.load_task(project, slug)
                report = S.read_json(S.task_dir(project, slug) / "report.json", {})
        except (KeyError, OSError, ValueError):
            live, report = {}, None
        if isinstance(report, dict):
            landed = report.get("landed") or {}
            if not isinstance(landed, dict):
                landed = {}
            deploy = str(landed.get("deploy") or "")
            deploy_status = (deploy.split(maxsplit=1) or [""])[0].rstrip(":")
            prs = landed.get("prs") or []
            runs = landed.get("main_runs") or []
            review = report.get("review") or []
            if (not report.get("decisions") and not report.get("blocked") and not report.get("fyi")
                    and not report.get("follow_ups") and deploy_status in ("healthy", "not-applicable")
                    and isinstance(prs, list) and all(isinstance(pr, dict) and pr.get("merged") is True for pr in prs)
                    and isinstance(runs, list) and all(isinstance(run, dict) for run in runs)
                    and isinstance(review, list) and all(isinstance(item, dict) for item in review)
                    and live.get("state") == "reported"):
                pr_text = ", ".join("PR #{} ({})".format(pr.get("number"), pr.get("title") or "untitled") for pr in prs) or "No PRs recorded"
                run_text = ", ".join("{}: {}".format(run.get("id"), run.get("conclusion")) for run in runs) or "none recorded"
                fixed = sum(item.get("disposition") == "fixed" for item in review)
                dismissed = sum(item.get("disposition") == "dismissed" for item in review)
                clean_digest = (f"No decisions. {pr_text} merged. Main runs: {run_text}. Deploy: {deploy}. "
                                f"Review findings: {fixed} fixed, {dismissed} dismissed.")
                T.done(project, slug, actor="altd", digest=clean_digest)
                T.fyi(project, slug, f"{slug}: closed by altd without an L3 turn — nothing to judge: verifier verdict ok; "
                      f"task class {t.get('class')}; hold_merge unset; PRs merged: {pr_text}; main runs: {run_text}; "
                      f"deploy: {deploy}; no decisions, blocked items, FYIs, follow-ups, or post-mortem signals.", actor="altd")
                with S.project_lock(project):
                    t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)
                log(f"[{project}/{slug}] clean report closed by altd; no L3 turn")
                return
    inc = improve.index()
    # [R-007] The report-landed substance belongs in the task record, not a turn-log reply.
    header = (f"Report landed for `{slug}` ({t['class']}): verdict **{v['verdict']}**. Problems: {v['problems'] or 'none'}. "
              f"Post-mortem signals: {v['signals'] or 'none'}. Spend: {v.get('spend')}. PRs: {v.get('prs')}. "
              f"Report excerpt: {json.dumps(v.get('report') or {})[:1500]}\n"
              f"Read <task_dir>/report.md if you need more. Incidents in other projects (for scope decisions): "
              f"{json.dumps([{k: r.get(k) for k in ('project', 'id', 'tags')} for r in inc[-20:]])}\n\n"
              "Do the report-landed procedure from your instructions: digest + `alt task done`, or block/resume with the gap; "
              "then the post-mortem pass (incident + right-sized rule, or one line saying nothing went wrong). "
              "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    res = l3.turn(project, header, trigger="report-landed")
    if (res or {}).get("limited"):
        log(f"[{project}/{slug}] report turn held: {res['error']}")  # not stamped: re-run when the window reopens
        return
    with S.project_lock(project):
        t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)


def resume_stranded_reports(project: str) -> None:
    """Reports that landed (state reported/blocked with report.json) but whose L3 turn never finished get it again."""
    if engines.usage_hold():
        return
    for t in S.list_tasks(project):
        if t["state"] not in ("reported", "blocked") or t.get("l3_handled"):
            continue
        if not (S.task_dir(project, t["slug"]) / "report.json").exists():
            continue
        key = f"finished:{project}:{t['slug']}"
        with _bg_guard:
            if (_bg.get(key) or threading.Thread()).is_alive():
                continue
        v = t.get("verified") or {"verdict": "missing", "problems": ["no verified report on the task"], "signals": [],
                                  "spend": {}, "prs": t.get("prs", []), "report": {}}
        log(f"[{project}/{t['slug']}] report turn resumed (previous run did not finish)")
        spawn(key, report_turn, project, t, v)


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
            res = dispatch.run(project, t["slug"])
            log(f"[{project}/{t['slug']}] dispatched {res['dispatch_id']} agent={res['agent'].get('id') if res.get('agent') else None}")
        except Exception as e:  # noqa: BLE001
            log(f"[{project}/{t['slug']}] dispatch failed: {e}")
            T.block(project, t["slug"], f"dispatch failed: {e}"[:300])
    hp = config.project_dir(project) / "hold.json"
    if hp.exists():
        hp.unlink()


PROMPT_FAULT_PREFIX = "permission_prompt_fault.py "   # hooks/permission_prompt_fault.py: prefix + one JSON object


def _permission_prompt_line(ln: str) -> dict | None:
    """The structured record of a permission-prompt line, or None for the plain lines the hooks write on their own faults."""
    if not ln.startswith(PROMPT_FAULT_PREFIX):
        return None
    rest = ln[len(PROMPT_FAULT_PREFIX):].strip()
    if not rest.startswith("{"):
        return None
    try:
        rec = json.loads(rest)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def raise_permission_prompt(rec: dict) -> None:
    """One `permission-prompt` system fault for a distinct project/task/actor/command (I-064), plus a task event."""
    project, task, actor, command = (rec.get(k) for k in ("project", "task", "actor", "command"))
    n = int(rec.get("occurrences") or 1)
    what = f"`{command}`" if command else (rec.get("message") or "a tool call")
    where = (project or "?") + (f"/{task}" if task else "")
    detail = (f"a {actor or 'claude'} session of {where} parked on a permission prompt for {what}"
              + (f" ({n}×)" if n > 1 else "") + " — no rule in altitude/permissions.py matched; nobody can answer it")
    fault = improve.system_fault("permission-prompt", detail[:400], project=project, task=task)
    if project and task and (S.task_dir(project, task) / "status.json").exists():
        S.append_event(project, task, "permission-prompt", actor=actor, command=command, tool=rec.get("tool"),
                       message=rec.get("message"), occurrences=n, incident=(fault or {}).get("incident"))


def drain_hook_faults() -> None:
    """Hooks run inside L2 sessions and cannot reach the server: they append to monitor/hook-faults.log; the tick raises them.

    Lines from `edit_count.py` / `subagent_cap.py` (and a prompt hook's own failure) are raised as before. Permission-prompt
    lines (`hooks/permission_prompt_fault.py`, I-064) are structured and deduplicated: one `permission-prompt` system fault
    per distinct project/task/actor/command however often it was seen, plus one task event when the line names a task.

    The log is rotated before it is read: `os.replace()` moves it to a private drain name, so a hook appending while the
    tick reads lands in a fresh log for the next tick instead of in a file about to be unlinked (decision 36: no fault is
    lost). The rotate holds `monitor/hook-faults.log.lock` — the advisory lock `hooks/permission_prompt_fault.py` holds
    across each of its appends — so a permission-prompt line whose writer opened the log before the rename is fully
    written before the rename happens and is read below, never left in a renamed inode after this tick has read it.
    `hooks/edit_count.py` and `hooks/subagent_cap.py` do NOT take this lock yet (guardrail files outside I-064's task
    lease): their appends keep the previous tiny open-before-rename window, unchanged and unweakened. A drain left
    behind by a tick that died mid-way is picked up by the next one."""
    p = config.MONITOR_DIR / "hook-faults.log"
    drains = sorted(p.parent.glob("hook-faults.log.*.drain")) if p.parent.is_dir() else []
    mine = p.with_name(f"hook-faults.log.{os.getpid()}-{time.time_ns()}.drain")
    lock_f = None
    try:
        lock_f = open(p.with_name(f"{p.name}.lock"), "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX)
    except OSError:                                                  # no monitor dir yet, or the lock cannot be taken:
        if lock_f:                                                   # rotate unlocked rather than skip the drain
            lock_f.close()
        lock_f = None
    try:
        os.replace(p, mine)
    except FileNotFoundError:
        pass                                                         # nothing appended since the last tick
    else:
        drains.append(mine)
    finally:
        if lock_f:
            try: fcntl.flock(lock_f, fcntl.LOCK_UN)
            except OSError: pass
            lock_f.close()
    if not drains:
        return
    lines = [ln for d in drains for ln in d.read_text().splitlines() if ln.strip()]
    plain, prompts = [], {}
    for ln in lines:
        rec = _permission_prompt_line(ln)
        if rec is None:
            plain.append(ln)
            continue
        key = tuple(rec.get(k) for k in ("project", "task", "actor", "command"))
        entry = prompts.setdefault(key, {**rec, "occurrences": 0})
        entry["occurrences"] += 1
    for ln in plain[-20:]:
        improve.system_fault("hook", ln[:400])
    for rec in prompts.values():
        raise_permission_prompt(rec)
    for d in drains:                                                 # only once every line is raised
        d.unlink(missing_ok=True)


def tick() -> None:
    try:
        quota_codex.refresh_if_due()
    except Exception as e:  # noqa: BLE001
        log(f"[quota-codex] refresh failed: {e}")
    drain_hook_faults()
    for project in list(config.load_projects()):
        try:
            for item in dispatch.poll(project):
                spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
            resume_stranded_reports(project)
            for slug in dispatch.resume_due(project):
                log(f"[{project}/{slug}] resumed: the usage window reopened")
            for t in S.list_tasks(project):
                if t["state"] == "requested" and not t.get("class") and not t.get("size_error"):  # sizer is Codex (decision 56)
                    spawn(f"size:{project}:{t['slug']}", size_task, project, t["slug"])  # decision 53
                    continue
                if t["state"] == "requested" and t["class"] in ("M", "L"):  # proposal is Codex, L3 turn falls to Codex when held (decision 56)
                    started = t.get("proposal_started")
                    key = f"propose:{project}:{t['slug']}"
                    alive = (_bg.get(key) or threading.Thread()).is_alive()
                    # the L3 turn a previous altd started outlives it, so "no thread in this process" is not proof
                    # the flow is over: while its recorded pid is alive the turn is still in flight, and resuming
                    # would run a second one on the same task (incident I-011)
                    if not alive and _turn_in_flight(t):
                        continue
                    has_proposal = (S.task_dir(project, t["slug"]) / "proposal.json").exists()
                    # a flow that is not running in this process died with the previous server: resume at once if the
                    # proposal is on disk, otherwise wait 30 min in case an orphaned proposal agent is still writing it
                    stale = started and not alive and (has_proposal or dispatch._seconds_since(started) > 1800)
                    if not started or stale:
                        if stale:
                            with S.project_lock(project):
                                t2 = S.load_task(project, t["slug"])
                                t2["proposal_started"] = None
                                t2["proposal_turn"] = None  # its turn is dead; the resume below is the only one
                                S.save_task(project, t2)
                            log(f"[{project}/{t['slug']}] proposal flow resumed (previous run did not finish)")
                        spawn(key, run_proposal_flow, project, t["slug"])
            dispatch_waiting(project)
            for t in S.list_tasks(project, include_archive=True):
                if t["state"] == "done" and not t.get("cleaned"):
                    notes = dispatch.cleanup_after_done(project, t)
                    with S.project_lock(project):
                        t2 = S.load_task(project, t["slug"]); t2["cleaned"] = S.now(); S.save_task(project, t2)
                    S.append_event(project, t["slug"], "cleanup", notes=notes)
                    log(f"[{project}/{t['slug']}] cleanup: {notes}")
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
    inf = l3.info(project)
    last = inf.get("last_audit")
    if last and time.time() - datetime.fromisoformat(last).timestamp() < 7 * 86400:
        return
    if not inf.get("session_id"):
        return
    inf["last_audit"] = S.now()
    l3.save_info(project, inf)
    data = improve.audit_input(project)
    # [R-007] The audit substance belongs in rule records and FYIs, not a turn-log reply.
    spawn(f"audit:{project}", l3.turn, project,
          "Weekly rule audit. Input (rules with their incidents, recent incidents, cross-project promotion candidates):\n"
          + json.dumps(data)[:12000] + "\n\nFor each probation/active rule: recurred? exercised? origin still true? Retire, tighten, or keep — "
          "each retirement/tightening via `alt rule propose` (FYI-with-veto). Propose promotions only where two projects share a tag. "
          "Put ids, slugs, decision or rule numbers, file names, code, and spend figures in the task record — the card `--detail`, "
          "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
          "and whether anything waits on Burak.",
          "audit")


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
        self.end_headers()
        self.wfile.write(data)

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
                        except (ValueError, TypeError) as e:
                            log(f"[{project}] warning: cannot display tool-shape histogram: {e}")
                return self._json({"quota": monitor.quota(), "sessions": monitor.sessions(),
                                   "agents": engines.claude_agents(), "tool_shapes": tool_shapes})
            if api == "digest":
                return self._json({"text": digest.text(), "audio": (config.ROOT / "digest.wav").exists()})
            if api == "chat" and len(parts) > 2:
                return self._json({"history": l3.chat_history(parts[2], int(q.get("limit", ["60"])[0])), "busy": l3.busy(parts[2]), "l3": l3.info(parts[2])})
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
                spawn(f"start:{name}", start_l3, name)
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
                        if t.get("session_id"):
                            spawn(f"resume:{project}:{slug}", dispatch.resume_blocked, project, slug, o.get("note") or "continue")
                        else:
                            T.resume(project, slug, actor="burak"); T.approve  # noqa: B018
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
                    t = S.load_task(project, slug); t["proposal_started"] = None; S.save_task(project, t)
                    spawn(f"propose:{project}:{slug}", run_proposal_flow, project, slug)
                elif action == "verify":
                    return self._json(verify.verify(project, slug))
                elif action == "new":
                    t = T.new(project, o["title"], o.get("class") or "auto", o.get("request") or o["title"], actor="burak")
                    return self._json({"ok": True, "slug": t["slug"]})
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "l2" and len(parts) > 2 and parts[2] == "message":
                project, slug, text = o["project"], o["slug"], o["text"]
                t = S.load_task(project, slug)
                qa = S.task_dir(project, slug) / "qa.md"
                with open(qa, "a") as f:
                    f.write(f"\n## Burak → L2 ({S.now()})\n{text}\n")
                res = dispatch.resume_blocked(project, slug, text) if t["state"] == "blocked" else dispatch.resume_session(project, slug, text)
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


def overview() -> dict:
    projects = config.discover_projects()
    for p in projects:
        if p["managed"]:
            ts = S.list_tasks(p["name"])
            p["counts"] = {s: sum(1 for t in ts if t["state"] == s) for s in S.STATES}
            p["l3"] = l3.info(p["name"])
            p["hold"] = S.read_json(config.project_dir(p["name"]) / "hold.json")
    return {"projects": projects, "queue": digest.queue(), "fyis": digest.fyis(30), "wip": digest.wip(), "quota": monitor.quota(),
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
    return {"name": name, "config": proj, "l3": l3.info(name), "busy": l3.busy(name), "tasks": tasks,
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
