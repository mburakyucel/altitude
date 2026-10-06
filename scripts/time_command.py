#!/usr/bin/env python3
"""Run a command with inherited streams and one portable wall/user/system summary."""
import errno
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: time_command.py COMMAND [ARG ...]", file=sys.stderr)
        return 2
    start = time.monotonic()
    try:
        child = subprocess.Popen(sys.argv[1:])
    except OSError as error:
        print(f"Cannot start timed command: {error.strerror}", file=sys.stderr)
        return 127 if error.errno == errno.ENOENT else 126
    # Terminal interrupts reach the child in our shared process group. Like time(1),
    # the timer waits for that exit so it never loses the command or its summary.
    saved = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGINT, signal.SIGQUIT)}
    try:
        _, status, usage = os.wait4(child.pid, 0)
        child.returncode = os.waitstatus_to_exitcode(status)
    finally:
        for number, handler in saved.items():
            signal.signal(number, handler)
    summary = (f"real {time.monotonic() - start:.2f}\n"
               f"user {usage.ru_utime:.2f}\nsys {usage.ru_stime:.2f}\n")
    os.write(2, summary.encode())
    if child.returncode < 0:
        number = -child.returncode
        if number != signal.SIGKILL:
            signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
    return child.returncode


if __name__ == "__main__":
    sys.exit(main())
