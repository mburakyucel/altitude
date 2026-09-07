# Set up an early private preview

Altitude currently targets one operator on a Linux machine. This guide uses a foreground server
on localhost so you can evaluate the workspace before configuring a persistent service or remote
access. Installation is manual; there is no packaged installer or verified native macOS/Windows
runtime. See the [walkthrough](WALKTHROUGH.md) for the experience this setup enables.

## Prerequisites

- Linux with a working systemd **user** manager (`systemctl --user status`) and support for the
  selected engine's sandbox. Both task integrations launch through transient user units, even
  with a foreground Altitude server. Ubuntu 24.04/Python 3.12 is the CI environment; a broader
  compatibility matrix is not established.
- Python 3.12, Git, GitHub CLI (`gh`), Node 22.22.2+ (22.x) or 24.15+ (24.x), and pnpm (pinned
  in [`web/package.json`](../web/package.json)). The [locked development dependencies](../web/pnpm-lock.yaml)
  require these newer Node releases even though the package declares 22+. The backend uses
  Python's standard library.
- Access to this repository and to a GitHub project you can fetch, push and open PRs in.
  Authenticate GitHub CLI, verify `gh auth status`, and configure Git name/email and your
  SSH or HTTPS Git credentials. Altitude's delivery path expects a clean primary `main`
  checkout with an `origin/main` branch and the project's applicable checks.
- At least one installed, authenticated **Codex or Claude Code CLI**, usable from the same
  Linux account that runs Altitude. Authenticate using the engine's native setup and verify a
  small interactive request before launching Altitude. CLI versions must support the headless,
  session and permission features in the [launcher](../altitude/engines.py); there is no tested
  version matrix yet. Codex task owners use the CLI's configured model by default. Its coordinator
  runs with user configuration ignored and uses the CLI default unless a project model override
  is supplied. Claude role/model defaults are in [`config.py`](../altitude/config.py) and must be
  available to your account.

Clone and build using the [README commands](../README.md#get-started). From that checkout, make
the CLI available in this terminal:

```sh
export PATH="$PWD/bin:$PATH"
```

Keep this checkout in place: the CLI resolves its source, personas, hooks and built web assets
relative to it. For engines outside their default locations, set `CODEX_BIN` or `CLAUDE_BIN` to
the executable's absolute path before starting Altitude. Confirm both it and the project's test
tools are on the launch environment's PATH.

## Register a project and start a conversation

Use a clean primary checkout of a small GitHub project you are comfortable giving the agent
write access to through tasks. Replace the path below with your project; this does not create or
clone one. Read its instructions file (`CLAUDE.md`, or `AGENTS.md` for the selected engine), and
record its build/test and delivery expectations there if they are not already documented.

```sh
# Choose the engine you installed and authenticated: codex or claude.
altitude_engine=codex
cd /absolute/path/to/example-project

# Installs this repository's commit/push guards; project registration alone does not.
alt install-git-guards
alt project add example --path "$PWD" \
  --l2-engine "$altitude_engine" --l3-engine "$altitude_engine"
alt project list

# Select a free port on your own machine; keep this terminal running.
ALTITUDE_HOST=127.0.0.1 ALTITUDE_PORT=18890 ALTITUDE_TLS=0 alt serve
```

The guards configure that project's `core.hooksPath`. If another hook system is already
configured, installation refuses; agree how to integrate it before continuing, rather than
overwriting or disabling it. Explicit engine pins are intentional: Auto routing considers quota
readings, not whether a CLI is installed or authenticated. The default engine list still contains
both integrations, so pins keep this example on your chosen engine without a second subscription.

Open **http://127.0.0.1:18890/projects/example**. Send:

> Describe this project and suggest one small improvement. Let's discuss it before creating work.

You should see your message and the coordinator's reply. The UI calls the coordinator L3. Once
you choose a concrete change, ask it to create a task; use the work panel to open the task, talk
to its owner (L2), and follow its session and PR. You can ask for a merge hold when you want to
review the result before merge. Engine calls use your authenticated account and can consume its
allowance or incur its normal charges.

Register before starting this foreground server so it creates the project's coordinator broker
at startup. The web app's First run / Add project flow can register folders and start the
coordinator while the server is running, but that flow uses default routing; prefer the CLI pins
above for a single-engine evaluation. A CLI conversation also works from another terminal with
the same PATH and Altitude home while the server is running:

```sh
alt --project example chat "Describe this project and suggest one small improvement."
```

## Configuration and limits

| Setting | Purpose |
| --- | --- |
| `ALTITUDE_HOME` | Runtime state directory, default `~/.altitude`; use the same value for CLI and server. Keep it out of Git. |
| `ALTITUDE_ROOTS` | Colon-separated parent folders scanned by First run, default `~/Projects`; `project add --path` also supports other folders. |
| `ALTITUDE_OPERATOR` | Name shown for the operator; defaults to “Operator”. |
| `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` | Bind address, port and TLS switch. Set all three as above for predictable localhost evaluation. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |

Quota telemetry is optional. The Monitor shows missing or stale readings rather than assuming
zero usage. Codex readings come from its app-server integration. For Claude usage readings,
`alt install-statusline` installs a global CLI statusline hook and an interactive session supplies
the snapshot; inspect your existing settings before choosing that optional installation. Unknown
readings do not prevent a pinned engine from running.

Single-engine pinning is supported; arbitrary provider/access configurations are not verified.
In particular, the launcher filters some engine environment variables and supplies role settings;
the Codex coordinator ignores user configuration, including custom provider settings in that file.
Do not assume an interactive API/Bedrock configuration transfers unchanged to a launched session.
See the [engine boundary and gaps](ARCHITECTURE.md#engine-integration-boundary).

For persistent operation, adapt the [systemd unit](../systemd/altitude.service) deliberately:
it assumes the maintainer's checkout location, PATH and private-network address. `make install-service`
installs and immediately starts that template; it is not the generic onboarding command. Remote
phone access needs your own private network and HTTPS/certificate trust setup. The server has no
application login layer; localhost or a deliberately controlled private network is the current
access model. Voice additionally needs `ffmpeg` and a compatible local speech service; typing
remains available without them. See [operations](OPERATIONS.md) for those steps and lifecycle rules.

## When something does not work

- **The page does not load:** check the foreground server output, selected port and URL scheme;
  `ALTITUDE_TLS=0` makes the example HTTP. Verify `web/dist/index.html` exists from the build.
- **The first conversation fails:** verify the chosen CLI works as this user, its binary path
  and model configuration, and the systemd user manager. CLI registration needs to precede
  foreground startup; the UI's registration path starts its broker itself.
- **A task cannot start or land:** inspect its reason, the clean `main`/`origin/main` checkout,
  Git guards, GitHub authentication and applicable check results. Do not bypass a guard.
- **Usage is unknown:** inspect Monitor's explanation and the optional telemetry setup above.

Report setup friction with the command, environment, commit and sanitized error through
[Feedback](../README.md#feedback). These commands are checked against the CLI/source and repository
tests; installation on a second clean machine remains an explicit
[onboarding follow-up](ROADMAP.md#early-user-onboarding-and-public-release).
