"""Observe service-manager calls in the disposable Ubuntu acceptance VM only.

The VM driver invokes this as guest root, never on the development host. All
messages and sender metadata describe fictional guest processes. Unknown writes
fail acceptance; reads and systemd's same-user delegated attachment are explicit.
"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


CONTROLS = {"altitude-fictional-missing.scope", "altitude-fictional-ending.scope"}


def classify(block, identity):
    """Return the permitted operation, or None for a forbidden/unknown attempt."""
    header = block.splitlines()[0]
    method = re.search(r"member=(\w+)", header)
    interface = re.search(r"interface=([^; ]+)", header)
    if not method or not interface:
        return None
    method, interface = method[1], interface[1]
    strings = re.findall(r'^\s+string "(.*)"$', block, re.M)
    if interface == "org.freedesktop.DBus.Properties" and method in {"Get", "GetAll"}:
        return "read"
    if interface == "org.freedesktop.DBus.Introspectable" and method == "Introspect":
        return "read"
    if (interface == "org.freedesktop.systemd1.Scope" and method == "Abandon"
            and identity.get("uid") == 0 and identity.get("exe") == "/usr/lib/systemd/systemd-logind"
            and re.search(r"path=/org/freedesktop/systemd1/unit/session_2d\d+_2escope;", header)):
        return "login-session"
    if interface != "org.freedesktop.systemd1.Manager":
        return None
    if method in {"GetUnit", "GetUnitByPID", "GetUnitByControlGroup", "ListUnits", "ListJobs"}:
        return "read"
    if method == "StopUnit" and len(strings) == 2 and strings[1] == "replace" and strings[0] in CONTROLS:
        return "control"
    if (method == "StartTransientUnit" and identity.get("uid") == 0
            and identity.get("exe") == "/usr/lib/systemd/systemd-logind"
            and strings and re.fullmatch(r"session-\d+\.scope", strings[0])):
        return "login-session"
    # systemd255 cgroup.c unit_attach_pid_to_cgroup_via_bus uses an empty
    # unit name: dbus-manager.c resolves that to the caller's own delegated
    # user@ service. dbus-unit.c requires sender/process/unit UIDs to agree;
    # it has no polkit authorization path. Verify the actual sender too.
    if (method == "AttachProcessesToUnit" and len(strings) == 2 and strings[0] == ""
            and re.fullmatch(r"/user\.slice/podman-(?:pause-)?[a-z0-9]+\.scope", strings[1])
            and identity.get("uid") == 1000
            and identity.get("exe") == "/usr/lib/systemd/systemd"
            and identity.get("argv") in (["/usr/lib/systemd/systemd", "--user"], ["/lib/systemd/systemd", "--user"])
            and identity.get("cgroup") == "0::/user.slice/user-1000.slice/user@1000.service/init.scope"):
        return "same-user-attachment"
    return None


def sender_identity(sender):
    try:
        result = subprocess.run(["busctl", "--system", "call", "org.freedesktop.DBus",
            "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", sender],
            capture_output=True, text=True, check=True, timeout=3)
        kind, value = result.stdout.split()
        if kind != "u":
            raise ValueError("Unexpected PID reply")
        return process_identity(int(value))
    except Exception as error:
        return {"unavailable": str(error)}


def process_identity(pid):
    try:
        process = Path("/proc") / str(pid)
        return {"pid": pid, "uid": process.stat().st_uid,
                "exe": str((process / "exe").resolve(strict=True)),
                "argv": (process / "cmdline").read_bytes().decode().rstrip("\0").split("\0"),
                "cgroup": (process / "cgroup").read_text().strip()}
    except Exception as error:
        return {"unavailable": str(error)}


def main():
    if os.environ.get("ALTITUDE_FICTIONAL_VM") != "1" or os.getuid() != 0:
        raise SystemExit("Only run through scripts/container_vm.py in its fictional guest")
    output = Path(sys.argv[1])
    output.mkdir()
    process = subprocess.Popen(["dbus-monitor", "--system",
        "type=method_call,path_namespace=/org/freedesktop/systemd1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    signal.signal(signal.SIGTERM, lambda *_: process.terminate())
    (output / "ready").write_text(str(os.getpid()))
    block, identity = [], {}
    calls = []
    def save():
        if block:
            text = "".join(block)
            attached = [process_identity(int(pid)) for pid in re.findall(r'^\s+uint32 (\d+)$',text,re.M)] \
                       if 'member=AttachProcessesToUnit' in text else []
            calls.append({"message": text, "sender": identity, "attached": attached, "allowed": classify(text, identity)})
            (output / "calls.json").write_text(json.dumps(calls, indent=2) + "\n")
    for line in process.stdout:
        if line.startswith("method call "):
            save()
            block = [line]
            sender = re.search(r"sender=([^ ]+)", line)
            identity = sender_identity(sender[1]) if sender else {}
        elif block:
            block.append(line)
    save()
    code = process.wait(timeout=5)
    (output / "exit.json").write_text(json.dumps({"exit": code, "stderr": process.stderr.read()}))
    return 0 if code == -signal.SIGTERM else 1


if __name__ == "__main__":
    raise SystemExit(main())
