# Changelog

Release notes describe user-visible behavior, compatibility and recovery. The project is a private
preview; see [release checkpoints](docs/RELEASING.md). An Unreleased entry is not a published release.

## Unreleased

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
