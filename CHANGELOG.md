# Changelog

Release notes describe user-visible behavior, compatibility and recovery. The project is an early
preview; see [release checkpoints](docs/RELEASING.md). An Unreleased entry is not a published release.

## Unreleased

- Altitude is licensed under the Functional Source License (`FSL-1.1-ALv2`): any use except a
  competing commercial product, converting to Apache-2.0 two years after each release. The release
  archive ships the license and third-party notices; contributions require the CLA (#219).

- PR checks keep running once the repository is public: the owner's own branches keep the required
  self-hosted `check`, every other pull request runs the same suite on a GitHub-hosted runner without
  touching the owner's machine, and `alt land` requires the check wherever the base ships its
  workflow instead of naming one repository (#469).

- Incident issues publish only to the repository named by `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in
  altd's environment. A fresh installation keeps incidents on the machine and shows that reason
  in `alt incident list`; the release metadata and source origin are no longer targets (#470).

- CLI help and errors say "the operator" instead of a person's name; `alt task block --for-burak`
  is `--for-operator` (#470).

- The repository is ready for public contributors: a [security policy](SECURITY.md) with private
  vulnerability reporting and scope, contributor guidance and issue templates written for fork
  contributors, and preview wording that no longer assumes invited collaborators (#219).

- Auto refreshes account quota without an interactive session, using native live usage reports.
  Missing or failed readings remain unknown; stale readings never become fresh by being reread.

- Setup reads crossing repair completion refresh the completed operation and verified Git guard
  receipts together; interrupted repairs remain distinguishable (#429). A retry accepted during
  a status read runs as soon as the read finishes instead of waiting for periodic maintenance.

- Fresh L2 activity previews scroll with chat and disappear after 60 seconds without fresh public
  output. Missing or unavailable output leaves no box; recorded output remains in Live session.

- Desktop chats use compact headers, with wrapping task titles and directly accessible actions.
  Task metadata and token usage open in Task details on phone and desktop.

- L3 keeps stalled authorized work actionable with an owned next step, a justified finite observation
  or a concrete decision when missing historical evidence prevents verified recovery (#386).

- Coordinator service reads expose bounded native worker termination and memory evidence;
  missing/collected units and unsupported fields remain unknown, with no service-control access (#384).

- Delayed image admission receipts preserve the accepted history row without adding a queued copy.
  Task reads crossing archival return ordinary not-found responses instead of failure tracebacks.

- Native helpers share a concise L1 persona, referenced explicitly in their assignments on both
  engines. L2 supplies task-specific scope, verifies results and retains delivery accountability.

- Launches, landing and restart builds discover the installed nvm default when Node is absent
  from PATH. Candidate installs run inside the web project so Corepack uses its pinned pnpm (#368).
- Private Linux x86_64 archives include the CLI, daemon and built UI, with per-user installation,
  prerequisite inspection and recoverable versioned updates. Uninstall preserves user data and
  referenced hooks. Fresh defaults use localhost HTTPS and a separate installation-local CA with
  explicit device trust and server-certificate renewal. Native macOS and clean-machine/provider
  acceptance remain pending; source deployments retain explicit lifecycle and network choices (#350).

- Model allowance exhaustion is recognized without inventing a reset time. Coordinators can use
  `alt task handoff` to continue an exited, fault-blocked owner as a fresh attempt on another
  configured engine, preserving saved work, task history, PRs, questions and merge holds (#310).
- The coordinator reconciles recorded UI merge choices and conversational reaffirmations using
  original-message citations, current question/hold evidence and the unchanged PR/head. Semantic
  interpretation belongs to L3; the daemon checks provenance and scope, records the release and
  leaves resume and normal checked landing to the owner (#294).

- Pending task designs can be reviewed from their conversation before merge: a versioned browser
  preview shows saved screenshots and proposal text, with a return to the existing question.
  Captures are confined to the owning task, active HTML is excluded, and replacement designs require
  a new question revision without releasing merge holds.

- Add private screenshot/photo input to project and task chat, compact previews and full-image
  viewing, durable retries and same-project image handoff to the assigned task owner.

- Protected Git hooks allow reference packing, loose-copy pruning and fetch garbage collection
  while main lags origin/main, preserving its tip and subsequent permitted fast-forward. Genuine
  unauthorized protected branch moves and deletions remain blocked (#291).
- Landing accepts a nonrequired skipped deployment with immutable condition
  `github.event_name != 'pull_request'` for an associated `pull_request` run. Required checks,
  exact candidate/source validation and at least one applicable passing check remain mandatory (#288).
- Mobile L3 and L2 chat use one compact header and composer, with bottom navigation hidden during
  detected software keyboard use and restored on dismissal. Task details hold metadata, full
  blocked/merge-hold reasons and existing actions; concise status, actionable failures and pending
  questions remain accessible. Drafts and reading position survive keyboard and details transitions.
- Full Python, web, build and phone/desktop browser checks run with disposable fictional state
  and deterministic external-engine fixtures. Core task delivery, messaging/resume and failure
  paths have programmatic integration evidence; routine checks make no model calls.
- Daily private-preview readiness and as-needed patch releases define candidate validation, versioning, notes and
  recovery. Version publication is explicit; merged changes continue activating automatically.
- Live-provider testing, including the real tiny validation task, is deferred under the operator's
  testing policy. See the [coverage limits](docs/DEVELOPMENT.md#coverage-and-limits).
