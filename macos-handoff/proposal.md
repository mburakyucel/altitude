# macOS native runtime — proposal r5

Goal: the same guarantees as Linux, on macOS 15 and 26 on Apple silicon. "Supported" is claimed only for a
version and chip with recorded evidence from a real Mac.

## Admin password: not needed

The main delivery installs and runs **without admin rights**. It uses a per-user background agent
(LaunchAgent): it starts at login, restarts on failure, updates and uninstalls, and gives the same
worker guarantees as Linux while the user has a session, whether local, locked-screen or SSH. Engine
sign-ins stay where the engines put them.

Running before anyone logs in (an always-on Mac mini after a reboot) is the one thing macOS reserves for
administrators: only a system-level service loads before login. It also needs a different place to start
worker jobs, because the per-user launchd domain may not exist before login, and it needs engine sign-ins
outside the locked Keychain. That makes it a separate later increment ("boot mode"). It gets its own
admin-run probe and a credentials contract covering where sign-ins live, what workers can read, refresh,
rollback, and what `boot off` and uninstall keep. That increment is proposed after the probe, not promised
now. Until then, an always-on Mac stays logged in, with the screen locked. With disk encryption, a reboot
waits for one unlock on either platform.

## Seam

`altitude/platform.py` is the only file that names systemd, launchd, procfs, pidfd, sysctl, libproc or
their tools. It has four small groups: **service** (define, start/stop/restart, status, logs),
**jobs** (today's transient units: start piped or detached, active, stop, runtime limit), **processes**
(table read and identity-checked signal; shared by Stop and the terminal), and **connection owner**
(the terminal's agent check). Linux keeps its current mechanisms unchanged.

| Guarantee | Linux | macOS |
| --- | --- | --- |
| Service | systemd user unit (+ linger) | LaunchAgent in `gui/<uid>`; `launchctl bootstrap/bootout/kickstart -k/print`. Pre-login start: later boot-mode increment |
| Jobs outlive altd restarts | transient unit outside altd's cgroup | each job is its own launchd job, outside altd's job |
| Stop takes every descendant | cgroup, `KillMode=control-group` | kernel coalition: every job gets its own coalition, which descendants inherit through `setsid`, double fork and a cleared environment. Stop kills every member, repeating until none is left; an unreadable table or a survivor means "not confirmed stopped", as today |
| Signal only the intended process | pidfd binds the signal to the checked process | **not identical.** macOS has no handle-based signal. Coalition and start time are checked right before each signal, and a PID can only be reused after exiting. If probe 0 shows macOS hands out PIDs in increasing order, reuse inside that gap needs the counter to wrap through about 100,000 other processes. This residual is presented for decision, not hidden |
| Time limit independent of altd, not tamperable | `RuntimeMaxSec` in the service manager | a launchd-owned supervisor per job enforces it. Every timed process runs in a sandbox that may signal only its own sandbox, so it cannot kill the supervisor. Per launch path: Claude L2/L3 turns and reviews use Altitude's profile. Codex L2/L3 turns and reviews use Codex's own Seatbelt policy (its signal rule is checked by probe 0), wrapped in Altitude's profile if nesting works. Machine-grant commands use Altitude's profile with only the signal rule, keeping launchd and everything else reachable, so a granted command can still manage services through `launchctl` but cannot kill unrelated processes directly. The coordinator adapter and shims run inside their job's tree and inherit its sandbox. Run B attempts tampering from each path |
| No service-manager access from workers | bus not reachable | same permission boundary; the Seatbelt profile also denies the launchd control service |
| File confinement | Codex native sandbox; Claude permission boundary | Codex native Seatbelt sandbox; Claude permission boundary plus Altitude's Seatbelt profile (write outside the worktree/runtime roots denied), stronger than this Linux host |
| Memory evidence | cgroup memory accounting | coalition members' footprint via `proc_pid_rusage`; no OOM-kill attribution (macOS has no per-job OOM killer) |
| Sleep | none | power assertion while any job runs (idle sleep only; a closed laptop lid still sleeps, as on Linux) |

Other platform items, all fixture-tested: `TMPDIR` stays the user's; no synthesized Linux bus variables;
Homebrew paths; LibreSSL or `brew install openssl@3`, chosen from probe 0; case-insensitive project names
on both platforms; short Unix-socket paths (104 bytes); `/usr/bin/time` flags made portable for `make check`;
lcms2 from Homebrew; `RLIMIT_AS` replaced where macOS ignores it. The source-checkout developer deployment
(systemd drop-ins, self-restart) stays Linux-only in this task; installed releases are the Mac path.

## Evidence before building

Probe 0 (provider-free, about 5 minutes, no admin) checks each mechanism above on the operator's Mac:
coalition inheritance through `setsid`, double fork and a cleared environment, and a separate coalition
per launchd job; readability of coalitions and environment; whether `kickstart -k` and `bootout` leave
other jobs running; the Seatbelt signal denial, nesting inside another sandbox, and Codex's own signal
rule; launchd reachability inside the profile; LibreSSL; `RLIMIT_AS`; the socket limit; engine sign-in
storage and Keychain state. A failed item is re-presented with its fallback before code.

## Increments

0. Rule PR #518 (green, held for merge approval).
1. Seam refactor, Linux only: host mechanisms move into `platform.py`, duplicated procfs readers are
   deleted, and `make check` becomes portable. No behavior change; Linux tests prove parity.
   Implementation review.
2. macOS vertical slice, one PR: service (per-user agent), jobs, processes, Seatbelt profile, terminal,
   install/update/uninstall and the macOS branch of the installer entry point. The platform gate opens
   only here, with fixture tests (fake `launchctl`, fixture coalitions and process tables) and
   `scripts/platform_probe.py`, which runs in Linux CI and on the Mac. Implementation review, then **Run B**
   on a spare macOS account, with fake engines only. It runs the repository's full deterministic suite
   (`make check-python`) natively on the Mac. That suite covers one-engine readiness, project Setup and
   guards, first conversation, isolated task, Stop/resume and checked PR delivery with fixture engines
   against real macOS platform code. It then runs `scripts/platform_probe.py` for: install and doctor, HTTPS CA, allowed and denied
   writes, L3 profile, ordinary, detached, environment-clearing and concurrently forking descendants on
   Stop, timeout after owner exit, tamper attempt on the supervisor, altd restart and update with
   adoption and no duplicate, preserved sessions, messages and holds, failed-update rollback, uninstall
   keeping data, logout/login, reboot followed by login, and sleep past a deadline. Every row passes before
   the slice merges.
3. Boot mode (optional, admin once): its own probe and proposal, as above.
4. Support claim: docs say "macOS <version>, Apple silicon: runtime verified". A real-engine core workflow
   run needs the operator's separate provider decision (question 3).
