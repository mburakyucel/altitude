# Operations

This is the runtime guide for an already configured installation. New users should start with
[setup](SETUP.md); contributors should use [development and checks](DEVELOPMENT.md).
The shipped [service unit](../systemd/altitude.service) is a maintainer deployment template:
its checkout path, PATH and tunnel binding need deliberate configuration for another machine.
Private archives generate their own user service; they do not install that source template.

## Runtime and inspection

`altitude/` is a standard-library Python package. `bin/alt` is the CLI; `personas/` contains the
project coordinator (L3) and task owner (L2) instructions, `schemas/` defines delivery reports,
and `hooks/` contains managed-repository Git hooks, inbox delivery and statusline telemetry.
`web/` builds into ignored `web/dist/`, served by the same Python process as the API.
See [architecture](ARCHITECTURE.md) for authorization and engine containment.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
and archived tasks, conversations, monitor snapshots, and private incident evidence. Keep it
out of source control. Provider session files have their engine's own retention policy.

```sh
bin/alt --project example task report example-task
bin/alt --project example task messages example-task --last 5
bin/alt --project example task events example-task --last 5
bin/alt --project example task status example-task --brief
bin/alt queue
bin/alt --project example repo
bin/alt --project example pr 42
bin/alt --project example l3 tools --days 7
bin/alt monitor
```

These illustrative inspection commands use a registered project, task and PR; substitute yours.
Add `--json` where supported for the full record. The [CLI reference](CLI.md) describes the
inspection and task lifecycle verbs.

Project verbs include `alt project add <name> [--path PATH]`, `list`, `discover`,
`remove <name>` and `set <name>` for routing preferences. All projects share one machine cap,
defaulting to 80; the operator can use `alt machine set --wip N --reason '…'` (including N above 80),
`alt machine set --unset-wip --reason '…'`, and `alt machine show` to set, reset and inspect it.
Machine changes, project add and remove are operator-only. See [concurrency examples](CLI.md#concurrency-limits).
A set persists a reason-bearing request that altd applies on its next tick,
without a PR, restart, or free task slot. Stored project caps impose no limit. Lowering the machine
cap preserves running work; launches wait until it has room. Blocked tasks consume no capacity, and
eligible resumes across all projects take available slots before fresh work. Planned file lists
guide coordination; overlapping files do not hold dispatch.
Owners rebase before landing and keep edits in shared documents to their own sections.

Inspect routing, usage windows and observed sessions in **Monitor**. Missing or stale readings
remain explicit. **Settings → This project**, opened from the project's three dots, sets independent
model and effort defaults for L3 and L2 on each engine. These settings preserve running task attempts;
L3 defaults apply on its next turn, while L2 defaults apply on fresh dispatch. Unset effort is native
except High for a Codex L2; **Native** explicitly requests no effort override. Higher effort can use more
time and tokens. The CLI reference covers [routing preferences](CLI.md#automatic-routing-preferences),
[model defaults](CLI.md#default-models) and [effort selection](CLI.md#task-reasoning-effort),
including the same settings from the terminal.

L3 and the operator file requested backlog through altd with `alt issue new --title '…' -`
or `alt issue comment <number> -` (body on stdin). Authorized complete deliveries use reviewed
PR closing links; when an already merged delivery lacks its link, L3 verifies the issue's full
scope and uses `alt issue close <number> --reason completed` without another routine request.
Other closures require the operator's request; `not-planned` records work the operator decides
not to pursue. Closure publishes no comment and records the actor, issue, reason and URL.
L3 does not select or clean up unrelated backlog autonomously. L2 routes direct closure evidence
through its task reply and report follow-ups and cannot mutate issues directly. See
[GitHub issues](CLI.md#github-issues) for arguments and the rule excluding home paths and private
incident evidence from published text.

## Remove a project

In the project's **More actions** menu, **Remove project** detaches its coordinator and stops
Altitude management. Finish or reject unfinished tasks, then wait for their workers and any
coordinator turn. The repository, remaining worktrees, history, provider sessions and queued
messages stay on disk. Add the same folder and project name again to restore the history and
resume waiting messages. Removing the last project opens First run; otherwise a remaining
project is selected. `alt project remove <name>` uses the same checks. See
[project lifecycle](CLI.md#project-lifecycle).

## Incident publication

Altitude records its own failures as sanitized incidents under the runtime directory. They stay
there until **Settings → Incident reports** (also a First run step) turns publishing on for a
GitHub `owner/repository`, Altitude's own filled in or a fork you name, or
`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in altd's environment names one for a non-interactive
install. Every incident then becomes one sanitized issue there, and `alt incident list` shows the
link or the pending reason. The saved setting outranks the environment, including when it turns
publishing off. Altitude's repository is public, so its issues are public; a fork you manage keeps
them under your control. An installed application keeps the environment value in its saved settings, so `alt install` from a shell where
it is exported carries it into the service; a source deployment sets it in the user service unit's
`Environment=`, as the checked-in `systemd/altitude.service` does for the maintainer's development
installation; copy the unit and run `systemctl --user daemon-reload` so the next restart applies
it. See [incident issues](CLI.md#incident-issues).

## Recovering dirty main

A dirty deployment checkout leaves isolated dispatch and resume available; deployment failures
remain visible separately. To reconcile its edits, use the
[dirty-checkout recovery procedure](CLI.md#dirty-checkout-recovery): select an unlaunched blocked
task, pause edits to main, and request `alt task preserve-checkout <slug> --reason '…'` as L3 or
the operator. Altd preserves staged, unstaged and untracked files on a local archive branch, records
its branch and immutable snapshot SHA in `checkout_archive` before cleanup, and requires the checkout
to pass the normal guard. The snapshot's parent retains staged-only content. Ignored files
remain in place. This needs no task slot, service restart or privileged worker.

Inspect `alt task status <slug>` for the request result, branch and SHA before sending the owner
its reconciliation instructions. The owner inspects the two archive commits and applies the net
binary diff from `<SHA>~2` to `<SHA>` inside its isolated worktree, as the CLI procedure describes.
This flattens staging intent; staged content remains inspectable in `<SHA>^`. Review the contents
against the authorized objective and publication rules and deliver through a PR. Resume blocked tasks
explicitly; isolated work does not require checkout cleanup. Archives remain local until explicit
operator removal; Altitude never
pushes or deletes them. An interrupted request is not replayed: inspect
`archive/checkout-<request-id>` and task events first, even if no task snapshot receipt exists.
Cleanup failures, ignored obstructions and submodule/nested-repository dirt keep preservation blocked;
retain the snapshot and fault evidence. Legacy `preserved_checkout` stash SHAs stay readable and
apply with `git stash apply --index <SHA>` in the owner's worktree; existing stashes remain untouched.
Restart notices retain unresolved faults and require observed resolution before resume.

## Installed application lifecycle

These are operator lifecycle actions for a packaged installation, not commands for ordinary code
agents or a source deployment:

```sh
alt doctor
alt service status
alt service logs
alt update --archive altitude-v0.1.0-rc.2.tar.gz --sha256 '<release SHA-256>'
```

Use the exact privately supplied version/checksum, not the placeholder above. Installation checks
the archive and manifest before selecting an immutable version. Activation waits for dispatch,
resume, L3 and report verification to be quiet, then verifies the selected version/commit, native
service PID, HTTPS health and built UI. An already stopped installation stays stopped on update.
Use `alt service start` or `alt service stop` only when deliberately changing its lifecycle;
independent task workers are not stopped with the daemon. Projects continue using ordinary checked
PR delivery; a managed source clone does not update the installed application.
Lifecycle commands reject shell overrides that disagree with the owned service's saved runtime,
binding, TLS or project-root settings. Remove the named overrides before retrying.

Failed activation restores the previous version and service. If interrupted or recovery remains
incomplete, use `alt recover`; if the launcher is unavailable, use the same trusted installer:
`python3.12 install.py --recover` (with `--prefix` when a custom prefix was selected). A recovery
receipt remains until restoration succeeds and prevents new dispatch/coordinator work while activation is unverified. Changed service ownership or unconfirmed stop refuses
further mutation. Retain the failing archive/version and sanitized error for diagnosis.

`alt uninstall` stops/removes only the owned service and launcher. It refuses unfinished tasks
that still own worker inputs. Registered projects keep installed hook resources; otherwise
application versions are removed. Configuration, certificate trust, histories, provider sessions
and project worktrees remain. Updates do not prune prior versions. Removing retained data or
device trust is a separate deliberate action, not part of uninstall or recovery. Code restoration
does not roll back data; incompatible state needs the [release recovery procedure](RELEASING.md#recovery).

## Service lifecycle

[Versioned releases](RELEASING.md) are validated checkpoints. For source deployments, they do not select the
deployed revision or delay activation. Recovery normally uses a checked revert/fix PR followed
by the activation path below; the release guide distinguishes web-bundle restoration from source
and runtime-state recovery. Never reset the deployment checkout to a release tag as a rollback.

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Source-deployed Altitude activates merged backend and web changes itself. The regular thirty-second tick discovers
merges even while their workers run. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it.
Dispatch continues while activation is pending. Once no dispatch or resume claim, L3 turn, or report
verification is in flight, `altd` runs the guarded restart script below as a transient user unit.
A failing restart unit files a system fault naming its reason for L3 immediately. While its
request is still pending it also records the reason as `error` in `monitor/restart-pending.json`
and marks it `failed`, and the hold lifts. A restart that has not happened
ten minutes after it was requested is the same fault, for a unit that died without reporting. The
transient unit is collected once it exits, so `systemctl --user status` reports it not-found;
`journalctl --user -u <unit>` keeps its output. A failed activation retries automatically after the
next merge that needs activation, or sooner from the Restart button.

An explicitly authorized operator can restart sooner by pressing Restart on the web app's banner
(shown only at the narrow quiet point, including while workers run) or running `make restart`
from the deployed primary checkout. The command refuses another clone/worktree, a non-exact or
dirty `main`, and an in-flight dispatch,
L3 turn, or report verification. Both engines run L2 workers in independent transient user units,
so workers survive and are adopted after restart. Dispatch and
L3 turns wait only from the restart request until the replacement daemon is ready.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits for both its API and
web page to answer from a new process. The prior bundle is restored if verification fails. There is
no separate web service and no `sudo` is required. Node 22.22.2+ (22.x) or 24.15+ (24.x) and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.

## Preserve source TLS before upgrading

An existing source deployment that relies on implicit certificate discovery needs an explicit TLS
setting before upgrading. Keep its current certificate directory and trust; this is not an archive
migration. Run as the operator from a supported Linux host with visibility into its own `/proc`
process/socket records. This operation handles legacy external certificate pairs, a concrete IP binding and a direct
source `bin/alt serve` unit (optionally invoked with Python 3). Managed Altitude CA directories,
wildcard/DNS bindings, environment files and foreign drop-ins require separate reconciliation.

Obtain the reviewed installer, archive and verified checksum, then inspect:

```sh
python3.12 install.py --archive altitude-v0.1.0-rc.1.tar.gz --sha256 '<release SHA-256>' \
  --prepare-source-tls /absolute/path/to/existing-certificates
```

The version is illustrative. The command verifies the archive and checks the active source unit,
effective process settings, listener ownership and the exact served certificate. It prints the
single service override without writing it. After reviewing that output, repeat with `--apply`.
With a CLI containing this operation, the equivalent is:

```sh
alt service prepare-tls --directory /absolute/path/to/existing-certificates
alt service prepare-tls --directory /absolute/path/to/existing-certificates --apply
```

Apply reloads the unit definition and verifies the loaded TLS-directory setting, unchanged daemon
PID and HTTPS identity. It does not restart the service, change binding or certificates, install
the archive, or alter device trust. Changed/ambiguous ownership or identity refuses preparation;
failed reload/verification restores only the unchanged owned override and reports any unconfirmed
restoration. Existing custom overrides need deliberate reconciliation. Save the successful result
privately and verify normal activation after the separately authorized upgrade. L2 and L3 cannot
run preparation, and a passing fixture test is not evidence that a production unit is prepared.
Verification compares the configured command and live process identity; command execution-history
timestamps can reset during a definition reload. On failure, the message names changed fields
without printing environment values and includes the original failure if restoration is uncertain.
Do not retry an uncertain restoration until the loaded unit and running identity are inspected.
If interrupted after writing the override, inspect that file and any other pending unit changes
before running `systemctl --user daemon-reload` from the operator terminal. This reloads definitions
without restarting services. Repeat check-only preparation and then `--apply`; an ambiguous state
never counts as successful preservation.

## Voice input

Dictation works on a fresh installation through the browser's own speech recognition. One machine
setting selects the backend; typing is never affected.

| `alt machine set --voice …` | How it works | Words appear | Where audio goes | Needs |
| --- | --- | --- | --- | --- |
| `browser` (default) | The browser's speech recognition (Safari on iPhone and Mac, Chrome, Edge). The server is not involved. | while you speak; the last phrase may still change until it is final | Safari recognizes on the device when it can, otherwise through Apple; Chrome and Edge send audio to Google or Microsoft speech services. Firefox and Chromium builds without a vendor key have no recognition and show a typing hint. | HTTPS and a supported browser |
| a service URL | The browser records; Altitude posts the recording unchanged to [your speech service](#your-speech-service) as an OpenAI-compatible `audio/transcriptions` request and returns its `text`. `--voice-model` names the model (default `whisper-1`) and `--voice-key-file` supplies the bearer key from a file or stdin; both matter only to hosted providers. A redirecting service is refused so the key never follows it. | after Stop or Send | to that URL only: this computer, another machine on your network, or a hosted provider | a running service |

```sh
alt machine show
alt machine set --voice http://127.0.0.1:8080/v1/audio/transcriptions --reason 'My speech server'
alt machine set --voice https://api.example.com/v1/audio/transcriptions --voice-model whisper-1 --voice-key-file - --reason 'Hosted transcription' < key.txt
alt machine set --unset-voice --reason 'Back to browser recognition'
```

The setting lands in the private `~/.altitude/settings.json` through the machine-settings request;
the key stays in that file and the request file, and `alt machine show` and the event log show it
only as `set`. **Settings → Voice input** edits the same setting: Browser recognition saves
immediately, and **Your speech service** asks for its URL and uses **Save service**; model and key
sit behind **Hosted provider? Add a key or model**. The stored key is never returned to the page; it
is retained only for an unchanged URL. **Replace** with a blank field removes it. Back discards
unsaved edits. The overview row names the service's host. Settings also shows read-only connection
details.
Use HTTPS when entering a key from another device: the browser sends that key to Altitude in the
save request, and an HTTP connection does not encrypt it.

When the service cannot be reached, refuses the recording or answers without text, the composer
names the configured URL (never the key) so you can fix it; your draft stays.

A Settings read or save updates the next capture in that browser document. Other documents and CLI changes
are picked up when opening Settings, on reload or after a stale upload is refused. Recordings identify their selected backend
and destination; changing either cannot silently reroute unfinished audio. Altitude keeps no
recordings; the speech service that transcribes them controls its own retention.

### Your speech service

Altitude speaks one standard interface to a speech-to-text service: OpenAI's audio transcription
API. It sends `POST <URL>` as `multipart/form-data` with `file` (the browser's recording, unchanged:
AAC/mp4 from Safari, opus/webm from Chromium), `model` and `response_format=json`, with
`Authorization: Bearer <key>` when a key is set, and reads `text` from the JSON reply. Many servers
implement it, locally and hosted. The service decodes the browser's container, so a local server
must accept compressed audio or convert it itself; Altitude runs no converter and owns no model.

A worked example with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)'s bundled server on the
computer running Altitude (flag names follow that server's `--help`; check yours):

```sh
# Build whisper.cpp and download a model as its README describes, then:
whisper-server -m models/ggml-base.en.bin --host 127.0.0.1 --port 8080 \
  --inference-path /v1/audio/transcriptions --convert   # --convert decodes browser audio with ffmpeg
alt machine set --voice http://127.0.0.1:8080/v1/audio/transcriptions --reason 'Local whisper.cpp'
```

A server on another machine on your network uses that machine's private address instead of
`127.0.0.1`; bind it only to that private interface. A hosted provider uses its documented
`…/v1/audio/transcriptions` URL, a key and, where it offers several, a model name. Recordings then
leave your network under that provider's storage policy and charges.

### On iPhone

Open your configured Altitude HTTPS URL through your private network. Safari exposes the microphone only in a
secure context, so the phone must trust the local CA used by Altitude's certificate. Follow the
[per-device trust steps](SETUP.md#trust-https-on-each-device); `/ca.crt` is available over already
trusted HTTPS, not a first-trust bootstrap. The microphone button remains a typing-only hint on plain
HTTP or an unsupported browser. Safari's Share → Add to Home Screen gives Altitude a Home Screen
icon with its mark; decision alerts on iPhone need Altitude opened from there.

For your own installation, set `ALTITUDE_HOST`/`ALTITUDE_PORT` to its private-network endpoint.
On the next start Altitude reissues its server certificate for that address under the same CA, so
trusted devices need no new step. A generated CA refuses public addresses and names.
`ALTITUDE_TLS_DIR` selects a private certificate directory separate from runtime/project data.
Install the CA on the phone and enable its trust in Certificate Trust Settings. Arrange the
private tunnel and any firewall rule for your chosen interface/port separately. The shipped
service template's tunnel address and checkout path are not defaults to copy to another machine.
Generated server certificates renew automatically while the original CA remains valid; an expired
or replaced CA needs explicit new trust on every device. External certificate pairs are not renewed
or overwritten. Existing configured TLS paths and exposure remain operator choices.

With the `browser` backend, recognized words appear in the draft while you speak, and the field keeps
the latest words in view. Altitude enables the browser's automatic punctuation when its recognizer
exposes `unspokenPunctuation`; browsers without it retain their own formatting. This requires no
extra installation or service. [Chrome documents support from version 151](https://developer.chrome.com/release-notes/151#web_speech_api_unspoken_punctuation);
this is not a guarantee of punctuation on Safari or other browsers. Altitude does not insert periods
at recognition-fragment boundaries or replace spoken words with punctuation. With your
speech service, the browser records at most ten minutes as AAC/mp4 on iOS or opus/webm where
available and uploads the recording when you stop; Altitude forwards it without keeping it, and
audio is never part of task or chat state. Either way a recording becomes text through
**Stop** (Ctrl/⌘+M), landing in the draft for editing, or the send arrow (Enter), landing and
sending at once (queued while L3 is busy). **Cancel** (Esc) discards it. An empty transcript or a
transcription failure sends nothing and preserves the draft.

For a manual Safari check, open each of a project conversation, a task conversation, and a project
task's **Message L2** panel; record and stop; confirm the transcript is appended to the existing
draft and nothing else appears; record again and use the arrow to transcribe and send or queue at once;
then cancel a recording and deny microphone access once and confirm the
typed draft remains usable. If Safari reports that voice needs HTTPS, use your configured secure URL and verify the local CA is
enabled under Certificate Trust Settings.
