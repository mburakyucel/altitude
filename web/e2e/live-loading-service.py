"""Long fictional transcripts behind real task storage, projection and HTTP handlers."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sys

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from altitude import config, engines, server, state as S, tasks as T, transcript


def main():
    configure()
    project = "atlas"
    repo = make_repo(config.PROJECT_ROOTS[0] / project)
    with config.add_project(project, path=repo):
        pass
    sources = {}
    counts = {}
    engine = config.ENGINES[0]
    start = datetime.now(timezone.utc) - timedelta(days=1)

    def record(index, label):
        # Varying, deterministic fictional text avoids measuring an unrealistically repetitive body.
        detail = " ".join(hashlib.sha256(f"fixture-{label}-{index}-{part}".encode()).hexdigest() for part in range(5))
        at = (start + timedelta(seconds=index)).isoformat()
        kind = ("message", "command", "result", "engine")[index % 4]
        row = {"source": engine, "kind": kind, "role": "assistant", "type": "fixture", "at": at,
               "text": f"Activity {label} {index}: {detail}"}
        if kind in ("command", "result"):
            row.update(tool_use_id=f"call-{index // 4}", tool="fixture-check", summary=f"Inspect fixture {index // 4}")
        return {"timestamp": at, "row": row}

    engines.transcript_sources = lambda task, **_kwargs: [sources[task["slug"]]]
    engines.activity_source = lambda task, **_kwargs: sources[task["slug"]]
    engines.session_context_source = lambda _task: None
    engines.transcript_rows = lambda _engine, value, _worktree: [dict(value["row"])]
    engines.public_message_identity = lambda _engine, _value: None

    tasks = []
    for count in ((0, 100) if "single" in sys.argv else (0, 100, 5000, 20000)):
        row = T.new(project, f"Inspect {count} activity records", "Fictional long-session loading measurement.")
        slug = row["slug"]
        worktree = add_worktree(repo, slug)
        session = f"fixture-{count}"
        T.dispatch(project, slug, attempt=1, session_id=session, agent_id=session,
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=engine)
        path = config.ROOT / f"fixture-{count}.jsonl"
        # Last record is a visible message, so measuring the landmark proves recent content arrived.
        rows = [record(index, count) for index in range(count)]
        if count:
            rows.append(record(count, count))
        path.write_text("".join(json.dumps(value) + "\n" for value in rows))
        sources[slug] = (path, {"id": session, "session_id": session, "started_at": start.isoformat(), "input_delivered": True})
        counts[slug] = count
        T.message(project, slug, "l2", f"Conversation for {count} records is ready.")
        tasks.append({"slug": slug, "count": count, "latest": f"Activity {count} {count}:"})

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/status":
                return self._json({"tasks": tasks})
            return super().do_GET()

        def do_POST(self):
            if self.path not in ("/fixture/append", "/fixture/control"):
                return super().do_POST()
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            slug = body["slug"]
            mode = "append" if self.path == "/fixture/append" else body["mode"]
            path = sources[slug][0]
            if mode == "reset-index":
                with transcript._indexes_lock:
                    transcript._indexes.clear()
                return self._json({"ok": True})
            if mode == "replace-source":
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
                return self._json({"ok": True})
            if mode == "empty":
                path.write_text("")
                return self._json({"ok": True})
            counts[slug] += 4
            value = record(counts[slug], "update")
            if mode == "late":
                value = record(-4, "late")
            elif mode == "command-result":
                # An appended result changes an existing old call, behind the recent tail.
                value["row"].update(kind="result", role="tool", tool_use_id="call-0",
                                    text="Late fixture command result: complete.")
            elif mode == "long-record":
                value["row"]["text"] = "Long fixture record " + "readable detail " * 900
            elif mode != "append":
                return self._json({"error": "Unknown fixture control"}, 400)
            with path.open("a") as stream:
                stream.write(json.dumps(value) + "\n")
            return self._json({"latest": value["row"]["text"].split(":")[0][:80] + ":"})

    serve(Handler)


if __name__ == "__main__":
    main()
