# Linux container deployment

Container support is under validation. The candidate uses rootless Podman with real systemd inside
the image. The launcher selects cgroupfs inside a delegated host user service. The earlier image
feasibility tuple used Ubuntu 24.04 x86_64, Podman 4.9.3, crun 1.14.1 and cgroup v2 with
its systemd manager. Image bootstrap, local HTTPS and elevation-file inventory passed on commit
`1639f3102b9e07215d49b1f45dc4a1fc396071f7`, archive SHA-256
`b014ddf7897ad177a60365e72c89a6cdc745e1c9b5c8c577d01cdb2ee7f5dfcc`.
That same image passed the native diagnostic task/coordinator permission matrix and actual daemon
environment/listener/certificate checks. Its admission lane also passed daemon and same-container
restart, independent fixture-job survival/descendant Stop, and replacement with retained fixture
project/certificate and explicit Continue. This evidence applies to that exact source and Linux tuple.
The `--workflow` lane on harness `499038e` passed against the same archive: registered project,
coordinator connection/resumption, real systemd task jobs, Stop, saved draft/session/merge hold,
queued steering and deliberate continuation after replacement. Its synchronous driver owns separate
fictional state; this does not establish background daemon scheduling or full onboarding.
Harness `99e5b7e` also passed claim-recovery fault injection on that archive after actual container
replacement: an exited owner's prelaunch claim restores input before continuation, while a launch
without confirmed worker identity faults without replay. This is not evidence of a real provider crash.
Full application, onboarding and recovery acceptance is not established yet. The tested rootless
payload reports AppArmor and SELinux disabled; no payload LSM protection is claimed.
Mac/Apple Silicon, Docker, other runtime versions, host project binds and emulation remain unverified.
Host authorization acceptance remains open for the complete application. The confirmed crun cleanup
fallback to the host system manager is removed by explicit cgroupfs selection and distribution crun.
A disposable Ubuntu 24.04 VM (kernel 6.8.0-142, Podman 4.9.3, crun 1.14.1) establishes nested real
systemd, effective outer and inner service quotas, graceful Stop/restart, forced cleanup and private
store retirement. Its system-manager monitor observes positive StopUnit controls before and after
runtime work and no libpod manager calls. These fixture results do not establish the new launcher's
complete lifecycle, final Altitude image, published HTTPS, provider sessions or Mac acceptance.

## Boundary

Packaged Git guards live in `/opt/altitude/hooks`. Approved custom-hook compositions and their
consent receipts live in `/home/altitude/.config/altitude/git-guards`, outside project and task-state
writable roots. They use the image interpreter and source directly, without native installer state.
Task machine commands, when explicitly authorized through the existing grant workflow, execute as
the application user inside this container. They do not grant host access; no host runtime socket
or command bridge is mounted. Host administration remains a separate operator action.

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
the persistent home. The operator shell explicitly includes `~/.local/bin` and skips login/startup
profiles, so a fresh volume needs no shell-profile edit. That user PATH is not given to container root.
Missing bundled Git/GitHub CLI is an incomplete-image error. Host tools and
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
The OS-sandboxed engine's receipt-directory denial passed for both generated roles on the exact
image above; the ordinary application-user control could write there. This establishes the tested
native profile boundary, not real provider-session/configuration stacking or other-engine parity.

Pause rejects new admissions atomically. Calls admitted earlier can finish, including a launch
already preparing its claim; status reports admitted calls still active. Detached workers can remain
after those calls return. Neither a quiet status nor a pause proves that a backup is consistent:
stop the controller before copying both volumes. Linux primitive admission/lifecycle checks pass
on the recorded image; full application recovery and Mac validation remain pending.

The reusable image gate's `--lifecycle` lane checks daemon and same-container restart, independent
fixture-job survival and descendant cleanup, then replacement with retained project data and TLS
identity. It checks paused provider refusal and rejects Continue for the previous instance before
accepting the replacement. The lane passed on the recorded image, including independent cleanup
inspection. It does not establish full application task recovery, backup restore or a version upgrade.

The host launcher owns one transient delegated user service per running instance. Its foreground
supervisor translates service termination into the image's graceful stop signal; after the bounded
grace, the user manager removes remaining descendants in that exact unit. Its stop-post action also
stops and cleans the exact owned runtime record when the supervisor dies unexpectedly; a unit's
inactive state alone is not accepted as container cleanup. The service outlives the
launching terminal but follows the existing user-manager/login lifetime. The launcher does not enable
lingering, host-boot startup or automatic restart. Keep its source location available while it runs.
Startup verifies the running container identity, effective resource limits and internal HTTPS health.
It also connects to the published host address with the advertised TLS name, trusting only that
instance's public CA, and checks that the health response names the recorded service process.
This installs no certificate trust and does not prove another device's routing or trust.
A failed start reports the unit log and retains its stopped container for diagnosis.
Image user jobs and the daemon have a 15-second stop grace, their user manager has 20 seconds,
and Podman has 30 seconds before forced termination. The host supervisor has a 45-second stop
budget. These bounds do not promise graceful completion for a job that ignores termination.

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
The automated backup/replacement/recovery path is unfinished, not an established operator procedure.
Do not migrate an existing native installation with these commands.

Resume claims identify their owner by PID, process start time, boot identity and PID namespace through
the platform seam. A numerically reused PID cannot keep an earlier claim live. Missing identity enters
existing claim reconciliation. Boot/start mismatches are checked before the protected namespace link;
inaccessible identity evidence for a matching lifetime does not establish that the owner died.
This removes the bare-PID liveness check for native and container resumes. The native macOS branch
uses the read-only kernel boot-session UUID and libproc owner start time, with a single native PID
namespace. Deterministic fixtures cover that integration; actual Mac acceptance remains pending
with the native runtime owner.

## Evidence

`make container-vm RESULTS=<new-directory>` builds the committed candidate and exercises its actual
host launcher and image in a disposable Ubuntu 24.04 amd64 KVM VM. Inside an owner task, the target
uses `alt task validate --kvm` and returns results to the task. It checks image creation, startup,
graceful Stop, same-container identity/admission, replacement pause, the displayed Continue command,
supervisor death, an unaffected neighboring container and private-store/VM cleanup. The guest gets
no host credentials or repositories beyond the committed candidate, and downloads public distribution
and image prerequisites. This is Linux fixture evidence, not Mac or provider acceptance.

The VM observes system-manager calls independently of the launcher's environment, before their result
is known. Beginning and ending fictional denied Stop controls prove coverage. Unexpected management
calls fail, regardless of the unit's name. The permitted mutating calls are root logind's fictional
login-session creation/abandonment and the verified systemd user manager attaching its own processes within its
delegated unit; the latter has no polkit authorization path in the tested systemd version. Sender
identity failures do not permit these exceptions. Raw calls and guest-only sender metadata remain
in the result directory.

`scripts/container_acceptance.py --archive … --sha256 … --results <new-directory>` is the finite
image-bootstrap gate. It uses isolated rootless storage, fresh volumes, network-none payloads,
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
The gate checks the running daemon's environment and owned listening socket using a non-default
internal port and fictional advertised certificate name. The recorded run verifies `0.0.0.0:19443`
and the requested certificate SAN; it does not test host port publication or client-device access.

An explicit `--native-sandbox-binary /path/to/diagnostic-executable` additionally copies an ordinary
installed native diagnostic into the disposable image and exercises the application-generated task
and coordinator profiles. It retains binary/probe hashes, positive controls, allowed/denied writes,
manager/broker/loopback socket checks and a fictional Git commit. The source deployment is already
root-owned and denies the unsandboxed write control. The stripped-image lane passed with diagnostic
CLI 0.158.0 and bubblewrap 0.12.0. Task writes were limited to worktree/Git/state/temp; coordinator
writes were limited to scratch. Both denied user-manager sockets and Git-consent writes; the task
retained broker/loopback access and the coordinator denied direct connections. Both sandboxed roles
reported zero effective capabilities, NoNewPrivs=1 and Seccomp=2. This makes no provider-session,
authentication or other-engine confinement claim.

Results explicitly name uncovered provider-session confinement parity, full task workflow, Stop/restart/
recreation, concurrent controller locks, updates/backups, published HTTPS/browser onboarding and Mac.
Workspace tests cover route refusals, native regressions, lock contention and fictional phone/desktop
states; they are not container/Mac acceptance. Required PR checks remain required, and optional hosted
installation checks remain non-blocking.
