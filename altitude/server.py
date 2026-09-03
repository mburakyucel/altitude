"""altd — the Altitude web/API server and task timers."""
from __future__ import annotations
import json
import mimetypes
import os
import ssl
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from . import config, digest, dispatch, engines, git_policy, incidents, l3, monitor, quota_codex, route, state as S, tasks as T, transcript, verify

LOG = config.ROOT / "altd.log"
_bg: dict[str, threading.Thread] = {}
_bg_guard = threading.Lock()
CAPACITY_RETRY_DELAYS = (30, 60, 120, 300, 600, 900)


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
                incidents.system_fault(f"workflow:{parts[0]}", f"{key}: {e}", project=parts[1] if len(parts) > 1 else None,
                                     task=parts[2] if len(parts) > 2 else None)
        t = threading.Thread(target=run, name=key, daemon=True)
        _bg[key] = t
        t.start()
        return True


# ---- workflows the timers and buttons trigger --------------------------------

def start_l3(project: str) -> None:
    # The start reply is a conversation with Burak, not a turn log.
    l3.turn(project, "You have just been started for this project. Read the state file and the repo's README/CLAUDE.md (skim), "
                     "then answer in a few plain sentences: what this project is, what is in flight, and what you would need from Burak. "
                     "Keep operational details in the task record rather than dumping them into chat. Run no other commands.",
            trigger="start")


def restart_notice() -> None:
    """One message per project with active tasks: L3 resumes what a fault had stopped and leaves Burak's to him."""
    for project in config.load_projects():
        active = [t for t in S.list_tasks(project) if t["state"] in ("running", "blocked", "reported")]
        if not active:
            continue
        lines = []
        for t in active:
            tag = (f"fault {t['fault']}" if t.get("fault") else f"waiting on {t.get('waiting_on', 'burak')}"
                   if t["state"] == "blocked" else t["state"])
            lines.append(f"- {t['slug']}: {t['state']} ({tag}); {T.short_reason(t.get('blocked_reason') or t.get('title') or '')}")
        l3.queue_message(project, "Altitude restarted with the code now on main. Its active tasks:\n" + "\n".join(lines)
                         + "\n\nCheck each with `alt task status <slug>`. Resume a task blocked by a fault the restart should "
                         "have fixed (`alt task resume <slug>`); leave a task waiting on Burak to him; a running task keeps "
                         "its worker. Reply in two or three plain sentences.", trigger="restart")
        log(f"[{project}] restart notice queued for L3 ({len(active)} active tasks)")


def on_l2_finished(project: str, item: dict) -> None:
    t = item["task"]
    slug = t["slug"]
    with S.project_lock(project):
        live = S.load_task(project, slug)
        snapshot = (t.get("state"), t.get("agent_id"))
        current = (live.get("state"), live.get("agent_id"))
    if current != snapshot:
        log(f"[{project}/{slug}] ignored stale finished worker snapshot {snapshot} → {current}")
        return
    t = live  # include completion/action fields that may have landed after poll took its worker snapshot

    def block_snapshot(reason: str, *, actor: str = "altd", updates: dict | None = None) -> dict:
        return T.block(project, slug, reason, actor=actor, expected_state=t.get("state"), updates=updates)

    if t.get("completion_requested"):
        a = item.get("agent") or {}
        if a.get("state") == "working" or a.get("status") in ("busy", "idle"):
            raise RuntimeError(f"{project}/{slug}: completion reached finished handling while its L2 is still live")
        T.finalize_completion(project, slug)
        log(f"[{project}/{slug}] no-code completion finalized after the L2 worker exited")
        return
    if item.get("capacity"):
        # Provider capacity is local to this task/model, unlike an exhausted subscription window or a system fault.
        # Keep its logical L2 identity and retry the same conversation after bounded exponential-ish backoff.
        retry = max(0, int(t.get("capacity_retries") or 0)) + 1
        delay = CAPACITY_RETRY_DELAYS[min(retry - 1, len(CAPACITY_RETRY_DELAYS) - 1)]
        until = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        engine = t.get("l2_engine") or "claude"
        model = t.get("engine_model") or "provider default"
        updates = {"capacity_retries": retry, "resume_after": until}
        try:
            block_snapshot(f"{engine} model {model} is temporarily at capacity; "
                           f"Altitude retries this same L2 after {until}", updates=updates)
        except T.TransitionError:
            log(f"[{project}/{slug}] capacity result lost a concurrent lifecycle race; ignored")
            return
        log(f"[{project}/{slug}] {engine}/{model} temporarily at capacity → retry {retry} after {delay}s")
        return
    if item.get("limited"):  # park until the window reopens, or start a fresh attempt on the other engine
        until, a = item["limited"], item.get("agent") or {}
        engine = t.get("l2_engine") or "claude"
        # Claude's hold file is consumed only by Claude turns. A Codex limit must not freeze Claude work.
        news = (engines.note_usage_limit(until, f"L2 {a.get('id', '')} of {slug}")
                if engine == "claude" else True)
        try:
            block_snapshot(f"usage limit: the subscription window is exhausted, resets {until} — "
                           "Altitude resumes this L2 itself after that", updates={"resume_after": until})
        except T.TransitionError:
            log(f"[{project}/{slug}] usage-limit result lost a concurrent lifecycle race; ignored")
            return
        other = "codex" if engine == "claude" else "claude"
        switch = route.pick_engine("l2", forced=other) if not t.get("engine") else {"engine": None}
        if switch.get("engine"):
            engines.remove_l2_worker(engine, t.get("agent_id"), job_root=dispatch.l2_job_root(project, slug))
            T.requeue(project, slug, engine=other, clear_worker=True,
                      reason=f"{engine} window exhausted until {until}; fresh attempt on {other} from saved progress")
            T.fyi(project, slug, f"{engine} usage window hit (resets {until}). {slug} continues as a fresh attempt "
                                 f"on {other} from its progress file.", actor="altd")
            log(f"[{project}/{slug}] L2 hit the usage limit → requeued for {other}")
            return
        if news:
            T.fyi(project, slug, f"{engine} usage window hit. This L2 resumes after {until}.", actor="altd")
        log(f"[{project}/{slug}] L2 hit the usage limit → blocked until {until}")
        return
    with S.project_lock(project):  # a new report: whatever L3 did with the previous one no longer counts
        t0 = S.load_task(project, slug)
        t0["l3_handled"] = None
        t0.pop("capacity_retries", None)
        S.save_task(project, t0)
    if item.get("needs_input"):
        a = item.get("agent") or {}
        reason = "L2 is idle without a report — probably waiting for permission or an answer; message the L2 directly."
        try:
            block_snapshot(reason)
        except T.TransitionError:
            log(f"[{project}/{slug}] idle result lost a concurrent lifecycle race; ignored")
            return
        T.fyi(project, slug, f"{slug}: L2 idle {dispatch.IDLE_NEEDS_INPUT_SECONDS}s without finishing — needs input? attach {a.get('id', '')}")
        log(f"[{project}/{slug}] L2 idle → blocked (needs input)")
        return
    if item.get("died"):
        a = item.get("agent") or {}
        engine = t.get("l2_engine") or "claude"
        try:
            block_snapshot(f"L2 session died before reporting (Altitude fault, not the L2's) — Resume from the card "
                           f"re-attaches its transcript (agent {a.get('id', '')})")
        except T.TransitionError:
            log(f"[{project}/{slug}] dead-worker result lost a concurrent lifecycle race; ignored")
            return
        incidents.system_fault("l2-died", f"L2 worker {a.get('id', '')} (attempt {t.get('attempt')}) died without a report: "
                               f"{engine} worker state=failed", project=project, task=slug)
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
    """Close a mechanically clean report, otherwise run the L3's report-landed turn.

    The clean-close gate uses the on-disk report and live task state. It requires an ok verifier with no problems or
    signals, no merge hold, only merged PRs, at least one well-shaped successful main run, a healthy or not-applicable
    deploy, only fixed or dismissed review findings, and no decisions, blocks, FYIs, follow-ups, or post-mortem work.
    Any malformed, corrupt, stale, or raced state fails closed to L3; corrupt JSON also raises a system
    fault. `l3_handled` is stamped only when the turn returns, so a turn that altd's restart cut short is re-run by
    `resume_stranded_reports` instead of leaving the task waiting for nobody.
    """
    slug = t["slug"]
    if v.get("verdict") == "ok" and not v.get("problems") and not v.get("signals"):
        report_error = task_error = None
        try:
            with S.project_lock(project):
                try:
                    live = S.load_task(project, slug)
                except ValueError as e:
                    task_error = e
                    live = {}
                    report = None
                else:
                    try:
                        report = S.read_json(S.task_dir(project, slug) / "report.json", {})
                    except ValueError as e:
                        report_error = e
                        report = None
        except (KeyError, OSError):
            live, report = {}, None
        if task_error is not None:
            incidents.system_fault("task-json", f"{project}/{slug}: {task_error}", project=project, task=slug)
        if report_error is not None:
            incidents.system_fault("report-json", f"{project}/{slug}: {report_error}", project=project, task=slug)
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
            if (not report.get("decisions") and not report.get("blocked") and not report.get("fyi")
                    and not report.get("follow_ups") and deploy_status in ("healthy", "not-applicable")
                    and isinstance(prs, list) and all(isinstance(pr, dict) and pr.get("merged") is True for pr in prs)
                    and isinstance(runs, list) and bool(runs)
                    and all(isinstance(run, dict) and isinstance(run.get("id"), str) and run.get("id")
                            and run.get("conclusion") == "success" for run in runs)
                    and isinstance(review, list)
                    and all(isinstance(item, dict) and item.get("disposition") in ("fixed", "dismissed") for item in review)
                    and live.get("state") == "reported" and not live_hold_merge):
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
                          f"hold_merge unset; PRs merged: {pr_text}; "
                          f"main runs: {run_text}; deploy: {deploy}; no decisions, blocked items, FYIs, follow-ups, or "
                          "post-mortem signals.", actor="altd")
                    with S.project_lock(project):
                        t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)
                    log(f"[{project}/{slug}] clean report closed by altd; no L3 turn")
                    return
    # Report details belong in the task record, not a turn-log reply.
    header = (f"Report landed for `{slug}`: verdict **{v['verdict']}**. Problems: {v['problems'] or 'none'}. "
              f"Post-mortem signals: {v['signals'] or 'none'}. Spend: {v.get('spend')}. PRs: {v.get('prs')}. "
              f"Report excerpt: {json.dumps(v.get('report') or {})[:1500]}\n\n"
              "Handle the report: write a concise digest and use `alt task done`, or block/resume with the exact gap; "
              "record an incident only when its evidence will help a later recovery or diagnosis. An incident never creates "
              "a repair task or healing workflow. "
              "Put ids, slugs, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    res = l3.turn(project, header, trigger="report-landed")
    if not (res or {}).get("completed") or (res or {}).get("error"):
        detail = (res or {}).get("error") or "L3 turn did not complete"
        log(f"[{project}/{slug}] report turn unfinished: {detail}")  # not stamped: stranded-report retry owns it
        return
    try:
        with S.project_lock(project):
            t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)
    except (KeyError, OSError, ValueError) as e:
        log(f"[{project}/{slug}] L3 turn completed but l3_handled could not be stamped: {e}")


def resume_stranded_reports(project: str) -> None:
    """Reports that landed (state reported/blocked with report.json) but whose L3 turn never finished get it again."""
    for t in S.list_tasks(project):
        if t["state"] not in ("reported", "blocked") or t.get("l3_handled"):
            continue
        report_path = S.task_dir(project, t["slug"]) / "report.json"
        if not report_path.exists():
            continue
        key = f"finished:{project}:{t['slug']}"
        with _bg_guard:
            if (_bg.get(key) or threading.Thread()).is_alive():
                continue
        v = t.get("verified") or {"verdict": "missing", "problems": ["no verified report on the task"], "signals": [],
                                  "spend": {}, "prs": t.get("prs", []), "report": {}}
        try:
            report = S.read_json(report_path)
        except (OSError, ValueError) as e:
            log(f"[{project}/{t['slug']}] cannot read stranded report: {e}")
            report = None
        last_block = next((ev for ev in reversed(S.read_events(project, t["slug"]))
                           if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
        if (t["state"] == "blocked" and v.get("verdict") == "ok" and isinstance(report, dict)
                and not report.get("blocked") and "attempt" in v and v.get("attempt") == t.get("attempt")
                and last_block and last_block.get("frm") == "running"):
            try:
                t = T.report(project, t["slug"], v, expected_state="blocked",
                             expected_attempt=t.get("attempt"), expected_block_from="running")
            except (T.TransitionError, KeyError) as e:
                log(f"[{project}/{t['slug']}] stranded report promotion skipped after a concurrent change: {e}")
                continue
        log(f"[{project}/{t['slug']}] report turn resumed (previous run did not finish)")
        spawn(key, report_turn, project, t, v)


def dispatch_waiting(project: str) -> None:
    for t in S.list_tasks(project):
        if t["state"] != "queued":
            continue
        hold = dispatch.wip_hold(project, t)
        if hold and dispatch.per_task_hold(hold):
            continue
        if hold:
            S.write_json(config.project_dir(project) / "hold.json", {"at": S.now(), "reason": hold})
            return
        try:
            res = dispatch.run(project, t["slug"])
            log(f"[{project}/{t['slug']}] dispatched attempt {res['attempt']} agent={res['agent'].get('id') if res.get('agent') else None}")
        except dispatch.DispatchFailure as e:
            log(f"[{project}/{t['slug']}] {e}")
        except T.TransitionError as e:
            log(f"[{project}/{t['slug']}] dispatch refused: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"[{project}/{t['slug']}] dispatch failed: {e}")
            dispatch.record_dispatch_failure(project, t["slug"], e)
    (config.project_dir(project) / "hold.json").unlink(missing_ok=True)


def drain_hook_faults() -> None:
    """Hooks run inside L2 sessions and cannot reach the server: they append to monitor/hook-faults.log; the tick raises them."""
    p = config.MONITOR_DIR / "hook-faults.log"
    if not p.exists():
        return
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    p.unlink()
    for ln in lines[-20:]:
        incidents.system_fault("hook", ln[:400])


def tick() -> None:
    try:
        quota_codex.refresh_if_due()
    except Exception as e:  # noqa: BLE001
        log(f"[quota-codex] refresh failed: {e}")
    drain_hook_faults()
    for project in list(config.load_projects()):
        try:
            if l3.queue_path(project).exists():
                spawn(f"l3-queue:{project}", l3.deliver_queued, project)
            for item in dispatch.poll(project):
                spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
            resume_stranded_reports(project)
            for slug in dispatch.resume_due(project):
                spawn(f"resume:{project}:{slug}", dispatch.resume, project, slug)
            dispatch_waiting(project)
            for t in S.list_tasks(project, include_archive=True):
                if t["state"] == "done" and not t.get("cleaned"):
                    notes = dispatch.cleanup_after_done(project, t)
                    deferred = any(note.startswith(("deferred ", "skipped ", "could not ")) for note in notes)
                    if not deferred:
                        with S.project_lock(project):
                            t2 = S.load_task(project, t["slug"]); t2["cleaned"] = S.now(); S.save_task(project, t2)
                    S.append_event(project, t["slug"], "cleanup", notes=notes)
                    log(f"[{project}/{t['slug']}] cleanup{' deferred' if deferred else ''}: {notes}")
        except Exception as e:  # noqa: BLE001
            log(f"[{project}] tick failed: {e}\n{traceback.format_exc()}")
            incidents.system_fault("tick", f"{project}: {e}", project=project)
    morning_digest()


_last_digest_day = [None]


def morning_digest() -> None:
    now = datetime.now()
    if now.hour >= 8 and _last_digest_day[0] != now.date():
        _last_digest_day[0] = now.date()
        digest.text()


def timer_loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log(f"tick: {e}\n{traceback.format_exc()}")
            try:
                incidents.system_fault("tick", str(e))
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
        an explicit 503 naming `make web`, never a silent fallback."""
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
            if api == "transcript" and len(parts) > 3:
                try:
                    return self._json(transcript.view(
                        parts[2], parts[3],
                        engine=q.get("engine", [""])[0], session_id=q.get("session_id", [""])[0],
                        cursor=int(q.get("cursor", ["0"])[0]), raw=q.get("raw", ["0"])[0] == "1"))
                except (KeyError, transcript.TranscriptAccessError):
                    return self._json({"error": "transcript unavailable for this task generation"}, 404)
            if api == "monitor":
                return self._json({"quota": monitor.quota(), "sessions": monitor.sessions(),
                                   "agents": engines.claude_agents()})
            if api == "digest":
                return self._json({"text": digest.text()})
            if api == "chat" and len(parts) > 2:
                return self._json({"history": l3.chat_history(parts[2], int(q.get("limit", ["60"])[0])), "busy": l3.busy(parts[2]), "l3": l3.info(parts[2]),
                                   "engine": config.project(parts[2]).get("l3_engine")})
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
                P[name] = {"path": str(path), "approval": o.get("approval") or "default",
                           "wip": int(o.get("wip") or config.WIP_PER_PROJECT)}
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
                if t["state"] != "blocked":
                    return self._json({"error": "only blocked tasks need a user decision"}, 409)
                choice = ["Resume", "Reject"][int(opt)]
                if choice == "Resume":
                    if o.get("note"):
                        T.message(project, slug, "burak", str(o["note"]))
                    spawn(f"resume:{project}:{slug}", dispatch.resume, project, slug)
                else:
                    T.reject(project, slug, o.get("note") or "rejected by Burak", actor="burak")
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "task" and len(parts) > 2 and parts[2] == "action":
                project, slug, action = o["project"], o["slug"], o["action"]
                reason = o.get("reason") or f"{action} by Burak"
                if action == "reject":
                    T.reject(project, slug, reason, actor="burak")
                elif action == "done":
                    T.done(project, slug, actor="burak")
                elif action == "stop":
                    dispatch.stop(project, slug)
                elif action == "dispatch":
                    spawn(f"dispatch:{project}", dispatch_waiting, project)
                else:
                    return self._json({"error": f"unknown task action {action}"}, 400)
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "l2" and len(parts) > 2 and parts[2] == "message":
                project, slug = o["project"], o["slug"]
                text = str(o.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty task message"}, 400)
                try:
                    message = T.message(project, slug, "burak", text)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                if S.load_task(project, slug).get("state") == "blocked":
                    spawn(f"resume:{project}:{slug}", dispatch.resume, project, slug)
                return self._json({"ok": True, "message": message})
            if api == "l3" and len(parts) > 2 and parts[2] == "reset":
                l3.reset(o["project"], "reset from the page"); return self._json({"ok": True})
            if api == "l3" and len(parts) > 2 and parts[2] == "engine":
                engine = o.get("engine") or None
                if engine and engine not in config.ENGINES:
                    return self._json({"error": f"engine must be one of {', '.join(config.ENGINES)}"}, 400)
                config.set_l3_engine(o["project"], engine)
                return self._json({"ok": True, "engine": engine})
            if api == "chat":
                project, text = o["project"], (o.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty"}, 400)
                if l3.busy(project):
                    return self._json({"error": "L3 is busy; try again in a moment"}, 409)
                self._stream_open()
                gone: list[BaseException] = []

                def send(t: str) -> None:
                    # The turn owns its answer, not the page that started it. 2026-09-03 07:54Z: Burak refreshed
                    # Chat mid-turn; the write error unwound the turn, the answer was never logged and the
                    # session bookkeeping was skipped. A lost client ends the stream and nothing else.
                    if gone:
                        return
                    try:
                        self._stream_send({"t": t})
                    except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:
                        gone.append(e)
                        log(f"POST {self.path}: client went away mid-turn ({type(e).__name__}: {e}); the turn continues")

                res = l3.turn(project, text, trigger="chat", on_text=send)
                if gone:
                    return
                self._stream_send({"done": {k: res.get(k) for k in ("session_id", "context_percent", "turns", "cost", "error", "engine")}})
                self._stream_close()
                return
            if api == "restart":
                status = restart_status()
                if not status:
                    return self._json({"error": "no restart is pending"}, 409)
                if status["waiting_for"]:
                    return self._json({"error": "restart waits for " + ", ".join(status["waiting_for"])}, 409)
                return self._json(restart_service())
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


def restart_status() -> dict | None:
    """The restart-pending flag plus what the page's Restart button waits for; None when nothing is pending."""
    pending = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING)
    if not pending:
        return None
    projects = list(config.load_projects())
    waiting = [f"{p}/{t['slug']}" for p in projects for t in S.list_tasks(p)
               if t.get("dispatching") or t.get("state") in ("running", "reported")]
    waiting += [f"{p} L3" for p in projects if l3.busy(p)]
    return {**pending, "waiting_for": waiting}


def restart_service() -> dict:
    """Run the operator restart script as a transient user unit: outside altd's cgroup, it survives the restart."""
    unit = f"altitude-restart-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    cmd = [engines.SYSTEMD_RUN_BIN, "--user", "--collect", "--quiet", f"--unit={unit}", "--same-dir",
           f"--setenv=PATH={os.environ.get('PATH', '')}", "--",
           sys.executable, str(config.REPO / "scripts" / "restart_altitude.py")]
    res = subprocess.run(cmd, cwd=str(config.REPO), capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"systemd-run refused the restart unit: {(res.stderr or res.stdout).strip()[:300]}")
    log(f"restart requested from the page → unit {unit}; follow it with: journalctl --user -u {unit}")
    return {"ok": True, "unit": unit}


def overview() -> dict:
    projects = config.discover_projects()
    for p in projects:
        if p["managed"]:
            ts = S.list_tasks(p["name"])
            p["counts"] = {s: sum(1 for t in ts if t["state"] == s) for s in S.STATES}
            p["l3"] = l3.info(p["name"])
            p["hold"] = S.read_json(config.project_dir(p["name"]) / "hold.json")
    return {"projects": projects, "queue": digest.queue(), "fyis": digest.fyis(30), "wip": digest.wip(), "quota": monitor.quota(),
            "restart": restart_status(), "now": S.now()}


def project_view(name: str) -> dict:
    proj = config.project(name)
    live = {s["slug"]: s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("project") == name}
    tasks = []
    for t in S.list_tasks(name):
        d = S.task_dir(name, t["slug"])
        prog = (d / "progress.md").read_text()[-1500:] if (d / "progress.md").exists() else ""
        tasks.append({**t, "live": live.get(t["slug"]), "progress_tail": prog, "has": {f: (d / f"{f}.md").exists() for f in ("request", "brief", "report", "digest", "progress")}})
    order = {"blocked": 0, "running": 1, "reported": 2, "queued": 3}
    tasks.sort(key=lambda t: (order.get(t["state"], 9), t["updated"]))
    return {"name": name, "config": proj, "l3": l3.info(name), "busy": l3.busy(name), "tasks": tasks,
            "archive": [{k: t.get(k) for k in ("slug", "state", "title", "updated")} for t in S.list_tasks(name, True) if t["state"] in ("done", "rejected")][-20:],
            "inbox": T.inbox(name, 30), "decisions": T.decisions(name), "log": S.read_project_log(name, 40),
            "incidents": [r for r in incidents.index() if r["project"] == name][-10:], "hold": S.read_json(config.project_dir(name) / "hold.json"),
            "state_md": (config.project_dir(name) / "STATE.md").read_text() if (config.project_dir(name) / "STATE.md").exists() else ""}


def task_view(project: str, slug: str) -> dict:
    t = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    files = {f: (d / f"{f}.md").read_text() for f in ("request", "brief", "report", "digest", "progress") if (d / f"{f}.md").exists()}
    return {**t, "files": files, "messages": T.task_messages(project, slug),
            "events": S.read_events(project, slug),
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
    if os.environ.get("ALTITUDE_SERVICE"):  # only the systemd instance clears the restart-pending flag
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
        restart_notice()
        threading.Thread(target=timer_loop, name="timers", daemon=True).start()
    else:
        log("timers disabled (ALTITUDE_TIMERS=0): serve-only instance, no polling/dispatch — for smoke tests against a shared ALTITUDE_HOME")
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # No silent fallback to loopback: exit non-zero and let systemd retry when the tunnel is ready.
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
