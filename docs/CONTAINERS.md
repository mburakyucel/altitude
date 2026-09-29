# Linux container deployment

Container support is under validation. The candidate uses rootless Podman with real systemd inside
the image. The feasibility tuple is Ubuntu 24.04 x86_64, Podman 4.9.3, crun 1.14.1 and cgroup v2 with
its systemd manager. Full application, onboarding and recovery acceptance is not established yet.
Mac/Apple Silicon, Docker, other runtime versions, host project binds and emulation remain unverified.

## Boundary

This deployment requires **container-wide `unmask=/proc/*`**. It permits nested native worker
confinement but removes Podman's default masked/read-only proc mounts for every container process,
including root. Default capabilities, seccomp, `/sys` protections and private namespaces remain.
This is not a claim of stronger isolation than a default container. Kernel exposure and runtime
behavior require evidence for each supported environment.

Container root maps to the ordinary host account running rootless Podman. PID 1 is real systemd;
fixed UID 1000 runs Altitude and its independent worker units. Image construction removes setuid/
setgid file bits and file capabilities and includes no sudo. Application code is root-owned under
`/opt/altitude`. A root-owned marker outside persistent volumes selects image behavior; environment
variables and forwarded headers cannot select native authority paths.

Two local named volumes hold `/home/altitude` and `/home/altitude/Projects`. No host home, credential
directory, device, runtime socket or source checkout is mounted. Clone/import projects inside the
project volume. Provider/Git credentials, machine credentials and TLS private keys remain accessible
to same-UID software where the engine's confinement permits; the image does not isolate mutually
hostile tasks or engines. Existing engine differences remain. Deliberate container-root exec is
trusted host administration. Networking uses explicitly selected slirp4netns, without a destination
allowlist or a claim that containerization prevents exfiltration.

Root bootstrap holds kernel locks on both volumes before the application user manager starts.
Sharing either volume between active controllers refuses startup. These protect normal operations,
not malicious same-UID software that can remove lock files. Bootstrap changes only the two mount
roots' ownership and never recursively rewrites user data.

Browser terminal, task-terminal transcript reads, host speech and in-app update/restart actions are
unavailable for every peer: published-port forwarding cannot reliably identify agent connections.
Browser speech and an explicitly configured external speech service retain their existing behavior.
No application command bridges to the host runtime.

## Build and start — host terminal

Build the existing [release archive](RELEASING.md) from reviewed source. It includes built web assets
and container packaging; image construction verifies its checksum and per-file manifest. It does
not run the native installer or publish an image. From that source checkout on the Linux host:

```sh
python3 scripts/container.py preflight
python3 scripts/container.py build --archive /path/to/altitude-release.tar.gz --sha256 '<release SHA-256>' --tag localhost/altitude:preview
python3 scripts/container.py start --image localhost/altitude:preview
python3 scripts/container.py status
```

The default name is `altitude`, with volumes `altitude-home` and `altitude-projects`. The launcher
creates and labels dedicated volumes; it refuses to adopt another application's volume. It
publishes port 8890 on host loopback and advertises `https://localhost:8890`. A conflicting port
fails; select an available `--port` explicitly. Host and container ports match. The launcher limits
the container to 4 GiB memory, two CPUs and 1024 PIDs. Failed prerequisites require correction,
never a privileged/unconfined fallback.

Private LAN/VPN access uses a host-side `--bind` address and matching `--public-host` DNS name/IP.
The container binds its own interface while certificates and pairing links name the advertised
address. This configures no firewall, public internet service, reverse proxy or tunnel. Published
networking remains unverified in this candidate.

## First use

In the **host terminal**, export only the public CA, obtain a pairing code and open the user shell:

```sh
python3 scripts/container.py certificate --output altitude-ca.crt
python3 scripts/container.py pair
python3 scripts/container.py shell
```

Add `--name` for a different container. Compare the certificate fingerprint and follow the
[device trust guide](SETUP.md#trust-https-on-each-device); never copy private keys. Export refuses an
existing output. Container `alt tls-share` explains this path instead of opening another HTTP port.

Run sign-in, engine installation and Git commands **inside the container shell**. First run and
Settings show inside-container checks and the host shell command. Missing engine tools install in
the persistent home; missing bundled Git/GitHub CLI is an incomplete-image error. Host tools and
authentication cannot establish container readiness. Set Git identity and authentication there,
without pasting credentials into chat. One engine suffices; live provider/auth compatibility is unverified.

Clone/import under `/home/altitude/Projects`, then follow First run: name, prerequisites, incident
preference and project selection. Registration retains repository/instructions checks, custom-hook
choices, Git guards and coordinator setup. The interface labels volume paths and refuses paths or
links escaping the project volume. Check again/Retry retain their existing journeys; complete
first-use and recreation acceptance remains pending.

## Lifecycle and recovery

Application and OS dependencies belong to one image. Source-project merges do not rebuild, swap or
restart it. Native installation/update/recovery commands and Monitor Restart refuse before scheduling
work. Periodic native update checks/notices are disabled; stale restart receipts cannot hold admission.

Use task Stop for owned task work before stopping the whole container. Host `stop` stops the selected
container; `remove` refuses a running one and retains both volumes. A new controller can use those
volumes after the old controller stops. Registrations, settings, sessions and holds persist there;
recreation cannot preserve processes. Automatic replay is not an update/recovery strategy. Native
Stop/descendant and recreation acceptance for the final image is pending.

Image replacement requires quiescent work and a private, consistent backup of **both** volumes.
An older image does not reverse schema/state changes: use a compatible image or matching backup.
The automated backup/replacement/recovery path is unfinished, not an established operator procedure.
Do not migrate an existing native installation with these commands.

## Evidence

`scripts/container_acceptance.py --archive … --sha256 … --results <new-directory>` is the finite
image-bootstrap gate. It uses isolated rootless storage, fresh volumes, network-none payloads,
resource/time limits, retained results and cleanup. It checks startup, local HTTPS, immutable-image
API state and elevation-file inventory without changing host installation, services, policy or
provider accounts. An owner needs the applicable runtime-access grant.

Results explicitly name uncovered final-image engine confinement, full task workflow, Stop/restart/
recreation, concurrent controller locks, updates/backups, published HTTPS/browser onboarding and Mac.
Workspace tests cover route refusals, native regressions, lock contention and fictional phone/desktop
states; they are not container/Mac acceptance. Required PR checks remain required, and optional hosted
installation checks remain non-blocking.
