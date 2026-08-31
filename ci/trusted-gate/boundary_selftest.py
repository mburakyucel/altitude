#!/usr/bin/python3
"""Checks the boundary from the same unprivileged context that will execute candidate code."""
from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import platform
import signal
import socket
import stat
import subprocess
import threading
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syscall.restype = ctypes.c_long


def raw_syscall(number: int, *arguments: object) -> tuple[int, int]:
    ctypes.set_errno(0)
    result = LIBC.syscall(ctypes.c_long(number), *arguments)
    return int(result), ctypes.get_errno()


def require_raw_socket(number: int, domain: int) -> None:
    result, error = raw_syscall(
        number, ctypes.c_int(domain), ctypes.c_int(socket.SOCK_STREAM), ctypes.c_int(0)
    )
    require(result >= 0, f"raw socket({domain}) failed with errno {error}")
    os.close(result)


def require_raw_eperm(number: int, *arguments: object) -> None:
    result, error = raw_syscall(number, *arguments)
    require(result == -1 and error == errno.EPERM,
            f"raw syscall {number} returned {result} with errno {error}, expected EPERM")


def check_raw_syscall_boundary() -> None:
    architecture = platform.machine().lower()
    if architecture == "x86_64":
        syscall_numbers = {
            "getpid": 39, "socket": 41, "socketpair": 53,
            "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        }
    elif architecture in ("aarch64", "arm64"):
        syscall_numbers = {
            "getpid": 172, "socket": 198, "socketpair": 199,
            "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        }
    else:
        raise AssertionError(f"unsupported syscall-test architecture: {architecture}")

    for domain in (socket.AF_UNIX, socket.AF_INET, socket.AF_INET6):
        require_raw_socket(syscall_numbers["socket"], domain)

    descriptors = (ctypes.c_int * 2)(-1, -1)
    result, error = raw_syscall(
        syscall_numbers["socketpair"], ctypes.c_int(socket.AF_UNIX),
        ctypes.c_int(socket.SOCK_STREAM), ctypes.c_int(0), ctypes.byref(descriptors),
    )
    require(result == 0, f"raw UNIX socketpair failed with errno {error}")
    os.close(descriptors[0])
    os.close(descriptors[1])

    af_vsock = getattr(socket, "AF_VSOCK", 40)
    require_raw_eperm(
        syscall_numbers["socket"], ctypes.c_int(af_vsock),
        ctypes.c_int(socket.SOCK_STREAM), ctypes.c_int(0),
    )
    forbidden_pair = (ctypes.c_int * 2)(-1, -1)
    require_raw_eperm(
        syscall_numbers["socketpair"], ctypes.c_int(af_vsock),
        ctypes.c_int(socket.SOCK_STREAM), ctypes.c_int(0), ctypes.byref(forbidden_pair),
    )

    require_raw_eperm(
        syscall_numbers["io_uring_setup"], ctypes.c_uint(0), ctypes.c_void_p(0)
    )
    require_raw_eperm(
        syscall_numbers["io_uring_enter"], ctypes.c_int(-1), ctypes.c_uint(0),
        ctypes.c_uint(0), ctypes.c_uint(0), ctypes.c_void_p(0), ctypes.c_size_t(0),
    )
    require_raw_eperm(
        syscall_numbers["io_uring_register"], ctypes.c_int(-1), ctypes.c_uint(0),
        ctypes.c_void_p(0), ctypes.c_uint(0),
    )

    if architecture == "x86_64":
        child = os.fork()
        if child == 0:
            raw_syscall(0x40000000 | syscall_numbers["getpid"])
            os._exit(125)
        waited, child_status = os.waitpid(child, 0)
        require(waited == child and os.WIFSIGNALED(child_status) and
                os.WTERMSIG(child_status) == signal.SIGSYS,
                "x32 syscall did not terminate its process with SIGSYS")


def main() -> int:
    expected_uid = int(os.environ["ALTITUDE_GATE_UID"])
    expected_gid = int(os.environ["ALTITUDE_GATE_GID"])
    require(os.getuid() == os.geteuid() == expected_uid, "UID was not dropped")
    require(os.getgid() == os.getegid() == expected_gid, "GID was not dropped")
    require(os.getgroups() == [], "supplementary groups remain")

    status = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        require(int(status[key], 16) == 0, f"{key} is not empty")
    require(status.get("NoNewPrivs") == "1", "no_new_privs is not installed")
    require(len(status.get("NSpid", "").split()) >= 2, "PID namespace is not nested")

    state_root = Path(os.environ["ALTITUDE_TEST_ROOT"]).resolve(strict=True)
    require(state_root == Path("/work/boundary-state"), "unexpected boundary state root")
    expected_state = {
        "HOME": "home", "TMPDIR": "tmp", "XDG_RUNTIME_DIR": "runtime",
        "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
        "ALTITUDE_HOME": "altitude", "ALTITUDE_JOBS_DIR": "altitude/jobs",
    }
    for variable, relative in expected_state.items():
        path = Path(os.environ[variable]).resolve(strict=True)
        require(path == state_root / relative, f"{variable} escaped its phase state")
        probe = path / ".write-probe"
        probe.write_text("yes")
        probe.unlink()
    require(os.environ.get("ALTITUDE_TEST_MODE") == "1", "test marker missing")
    require(os.environ.get("ALTITUDE_TEST_SIGNAL_MODE") == "allowed", "signal contract changed")
    require(os.environ.get("ALTITUDE_TEST_NETWORK_MODE") == "loopback", "network contract changed")

    stdin = os.fstat(0)
    require(stat.S_ISCHR(stdin.st_mode) and os.path.realpath("/proc/self/fd/0") == "/dev/null",
            "stdin is not the private /dev/null")
    require(stat.S_ISFIFO(os.fstat(1).st_mode) and stat.S_ISFIFO(os.fstat(2).st_mode),
            "stdout/stderr are not trusted logger pipes")
    require({int(fd) for fd in os.listdir("/proc/self/fd") if fd.isdigit()} <= {0, 1, 2, 3},
            "an unexpected inherited descriptor is visible")

    mount_points = set()
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        mount_points.add(fields[4].replace("\\040", " "))
    allowed = {"/", "/usr", "/proc", "/dev", "/dev/shm", "/gate"}
    require(mount_points <= allowed, f"unexpected mounts: {sorted(mount_points - allowed)}")
    for required in allowed:
        require(required in mount_points, f"missing mount: {required}")

    require(sorted(path.name for path in Path("/dev").iterdir()) ==
            ["full", "null", "random", "shm", "urandom", "zero"], "device tree is not minimal")
    require(not Path("/sys").exists(), "sysfs is exposed")
    require(not Path("/workspace").exists(), "host workspace is exposed")
    try:
        list(Path("/root").iterdir())
    except PermissionError:
        pass
    else:
        raise AssertionError("unprivileged test user can inspect /root")

    for path in (Path("/usr/.altitude-write-probe"), Path("/gate/write-probe")):
        try:
            path.write_text("no")
        except PermissionError:
            pass
        else:
            raise AssertionError(f"write unexpectedly succeeded: {path}")
    probe = Path("/tmp/write-probe")
    probe.write_text("yes")
    probe.unlink()

    with Path("/usr/bin/python3").open("rb") as system_file:
        fcntl.flock(system_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(system_file, fcntl.LOCK_UN)
        fcntl.lockf(system_file, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.lockf(system_file, fcntl.LOCK_UN)

    interfaces = []
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        interfaces.append(line.split(":", 1)[0].strip())
    require(interfaces == ["lo"], f"network namespace exposes interfaces: {interfaces}")
    require(socket.gethostname() == "altitude-test", "UTS namespace hostname changed")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())
            connection, _ = server.accept()
            connection.close()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.5)
        try:
            client.connect(("1.1.1.1", 53))
        except OSError as exc:
            require(exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT),
                    f"external connect failed unexpectedly: {exc}")
        else:
            raise AssertionError("external network route exists")

    check_raw_syscall_boundary()

    child = os.fork()
    if child == 0:
        os._exit(0)
    waited, child_status = os.waitpid(child, 0)
    require(waited == child and os.waitstatus_to_exitcode(child_status) == 0, "contained fork/reap failed")
    thread_ran = []
    thread = threading.Thread(target=lambda: thread_ran.append(True))
    thread.start()
    thread.join()
    require(thread_ran == [True], "contained native thread failed")
    command = subprocess.run(
        ["/usr/bin/python3", "-I", "-c", "print('contained-subprocess')"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    require(command.returncode == 0 and command.stdout == "contained-subprocess\n",
            "contained subprocess failed")
    print("ALTITUDE_BOUNDARY_SELFTEST ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
