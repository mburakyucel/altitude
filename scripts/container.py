#!/usr/bin/env python3
"""Build and manage Altitude's rootless, volume-only Linux container from a verified release archive."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import installation, platform

LABEL = "io.altitude.container"


def name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", value):
        raise ValueError("Choose a simple container/volume name, without a path or runtime options")
    return value


def build_command(root: Path, arguments: list[str], **kwargs) -> str:
    return platform.container_command(["--root", str(root / "store"), "--runroot", str(root / "run"),
                                       *arguments], runtime_dir=root / "runtime", **kwargs)


def cleanup_build(root: Path) -> None:
    """Retire only this build's private store and pause helper, including after SIGKILL (#543)."""
    if not root.exists():
        return
    info = root.lstat()
    if not root.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Build cleanup requires its owned private directory")
    store = json.loads(build_command(root, ["info", "--format=json"]))["store"]
    if Path(store["graphRoot"]) != root / "store" or Path(store["runRoot"]) != root / "run":
        raise RuntimeError("Build store identity changed; retain it for inspection")
    # Podman --external lists interrupted Buildah working containers too. Force
    # removal accepts those IDs; inventory is confined to the verified private store.
    containers = build_command(root, ["ps", "--all", "--external", "--quiet"]).split()
    if containers:
        build_command(root, ["rm", "--force", *containers], timeout=20)
    build_command(root, ["rmi", "--force", "--all"], timeout=20)
    if build_command(root, ["ps", "--all", "--external", "--quiet"]).strip() or build_command(root, ["images", "--quiet"]).strip():
        raise RuntimeError("Private build resources remain; retain the directory")
    build_command(root, ["system", "migrate"])
    # No more Podman calls after migration: they can create another pause helper.
    shutil.rmtree(root)


def build(archive: Path, checksum: str, tag: str, *, delegated: str | None = None,
          root: Path | None = None) -> dict:
    platform.container_runtime()
    if not re.fullmatch(r"localhost/[a-z0-9][a-z0-9/_.-]*:[a-zA-Z0-9_.-]+", tag):
        raise ValueError("Use a local image tag such as localhost/altitude:v0.1.0-rc.2")
    if delegated is None:
        import uuid
        unit = "altitude-container-build-" + uuid.uuid4().hex + ".service"
        root = Path(tempfile.mkdtemp(prefix="acb-"))
        (root / "runtime").mkdir(mode=0o700)
        (root / "runtime/bus").symlink_to(f"/run/user/{os.getuid()}/bus")
        command = [sys.executable, "-c", "import json,sys;from pathlib import Path;"
            "from scripts.container import build;print(json.dumps(build(Path(sys.argv[1]),sys.argv[2],sys.argv[3],delegated=sys.argv[4],root=Path(sys.argv[5]))))",
            str(archive.resolve()), checksum, tag, unit, str(root)]
        cleanup = [sys.executable, "-c", "import sys;from pathlib import Path;"
                   "from scripts.container import cleanup_build;cleanup_build(Path(sys.argv[1]))", str(root)]
        try:
            return json.loads(platform.container_job(unit, command, wait=True, after_stop=cleanup))
        finally:
            if root.exists():
                # If the waiting client timed out, stop the exact build service before
                # touching its store. A refused stop retains evidence; never prune shared data.
                environment = platform.container_user_environment()
                if platform.job_active(unit, environment):
                    stopped = subprocess.run(["systemctl", "--user", "stop", unit], env=environment,
                                             capture_output=True, text=True, timeout=70)
                    if stopped.returncode or platform.job_active(unit, environment):
                        raise RuntimeError(f"Build remains active; private artifacts retained at {root}")
                cleanup_build(root)
    parent = platform.container_parent(delegated)
    if root is None:
        raise ValueError("The build needs its private store")
    context = root / "context"
    app = context / "app"
    app.mkdir(parents=True)
    release = installation.extract(archive, checksum, app)
    packaging = app / "container"
    if not (packaging / "Containerfile").is_file():
        raise ValueError("This release predates container support; use a release containing container packaging")
    shutil.copyfile(packaging / "Containerfile", context / "Containerfile")
    build_command(root, ["build", "--force-rm", "--isolation=oci", "--network=slirp4netns",
                                    "--cgroup-parent", parent + "/image-build",
                                    "--memory=1g", "--cpu-quota=100000", "--tag", tag,
                                    "--label", f"{LABEL}=1", "--label", f"org.opencontainers.image.version={release['version']}",
                                    "--label", f"org.opencontainers.image.revision={release['commit']}",
                                    "--label", f"io.altitude.archive.sha256={checksum.lower()}", str(context)], timeout=480)
    image = json.loads(build_command(root, ["image", "inspect", tag]))[0]
    exported = root / "image.tar"
    build_command(root, ["save", "--format=oci-archive", "--output", str(exported), tag], timeout=60)
    platform.container_command(["load", "--input", str(exported)], timeout=60)
    imported = json.loads(platform.container_command(["image", "inspect", tag]))[0]
    if imported["Id"] != image["Id"]:
        raise RuntimeError("Loaded image differs from the completed private build")
    return imported


def local_volume(volume: str) -> None:
    name(volume)
    existing = json.loads(platform.container_command(["volume", "ls", "--format", "json"]))
    if not any(item.get("Name") == volume for item in existing):
        platform.container_command(["volume", "create", "--label", f"{LABEL}=1", volume])
    value = json.loads(platform.container_command(["volume", "inspect", volume]))[0]
    if value.get("Driver") != "local" or value.get("Options"):
        raise ValueError("Use plain local named volumes; host binds and network/shared filesystems are unsupported")
    if (value.get("Labels") or {}).get(LABEL) != "1":
        raise ValueError("Use dedicated volumes created by this launcher; an unrelated volume is not adopted")


def start(image: str, instance: str, home: str, projects: str, bind: str, public_host: str, port: int) -> str:
    platform.container_runtime()
    name(instance)
    if name(home) == name(projects):
        raise ValueError("Home and projects need separate named volumes")
    address = ipaddress.ip_address(bind)
    private = any(address in ipaddress.ip_network(network) for network in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))
    if not (address.is_loopback or private):
        raise ValueError("Publish to one loopback or private LAN/VPN address, not every interface or a public address")
    if not 1024 <= port <= 65535:
        raise ValueError("Choose an unprivileged port between 1024 and 65535")
    try:
        public_ip = ipaddress.ip_address(public_host)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}\Z)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", public_host):
            raise ValueError("Name the host/IP devices open over HTTPS, without a URL or port") from None
        public_ip = None
    if public_ip and (public_ip.is_unspecified or public_ip.is_multicast):
        raise ValueError("HTTPS needs a reachable host address")
    if address.is_loopback and not (public_host == "localhost" or public_ip and public_ip.is_loopback):
        raise ValueError("A loopback publication needs a localhost or loopback HTTPS identity")
    if not address.is_loopback and (public_host == "localhost" or public_ip and public_ip != address):
        raise ValueError("The advertised HTTPS address must match the private address published on the host")
    image_info = json.loads(platform.container_command(["image", "inspect", image]))[0]
    if (image_info.get("Labels") or {}).get(LABEL) != "1":
        raise ValueError("Select an Altitude image built from a verified release")
    local_volume(home)
    local_volume(projects)
    publish = f"[{address}]" if address.version == 6 else str(address)
    return platform.container_launch(instance, [
        "--name", instance, "--hostname", instance, "--label", f"{LABEL}=1",
        "--network=slirp4netns", "--cgroupns=private", "--security-opt=unmask=/proc/*",
        "--memory=4g", "--cpus=2", "--pids-limit=1024", "--stop-timeout=30",
        "--volume", f"{home}:/home/altitude:nocopy", "--volume", f"{projects}:/home/altitude/Projects:nocopy",
        "--publish", f"{publish}:{port}:{port}", "--env", f"ALTITUDE_PORT={port}",
        "--env", f"ALTITUDE_PUBLIC_HOST={public_host}", image_info["Id"]])


def owned(instance: str) -> dict:
    return platform.container_owned(name(instance))


def execute(instance: str, command: list[str], *, interactive: bool = False) -> str:
    value = owned(instance)
    return platform.container_command(["exec", *(["-it"] if interactive else []), "--user", "1000:1000",
                                       "--workdir", "/home/altitude",
                                       "--env", "HOME=/home/altitude", "--env", "XDG_RUNTIME_DIR=/run/user/1000",
                                       "--env", "PATH=" + platform.CONTAINER_USER_PATH,
                                       value["Id"], *command], timeout=3600 if interactive else 30, interactive=interactive)


def lifecycle(instance: str, action: str = "status", *, expected: str | None = None) -> dict:
    prefix = "import sys,json; sys.path.insert(0,'/opt/altitude'); from altitude import platform; "
    if action == "continue":
        if not expected or not re.fullmatch(r"[0-9a-f]{32}", expected):
            raise ValueError("Pass --instance with the identity from the status or browser notice you inspected")
        return json.loads(execute(instance, ["python3", "-c", prefix +
            "print(json.dumps(platform.change_container_lifecycle(sys.argv[1],sys.argv[2])))", action, expected]))
    observed = json.loads(execute(instance, ["python3", "-c", prefix + "print(json.dumps(platform.container_lifecycle()))"]))
    if action == "status":
        return observed
    if not observed or not observed.get("instance"):
        raise ValueError("Container identity is unavailable; repair its startup before continuing")
    return json.loads(execute(instance, ["python3", "-c", prefix +
        "print(json.dumps(platform.change_container_lifecycle(sys.argv[1],sys.argv[2])))", action, observed["instance"]]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    image = sub.add_parser("build", help="verify the existing application release and build a local image")
    image.add_argument("--archive", required=True, type=Path)
    image.add_argument("--sha256", required=True)
    image.add_argument("--tag", required=True)
    run = sub.add_parser("start", help="create a rootless controller with two persistent named volumes")
    run.add_argument("--image", required=True)
    run.add_argument("--name", default="altitude")
    run.add_argument("--home-volume", default="altitude-home")
    run.add_argument("--projects-volume", default="altitude-projects")
    run.add_argument("--bind", default="127.0.0.1")
    run.add_argument("--public-host", default="localhost")
    run.add_argument("--port", type=int, default=8890)
    sub.add_parser("preflight", help="check local rootless-runtime prerequisites without starting containers")
    for action in ("status", "pause", "continue", "stop", "restart", "remove", "pair", "shell", "certificate"):
        command = sub.add_parser(action)
        command.add_argument("--name", default="altitude")
        if action == "continue":
            command.add_argument("--instance", required=True, help="instance identity shown in the status you inspected")
        if action == "certificate":
            command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "build":
            print(json.dumps(build(args.archive, args.sha256, args.tag), indent=2))
        elif args.action == "preflight":
            print(json.dumps(platform.container_runtime(), indent=2))
        elif args.action == "start":
            print(start(args.image, args.name, args.home_volume, args.projects_volume, args.bind, args.public_host, args.port))
        elif args.action == "status":
            state = owned(args.name)["State"]
            print(json.dumps({"container": state, "lifecycle": lifecycle(args.name) if state["Running"] else None}, indent=2))
        elif args.action in ("pause", "continue"):
            print(json.dumps(lifecycle(args.name, args.action, expected=getattr(args, "instance", None)), indent=2))
            if args.action == "continue":
                print("Queued coordinator work and authorized task requests may now run; existing holds still apply.")
        elif args.action == "restart":
            platform.container_stop(args.name)
            print(platform.container_launch(args.name))
        elif args.action in ("stop", "remove"):
            value = owned(args.name)
            if args.action == "remove" and value["State"]["Running"]:
                raise ValueError("Stop this container before removal; both data volumes are retained")
            if args.action == "stop":
                platform.container_stop(args.name)
            else:
                print(platform.container_command(["rm", value["Id"]], timeout=60))
        elif args.action == "certificate":
            facts = execute(args.name, ["python3", "-c", "import sys,json; sys.path.insert(0,'/opt/altitude'); "
                                       "from altitude import tls; print(json.dumps(tls.info(),indent=2))"])
            certificate = execute(args.name, ["cat", "/home/altitude/.config/altitude/tls/ca.crt"])
            with args.output.open("x") as target:
                target.write(certificate)
            print(facts)
            print(f"Public CA saved to {args.output}. Compare its fingerprint before trusting it on a device.")
        else:
            print(execute(args.name, ["/bin/bash", "--noprofile", "--norc"] if args.action == "shell" else ["alt", "pair"],
                          interactive=args.action == "shell"))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Container operation refused: {exc}\n")


if __name__ == "__main__":
    main()
