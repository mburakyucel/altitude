# Linux container deployment

Container support is under validation on Linux x86_64 (also called amd64). The candidate uses
rootless Podman, cgroupfs inside a delegated host user service, and real systemd inside the image.
The tested environment is Ubuntu 24.04, kernel 6.8.0-142, Podman 4.9.3, crun 1.14.1 and cgroup v2.
The actual launcher/image lanes establish published local HTTPS, effective resource limits,
Stop/restart/replacement, worker profiles, fictional task workflows, private backup/restore and
bounded failure recovery. An independent guest service-manager monitor observes denied controls
and fails on unexpected management requests, including cleanup. See [evidence](#evidence) for the
reproducible commands and their limits.

Daemon-driven onboarding and task continuation pass at phone and desktop viewports with fictional
engines. Real provider sessions, physical-device routing/trust, Mac/Apple Silicon, Docker, other
runtime versions, host project binds and emulation are unverified.
The tested payload reports AppArmor and SELinux disabled; no payload LSM protection is claimed.
Containerized Linux does not provide native macOS, Xcode or iOS builds. The Mac stage needs its own
reviewed VM/control transport and native host evidence before a support claim.

## Boundary

Packaged Git guards live in `/opt/altitude/hooks`. Approved custom-hook compositions and their
consent receipts live in `/home/altitude/.config/altitude/git-guards`, outside project and task-state
writable roots. They use the image interpreter and source directly, without native installer state.
Task machine commands, when explicitly authorized through the existing grant workflow, execute as
the application user inside this container. They do not grant host access; no host runtime socket
or command bridge is mounted. On this project's machine, the
[standing container approval](../AGENTS.md#container-operations-on-this-machine) covers host launcher
operations, rootless Podman, launcher-created containers/images/volumes and execution inside them.
The owner requests that exact policy scope from L3, which records an operator grant citing
the standing approval without another operator question; see [operator grant](CLI.md#operator-grant).
Native Altitude installation/service, host trust/network configuration, host credential directories
and other host resources remain outside that approval. It does not apply to other projects.

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
Inside a worker's single-user namespace, image ownership can appear as the kernel's unmapped UID.
The platform seam anchors that view to the protected filesystem root and checks the marker,
instance file and every ancestor for matching ownership, type and permissions. It also rejects
effective worker write access in that namespace. The unmapped UID cannot attest the original UID;
the fixed image path and protected ancestors provide the trust boundary. Invalid existing identity
fails closed. Container workers edit with bundled `python3` through confined exec; launch and resume
instructions replace the host-only patch command without changing writable roots.

Two local named volumes hold `/home/altitude` and `/home/altitude/Projects`. No host home, credential
directory, device, runtime socket or source checkout is mounted. Clone/import projects inside the
project volume. Provider/Git credentials, machine credentials and TLS private keys remain accessible
to same-UID software where the engine's confinement permits; the image does not isolate mutually
hostile tasks or engines. Each task worker receives the container's GitHub CLI token from its
launcher on its input, as on a native host, while its user-manager sockets stay denied
([isolation](ARCHITECTURE.md#isolation-and-landing)). Existing engine differences remain. Deliberate container-root exec is
trusted host administration. Networking uses explicitly selected slirp4netns, without a destination
allowlist or a claim that containerization prevents exfiltration.

The daemon's per-project capability sockets live in `/run/user/1000/altitude-l3`, outside both
persistent volumes. They are recreated with the user-manager lifetime and are not backup data.

Root bootstrap holds kernel locks on both volumes before the application user manager starts.
Sharing either volume between active controllers refuses startup. These protect normal operations,
not malicious same-UID software. Locks cover the mounted directory inodes, so restoring entries
cannot replace the locks. Bootstrap changes only the two mount
roots' ownership and never recursively rewrites user data.

Browser terminal, task-terminal transcript reads, host speech, the optional validation runner and in-app update/restart actions are
unavailable for every peer: published-port forwarding cannot reliably identify agent connections.
Browser recognition remains available where the browser supports it. Host voice is unavailable in
the container; the image does not pass through host audio devices or a GPU.
No application command bridges to the host runtime.
Daemon startup does not queue the native “code now on main” coordinator notice: restarting the
daemon does not activate an image update or authorize a recovery turn. Saved tasks and messages
remain available.

## Build and start — host terminal

Build the existing [release archive](RELEASING.md) from reviewed source. It includes built web assets
and container packaging; image construction verifies its checksum and per-file manifest. It does
not run the native installer or publish an image. From that source checkout on the Linux host:

```sh
python3 scripts/container.py preflight
python3 scripts/container.py build --archive /path/to/altitude-release.tar.gz --sha256 '<release SHA-256>' --tag localhost/altitude:preview
python3 scripts/container.py start --image localhost/altitude:preview --new-volumes
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
LAN/VPN routing and real-device trust remain unverified; the local published HTTPS endpoint passes.

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
the persistent home. The operator shell explicitly includes `~/.local/bin` and skips login/startup
profiles, so a fresh volume needs no shell-profile edit. That user PATH is not given to container root.
Missing bundled Git/GitHub CLI is an incomplete-image error. Host tools and
authentication cannot establish container readiness. Set Git identity and authentication there,
without pasting credentials into chat. One engine suffices; the [live Linux run](#live-linux-run)
records the tested engine and authentication limits.

The image installs GitHub CLI 2.100.0 from the upstream release, with architecture-specific SHA-256
checksums verified before installation. Its JSON PR fields satisfy Altitude's landing contract;
the distribution's older CLI can authenticate but lacks a required field. The image gate checks
that capability without credentials or a GitHub request.

For phone-only sign-in, use the installed engine's device-code flow when it offers one, and
enable device-code login in the account's security settings if required. Complete the displayed
URL and short-lived code in the phone browser; the waiting CLI stays inside the container.
GitHub CLI supports this flow with `gh auth login --hostname github.com --git-protocol https --web`.
After signing in, configure Git to use that container-local login:

```sh
gh auth setup-git --hostname github.com
```

Verify `gh auth status` and, from the selected repository, `git fetch origin main` before starting
a task. A successful GitHub CLI sign-in alone does not prove Git can authenticate: the live
phone-login run left the HTTPS credential helper unset until `gh auth setup-git` configured it.
These commands run inside the container; do not mount or copy host credentials as a shortcut.

Clone/import under `/home/altitude/Projects`, then follow First run: name, prerequisites, incident
preference, voice and project selection. Registration retains repository/instructions checks, custom-hook
choices, Git guards and coordinator setup. The interface labels volume paths and refuses paths or
links escaping the project volume. Check again/Retry retain their existing journeys. The Linux
browser lane covers fresh state through a working task and replacement with fixture authentication.
The separate [live run](#live-linux-run) observes real sign-in, Check again and registration.

## Lifecycle and recovery

Application and OS dependencies belong to one image. Source-project merges do not rebuild, swap or
restart it. Native installation/update/recovery commands and Monitor Restart refuse before scheduling
work. Periodic native update checks/notices are disabled; stale restart receipts cannot hold admission.

The root bootstrap records an instance identity in the container layer before starting the user
manager. It admits genuinely empty home **and** project volumes once, as the application user.
The persistent receipt lives beside private configuration, outside task-writable state/projects.
Same-container and daemon restarts keep the previous admitted/paused state. A replacement or
missing/corrupt receipt pauses new AI work until deliberate host continuation. Restore a home
backup into a **new container**: an admitted receipt restored into its original container still
matches that instance and can release work automatically. Same-container restore is unsupported.
Neither chat messages nor task Continue bypass that global pause; their requests remain queued.
Stop, worker observation, claim reconciliation and report processing remain available. An ambiguous
interrupted launch still requires its existing recovery decision; global Continue clears no task hold.

From the source checkout on the **host**, select the container you operate:

```sh
python3 scripts/container.py status --name altitude
python3 scripts/container.py pause --name altitude
python3 scripts/container.py continue --name altitude --instance INSTANCE_FROM_STATUS
```

Replace `INSTANCE_FROM_STATUS` with the instance shown by the status you inspected, or copy the
complete command from the browser notice. Continue proves local HTTPS health against the service's
CA and PID, then checks that inspected instance before releasing
queued coordinator turns, reports and authorized task requests. The browser shows this host action
while paused and removes the notice after a fresh admitted status. It offers no lifecycle mutation
route. Task/coordinator credentials refuse the host action; same-UID trust limits remain unchanged.
Policy-confined workers running as the application UID can rewrite the receipt directly. These
controls manage Altitude launch admission and are not a security boundary against those workers.
The OS-sandboxed engine's receipt-directory denial passed for both generated roles in the native
profile lane; the ordinary application-user control could write there. This establishes the tested
native profile boundary, not real provider-session/configuration stacking or other-engine parity.

Pause rejects new admissions atomically. Calls admitted earlier can finish, including a launch
already preparing its claim; status reports admitted calls still active. Detached workers can remain
after those calls return. Neither a quiet status nor a pause proves that a backup is consistent:
stop the controller before copying both volumes. Linux admission/lifecycle and planned application
backup/recovery checks pass on the recorded image; Mac validation remains pending.

The reusable image gate's `--lifecycle` lane checks daemon and same-container restart, independent
fixture-job survival and descendant cleanup, then replacement with retained project data and TLS
identity. It checks paused provider refusal and rejects Continue for the previous instance before
accepting the replacement. The lane passed on the recorded image, including independent cleanup
inspection. It does not establish full application task recovery, backup restore or a version upgrade.

The host launcher owns one transient delegated user service per running instance. Its foreground
supervisor translates service termination into the image's graceful stop signal. After the bounded
grace, the user manager removes remaining descendants in that exact unit. Its stop-post action also
stops and cleans the exact owned runtime record when the supervisor dies unexpectedly; a unit's
inactive state alone is not accepted as container cleanup. The service outlives the
launching terminal but follows the existing user-manager/login lifetime. The launcher does not enable
lingering, host-boot startup or automatic restart. Keep its source location available while it runs.
The `--logout` VM lane verifies actual user-manager exit, graceful inner-worker shutdown and a new
login followed by an explicit same-container start. It preserves instance identity/admission and
cleans up without unexpected system-manager requests. This passes on candidate `f280737`; it is
not host-boot autostart or Mac sleep/wake evidence.
Startup verifies the running container identity, effective resource limits and internal HTTPS health.
It also connects to the published host address with the advertised TLS name, trusting only that
instance's public CA, and checks that the health response names the recorded service process.
This installs no certificate trust and does not prove another device's routing or trust.
A failed start reports the unit log and retains its stopped container for diagnosis.
Image user jobs and the daemon have a 15-second stop grace, their user manager has 20 seconds,
and Podman has 30 seconds before forced termination. The host supervisor has a 45-second stop
budget followed by a stop-post cleanup phase. The client allows 105 seconds for both phases and reports
unconfirmed cleanup with the unit log location if that wait expires. Supervisor death and startup
interruption can force termination; only an ordinary completed Stop is described as graceful.
`Running=false` alone is insufficient: a lingering `stopping` record or payload PID refuses Stop
completion, backup and another copy's admission. Unsettled runtime evidence is retained for recovery.
After forced supervisor death, Podman can retain this stale state even though the unit killed its
processes. `python3 scripts/container.py recreate --name altitude` verifies the owned user service is
inactive and its kernel cgroup is empty, then replaces only that container record/layer with the
same image and volume pair. The new instance is paused until explicit Continue. It refuses surviving
processes or another active copy. If startup fails after removal, both volumes remain available to
the ordinary `start --image … --home-volume … --projects-volume …` command without `--new-volumes`.
These bounds do not promise graceful completion for a job that ignores termination.

`python3 scripts/container.py restart --name altitude` stops and restarts the same container under
its stable user-unit/cgroup identity. It preserves the instance's admission state. A replacement
creates a new container and still requires the explicit host-side Continue. Use these launcher
operations rather than direct Podman start/stop; a container without its expected supervisor is not
reported healthy or stopped merely because a command returned.

Use task Stop for owned task work before stopping the whole container. Host `stop` stops the selected
container; `remove` refuses a running one and retains both volumes. A new controller can use those
volumes after the old controller stops. Registrations, settings, sessions and holds persist there;
recreation cannot preserve processes. Automatic replay is not an update/recovery strategy. Native
primitive Stop/descendant and recreation checks pass. The deterministic task workflow also passes
Stop/resume across planned replacement; destruction during a running task or launch remains unverified.

Image replacement requires quiescent work and a private, consistent backup of **both** volumes.
An older image does not reverse schema/state changes: use a compatible image or matching backup.
The combined phone/desktop lane passes registered-project backup, replacement by a distinct image
version and restoration of the matching image/data backup on Ubuntu amd64. The two fixture release
versions use the same application schema: this proves image replacement and matching-backup recovery,
not arbitrary schema migrations or future-release compatibility. Mac and final delivery acceptance
remain open. Do not migrate an existing native installation with these commands.

### Private backup and restore

Run these on the **host**, after stopping the selected container. The output directory must not
exist. Keep it private: it contains unencrypted credentials, TLS keys, executable configuration,
projects and the exact application image. Use only your own trusted backups. Checksums detect
damage and truncation; they do not establish authenticity. If it is exposed, rotate credentials
and re-pair devices. No backup is uploaded or sent to an external service.

```sh
python3 scripts/container.py stop --name altitude
python3 scripts/container.py backup --name altitude --output /private/path/altitude-backup
python3 scripts/container.py restore --backup /private/path/altitude-backup \
  --name altitude-restored --home-volume altitude-restored-home \
  --projects-volume altitude-restored-projects
```

Restore uses two new volumes and the recorded image, starts a new container identity, and leaves
work paused until the displayed host Continue command is run. The original volumes remain intact.
Within the launcher's current account and store, either start order refuses another running copy
from the same backup lineage. It also refuses any other running container mounting either volume,
including an unlabeled container. This coordinates supported launcher operations; manual bypass,
another host or another runtime store is outside that guarantee. Experimental unlabeled volumes
are not adopted. Existing/restored starts omit `--new-volumes`; missing names fail rather than
creating empty replacements.

Backup holds both mounted directory locks and requires all known controllers stopped. The image
stops its user manager if the lock owner dies. A dedicated helper has no network, no host binds,
a read-only image, default seccomp, no new privileges, and only CHOWN/DAC_OVERRIDE/FOWNER within its
rootless mapping. It has one CPU, 1 GiB and 64 PIDs. Binary payload descriptors bypass text capture,
container logging and the user journal. A finite delegated transfer service owns the helper even
if the waiting terminal exits; its exact cleanup removes incomplete payloads or new restore volumes.

The two volumes use separate archive roots. Hidden content under the home volume's Projects
mountpoint is refused. Ordinary files, directory modes, namespace UID/GID 0 or 1000, timestamps,
symlinks, same-volume hardlinks, user attributes and POSIX ACLs are preserved. Devices, sockets,
FIFOs, set-ID modes and other security attributes are refused. The archive has an in-band final
content digest/count record; the host manifest also hashes both complete files. Restore verifies
both before writing pair-bound completion markers. It never follows restored links while writing
files or executes restored startup hooks. Incomplete/mismatched pairs cannot start.

Data transfers are bounded to 64 GiB, the image archive to 8 GiB, and archive entries to one million.
The service has a 30-minute deadline, with a 25-minute helper limit and separate bounded cleanup.
These are ceilings, not a promise that every archive of that size fits the deadline: source reading,
streaming, integrity passes and image transfer share the time budget. Slow storage or large metadata
sets can therefore fail below the byte limit; the source is retained and completion is refused.
The OCI image is exported by immutable identity; mutable tags are refused before import. File and
filesystem limits can still make an operation fail; an unfinished directory is not a completed
backup. The launcher reports retained artifacts if cleanup cannot be confirmed.
Transfer records live in the host account's private
`$XDG_DATA_HOME/altitude-container/transfers` (normally `~/.local/share/altitude-container/transfers`),
outside `/tmp`. Runtime preflight and start/backup/restore report records for their selected store,
including after a reboot skips service cleanup. Inspect them with
`python3 scripts/container.py transfers`. Once the recorded service is inactive, clean one exact
operation with `python3 scripts/container.py recover-transfer --id <recorded-id>`; active operations
and changed helper/volume ownership refuse recovery. Completed backups and original data volumes
remain intact; only the operation's incomplete payloads or new restore volumes are removed.
Keep this private state directory with the runtime store until all operations are resolved.
Policy refusals name their category without logging private file names: unsupported ownership,
set-ID modes, special files, extended attributes, ACL identifiers or volume layout. Namespace file
owners and named POSIX ACL users/groups must use IDs 0 or 1000. Paths have at most 128 components.
Sockets left by Git fsmonitor or SSH ControlMaster must be removed only after their owner stops;
setgid shared repositories and `security.selinux` labels are unsupported. Backup on an SELinux-labeled
volume is therefore unavailable with this image; the tested Ubuntu tuple has no SELinux enforcement.

Resume claims identify their owner by PID, process start time, boot identity and PID namespace through
the platform seam. A numerically reused PID cannot keep an earlier claim live. Missing identity enters
existing claim reconciliation. Boot/start mismatches are checked before the protected namespace link;
inaccessible identity evidence for a matching lifetime does not establish that the owner died.
This removes the bare-PID liveness check for native and container resumes. The native macOS branch
uses the read-only kernel boot-session UUID and libproc owner start time, with a single native PID
namespace. Deterministic fixtures cover that integration; actual Mac acceptance remains pending
with the native runtime owner.

## Evidence

### Live Linux run

The operator-authorized run on 2026-10-06/07 uses one private throwaway repository and one owner
task, initially with GitHub CLI 2.46.0 and Codex CLI 0.160.1 inside the container. The real coordinator and
owner use the configured engine; the observed model is `gpt-6.1-sol`. This is one manual acceptance
run, not an unattended suite or release gate. The second engine and macOS are untested; Mac
confirmation remains tracked in [#643](https://github.com/mburakyucel/altitude/issues/643).

| Image | Source commit | Local release | Image ID |
| --- | --- | --- | --- |
| Fresh installation | `79896693f4ffc31371f793557ebbd0c4a15bc682` | `v0.1.0-rc.2026100601` | `bbc2d7d9f6a28c5521543a36de42c95e7eb5bff3eef2844b72bd73a0accb74ff` |
| Repaired replacement | `1235573c80028135a50c9ba29a6b228075912cc2` | `v0.1.0-rc.2026100702` | `8467b7cf01662984d3213078f111901527e23685e25310fcc91ff6a2c67492f1` |

The host is Ubuntu 24.04 amd64 with Podman 4.9.3 and crun 1.14.1. The launcher retains its normal
limits and exactly two named volumes; no host credentials or home directories are mounted.

The observed sequence establishes:

- Fresh tools require sign-in inside the container. Phone device flows authenticate both tools;
  the engine account requires device-code sign-in enabled in its security settings. Short-lived
  codes expire while waiting for the operator. No password or token passes through chat.
- Authentication resides in the persistent home, including the GitHub CLI configuration and
  engine credential file. Both sign-ins, registered project and original task/session/hold survive
  the observed host reboot followed by launcher restart and the later image replacement.
- Authenticated onboarding passes at 390×844 and 1440×900: installed tools and sign-in observations,
  Check again, private incident reports, unavailable host voice, project-volume selection,
  repository registration, Git guards and the coordinator's first conversation.
- GitHub CLI sign-in alone leaves Git's HTTPS helper unset in this phone flow. Configuring it
  with `gh auth setup-git` makes the real private-repository fetch succeed.
- A graceful launcher Stop precedes a complete private backup of both volumes and the old image.
  The repaired image starts with the same volumes, retains authentication and saved work, and
  pauses new AI work. Host Continue succeeds for the inspected replacement instance.

The live owner exposes a namespace identity/editing defect repaired in
[PR #664](https://github.com/mburakyucel/altitude/pull/664). The installed repair passes normal
confined task status and bundled-Python editing while protected writes and native operations remain
denied. The original provider session resumes and creates its requested document without another
login and creates the explicitly authorized test issue. The bundled GitHub CLI then rejects
`baseRefOid` during normal PR delivery; the image's pinned CLI and capability check address this
separate packaging defect. PR delivery and planned same-container restart/stop-start checks remain
in progress.

Private task evidence retains image/archive identities, command results and named phone/desktop
screenshots. Account identifiers, credential material and the scratch repository are excluded from
public evidence. Chromium keeps its sandbox; a temporary harness forwards exact local HTTPS
responses to the browser without substituting application, tool or provider responses. This is
viewport evidence, not direct runner networking or physical-phone certificate trust. Backup restore,
large-volume throughput and provider-session confinement parity across engines remain unverified.

### Reusable fixture lanes

`make container-vm RESULTS=<new-directory>` builds the committed candidate and exercises its actual
host launcher and image in a disposable Ubuntu 24.04 amd64 KVM VM. Inside an owner task, the target
uses `alt task validate --kvm` and returns results to the task. It checks image creation, startup,
graceful Stop, same-container identity/admission, replacement pause, the displayed Continue command,
supervisor death, an unaffected neighboring container and private-store/VM cleanup. The guest gets
no host credentials or repositories beyond the committed candidate, and downloads public distribution
and image prerequisites. This is Linux fixture evidence, not Mac or provider acceptance.
`scripts/container_vm.py RESULTS --image-workflow` runs the separate image-level task/coordinator,
Stop/recreation and state-recovery fixtures in that VM. Add `--native-sandbox-binary PATH` for the
existing actual-profile diagnostic matrix. That executable is copied only into the disposable
guest/image; the lane makes no authenticated provider calls. It complements the actual launcher lane.

The VM observes system-manager calls independently of the launcher's environment, before their result
is known. Beginning and ending fictional denied Stop controls prove coverage. Unexpected management
calls fail, regardless of the unit's name. The permitted mutating calls are root logind's fictional
login-session creation/abandonment and the verified systemd user manager attaching its own processes within its
delegated unit; the latter has no polkit authorization path in the tested systemd version. Sender
identity failures do not permit these exceptions. Raw calls and guest-only sender metadata remain
in the result directory.
Podman can move an ordinary frontend process as well as its pause helper into a user-owned scope;
the monitor records attached PIDs and available executable/cgroup evidence. Application readiness
separately requires both PID1 and conmon to remain in the deployment's delegated subtree.

The proc exposure gate records bounded metadata for the listed masked interfaces and their
subdirectories for container root and the application user. Every baseline proc mount must have an
inventory row. It never reads kernel-interface contents or writes tunables. Writable predicates
must stay within the approved v5 list. For readable descendants whose names differ across kernels
or network interfaces, a second small container with default proc protections must show the same
readable file type; this comparison never treats a top-level masked
device placeholder as a readable kernel file. Extra access fails the gate and retains both records
for review. Access predicates are not successful opens/writes or proof of kernel isolation.
The reviewed Ubuntu tuple additionally permits these exact readable predicates for both UIDs:
`/proc/acpi/wakeup`, `/proc/scsi/device_info`, `/proc/scsi/scsi`, `/proc/scsi/sg`, and
`/proc/scsi/sg/{allow_dio,debug,def_reserved_size,device_hdr,device_strs,devices,version}`.
The expanded inventory measured these descendants without reading their contents; none adds a
writable predicate. The acceptance policy rejects another kernel/runtime/network/architecture
tuple rather than inheriting this list. Mac and other tuples require full remeasurement and review.

`scripts/container_acceptance.py --archive … --sha256 … --results <new-directory>` is the finite
image-bootstrap gate. It uses isolated rootless storage, fresh volumes, the reviewed `slirp4netns` network,
resource/time limits, retained results and cleanup. It checks startup, local HTTPS, immutable-image
API state and elevation-file inventory without changing host installation, services, policy or
provider accounts. An owner needs the applicable runtime-access grant.
The launcher requires the runtime directory's bus to resolve to the local account's owned user-bus
socket. This lets Podman place its pause helper in a separate user scope, independent of a build,
application service or terminal. The system-bus address points beneath a non-directory, and every
runtime command selects cgroupfs and ordinary distribution crun. Persisted container ownership,
manager and parent checks refuse adoption of containers created with another lifecycle contract.
No OCI adapter, rejecting socket or asynchronous denial ledger is installed. The disposable VM
acceptance monitors system-manager methods independently of child environments; the fictional
positive control proves that its detector is active. This is not protection against arbitrary
same-account software that ignores the launcher, nor a change to host polkit or service policy.
The image gate retires only its verified empty private store's pause helper with Podman's own
operation. It never migrates a shared store or guesses a helper's executable/PID.
Image construction uses a separate private build store and imports only the completed image into
the operator's store, verifying its identity. The finite build service's stop cleanup removes its
own working containers, image layers and pause helper, including after an interrupted build.
It never prunes shared storage. Refused cleanup retains the private directory for diagnosis.
An explicit minimal storage configuration excludes inherited additional image stores. The build's
15-minute service budget covers construction, image export and import; cleanup has a separate
40-second command budget within the stop-post allowance.
The gate checks the running daemon's environment and owned listening socket using a non-default
internal port and fictional advertised certificate name. The recorded run verifies `0.0.0.0:19443`
and the requested certificate SAN; it does not test host port publication or client-device access.

An explicit `--native-sandbox-binary /path/to/diagnostic-executable` additionally copies an ordinary
installed native diagnostic into the disposable image and exercises the application-generated task
and coordinator profiles. It retains binary/probe hashes, positive controls, allowed/denied writes,
manager/broker/loopback socket checks and a fictional Git commit. It also checks the normal confined
`alt task status` command, image-instance recognition, a Python workspace edit, denied image/ancestor
writes and native-operation refusal. The task probe requires the actual unmapped-owner namespace;
a literal-root view cannot satisfy that regression check. This proves the bundled interpreter's
editing operation inside the native sandbox, not a provider session following the instruction.
The source deployment is already root-owned and denies the unsandboxed write control.
The earlier permission-matrix lane (before these identity/status/edit checks) passed with diagnostic
CLI 0.158.0 and bubblewrap 0.12.0. Task writes were limited to worktree/Git/state/temp; coordinator
writes were limited to scratch. Both denied user-manager sockets and Git-consent writes; the task
retained broker/loopback access and the coordinator denied direct connections. Both sandboxed roles
reported zero effective capabilities, NoNewPrivs=1 and Seccomp=2. This makes no provider-session,
authentication or other-engine confinement claim.

The Ubuntu 24.04 launcher lane passes private backup/restore, truncated transfer, byte-limit refusal,
killed transfer worker/client cleanup, interrupted-restore volume cleanup and recovery after the
source image is removed. A conflicting mutable image tag stays unchanged. The restored private
file and TLS keys retain ownership, modes, timestamps and user/POSIX ACL metadata. Both copy-start
orders refuse for the exact lineage-admission reason; restored identity remains paused. Actual
helper log commands and store paths contain no archive output, and the user journal contains no
fictional secret sentinel. The byte limit applies to the binary output stream, not Podman's own
database writes. The lane also omits a fixture cleanup callback, kills the transfer frontend/worker,
then proves that the next preflight reports the retained private transfer and exact recovery removes
its partial artifacts. This covers a skipped callback, not a physical host reboot during transfer.
These are fictional data and small-archive checks, not real-account or maximum-size throughput acceptance.

`scripts/container_vm.py <new-results> --browser` runs the actual image daemon with deterministic
external engine/tool observations through its published HTTPS endpoint. Chromium runs in the
validation container with its own sandbox; a disposable SSH forward connects to the guest and a
temporary browser trust database contains only the fictional public CA. It passes on candidate
`222becf` at 390×844 and 1440×900: pairing, operator identity, missing tools/sign-in observations,
Check again, interrupted first run, private incidents, unavailable host voice, volume selection and
outside-volume refusal, repository/instructions/guards, coordinator failure/Retry, task creation
and Stop, same-container restart, paused replacement and exactly-once queued continuation, custom
hook keep/combine decisions, conversation-only folders and persisted Settings. Application routes,
storage, Git and scheduling stay real; only external engine/tool observations are fictional.
After those journeys, each viewport's registered projects and task survive private backup,
replacement by a distinct fixture release image, and restoration of the matching older image/data.
The restore retains operator settings, task session and hold, starts paused, and excludes a marker
created after the backup. Both versions share one schema; the lane makes no migration claim.
Named screenshots and failures are retained by the runner. No application response is intercepted.
It neither changes host trust nor establishes physical phone or Mac access. The separate emulated
iPhone container walkthrough passes in desktop WebKit; this is not physical iOS acceptance.

Results explicitly name uncovered provider-session confinement parity, real authentication,
real-device HTTPS routing/trust, maximum-size backup throughput and Mac.
Workspace tests cover route refusals, native regressions, lock contention and fictional phone/desktop
states; they are not container/Mac acceptance. Required PR checks remain required, and optional hosted
installation checks remain non-blocking.
