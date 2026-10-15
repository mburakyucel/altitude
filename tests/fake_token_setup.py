"""A stand-in for Claude's `setup-token` screen, run on the pseudo-terminal Altitude gives it.

It draws the way a full-screen program does: the alternate screen, colours, a hyperlinked sign-in link, and a
differential redraw that rewrites only the cells that change, so the token is never written in one piece. The code
`fixture-code#fixture-state` is accepted, `fixture-mode:<mode>` is accepted and then misbehaves as FAKE_SETUP=<mode>
would, and any other code makes it start over with a new link. FAKE_SETUP picks a misbehaviour:
`exit` ends before any code, `silent` never shows a link, `hang` takes the code and never answers, `fail` exits 1
after the code, `two` shows a cut token before the whole one, `wrapped` wraps the token over two lines, `child` leaves
a process of its own running (its id in FAKE_SETUP_LOG + ".child"). FAKE_SETUP_LOG receives what it started with.
"""
import json
import os
import subprocess
import sys
import termios
import time

TOKEN = "sk-ant-oat01-" + "fixtureToken_0123456789-abcdefghijklmnopqrstuvwxyzABCDEFGH" + "AA"
CODE = "fixture-code#fixture-state"
MODE = os.environ.get("FAKE_SETUP", "")


def draw(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def link(n: int) -> str:
    return f"https://claude.com/cai/oauth/authorize?code=true&client_id=fixture&state=fixture-{n}&code_challenge=x{n}"


def waiting(n: int) -> None:
    url = link(n)
    draw("\x1b[H\x1b[2J\x1b[1m This will guide you through long-lived (1-year) auth token setup.\x1b[22m\r\n\r\n"
         " \x1b[2mBrowser didn't open? Use the url below to sign in \x1b[22m\r\n\r\n"
         f" \x1b]8;;{url}\x07\x1b[2m{url}\x1b[22m\x1b]8;;\x07\r\n\r\n Paste code here if prompted > ")


def main() -> None:
    global MODE
    if os.environ.get("FAKE_SETUP_LOG"):
        with open(os.environ["FAKE_SETUP_LOG"], "w") as log:
            json.dump({"argv": sys.argv[1:], "tty": os.isatty(0), "leader": os.getsid(0) == os.getpid(),
                       "columns": os.get_terminal_size(0).columns, "cwd": os.getcwd(),
                       "env": {key: os.environ.get(key) for key in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
                                                                     "ANTHROPIC_API_KEY", "BROWSER")}}, log)
    if MODE == "child":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(os.environ["FAKE_SETUP_LOG"] + ".child", "w") as out:
            out.write(str(child.pid))
    draw("\x1b[?1049h\x1b[?25l")
    if MODE == "silent":
        time.sleep(600)
    if MODE == "exit":
        time.sleep(0.3)
        sys.exit(0)
    attempt = 1
    waiting(attempt)
    while True:
        code = sys.stdin.readline().strip()
        if code == CODE:
            break
        if code.startswith("fixture-mode:"):
            MODE = code.split(":", 1)[1]
            break
        attempt += 1
        draw("\x1b[H\x1b[2J \x1b[31mOAuth error: Invalid code\x1b[39m\r\n")
        time.sleep(0.2)
        waiting(attempt)
    if MODE == "hang":
        time.sleep(600)
    if MODE == "fail":
        draw("\r\n \x1b[31mSign-in failed\x1b[39m\r\n")
        sys.exit(1)
    # Frame one shows a placeholder where the token goes; frame two rewrites only the cells after the shared prefix.
    prefix = "sk-ant-oat01-"
    draw(f"\x1b[H\x1b[2J \x1b[32m✓ Long-lived authentication token created successfully!\x1b[39m\r\n\r\n"
         f" Your OAuth token (valid for 1 year):\r\n\r\n {prefix}creating…\r\n\r\n"
         " \x1b[2mStore this token securely. You won't be able to see it again.\x1b[22m\r\n")
    time.sleep(0.2)
    if MODE == "two":
        draw(f"\x1b[5;{2 + len(prefix)}H\x1b[33m{TOKEN[len(prefix):60]}\x1b[39m\x1b[K")
        time.sleep(0.2)
    if MODE == "wrapped":
        draw(f"\x1b[5;{2 + len(prefix)}H\x1b[33m{TOKEN[len(prefix):30]}\x1b[39m\x1b[K\r\n {TOKEN[30:]}")
    else:
        draw(f"\x1b[5;{2 + len(prefix)}H\x1b[33m{TOKEN[len(prefix):]}\x1b[39m\x1b[K")
    time.sleep(0.3)
    draw("\x1b[?25h\x1b[?1049l")  # leaving the alternate screen wipes what it showed
    termios.tcdrain(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
