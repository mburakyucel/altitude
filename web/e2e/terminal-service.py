"""Real terminals over the real API: a plain bash on a real pseudo-terminal in a fictional task worktree
and project folder, whose saved conversations hold chat commands (`run` blocks) to open in them. Three
things are fixtures: the agent check (service_support passes every request; /fixture/agent refuses them)
and the shell (no profile files, a fixed prompt) and its job, which the service manager runs in production.
Routes under /fixture/ drive the lifecycle events a walkthrough cannot cause from the page: a lost stream,
a finished task, an agent request, a restart, an update that replaces the built app under an open page, the
app's files out of reach. /fixture/notices reads what waits for the task owner and the coordinator."""
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

from service_support import configure, serve
from tests.support import add_worktree, local_terminal_launch, local_terminal_stop, make_repo
from altitude import config, l3, server, state as S, tasks as T, terminal


def main():
    configure()
    # The page is served from hard links to this checkout's build, beside it in the ignored artifacts
    # folder, so an update can replace files without touching the build other walkthroughs read.
    artifacts = config.WEB_DIST.parent / "ui-artifacts"
    artifacts.mkdir(exist_ok=True)
    dist = Path(tempfile.mkdtemp(prefix="dist-", dir=artifacts))
    shutil.copytree(config.WEB_DIST, dist, copy_function=os.link, dirs_exist_ok=True)
    config.WEB_DIST = dist
    os.environ.update({"PS1": r"\W $ ", "PROMPT_COMMAND": ""})
    terminal.shell_command = lambda: ["bash", "--noprofile", "--norc"]
    terminal.launch, terminal.stop = local_terminal_launch, local_terminal_stop
    agent = threading.Event()
    terminal.agent_connection = lambda _peer, _local: agent.is_set()

    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    slug = T.new("atlas", "Prepare index migration", "Fictional terminal work, never sent to a live provider.")["slug"]
    worktree = add_worktree(repo, slug)
    T.dispatch("atlas", slug, attempt=1, session_id=f"fixture-{slug}", agent_id=f"fixture-{slug}",
               worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    T.message("atlas", slug, "l2", "Only you can take this step. Nothing runs until you press Enter:\n\n"
              "```run\necho ran-$((20+22))\n```")
    l3.save_info("atlas", {"session_id": "fixture-atlas", "engine_last": config.ENGINES[0], "turns": 1})
    l3.chat_log("atlas", "assistant", "This one is yours to run here:\n\n```run\necho project-$((5*5))\n```\n\n"
                "Run this one on the other machine:\n\n~~~sh\nuname -a\n~~~\n\n"
                "```run\ncd /tmp\nls\n```", trigger="chat", turn_id="saved-chat")
    streams = set()
    held = threading.Event()
    unreachable = threading.Event()

    class Handler(server.Handler):
        def _terminal_stream(self, *args):
            streams.add(self)
            try:
                return super()._terminal_stream(*args)
            finally:
                streams.discard(self)

        def _static(self, raw_path):
            if unreachable.is_set() and (raw_path == "/" or raw_path.startswith("/assets/TerminalScreen-")):
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return None
            return super()._static(raw_path)

        def _terminal_get(self, parts, q):
            if held.is_set():
                return self._json({"error": "Altitude is unreachable."}, 503)
            return super()._terminal_get(parts, q)

        def do_POST(self):
            if self.path == "/fixture/drop":  # the connection drops; the page cannot reach altd until /fixture/back
                held.set()
                for stream in list(streams):
                    stream.connection.shutdown(socket.SHUT_RDWR)
                return self._json({"ok": True})
            if self.path == "/fixture/back":
                held.clear()
                unreachable.clear()
                return self._json({"ok": True})
            if self.path == "/fixture/unreachable":  # the connection drops for the terminal's code and the page
                unreachable.set()
                return self._json({"ok": True})
            if self.path == "/fixture/notices":  # what waits for the task owner's next checkpoint and the coordinator
                return self._json({"notices": [row["text"] for row in T.pending("atlas", slug)
                                               if row.get("by") == "terminal"],
                                   "coordinator": [row["text"] for row in l3.queued("atlas")
                                                   if row["trigger"] == "terminal"]})
            if self.path == "/fixture/finish":  # the task finishes; altd's next tick closes its terminal
                row = S.load_task("atlas", slug)
                row.update(state="done", agent_id=None)
                S.save_task("atlas", row)
                terminal.sweep()
                return self._json({"ok": True})
            if self.path == "/fixture/agent":
                agent.set() if self._body().get("on") else agent.clear()
                return self._json({"ok": True})
            if self.path == "/fixture/update":  # activation swaps the build: new names, the old files gone
                assets = dist / "assets"
                renames = {path.name: path.name.replace("-", "-updated-", 1)
                           for path in [*assets.glob("TerminalScreen-*.js"), *assets.glob("index-*.js")]}
                for old, new in renames.items():
                    text = (assets / old).read_bytes()
                    for before, after in renames.items():
                        text = text.replace(before.encode(), after.encode())
                    (assets / old).unlink()
                    (assets / new).write_bytes(text)
                page = (dist / "index.html").read_text()
                for before, after in renames.items():
                    page = page.replace(before, after)
                (dist / "index.html").unlink()
                (dist / "index.html").write_text(page)
                return self._json({"ok": True})
            if self.path == "/fixture/restart":  # altd stops without a word to the page and a new one starts
                held.set()
                for stream in list(streams):
                    stream.connection.shutdown(socket.SHUT_RDWR)
                for key in list(terminal._terminals):
                    terminal.close(*key)
                held.clear()
                return self._json({"ok": True})
            return super().do_POST()

    def release():
        terminal.close_all()
        shutil.rmtree(dist)

    serve(Handler, release=release)


if __name__ == "__main__":
    main()
