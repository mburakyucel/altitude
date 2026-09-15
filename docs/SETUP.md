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
  version matrix yet. Codex task owners use the CLI's configured model unless an Auto option or
  explicit pin supplies one. Its coordinator runs with user configuration ignored and uses the CLI
  default unless a model override is supplied. Configure Auto with the engine/models you intend to
  use; access to every default preference is not required.

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
clone one. Read its instructions file (`AGENTS.md` when present, otherwise `CLAUDE.md`), follow its references/imports, and
record its build/test and delivery expectations there if they are not already documented.

```sh
cd /absolute/path/to/example-project

# Registers this existing folder; the running daemon performs routine setup.
alt project add example --path "$PWD"
alt project list

# Select a free port on your own machine; keep this terminal running.
ALTITUDE_HOST=127.0.0.1 ALTITUDE_PORT=18890 ALTITUDE_TLS=0 alt serve
```

Open the project's **Setup** checklist to inspect its results. Altitude automatically configures
missing or stale guards that it owns. If another hook system is present, **Review integration**
offers a supported way to use both sets or leaves the current setup intact for discussion.
Auto skips missing CLIs and known exhausted or rejected options.
Its default ties Codex's default model and Claude Fable, with Claude Opus in the next tier. One
installed engine is enough. Availability of a model is unverified until supported evidence says
otherwise; a subscription's plan name is not evidence of model access.

For an account with only Claude Opus, set that preference from another terminal using the same PATH
and Altitude home while the server is running:

```sh
alt project set example --routing 'claude:opus' --reason 'Use the model available on this account'
```

The daemon applies the setting on its next tick. This reason-bearing command is also available to
the project's L3; no restart is needed. See [CLI routing examples](CLI.md#automatic-routing-preferences)
for tied options, fallback tiers, unavailable Fable and strict one-off pins.

Open **http://127.0.0.1:18890/projects/example**. Send:

> Describe this project and suggest one small improvement. Let's discuss it before creating work.

You should see your message and the coordinator's reply. The UI calls the coordinator L3. Once
you choose a concrete change, ask it to create a task; use the work panel to open the task, talk
to its owner (L2), and follow its session and PR. You can ask for a merge hold when you want to
review the result before merge. Engine calls use your authenticated account and can consume its
allowance or incur its normal charges.

Register before starting this foreground server when you want to use `alt chat` immediately:
startup creates the project's coordinator broker. Web conversations also create the broker on
demand, so a project registered after startup can start from the web app. Its First run / Add
project flow registers the folder and opens Setup immediately, using Auto and the project's
preferences. Closing the checklist leaves accepted work running. A CLI conversation
also works from another terminal with
the same PATH and Altitude home while the server is running:

```sh
alt --project example chat "Describe this project and suggest one small improvement."
```

## Project setup and repair

**Altitude performs routine setup automatically. If a step fails, L3 helps investigate, and
Altitude checks the result before marking it complete.** The project header's permanent **Setup**
status opens its checklist on phone and desktop. It shows the latest observations, with **Check
again** for a fresh check and relevant actions on incomplete rows.

| Step | What happens |
| --- | --- |
| Project folder | Register the selected folder or reuse its existing registration and history. |
| Git repository | Detect an existing repository and task-delivery prerequisites. A non-Git folder supports conversation with Git tasks unavailable. |
| Project instructions | Show the existing instruction file selected by the rule loader, or explain that none exists. Setup creates or overwrites no instructions. |
| Git guards | Install missing owned guards, refresh stale Altitude guards, or show the custom-hook integration choice. Non-Git folders show Not applicable. |
| Coordinator | Establish the command connection; show the first conversation's actual progress or reuse the existing conversation. |

The checklist distinguishes pending, running, completed, already configured, not applicable and
failed/input-needed outcomes. It reports creation only after an actual write; detection is reuse.
Ready describes these project checks, not every future remote operation or model's authentication.
Setup never initializes Git. Optional capabilities such as voice do not prevent readiness.

Existing projects receive the same current checks as new projects. Missing requirements introduced
by an update appear without detach/reattach or repeating healthy work. Routine maintenance and
launch checks refresh recognized owned guard paths to the active source; saved task-worktree
overrides receive the same checks. Failed or interrupted introductory
agent calls wait for an explicit Retry; routine maintenance does not repeat them. Refresh, reconnection
and interruption retain operation records; observations reconcile completed writes before retry.
Unconfirmed results remain unknown until checked.

**Retry** requests the supported programmatic operation again. L3 receives configuration faults and can
investigate with its existing tools or request bounded daemon repair, even when no task can launch.
**Discuss with L3** opens the existing project conversation; it does not send a message or start
another agent. Code fixes follow the usual task/PR process. L3's explanation cannot turn a row
green: programmatic checks verify the actual result.

For supported ordinary custom-hook directories, **Review integration → Use both hook sets** is
an explicit operator choice. Original hook files remain intact; both sets receive the same input
and either can reject the Git operation. **Keep current setup** leaves the conflict unresolved.
Review the displayed hook directory and events. Only combine trusted hooks: they and programs
they call run with Altitude's Git permissions, outside the task agent's sandbox. File checks
detect changed hook scripts; they do not establish the safety of their dependencies.
Changed custom hooks require a renewed choice. Unsupported hook managers and relative custom
hook selections stay unchanged with an explanation and discussion action. Routine
repair cannot make this choice for you. See the
[coordinator repair command and verification procedure](CLI.md#project-setup-and-guard-recovery).

## Configuration and limits

| Setting | Purpose |
| --- | --- |
| `ALTITUDE_HOME` | Runtime state directory, default `~/.altitude`; use the same value for CLI and server. Keep it out of Git. |
| `ALTITUDE_ROOTS` | Colon-separated parent folders scanned by First run, default `~/Projects`; `project add --path` also supports other folders. |
| `ALTITUDE_OPERATOR` | Name shown for the operator; defaults to “Operator”. |
| `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` | Bind address, port and TLS switch. Set all three as above for predictable localhost evaluation. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |
| `ALTITUDE_PRIMARY_ENGINE` | Tie order in the default Auto top tier; project `--routing` overrides those tiers. |

Quota telemetry is optional. The Monitor shows missing or stale readings rather than assuming
zero usage. Codex readings come from its app-server integration. For Claude usage readings,
`alt install-statusline` installs a global CLI statusline hook and an interactive session supplies
the snapshot; inspect your existing settings before choosing that optional installation. Unknown
readings leave options eligible in Auto and for explicit pins. Within a tied tier, Auto uses
configured order when weekly readings are unknown or incomparable, retaining a current L3 option
in that tier. It never invents separate model allowances from a shared account reading.

Single-engine Auto and explicit pins are supported; arbitrary provider/access configurations are not verified.
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
  and model configuration, and the systemd user manager. For immediate `alt chat` use, register
  before foreground startup; a web conversation can start its coordinator broker on demand.
- **No configured option is available:** inspect the route explanation. Install or authenticate an
  intended CLI, wait for a reported quota reset, or set an available preference. Explicit model/access
  rejections are remembered for thirty minutes; model rejections affect only that model and
  authentication rejections affect that engine. A strict pin must itself become usable or be changed.
- **A task cannot start or land:** inspect its reason, the clean `main`/`origin/main` checkout,
  Git guards, GitHub authentication and applicable check results. Do not bypass a guard.
  For dirty main, L3 or the operator explicitly requests `alt task preserve-checkout <slug> --reason "…"`
  for an unlaunched blocked task. A local archive branch retains the working snapshot and its staged
  parent; task status records the branch and SHA. Review/apply in the owner's isolated worktree and
  deliver through a PR; applying the complete snapshot flattens staging intent. Ignored files stay
  untouched. Archives are never automatically pushed or deleted, and legacy stash records remain
  recoverable. See [Recovering dirty main](OPERATIONS.md#recovering-dirty-main) for inspection,
  interruption handling and the separate resume step.
- **Usage is unknown:** inspect Monitor's explanation and the optional telemetry setup above.

Report setup friction with the command, environment, commit and sanitized error through
[Feedback](../README.md#feedback). These commands are checked against the CLI/source and repository
tests; installation on a second clean machine remains an explicit
[onboarding follow-up](ROADMAP.md#early-user-onboarding-and-public-release).
