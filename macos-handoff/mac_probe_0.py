#!/usr/bin/env python3
"""Altitude macOS feasibility probe 0. Provider-free, no network, no model downloads, no sudo.

Run as a standard (non-admin is fine) user in a logged-in desktop session:
    python3 mac_probe_0.py
It installs a throwaway LaunchAgent `dev.altitude.probe0` for about a minute, removes it again,
and prints one JSON document (also saved to ~/altitude-probe0/result.json). Nothing else changes.
Works with the system /usr/bin/python3 (3.9).
"""
import ctypes
import ctypes.util
import json
import os
import platform
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

LABEL = "dev.altitude.probe0"
HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "altitude-probe0")
AGENT_PLIST = os.path.join(HOME, "Library", "LaunchAgents", LABEL + ".plist")
MARK = "ALTITUDE_PROBE0_MARK"
UID = os.getuid()
DOMAIN = "gui/%d" % UID
FILE = os.path.abspath(__file__)


def sh(*args, timeout=60, **kw):
    try:
        p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, **kw)
        return {"rc": p.returncode, "out": p.stdout.strip()[-2000:], "err": p.stderr.strip()[-2000:]}
    except Exception as exc:  # noqa: BLE001 - evidence, not control flow
        return {"rc": None, "error": repr(exc)}


# ---- agent side: runs under launchd ---------------------------------------------------------------

def tick_forever(path):
    while True:
        with open(path, "a") as f:
            f.write("%f\n" % time.time())
        time.sleep(0.5)


def spawn_detached(name, keep_env):
    """Double fork + setsid; the grandchild ticks forever."""
    pid = os.fork()
    if pid == 0:
        os.setsid()
        if os.fork():
            os._exit(0)
        if not keep_env:
            os.environ.pop(MARK, None)
            os.execve(sys.executable, [sys.executable, FILE, "tick", os.path.join(ROOT, name + ".ticks")],
                      {"PATH": "/usr/bin:/bin"})
        tick_forever(os.path.join(ROOT, name + ".ticks"))
    os.waitpid(pid, 0)


def agent():
    os.environ[MARK] = "1"
    children = {}
    same_group = subprocess.Popen([sys.executable, FILE, "tick", os.path.join(ROOT, "same_group.ticks")])
    children["same_group"] = same_group.pid
    own_session = subprocess.Popen([sys.executable, FILE, "tick", os.path.join(ROOT, "own_session.ticks")],
                                   start_new_session=True)
    children["own_session"] = own_session.pid
    spawn_detached("detached_marked", keep_env=True)
    spawn_detached("detached_unmarked", keep_env=False)
    with open(os.path.join(ROOT, "agent.%d.json" % os.getpid()), "w") as f:
        json.dump({"agent": os.getpid(), "children": children}, f)
    tick_forever(os.path.join(ROOT, "agent.ticks"))


# ---- process facilities ---------------------------------------------------------------------------

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
CTL_KERN, KERN_PROC, KERN_PROC_ALL, KERN_PROCARGS2, KERN_ARGMAX = 1, 14, 0, 49, 8


def sysctl(mib, size=None):
    arr = (ctypes.c_int * len(mib))(*mib)
    n = ctypes.c_size_t(0)
    if libc.sysctl(arr, len(mib), None, ctypes.byref(n), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "sysctl size")
    buf = ctypes.create_string_buffer(n.value + 4096)
    n = ctypes.c_size_t(len(buf))
    if libc.sysctl(arr, len(mib), buf, ctypes.byref(n), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "sysctl")
    return buf.raw[:n.value]


def environment(pid):
    """KERN_PROCARGS2: argc, exec path, argv, then environment."""
    raw = sysctl([CTL_KERN, KERN_PROCARGS2, pid])
    argc = int.from_bytes(raw[:4], sys.byteorder)
    parts = [p for p in raw[4:].split(b"\0") if p]
    return [p.decode(errors="replace") for p in parts[1 + argc:]]


libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
PROC_PIDCOALITIONINFO = 20


def coalition(pid):
    """XNU coalition ids (resource, jetsam): inherited by every descendant, whatever setsid does."""
    buf = (ctypes.c_uint64 * 5)()
    n = libproc.proc_pidinfo(pid, PROC_PIDCOALITIONINFO, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
    return list(buf[:2]) if n == ctypes.sizeof(buf) else "unavailable rc=%d errno=%d" % (n, ctypes.get_errno())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def advancing(name):
    path = os.path.join(ROOT, name + ".ticks")
    try:
        before = os.path.getsize(path)
        time.sleep(1.5)
        return os.path.getsize(path) > before
    except OSError:
        return False


def all_pids():
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,ppid=,pgid=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        bits = line.split(None, 3)
        if len(bits) == 4:
            rows.append((int(bits[0]), int(bits[1]), int(bits[2]), bits[3]))
    return rows


def probe_pids():
    return [r for r in all_pids() if FILE in r[3] and r[0] != os.getpid()]


def snapshot(label):
    rows = probe_pids()
    marked = []
    for pid, _, _, _ in rows:
        try:
            marked.append([pid, (MARK + "=1") in environment(pid)])
        except OSError as exc:
            marked.append([pid, "unreadable: %s" % exc])
    return {"when": label, "processes": [list(r[:3]) for r in rows], "marker_readable": marked,
            "coalitions": [[pid, coalition(pid)] for pid, _, _, _ in rows], "self_coalition": coalition(os.getpid()),
            "ticking": {n: advancing(n) for n in ("agent", "same_group", "own_session", "detached_marked",
                                                   "detached_unmarked")}}


# ---- other host facts -----------------------------------------------------------------------------

def openssl_probe():
    d = tempfile.mkdtemp(prefix="tls", dir=ROOT)
    o = "/usr/bin/openssl"
    r = {"version": sh(o, "version")}
    steps = [
        ("ca_key", [o, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", d + "/ca.key"]),
        ("leaf_key", [o, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", d + "/s.key"]),
        ("ca", [o, "req", "-x509", "-new", "-key", d + "/ca.key", "-sha256", "-days", "3650", "-out", d + "/ca.crt",
                "-subj", "/CN=Probe CA", "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-addext", "nameConstraints=critical,permitted;IP:127.0.0.0/255.0.0.0,permitted;IP:192.168.0.0/255.255.0.0,"
                           "permitted;DNS:localhost,permitted;DNS:.local"]),
        ("csr", [o, "req", "-new", "-key", d + "/s.key", "-subj", "/CN=Probe", "-out", d + "/s.csr"]),
    ]
    for name, args in steps:
        r[name] = sh(*args)
    for leaf, san in (("good", "DNS:localhost,IP:127.0.0.1"), ("bad", "DNS:example.com")):
        with open(d + "/%s.ext" % leaf, "w") as f:
            f.write("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                    "extendedKeyUsage=serverAuth\nsubjectAltName=%s\n" % san)
        r[leaf + "_issue"] = sh(o, "x509", "-req", "-in", d + "/s.csr", "-CA", d + "/ca.crt", "-CAkey", d + "/ca.key",
                                "-set_serial", "0x01", "-days", "365", "-sha256", "-out", d + "/%s.crt" % leaf,
                                "-extfile", d + "/%s.ext" % leaf)
        r[leaf + "_verify"] = sh(o, "verify", "-CAfile", d + "/ca.crt", "-purpose", "sslserver",
                                 "-verify_hostname", "localhost" if leaf == "good" else "example.com", d + "/%s.crt" % leaf)
    r["ip_verify"] = sh(o, "verify", "-CAfile", d + "/ca.crt", "-verify_ip", "127.0.0.1", d + "/good.crt")
    return r


def rlimit_probe():
    code = ("import resource,os;resource.setrlimit(resource.RLIMIT_AS,(1<<30,1<<30));"
            "print(resource.getrlimit(resource.RLIMIT_AS));os.execv('/bin/echo',['echo','exec-ok'])")
    return sh(sys.executable, "-c", code)


def unix_socket_probe():
    base = os.environ.get("TMPDIR", "/tmp")
    out = {"tmpdir": base, "tmpdir_len": len(base)}
    for n in (100, 103, 104, 110):
        path = os.path.join(base, "s" * max(1, n - len(base) - 1))[:n]
        s = socket.socket(socket.AF_UNIX)
        try:
            s.bind(path)
            out[str(len(path.encode()))] = "ok"
        except OSError as exc:
            out[str(len(path.encode()))] = str(exc)
        finally:
            s.close()
            try:
                os.unlink(path)
            except OSError:
                pass
    return out


def seatbelt_probe():
    """Can a sandbox profile stop a worker from signalling processes outside its own tree?"""
    target = subprocess.Popen(["/bin/sleep", "30"])
    profile = "(version 1)(allow default)(deny signal (target others))"
    try:
        return {"other_denied": sh("/usr/bin/sandbox-exec", "-p", profile, "/bin/kill", "-0", str(target.pid)),
                "self_allowed": sh("/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", "kill -0 $$"),
                "launchctl_inside": sh("/usr/bin/sandbox-exec", "-p", profile, "/bin/launchctl", "print",
                                       "gui/%d" % UID)["rc"],
                "nested_sandbox": sh("/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/sandbox-exec", "-p",
                                     "(version 1)(allow default)", "/usr/bin/true"),
                "codex_signal_other": sh(shutil.which("codex") or "codex", "sandbox", "macos", "--", "/bin/kill", "-0",
                                         str(target.pid)) if shutil.which("codex") else "codex absent"}
    finally:
        target.kill()


def pid_allocation_probe():
    """Are new PIDs handed out in increasing order (reuse only after the counter wraps)?"""
    pids = []
    for _ in range(30):
        child = subprocess.Popen(["/usr/bin/true"])
        child.wait()
        pids.append(child.pid)
    steps = [b - a for a, b in zip(pids, pids[1:])]
    return {"pids": pids, "increasing": all(step > 0 for step in steps), "max_step": max(steps),
            "pid_max": sh("/usr/sbin/sysctl", "-n", "kern.maxproc")["out"]}


def credentials_probe():
    """Where engine sign-ins live (existence only; nothing is read) and whether the login keychain is unlocked."""
    return {"claude_credentials_file": os.path.exists(os.path.join(HOME, ".claude", ".credentials.json")),
            "codex_auth_file": os.path.exists(os.path.join(HOME, ".codex", "auth.json")),
            "login_keychain": sh("/usr/bin/security", "show-keychain-info",
                                 os.path.join(HOME, "Library/Keychains/login.keychain-db"))}


def codex_probe():
    codex = shutil.which("codex")
    if not codex:
        return {"present": False}
    r = {"present": True, "version": sh(codex, "--version"), "help": sh(codex, "sandbox", "--help")}
    work = tempfile.mkdtemp(prefix="work", dir=ROOT)
    outside = os.path.join(HOME, "altitude-probe0-outside.txt")
    settings = ['sandbox_mode="workspace-write"', 'sandbox_workspace_write.writable_roots=["%s"]' % work,
                'sandbox_workspace_write.network_access=true']
    for name, target in (("write_inside", os.path.join(work, "inside.txt")), ("write_outside", outside)):
        args = [codex, "sandbox", "macos"]
        for s in settings:
            args += ["-c", s]
        r[name] = sh(*args, "--", "/bin/sh", "-c", "echo x > '%s'" % target, cwd=work, timeout=60)
        r[name]["file_exists"] = os.path.exists(target)
    try:
        os.unlink(outside)
    except OSError:
        pass
    return r


def main():
    if sys.platform != "darwin":
        sys.exit("macOS only")
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(os.path.dirname(AGENT_PLIST), exist_ok=True)
    result = {"probe": "altitude-mac-probe-0", "sw_vers": sh("/usr/bin/sw_vers"), "machine": platform.machine(),
              "chip": sh("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"), "python": sys.version,
              "brew_python": sh("/opt/homebrew/bin/python3", "--version"), "git": sh("git", "--version"),
              "gh": sh("gh", "--version"), "claude": sh("claude", "--version"),
              "launchctl_version": sh("/bin/launchctl", "version")}
    result["user_domain"] = sh("/bin/launchctl", "print", "user/%d" % UID)["rc"]
    result["gui_domain"] = sh("/bin/launchctl", "print", DOMAIN)["rc"]
    with open(AGENT_PLIST, "wb") as f:
        plistlib.dump({"Label": LABEL, "ProgramArguments": [sys.executable, FILE, "agent"],
                       "RunAtLoad": True, "KeepAlive": False, "WorkingDirectory": ROOT,
                       "StandardOutPath": ROOT + "/agent.log", "StandardErrorPath": ROOT + "/agent.log"}, f)
    try:
        result["bootstrap"] = sh("/bin/launchctl", "bootstrap", DOMAIN, AGENT_PLIST)
        time.sleep(4)
        result["print_running"] = sh("/bin/launchctl", "print", "%s/%s" % (DOMAIN, LABEL))["out"][-1500:]
        result["before"] = snapshot("running")
        result["kickstart_k"] = sh("/bin/launchctl", "kickstart", "-k", "%s/%s" % (DOMAIN, LABEL))
        time.sleep(4)
        result["after_kickstart"] = snapshot("after kickstart -k")
        result["bootout"] = sh("/bin/launchctl", "bootout", "%s/%s" % (DOMAIN, LABEL))
        time.sleep(4)
        result["after_bootout"] = snapshot("after bootout")
    finally:
        sh("/bin/launchctl", "bootout", "%s/%s" % (DOMAIN, LABEL))
        for pid, _, _, _ in probe_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.unlink(AGENT_PLIST)
        except OSError:
            pass
    result["openssl"] = openssl_probe()
    result["rlimit_as"] = rlimit_probe()
    result["unix_socket"] = unix_socket_probe()
    result["codex_sandbox"] = codex_probe()
    result["seatbelt"] = seatbelt_probe()
    result["credentials"] = credentials_probe()
    result["pid_allocation"] = pid_allocation_probe()
    result["session"] = {"ssh": bool(os.environ.get("SSH_CONNECTION")), "console_user": sh("/usr/bin/stat", "-f", "%Su", "/dev/console")}
    result["case_insensitive_home"] = os.path.exists(HOME.upper()) and os.path.samefile(HOME, HOME.upper())
    text = json.dumps(result, indent=1)
    with open(os.path.join(ROOT, "result.json"), "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        agent()
    elif len(sys.argv) > 2 and sys.argv[1] == "tick":
        tick_forever(sys.argv[2])
    else:
        main()
