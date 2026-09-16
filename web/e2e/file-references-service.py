"""File references through real saved conversations and bounded file reads in a disposable home."""
from service_support import configure, serve
from altitude import config, l3, server, state as S, tasks


def main():
    configure()
    repo = config.PROJECT_ROOTS[0] / "alpha"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("Fictional browser project.\n")
    with config.add_project("alpha", path=repo):
        pass
    task = tasks.new("alpha", "File reference task", "Read fictional instructions.")
    slug = task["slug"]
    directory = S.task_dir("alpha", slug)
    source = ("# Setup instructions\n\nRead **carefully** before using these commands.\n\n"
              "- Inspect the configuration.\n- Keep this document open.\n\n"
              "```sh\necho 'fictional command; never executed by opening this document'\n```\n\n"
              "<script>window.documentExecuted = true</script>\n\n"
              "![tracking](https://example.test/tracker.png)\n\n"
              "[Documentation](https://example.test/docs)\n")
    names = ["commands.md", "notes.txt", "empty.md", "missing.md", "binary.md", "large.md", "image.png", "inaccessible.md"]
    paths = {name: str(directory / name) for name in names}
    paths["outside.txt"] = "/etc/fictional-private.txt"
    (directory / "commands.md").write_text(source)
    (directory / "notes.txt").write_text("# Literal text\n<b>Keep the brackets.</b>\n")
    (directory / "empty.md").write_text("")
    (directory / "binary.md").write_bytes(b"\xff\xfe")
    (directory / "large.md").write_bytes(b"x" * (1024 * 1024 + 1))
    (directory / "image.png").write_bytes(b"not a text document")
    (directory / "inaccessible.md").symlink_to(directory / "commands.md")
    prose = (f"Read file://{paths['commands.md']} before continuing.\n\n"
             f"The plain document is {paths['notes.txt']}. "
             f"A labeled [setup guide]({paths['commands.md']}) opens the same document.\n\n"
             "Keep [normal web links](https://example.test/docs) and `file:///tmp/code.md` as written.\n\n"
             "```sh\ncat /tmp/code-block.md\n```\n\n"
             + "\n\n".join(f"[{name}]({path})" for name, path in paths.items() if name not in ("commands.md", "notes.txt")))
    tasks.block("alpha", slug, "Fictional saved walkthrough.", actor="l2", updates={"waiting_on": "l3"})
    tasks.message("alpha", slug, "l2", prose)
    l3.save_info("alpha", {"session_id": "fictional-session", "engine_last": config.ENGINES[0], "turns": 1})
    l3.chat_log("alpha", "assistant", prose, trigger="chat", turn_id="saved-files")
    S.regen_state_md("alpha")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/files":
                return self._json({"paths": paths, "source": source, "slug": slug})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/create-missing":
                (directory / "missing.md").write_text("# Recovered document\n\nAvailable after retry.\n")
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
