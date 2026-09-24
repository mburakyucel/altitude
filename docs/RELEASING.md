# Private-preview release checkpoints

Altitude uses private versioned archives and source checkpoints for invited collaborators. A release identifies a validated commit
and its notes. The operator decides when to publish it. No release or tag is created by running
the test suite, merging a PR, or recording an Unreleased changelog entry.

Source-deployed merged changes activate through the existing [service lifecycle](OPERATIONS.md#service-lifecycle).
That process continues while a release candidate is evaluated. A service can therefore run a
newer commit than the latest published version. Release readiness and activation are separate;
record the exact source SHA when reporting either. Installed archives update only through the
explicit [application update command](OPERATIONS.md#installed-application-lifecycle). Public visibility and licensing
remain separate [release prerequisites](ROADMAP.md#early-user-onboarding-and-public-release).

## Cadence and versions

During active preview development, check readiness daily and release a useful batch of fixes or
features as soon as it is validated. Several patch releases in one day are reasonable when fixes
warrant them; there is no weekly wait or mandatory overnight observation period. Every candidate
passes the complete gate and targeted validation. Higher-risk lifecycle, permissions or storage
changes include an observation interval chosen for their failure modes and recorded in the
readiness record. A checkpoint can conclude that no release is ready. There is no scheduled
publication, release branch or freeze of unrelated development.

Tags are the source version authority: `v0.MINOR.PATCH`, beginning with `v0.1.0` when a first
release is approved. Compatible fixes increment PATCH; features or breaking preview behavior
increment MINOR and reset PATCH. Breaking behavior is described even during `0.x`. Candidate
labels use `v0.1.0-rc.1`, incrementing `rc.N` when the candidate changes. Before tag publication,
the candidate label is just a label in the readiness record. Never move a published tag.
The web package's private build metadata is not a separate product version.

## Candidate and release gates

This repository's temporary hosted-CI suspension uses the full local `make check` gate with
candidate SHA/tree evidence. The [local delivery policy](DEVELOPMENT.md#ci-and-candidate-identity)
preserves review and publication decisions; it does not establish hosted billing/artifact recovery.

1. Select an exact commit already on `origin/main`. Record its SHA, proposed version, previous
   release/known-good SHA and the PRs included since that point. Later main commits are outside
   this candidate's evidence and notes. Use an isolated checkout for candidate testing.
2. Require the complete deterministic gate: Python, web tests, typecheck/build and all applicable
   phone/desktop browser cases. Record the successful CI run for that exact SHA, commands,
   runtime/tool versions, executed counts, exceptions and artifacts. Missing, failed, canceled
   or skipped required checks do not establish readiness. A changed candidate needs a new full
   gate; a green run on an earlier SHA is not its validation.
3. Review [journey coverage and limits](DEVELOPMENT.md#coverage-and-limits) for included changes.
   Assert affected failure, retry, permission and terminal states programmatically. Fix material
   findings or explicitly dismiss them with evidence. Record remaining external/host confidence
   gaps rather than describing the preview as universally validated. Live-provider tests and
   the real tiny task are deferred; they are not candidate gates under the current policy.
4. Keep [Unreleased](../CHANGELOG.md) current in every behavior-changing PR. Prepare curated
   notes with user-visible changes, compatibility/migration needs, known limitations and a
   recovery plan. Review notes/artifacts for fictional or sanitized data; never publish runtime
   conversations, credentials or private incident records. Docs describing behavior ship in
   the same PR as that behavior.
5. Record targeted validation and any applicable observation interval and results. When it is already activated, normal
   use and existing API/UI health evidence can inform this record without starting model-using
   validation tasks. Any changed code gives a new candidate and new evidence. A version's
   notes and observations must identify which SHA was observed.
6. Present the readiness record and proposed notes to the operator for the publication decision.
   Approval names the version and SHA. Then deliver the dated changelog section through a PR
   and publish the immutable source tag/GitHub release only as authorized. If that PR changes
   the release SHA, repeat the deterministic gate on the final SHA before publishing it.
   Use the same source for notes and tag; a private preview remains labeled as such.

The readiness record can be a small file attached to the task report; it needs no new daemon
record or release service. Include: version, candidate SHA, included PRs, previous known-good
SHA, CI run and artifact links, test counts/timings, tested runtime/engine fixture formats,
observation interval, compatibility/recovery notes, finding dispositions and publication decision.
An example candidate command sequence, from an isolated checkout at the recorded SHA:

```sh
git rev-parse HEAD
pnpm --dir web install --frozen-lockfile
# Install the locked Chromium build as described in DEVELOPMENT.md.
make check
```

This validates the selected source. It does not create a release, switch a deployment to that
source or prove live provider compatibility.

## Pre-publication history audit

Before the repository or an archive becomes visible to anyone new, scan everything Git can reach for
material that belongs to a machine or a runtime rather than a repository: credentials, private keys,
home paths, email addresses, private network addresses, transcript-like records, runtime state,
build artifacts, large blobs and a private word list such as the names of other managed projects.
The scan is offline and Git-native; it validates nothing, sends nothing and rewrites nothing.

```sh
make audit-history AUDIT_WORDS="$HOME/.altitude/altitude/history-audit/words.txt"
make audit-history AUDIT_SINCE="$HOME/.altitude/altitude/history-audit/<previous>.json"   # delta only
```

Every blob reachable from any local ref (remote, tag, local, archive, quarantine, review and stash)
is read once, then every commit message. The console prints the sanitized summary: scanned refs by
namespace, the cutoff (`origin/main` and `HEAD`), commit and blob counts, the rule names and counts
by category with how many hits published refs reach. Exact refs, blobs, paths, lines and matches go
only to the findings file, mode 0600, under the runtime home; the script refuses a path inside the
repository. The private word list stays outside the repository as well.

A delta run takes the previous findings file and scans only objects and commits that none of its
recorded ref heads reach, so re-checking shortly before publication costs seconds. Inspect the
findings file directly; the audit record (task report, issue #233) carries the cutoff, the rules, the
counts and the conclusion, never the matches. Regular expressions and a word list find shaped and
known material, binary blobs get only path and size checks, and objects no ref reaches are not
scanned: a clean run is evidence within that coverage, not a guarantee. Bring any remediation
(history rewrite, credential rotation, file removal, visibility change) to the operator as a
decision; the audit itself changes nothing.

## Build a private archive

From the clean, committed candidate checkout, with the locked web build tools available:

```sh
python3.12 scripts/build_release.py --version v0.1.0-rc.1 --output /tmp/altitude-release
```

Use the approved candidate label. The builder exports the exact Git revision, installs frozen web
dependencies and builds the UI, then emits the application archive, `install.py` and archive SHA-256
file. The manifest records source identity and every packaged file hash. The archive contains the
CLI, Python daemon, built UI, personas, hooks, templates and schemas; users need no source build.
Existing archive names are immutable. Building artifacts creates no tag, GitHub release or public
publication. Deliver the installer and checksum through the approved private channel.

Record archive checksum and install/update/recovery evidence alongside candidate checks. The initial
runtime target is Ubuntu 24.04 x86_64; macOS 15/26 Apple silicon remains pending native confinement
and host validation. Deterministic fixtures do not establish physical Mac, fresh-machine, browser
trust or live provider compatibility. No public support claim precedes that evidence.

## Recovery

Packaged activation restores the preceding application version/service after failure, retaining
configuration, TLS identity and user data. Interrupted recovery uses `alt recover` or the standalone
installer's `--recover`; see [retention and recovery](OPERATIONS.md#installed-application-lifecycle).

For source deployments, ordinary recovery is a checked revert or forward-fix PR to `main`, followed by normal
automatic activation and verification of the affected API/UI behavior. Record the bad and
known-good SHAs, the symptom, the recovery PR and its check/activation evidence. Keep main and
its deployment checkout under the normal Git guards; do not reset that checkout to a tag or
force-push a release rollback around the PR path.

The restart helper stages/builds the web app and restores the prior web bundle if verification
fails. That restores only the bundle: Python source, task records, conversations and provider
sessions are not rolled back. Check state compatibility before reverting code. A forward fix
is preferable when earlier code cannot read the current persisted records.

Before publishing an incompatible storage change, its release owner documents the affected
records and obtains explicit authorization for a consistent private backup and any lifecycle
actions needed to take it. Include provider-owned data only when needed and permitted. Rehearse
restoring that snapshot in disposable storage with the intended code version, verify representative
task/message/archive reads and writes, and retain the private backup until recovery is verified.
An actual restore needs separate authorization, a record of the current state it replaces and
post-restore API/UI checks. Never overwrite live state from a test or infer that a code revert
undoes its data changes. This test/release policy does not install a backup system or change
activation, service permissions or state formats.
