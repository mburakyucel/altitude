"""Small external-worker fixture. Task state, routing, inboxes and Git stay real."""
from tests.support import engines


class FakeL2:
    def __init__(self):
        self.calls = []
        self.workers = {}
        self.outcomes = []
        self.on_resume = None

    def install(self, case):
        for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
            case.patch(engines, name, new=getattr(self, name))

    def _launch(self, engine, name, prompt, session_id, kwargs):
        self.calls.append({"engine": engine, "name": name, "prompt": prompt,
                           "session_id": session_id, **kwargs})
        if self.outcomes:
            result = self.outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                return result
        worker_id = f"fixture-worker-{len(self.calls)}"
        row = {"id": worker_id, "sessionId": session_id or f"fixture-session-{len(self.calls)}",
               "state": "working", "status": "busy", "engine_model": kwargs.get("model"), "input_delivered": True}
        self.workers[worker_id] = row
        return {"returncode": 0, "stdout": "", "stderr": "", "agent": dict(row)}

    def start_l2(self, engine, name, prompt, **kwargs):
        return self._launch(engine, name, prompt, None, kwargs)

    def resume_l2(self, engine, name, session_id, prompt, **kwargs):
        if self.on_resume:
            self.on_resume()
        return self._launch(engine, name, prompt, session_id, kwargs)

    def worker(self, engine, task, *, job_root):
        row = self.workers.get(task.get("agent_id"))
        return dict(row) if row else None

    def stop_l2_worker(self, engine, worker_id, *, job_root):
        if worker_id in self.workers:
            self.workers[worker_id].update(state="stopped", status="exited")
        return "fixture worker stopped"

    remove_l2_worker = stop_l2_worker
