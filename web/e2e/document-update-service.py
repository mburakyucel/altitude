"""Documents opened across a built-app replacement, using real handlers and fictional storage."""
import re
import socket
import shutil
import struct
import zlib

from service_support import configure, copy_build, serve
from tests.support import add_worktree, make_repo
from altitude import config, l3, server, state as S, tasks


def main():
    configure()
    dist = config.WEB_DIST = copy_build(config.WEB_DIST)
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    slug = tasks.new("atlas", "Read the migration plan", "Fictional document walkthrough.")["slug"]
    worktree = add_worktree(repo, slug)
    tasks.dispatch("atlas", slug, attempt=1, session_id="fixture-documents", agent_id="fixture-documents",
                   worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    source = "# Migration plan\n\nKeep **the conversation** open.\n\n| Step | Status |\n| --- | --- |\n| Read | Ready |\n"
    source += "\n![Migration diagram](diagram.png)\n"
    # A fictional diagram copied through the same shown-document path as production saved images.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + b"".join(bytes((99, 102, 241) if 30 < y < 70 and
                         (20 < x < 100 or 180 < x < 260) else (241, 245, 249))
                         for x in range(280)) for y in range(100))
    (worktree / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 100, 8, 2, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
    (worktree / "PLAN.md").write_text(source)
    saved = S.task_dir("atlas", slug) / "PLAN.md"
    saved.write_text(source)
    shown = tasks.message("atlas", slug, "l2", "Read the migration plan.", by="l2", expected_attempt=1,
                          show=tasks.shown_files("atlas", S.load_task("atlas", slug), ["PLAN.md"]))["id"]
    (worktree / "SECOND.md").write_text("# Second plan\n\nA different document stays readable.\n")
    second = tasks.message("atlas", slug, "l2", "Read the second plan.", by="l2", expected_attempt=1,
                           show=tasks.shown_files("atlas", S.load_task("atlas", slug), ["SECOND.md"]))["id"]
    l3.save_info("atlas", {"session_id": "fixture-documents", "engine_last": config.ENGINES[0], "turns": 1})
    l3.chat_log("atlas", "system", source, trigger="project-message", turn_id="fixture-document-message",
                project_message={"sender": "lab", "recipient": "atlas", "summary": "Migration plan",
                                 "status": "sent", "direction": "sent", "message_id": "fictional-document-message",
                                 "exchange_id": "fictional-document-exchange"})

    unreachable = False

    class Handler(server.Handler):
        def _static(self, raw_path):
            if unreachable and raw_path.startswith("/assets/MarkdownDocument-"):
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return None
            return super()._static(raw_path)

        # Fixture controls do not interpose on the application's GET or POST dispatchers.
        def do_PUT(self):
            nonlocal unreachable
            if self.path == "/fixture/documents":
                return self._json({"slug": slug, "shown": shown, "second": second, "path": str(saved), "source": source})
            if self.path == "/fixture/unreachable":
                unreachable = True
                return self._json({"ok": True})
            if self.path == "/fixture/back":
                unreachable = False
                return self._json({"ok": True})
            if self.path == "/fixture/renderer-error":
                # The module loads successfully; its default component throws only when rendered.
                for path in (dist / "assets").glob("MarkdownDocument-*.js"):
                    text, count = re.subn(r"[A-Za-z_$][\w$]*\s+as\s+default", "fixtureRendererFailure as default",
                                         path.read_text(), count=1)
                    assert count == 1, "The built renderer must have one default export"
                    path.unlink()
                    path.write_text(text + "\nfunction fixtureRendererFailure(){throw new Error('Fictional renderer failure');}\n")
                return self._json({"ok": True})
            if self.path == "/fixture/update":
                kind = self._body().get("asset", "both")
                # Like activation, replace the served build and remove the previous hashed JS/CSS.
                # Rewrite references in the actual built output; no mocked import exceptions or asset responses.
                assets = dist / "assets"
                renames = {path.name: path.name.replace("-", "-updated-", 1)
                           for path in assets.iterdir() if path.suffix in (".js", ".css")}
                for path in [dist / "index.html", *assets.glob("*.js"), *assets.glob("*.css")]:
                    original = text = path.read_bytes()
                    for old, new in renames.items():
                        text = text.replace(old.encode(), new.encode())
                    path.unlink()
                    (path.parent / renames.get(path.name, path.name)).write_bytes(text)
                    # Isolate which removed asset causes the failure while the new page still names a
                    # complete replacement build. Retained files keep their original immutable bytes.
                    if (kind == "css" and path.suffix == ".js") or (kind == "js" and path.suffix == ".css"):
                        path.write_bytes(original)
                return self._json({"ok": True, "removed": list(renames)})
            return self._json({"error": "Unknown fixture control"}, 404)

    serve(Handler, release=lambda: shutil.rmtree(dist))


if __name__ == "__main__":
    main()
