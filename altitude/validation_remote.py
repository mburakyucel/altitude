"""One bounded remote validation target, using validation's task ledger and evidence.

The host broker trusts installed code and its own receipts, never candidate output.
There is no automatic replay: an uncertain submission reserves its identity until
the same remote run is reconciled. Local validation uses a separate lock.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import platform, validation_payload as payload

TIMEOUT = 3600
CLEANUP_SECONDS = 120
POLL_SECONDS = 5
POLL_MAX_SECONDS = 30
ID = re.compile(r"[0-9a-f]{32}\Z")
_lock = threading.Lock()
_cancel = threading.Event()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".new")
    with open(temporary, "x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path, limit: int = 65536) -> dict:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Validation record exceeds its limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid validation record")
    return value


@contextmanager
def locked(home: Path):
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(home / "lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def request_identity(request: dict) -> str:
    ident = request.get("run_id")
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ValueError("Invalid validation run identity")
    return ident


def command(argv: object) -> list[str]:
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a and "\0" not in a for a in argv)
            or sum(len(a) + 1 for a in argv) > 16384):
        raise ValueError("Supply a validation command of at most 16384 characters")
    return argv


class Broker:
    """Trusted endpoint. Its configuration is installed separately from candidates."""

    def __init__(self, home: Path, template: Path, worker: list[str]):
        self.home, self.template, self.worker = home, template, worker

    def _area(self, ident: str) -> Path:
        return self.home / "runs" / ident

    def _record(self, ident: str) -> dict:
        path = self._area(ident) / "record.json"
        return read_json(path) if path.exists() else {"run_id": ident, "status": "absent"}

    def revoke(self) -> dict:
        """Local setup recovery after key removal; preserve evidence without remote acknowledgement."""
        result = {"status": "revoked", "cleanup": False}
        try:
            with locked(self.home):
                if not (self.home / "off").is_file():
                    raise RuntimeError("Admission must be disabled before local revocation")
                active = self.home / "active.json"
                if not active.exists():
                    if active.is_symlink():
                        raise RuntimeError("Invalid active-run record")
                    return {**result, "cleanup": True}
                ident = request_identity(read_json(active))
                result["run_id"] = ident
                area = self._area(ident)
                (area / "cancel").touch()
                platform.validation_broker_stop(ident)
                # Preserve any completed export. If work was interrupted before export,
                # collect bounded results before the existing reaper detaches its image.
                if not (area / "evidence.json").exists() and (area / "results").is_dir():
                    try:
                        evidence = payload.collect_results(area / "results")
                    except (OSError, ValueError):
                        evidence = payload.collect_results(area / "results", names=("output.log", "receipt.json"))
                        result["evidence_complete"] = False
                    write_json(area / "evidence.json", evidence)
                    record = self._record(ident)
                    record["evidence_digest"] = evidence["digest"]
                    if result.get("evidence_complete") is False:
                        record["evidence_error"] = "Incomplete guest results; available protected summaries retained"
                    write_json(area / "record.json", record)
                self._reap()
                result["cleanup"] = not active.exists()
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            pass
        if not result["cleanup"]:
            result["error"] = "Keys revoked; local validation stop or cleanup needs recovery"
        return result

    def _reap(self) -> None:
        """An expired or dead supervisor never releases admission before VM cleanup."""
        active = self.home / "active.json"
        if not active.exists():
            return
        saved = read_json(active)
        record = self._record(saved["run_id"])
        if record.get("status") == "finished" and record.get("cleanup"):
            active.unlink()
            return
        ident = request_identity(saved)
        if record.get("status") == "absent":
            raise RuntimeError("Mac validation admission has no matching receipt")
        if platform.validation_broker_active(ident) and time.time() < record["expires"]:
            return
        # Confirm that the native job and all VM descendants stopped before touching
        # its mounted output or releasing admission. Never launch a replacement worker.
        platform.validation_broker_stop(ident)
        area = self._area(ident)
        try:
            platform.validation_vm_cleanup(area)
            if (area / "input").exists():
                shutil.rmtree(area / "input")
        except (OSError, RuntimeError, subprocess.SubprocessError):
            record.update(status="finished", exit=None, ended="unavailable", cleanup=False,
                          error="Mac validation cleanup needs recovery")
            write_json(area / "record.json", record)
            return
        expired = time.time() >= record["expires"]
        record.update(status="finished", exit=None, ended="timeout" if expired else "interrupted", cleanup=True,
                      error="Mac validation deadline expired" if expired else
                      "Mac validation supervisor stopped without a confirmed result")
        write_json(area / "record.json", record)
        active.unlink()

    def dispatch(self, request: dict) -> dict:
        ident = request_identity(request)
        operation = request.get("operation")
        allowed = {"operation", "run_id"}
        if operation == "submit":
            allowed |= {"argv", "payload", "duration"}
        elif operation == "result":
            allowed |= {"received"}
        elif operation not in {"status", "cancel"}:
            raise ValueError("Unsupported validation operation")
        if request.keys() - allowed:
            raise ValueError("Unsupported validation request fields")
        with locked(self.home):
            # A fresh submit's reachability probe does not reap another run.
            # Existing identities still reconcile through the normal status path.
            if operation == "status":
                record = self._record(ident)
                if record["status"] == "absent":
                    return record
            self._reap()
            area = self._area(ident)
            record = self._record(ident)
            if operation == "submit":
                argv = command(request.get("argv"))
                candidate = request.get("payload")
                if record["status"] == "cancelled":
                    return record
                if record["status"] != "absent":
                    if (not isinstance(candidate, dict) or candidate.get("digest") != record.get("payload_digest")
                            or argv != record.get("argv")):
                        raise ValueError("Run identity already belongs to a different request")
                    return record
                duration = request.get("duration")
                if type(duration) is not int or not CLEANUP_SECONDS < duration <= TIMEOUT:
                    raise ValueError("Validation duration must reserve cleanup time and not exceed one hour")
                # The executor owns its clock. Cross-host wall-clock skew cannot extend
                # or reject a bounded run; the submitter also keeps its own deadline.
                expires = time.time() + duration
                if (self.home / "active.json").exists():
                    return {"run_id": ident, "status": "unavailable", "accepted": False,
                            "error": "Another Mac validation run needs completion or cleanup"}
                if (self.home / "off").exists():
                    return {"run_id": ident, "status": "unavailable", "accepted": False,
                            "error": "Mac validation is disabled"}
                # Retained unacknowledged results cannot grow without bound.
                used = sum(p.stat().st_size for p in (self.home / "runs").glob("*/evidence.json"))
                if used > 512 << 20:
                    return {"run_id": ident, "status": "unavailable", "accepted": False,
                            "error": "Mac validation evidence storage needs reconciliation"}
                area.mkdir(mode=0o700, parents=True)
                try:
                    (area / "input").mkdir()
                    payload.restore(candidate, area / "input" / "candidate")
                    record = {"run_id": ident, "status": "running", "argv": argv, "expires": expires,
                              "commit": candidate["commit"], "tree": candidate["tree"],
                              "payload_digest": candidate["digest"], "exit": None, "cleanup": False}
                    write_json(area / "input" / "request.json", {"argv": argv, "run_id": ident,
                                                                 "commit": record["commit"], "tree": record["tree"]})
                    write_json(area / "record.json", record)
                    write_json(self.home / "active.json", {"run_id": ident})
                except BaseException:
                    shutil.rmtree(area)
                    raise
                try:
                    platform.validation_broker_launch(self.worker, ident, area)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    # The launcher may have accepted the job before contact failed.
                    # Retain its identity and admission until confirmed termination.
                    record.update(error="Mac supervisor launch needs reconciliation")
                    write_json(area / "record.json", record)
                return record
            if operation == "cancel":
                if record["status"] == "absent":
                    # A delayed submit must never start after cancellation was confirmed.
                    area.mkdir(mode=0o700, parents=True)
                    record = {"run_id": ident, "status": "cancelled", "accepted": False}
                    write_json(area / "record.json", record)
                elif record["status"] == "running":
                    (area / "cancel").touch()
                    return {**record, "cancellation_requested": True}
            if operation == "result" and record["status"] == "finished":
                evidence_path = area / "evidence.json"
                if "received" in request:
                    if request["received"] != record.get("evidence_digest"):
                        raise ValueError("Evidence acknowledgement does not match")
                    evidence_path.unlink(missing_ok=True)
                    return record
                if evidence_path.exists():
                    return {**record, "evidence": read_json(evidence_path, 384 << 20)}
            return record

    def work(self, ident: str) -> None:
        """Supervise outside the guest; stopping the VM stops every candidate process."""
        request_identity({"run_id": ident})
        area = self._area(ident)
        with open(area / "worker.lock", "a") as worker_lock:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            record = self._record(ident)
            if record["status"] != "running":
                return
            process = None
            start = platform.validation_deadline_clock()
            execution_expires = record["expires"] - CLEANUP_SECONDS
            remaining = max(0, execution_expires - time.time())
            evidence = None
            try:
                if (area / "cancel").exists():
                    raise InterruptedError
                if time.time() >= execution_expires:
                    raise TimeoutError
                prepared = platform.validation_vm_prepare(self.template, area)
                record.update(host=prepared["host"], template=prepared["template"])
                if (area / "cancel").exists():
                    raise InterruptedError
                if time.time() >= execution_expires:
                    raise TimeoutError
                process = subprocess.Popen(prepared["argv"], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True, close_fds=True)
                while process.poll() is None:
                    if (area / "cancel").exists():
                        record.update(ended="cancelled", error="Mac validation cancelled")
                        break
                    if time.time() >= execution_expires or platform.validation_deadline_clock() - start >= remaining:
                        record.update(ended="timeout", error="Mac validation exceeded its deadline")
                        break
                    if (area / "results" / "receipt.json").is_file():
                        break
                    time.sleep(.2)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                process.wait()
                if "ended" not in record:
                    receipt = read_json(area / "results" / "receipt.json")
                    if (type(receipt.get("exit")) not in (int, type(None)) or receipt.get("run_id") != ident
                            or receipt.get("commit") != record["commit"] or receipt.get("tree") != record["tree"]):
                        raise ValueError("Guest receipt does not match the admitted run")
                    record.update({key: receipt.get(key) for key in
                                   ("exit", "ended", "error", "guest", "started", "finished")})
                    if record["exit"] == 0 and record["ended"] != "exit":
                        raise ValueError("Guest receipt has no successful command exit")
                try:
                    evidence = payload.collect_results(area / "results")
                except (OSError, ValueError):
                    record.update(exit=None, ended="unavailable",
                                  error="Mac validation artifacts are incomplete or invalid; protected log and receipt retained")
                    evidence = payload.collect_results(area / "results", names=("output.log", "receipt.json"))
                write_json(area / "evidence.json", evidence)
                record["evidence_digest"] = evidence["digest"]
            except InterruptedError:
                record.update(exit=None, ended="cancelled", error="Mac validation cancelled before execution")
            except TimeoutError:
                record.update(exit=None, ended="timeout", error="Mac validation expired before execution")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                record.update(exit=None, ended="unavailable", error="Mac validation prerequisites, execution or evidence failed")
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                try:
                    platform.validation_vm_cleanup(area)
                    shutil.rmtree(area / "input", ignore_errors=False)
                    record["cleanup"] = True
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    record.update(exit=None, error="Mac validation cleanup needs recovery", cleanup=False)
                record["status"] = "finished"
                with locked(self.home):
                    write_json(area / "record.json", record)
                    if record["cleanup"]:
                        (self.home / "active.json").unlink(missing_ok=True)


def _pending() -> Path:
    from . import validation
    return validation.home() / "remote" / "pending.json"


def _call(operation: str, ident: str, **fields) -> dict:
    return platform.validation_remote_request({"operation": operation, "run_id": ident, **fields})


def _matches(response: dict, pending: dict) -> bool:
    return all(response.get(key) == pending[key] for key in ("run_id", "commit", "tree", "payload_digest", "argv"))


def _deliver(response: dict, pending: dict, *, recovered: bool = False) -> dict:
    from . import state as S, validation
    area = _pending().parent / "evidence"
    if area.exists():
        shutil.rmtree(area)
    payload.restore_results(response["evidence"], area)
    fd = validation._task_dir_fd(pending["project"], pending["slug"])
    name = str(pending["n"]) + ("-recovered" if recovered else "")
    temporary = "." + name + "-" + uuid.uuid4().hex
    try:
        try:
            existing = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            skipped = validation.copy_results(area, fd, temporary)
            if skipped:
                raise ValueError("Remote evidence was incomplete")
            # A crash leaves only an unpublished temporary directory. The final
            # result directory is always a complete copy and can be verified on retry.
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            try:
                if payload.collect_results(existing) != response["evidence"]:
                    raise ValueError("Previously delivered remote evidence changed")
            finally:
                os.close(existing)
        log = area / "output.log"
        if log.is_file():
            validation._copy_file(str(log), fd, temporary + ".log")
            os.replace(temporary + ".log", name + ".log", src_dir_fd=fd, dst_dir_fd=fd)
        else:
            raise ValueError("Remote command log is missing")
    finally:
        os.close(fd)
    with log.open("rb") as stream:
        output = stream.read(65536).decode("utf-8", errors="replace")
    truncated = log.stat().st_size > 65536
    target = S.task_dir(pending["project"], pending["slug"]) / "validation"
    shutil.rmtree(area)
    return {"output": output, "output_truncated": truncated,
            "results": str(target / name), "log": str(target / (name + ".log")), "results_skipped": []}


def _reconcile() -> bool:
    from . import state as S, tasks as T
    path = _pending()
    if not path.exists():
        return True
    pending = read_json(path)
    # First-finish semantics retain any already recorded outcome. A daemon death
    # before finishing its ledger row is an interruption, with later evidence separate.
    T.finish_machine_run(pending["project"], pending["slug"], {
        **pending["row"], "exit": None, "ended": "interrupted", "finished": S.now(),
        "error": "Mac validation interrupted; remote cancellation and evidence need reconciliation"})
    response = _call("cancel", pending["run_id"])
    if response.get("status") == "cancelled" and response.get("accepted") is False and response.get("run_id") == pending["run_id"]:
        path.unlink()
        return True
    if response.get("status") != "finished" or not response.get("cleanup") or not _matches(response, pending):
        return False
    response = _call("result", pending["run_id"])
    if not _matches(response, pending):
        return False
    evidence = _deliver(response, pending, recovered=not pending.get("delivered")) if "evidence" in response else {}
    if not any(event.get("kind") == "validation-reconciled" and event.get("run_id") == pending["run_id"]
               for event in S.read_events(pending["project"], pending["slug"])):
        S.append_event(pending["project"], pending["slug"], "validation-reconciled", run_id=pending["run_id"],
                       exit=response.get("exit"), cleanup=True, **evidence)
    if "evidence" in response:
        acknowledgement = _call("result", pending["run_id"], received=response["evidence"]["digest"])
        if not _matches(acknowledgement, pending):
            return False
    path.unlink()
    return True


def reconcile() -> None:
    with _lock:
        try:
            _reconcile()
        except (OSError, ValueError, RuntimeError):
            return  # Pending identity retains admission; local validation stays usable.


def stop_all() -> threading.Thread | None:
    _cancel.set()
    # An active caller owns cancellation. After restart there may be only a
    # durable pending identity; turning validation off still requests its stop.
    if not _lock.acquire(blocking=False):
        return None
    if not _pending().exists():
        _lock.release()
        return None

    def cancel_pending():
        try:
            _reconcile()
        except (OSError, ValueError, RuntimeError):
            pass  # Keep the identity until a later reconciliation confirms cleanup.
        finally:
            _lock.release()

    worker = threading.Thread(target=cancel_pending, name="remote-validation-cancel", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _lock.release()
        raise
    return worker


def run(project: str, slug: str, task: dict, argv: list[str]) -> dict:
    from . import state as S, tasks as T, validation
    import shlex
    if not _lock.acquire(blocking=False):
        raise ValueError("Another Mac validation run is active")
    row = None
    result = {"exit": None, "timed_out": False, "output": "", "output_truncated": False,
              "results": None, "results_skipped": [], "publish": None, "error": None}
    pending = None
    try:
        if not _reconcile():
            raise ValueError("Previous Mac validation cancellation or cleanup remains unconfirmed")
        _cancel.clear()
        if not validation.enabled():
            raise PermissionError(validation.OFF)
        candidate = payload.export(Path(task["worktree"]))
        if _cancel.is_set() or not validation.enabled():
            raise PermissionError(validation.OFF)
        ident = uuid.uuid4().hex
        unit = validation.UNIT_PREFIX + "mac-" + ident
        row = T.start_machine_run(project, slug, lambda n: {
            "purpose": "validation", "target": "macos", "command": shlex.join(argv), "unit": unit,
            "commit": candidate["commit"], "tree": candidate["tree"], "payload_digest": candidate["digest"],
            "run_id": ident, "log": str(S.task_dir(project, slug) / "validation" / f"{n}.log")})
        pending = {"project": project, "slug": slug, "n": row["n"], "run_id": ident, "row": row, "argv": argv,
                   "commit": candidate["commit"], "tree": candidate["tree"], "payload_digest": candidate["digest"]}
        _pending().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(_pending(), pending)
        deadline = time.monotonic() + TIMEOUT
        # The hour includes admission and the final status/result/ack exchanges.
        # The Mac starts its own clock only after upload; reserving these windows
        # keeps a full-length executor result inside the submitter's deadline.
        duration = int(TIMEOUT - 4 * platform.VALIDATION_REQUEST_SECONDS - POLL_MAX_SECONDS)
        response = _call("submit", ident, argv=argv, payload=candidate, duration=duration)
        not_admitted = response.get("accepted") is False
        pause = POLL_SECONDS
        while response.get("status") != "finished" and not not_admitted:
            if response.get("status") == "running" and not _matches(response, pending):
                raise ValueError("Mac validation identity mismatch")
            if (response.get("status") == "absent" or _cancel.is_set()
                    or not validation.enabled() or time.monotonic() >= deadline):
                response = _call("cancel", ident)
                not_admitted = (response.get("status") == "cancelled" and response.get("accepted") is False
                                and response.get("run_id") == ident)
                break
            _cancel.wait(min(pause, max(0, deadline - time.monotonic())))
            if _cancel.is_set():
                continue
            pause = min(pause * 2, POLL_MAX_SECONDS) if response.get("status") == "unavailable" else POLL_SECONDS
            response = _call("status", ident)
        if response.get("status") == "finished" and _matches(response, pending):
            response = _call("result", ident)
            pause = POLL_SECONDS
            while response.get("status") == "unavailable" and time.monotonic() < deadline and not _cancel.is_set():
                _cancel.wait(min(pause, max(0, deadline - time.monotonic())))
                pause = min(pause * 2, POLL_MAX_SECONDS)
                response = _call("result", ident)
            if not _matches(response, pending):
                raise ValueError("Mac validation result identity mismatch")
            result.update({key: response.get(key) for key in ("exit", "error", "host", "guest", "template", "cleanup")})
            result["timed_out"] = response.get("ended") == "timeout"
            if "evidence" in response:
                result.update(_deliver(response, pending))
                pending["delivered"] = True
                write_json(_pending(), pending)
                acknowledgement = _call("result", ident, received=response["evidence"]["digest"])
                acknowledged = _matches(acknowledgement, pending)
            elif result["exit"] == 0:
                raise ValueError("Successful Mac validation has no evidence")
            else:
                acknowledged = True
            if response.get("cleanup") and acknowledged:
                _pending().unlink()
            elif not response.get("cleanup"):
                result.update(exit=None, error="Mac validation cleanup remains unconfirmed")
            result["ended"] = response.get("ended", "unavailable")
        else:
            result.update(error="Mac unavailable; no passing result", ended="unavailable")
            if not_admitted:
                _pending().unlink()
            else:
                result["error"] += "; cancellation must be reconciled"
    except (OSError, ValueError, RuntimeError) as exc:
        if row is None:
            raise
        if pending is not None:
            try:
                _call("cancel", pending["run_id"])
            except (OSError, ValueError, RuntimeError):
                pass  # Retain pending identity; refusal is never proof of remote termination.
        result.update(exit=None, ended="unavailable", error="Mac validation failed; no passing result")
    finally:
        try:
            if row is not None:
                if not result.get("log"):
                    try:
                        directory = validation._task_dir_fd(project, slug)
                        try:
                            log = os.open(f"{row['n']}.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                          0o600, dir_fd=directory)
                            with os.fdopen(log, "w") as stream:
                                stream.write((result.get("error") or "Mac validation produced no command log") + "\n")
                        finally:
                            os.close(directory)
                    except OSError:
                        result.update(exit=None, error="Mac validation evidence delivery failed")
                result.update(n=row["n"], commit=row["commit"], unit=row["unit"],
                              log=result.get("log", row["log"]), started=row["started"], finished=S.now())
                T.finish_machine_run(project, slug, {**row, **{k: v for k, v in result.items() if k != "output"}})
        finally:
            _lock.release()
    return result
