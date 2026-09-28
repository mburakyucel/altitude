# macOS runtime: where the Mac session stands

Host: macOS 26.6.2 on Apple M3, logged-in desktop session, no admin. Evidence is from this one Mac; nothing here
claims support.

## Probe 0 against proposal.md

| Row | Result |
| --- | --- |
| LaunchAgent in `gui/<uid>`; bootstrap, kickstart -k, print, bootout | pass |
| Coalition inherited through setsid, double fork, cleared environment | pass |
| Separate coalition per launchd job | pass (a relaunched label gets a new one; bootout survivors keep the old one) |
| launchd reaps the coalition | no: `kickstart -k` and `bootout` end only the main process group; Stop kills members itself, as designed |
| Coalition and environment readable | coalition yes; **environment no for Apple platform binaries** (see 3) |
| PIDs increasing | yes, step 1 |
| `(deny signal (target others))` | **does not block anything**; `(deny signal)(allow signal (target same-sandbox))` does exactly what the proposal needs |
| Nesting Altitude's profile and Codex's | **fails both ways** (`sandbox_apply: Operation not permitted`); proposal's fallback applies |
| Codex's own signal rule (`codex sandbox -P :workspace`) | denies outside processes and the parent; allows own children |
| launchd reachable inside a profile | `print` yes; **every service control (kill, kickstart, bootout, bootstrap, remove) refused to any sandboxed caller** |
| LibreSSL 3.3.6 | issues the name-constrained CA; **lacks `verify -verify_hostname/-verify_ip`** used by tls.py → OpenSSL 3 required |
| `RLIMIT_AS` | rejected (EINVAL) → replaced |
| Unix socket limit | 104 bytes confirmed; `$TMPDIR` is ~50 bytes |
| Case-insensitive home | yes |

Probe harness fixes: `ps` output was truncated by the 2000-character capture (committed fix); Codex 0.142 has no
`codex sandbox macos` subcommand (`codex sandbox -P :workspace -C <dir> -- cmd`).

## Operator decisions (2026-09-27)

1. Machine-grant commands run **outside Seatbelt** on macOS (launchd refuses service control to sandboxed callers, so
   the signal-only profile would break service-management grants). A granted command could signal its own supervisor.
2. **Terminal shells run as their own launchd jobs** on macOS: the environment mark cannot find Apple binaries, so
   Close kills the shell's coalition instead. The operator wants to revisit macOS terminal behavior once Altitude runs
   there.

## Implemented on `mac-runtime` (increment 2)

- `platform.py` macOS host: LaunchAgent service (same status/control contract; restart re-bootstraps to read the
  definition), jobs as launchd jobs with a supervisor (time limit, coalition cleanup when the command exits, idle-sleep
  assertion, self-removal), coalition Stop with a start-time and coalition check before each signal, the Seatbelt
  profile, terminal shells as jobs, libproc/sysctl process and socket facts, Homebrew library lookup, and a footprint
  watcher in place of `RLIMIT_AS`. Linux paths are unchanged.
- Claude L2 workers, L3 turns (job or child) and reviews run under the profile with writable roots from engines.py;
  Codex keeps its own sandbox.
- Project names differing only by case are refused on both hosts. `platform.source_service()` keeps the source
  deployment's TLS preparation and self-restart Linux-only.
- install.sh Mac branch: Apple silicon, macOS 15+, OpenSSL 3, a logged-in `gui` domain; the Mac stop is removed.
- Tests: the suite runs natively; systemd-fixture classes set `host = "linux"`; `tests/test_platform_darwin.py`
  covers the macOS seam with fixtures on any host; `InstallationDarwin` reruns the install lifecycle with the plist.
- `scripts/platform_probe.py` (both hosts, throwaway jobs; `--service` adds a throwaway service).
- Docs describe the runtime without claiming support.

## Evidence

- `make check-python` natively on the Mac: 1911 tests pass (1 skip).
- `scripts/platform_probe.py --service` natively: all 12 rows pass (pipe, file output, exit cleanup, Stop of a
  10-12 process tree in 0.1 s, time limit, limit after owner exit, logged, detached, confinement and tamper,
  process facts, memory limit, service lifecycle).
- `claude --version` starts under the profile; no provider request was made.
- Altitude itself, run from the checkout in the foreground with a throwaway home, port and TLS directory and stub
  engines: HTTPS with a generated CA (OpenSSL 3), the built UI, `alt doctor`, project registration, a terminal as a
  launchd job (commands run, busy command reported, Close kills a detached `setsid` process, job gone), a terminal
  request from inside an Altitude job refused as an agent's while the operator's is allowed, clean shutdown.
- Found while building the UI on the Mac: `VoiceDiagnostics.tsx` and `voiceDiagnostics.ts` (#552) resolve to one
  import on a case-insensitive disk, so the web app did not build on any Mac; renamed to `voiceTrace.ts`.
- Apple's system Python 3.9 (LibreSSL) refuses to verify Altitude's name-constrained CA ("unsupported name
  constraint type"); macOS `curl` and Python 3.12 with OpenSSL 3 accept it. Safari and Chrome on the Mac are untested.

## Open for the owner task

- Linux CI run of the branch (test_platform_darwin and the pinned classes have only run on the Mac).
- Run B on a spare account with fake engines; real-engine turns under confinement need the separate provider decision.
- The Claude write allowlist is unverified against a real turn.
- L3's `journalctl` shim is Linux-only; on macOS logs are in `~/Library/Logs/altitude/`.
- `make check-web`: web unit tests (607) and the build pass on the Mac; the Playwright browser walkthroughs were not run.
- Session incident: an early draft called `/bin/launchctl` by absolute path, bypassing the suite's offline shim, and a
  test briefly bootstrapped a real `dev.altitude.altd` from a temporary home; it was booted out at once.
  `launchctl` now resolves through PATH. launchd's per-user override database keeps a harmless `enabled` entry for
  that label (removal needs admin).
- A PR is open at the operator's request for review from the Linux session; it is not merged. This directory is
  still here for the owner task to delete before landing.
