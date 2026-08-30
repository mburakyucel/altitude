"""altd — the Altitude server: web app + JSON API + timers. Stdlib http.server, the pocketbook's shape (decision 29)."""
from __future__ import annotations
import json
import mimetypes
import os
import ssl
import subprocess
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import config, digest, dispatch, engines, improve, intake, l3, monitor, propose, refs, rules, state as S, tasks as T, verify

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
    l3.turn(project, "You have just been started for this project. Read the state file and the repo's README/CLAUDE.md (skim), "
                     "then say in ≤4 sentences what this project is, what is in flight, and what you would need from Burak. Run no other commands.",
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


def _turn_started_at(project: str, header: str) -> str | None:
    """When our proposal-ready turn actually began (incident I-008), or None if we cannot tell.

    `l3.turn` serializes on a per-project lock and logs the prompt verbatim *inside* it, right
    before the engine runs — so the chat entry's `at` is the moment the L3 started on our turn,
    not the moment we queued behind another one. Using the queue time instead would read a park
    the L3 made for Burak during that earlier turn as an in-turn park, and override it.

    The entry always exists once `l3.turn` has returned, so None is unreachable in a healthy
    system — which is the point. It fails closed: without a trustworthy start the guard leaves
    the park standing rather than risk overriding one (decision 36 — no silent fallback).
    """
    for ev in reversed(l3.chat_history(project, limit=200)):
        if ev.get("role") == "user" and ev.get("text") == header:
            return ev.get("at") or None
    return None


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
            log(f"[{project}/{slug}] critic")
            crit = propose.run_critic(project, slug)
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
              "If the critic says revise and you agree, `alt task park` with the reason and say what should change. Report in ≤5 sentences.")
    # Burak (or the L3 in a chat turn) may have parked, approved, rejected or proposed the task while the proposal and
    # the critic ran (incident I-008): re-read the state and skip the turn rather than talk to the L3 about a task that
    # has already been decided. proposal.json stays on disk; clearing proposal_started lets tick() re-run the flow
    # (requested only) if it comes back to requested later.
    t1 = S.load_task(project, slug)
    if t1["state"] != "requested":
        with S.project_lock(project):
            t1b = S.load_task(project, slug); t1b["proposal_started"] = None; S.save_task(project, t1b)
        if t1["state"] != "proposed":
            log(f"[{project}/{slug}] no longer requested (state={t1['state']}) — skipping the proposal-ready L3 turn; proposal kept on disk")
            return
        log(f"[{project}/{slug}] proposed outside this flow — skipping the proposal-ready L3 turn; still checked for FYI-only auto-approve")
        t2 = t1  # proposed outside this flow: the FYI-only auto-approve check below is the only thing that would
    else:       # ever approve it (an FYI-only proposal raises no card), so it still owes this task a decision
        try:
            res = l3.turn(project, header, trigger="proposal-ready",
                          on_start=lambda pid: _record_l3_turn(project, slug, pid))
        finally:  # the turn is over — unless this altd died first, and then the record is exactly the point
            _record_l3_turn(project, slug, None)
        turn_start = _turn_started_at(project, header)
        if turn_start is None:  # fails closed (decision 36): an unknown turn start must never override a park
            log(f"[{project}/{slug}] no chat entry for the proposal-ready turn — cannot date it, so any park stands")
        t2 = S.load_task(project, slug)
        # critic said revise and the L3 parked with a revision brief *in this turn*: re-propose, at most twice, then it
        # waits for Burak. A park by Burak, or one the L3 made for him in an earlier turn (I-008), must stand.
        if (t2["state"] == "parked" and crit and crit.get("verdict") == "revise" and turn_start is not None
                and _l3_parked_during_turn(project, slug, turn_start)):
            n = int(t2.get("revisions", 0))
            if n < 2:
                with S.project_lock(project):
                    t3 = S.load_task(project, slug); t3["revisions"] = n + 1; t3["proposal_started"] = None; S.save_task(project, t3)
                for old in ("proposal.md", "proposal.json", "critique.json"):  # history as -vN; the reviser reads the latest critique-vN
                    src = S.task_dir(project, slug) / old
                    if src.exists():
                        src.rename(src.with_name(f"{src.stem}-v{n + 1}{src.suffix}"))
                T.unpark(project, slug, actor="altd")
                log(f"[{project}/{slug}] critic revise → revision {n + 1} queued (bounded at 2)")
                return
            T.fyi(project, slug, f"{slug}: parked after {n} revisions — the proposal and critic keep disagreeing; needs your read (task folder has proposal-v*.md / critique-v*.json).")
            return
    # an FYI-only proposal (no question) is auto-approved by the class table (M, no always-list hits)
    if t2["state"] == "proposed" and not t2.get("decision") and t2["class"] in ("S", "M") and not (p.get("always_list_hits")):
        T.approve(project, slug, None, actor="burak", note="auto: FYI-class proposal (decision 13)")  # recorded as auto in event note
        T.fyi(project, slug, f"{slug} ({t2['class']}): proposal needs no decision — dispatching. Summary: {p.get('summary', '')[:300]}")


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
    inc = improve.index()
    header = (f"Report landed for `{slug}` ({t['class']}): verdict **{v['verdict']}**. Problems: {v['problems'] or 'none'}. "
              f"Post-mortem signals: {v['signals'] or 'none'}. Spend: {v.get('spend')}. PRs: {v.get('prs')}. "
              f"Report excerpt: {json.dumps(v.get('report') or {})[:1500]}\n"
              f"Read <task_dir>/report.md if you need more. Incidents in other projects (for scope decisions): "
              f"{json.dumps([{k: r.get(k) for k in ('project', 'id', 'tags')} for r in inc[-20:]])}\n\n"
              "Do the report-landed procedure from your instructions: digest + `alt task done`, or block/resume with the gap; "
              "then the post-mortem pass (incident + right-sized rule, or one line saying nothing went wrong).")
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


def drain_hook_faults() -> None:
    """Hooks run inside L2 sessions and cannot reach the server: they append to monitor/hook-faults.log; the tick raises them."""
    p = config.MONITOR_DIR / "hook-faults.log"
    if not p.exists():
        return
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    p.unlink()
    for ln in lines[-20:]:
        improve.system_fault("hook", ln[:400])


def tick() -> None:
    drain_hook_faults()
    for project in list(config.load_projects()):
        try:
            for item in dispatch.poll(project):
                spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
            resume_stranded_reports(project)
            for slug in dispatch.resume_due(project):
                log(f"[{project}/{slug}] resumed: the usage window reopened")
            for t in S.list_tasks(project):
                if t["state"] == "requested" and t["class"] in ("M", "L") and not engines.usage_hold():
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
    spawn(f"audit:{project}", l3.turn, project,
          "Weekly rule audit. Input (rules with their incidents, recent incidents, cross-project promotion candidates):\n"
          + json.dumps(data)[:12000] + "\n\nFor each probation/active rule: recurred? exercised? origin still true? Retire, tighten, or keep — "
          "each retirement/tightening via `alt rule propose` (FYI-with-veto). Propose promotions only where two projects share a tag. ≤10 lines.",
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

class Handler(BaseHTTPRequestHandler):
    server_version = "altd/0.1"

    _seen_clients: set = set()

    def log_message(self, fmt, *args):  # quieter: one line per new client address, nothing per request
        ip = self.client_address[0]
        if ip not in self._seen_clients:
            self._seen_clients.add(ip)
            log(f"first request from {ip}: {self.command} {self.path}")
        if "/api/" not in (args[0] if args else ""):
            return

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
                return self._json({"quota": monitor.quota(), "sessions": monitor.sessions(), "agents": engines.claude_agents()})
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
                t = T.approve(project, slug, int(opt) if opt is not None else None, actor="burak", note=o.get("note") or "")
                if t["state"] == "requested":  # revise → proposal again
                    t["proposal_started"] = None; S.save_task(project, t)
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
                    t = T.new(project, o["title"], o.get("class") or "M", o.get("request") or o["title"], actor="burak")
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
    (config.MONITOR_DIR / dispatch.RESTART_PENDING).unlink(missing_ok=True)  # this process now runs current main
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
