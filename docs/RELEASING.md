# Release checkpoints

The [container candidate](CONTAINERS.md) consumes this same checksummed application archive, including
its container packaging. Image labels retain release/source/archive identity. Building an image neither
publishes it nor establishes runtime or architecture acceptance. Native release/installer publication
and its optional hosted workflow remain independent of container validation.
Each rebuilt image also needs bootstrap/admission evidence: an empty-volume first start, preserved
same-container restart state, replacement requiring host Continue, and protected receipt writes.
Passing an earlier image gate does not validate later lifecycle changes.

Altitude uses versioned archives and source checkpoints. A release identifies a validated commit
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

This repository's required self-hosted PR `check` runs the full `make check` gate with candidate
SHA/tree evidence. The [delivery policy](DEVELOPMENT.md#ci-and-candidate-identity) preserves review
and publication decisions. The separately dispatched
[installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance) is optional
acceptance evidence; its pending or failed runs on main do not gate PR delivery or release publication.

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
   Approval names the version and SHA. Then deliver the dated changelog section
   (`## v0.1.0 — 2026-10-01`) through a PR. If that PR changes the release SHA, repeat the
   deterministic gate on the final SHA before publishing it. The operator publishes directly or
   authorizes the task owner through a recorded [release grant](#publish-a-release). Use the same source for
   notes and tag; an early preview remains labeled as such.

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

## Publish a release

The operator approves a repository, version and commit. A task owner records that approval with
[`alt task publish`](CLI.md#release-publication); the fixed command publishes only that release.
Direct approval names the version and SHA; a contextual answer cites the exact question revision
that names them. An unambiguous short SHA resolves once to the full commit. The owner records its
task-chat answer; L3 records an answer from project chat. Both read the original answer and later
corrections, including conditions or revocation, before applying it.

### Approved task owner

Build from the approved commit, retaining the build evidence. Use a new asset subdirectory inside
the task folder or its worktree, containing exactly the five output files. Keep notes and build
evidence outside that directory. Replace `<task>` with the task's literal slug:

```sh
python3.12 scripts/build_release.py --version v0.1.0-rc.2 --source <approved SHA> --output <task-folder>/release-files
alt task publish <task> --grant --approval <message-id> --version v0.1.0-rc.2 --sha <approved SHA> --files <task-folder>/release-files --reason 'Publish the approved candidate'
```

Altitude binds the grant to the task, attempt, repository, full SHA, version, committed changelog
notes, validated asset paths and hashes. It verifies archive identity, manifest, installer and
checksums; publication captures unchanged bytes for upload instead of storing them with the grant.
These hashes establish integrity; the manual path trusts the owner to build the approved source
and supplies no independent build attestation. Publication appends this disclosure to the dated
changelog notes: “Manual publication from an operator-approved owner build. Checksums verify
integrity; this release has no hosted build attestation.” Retain that limit in the readiness record.

When a grant is recorded during a turn, the owner checkpoints and parks for L3 to resume the same
attempt so the engine loads its narrow command permission. The resumed owner uses the exact
commands shown by the grant result, replacing `<task>` with its literal slug:

```sh
alt task publish <task> --check
alt task publish <task>
```

Shell-variable or quoted variants are not promised to match the exact native permission rule.
`--check` verifies scope and prerequisites without writing to GitHub. Publish requires the actual
current owner process, the approved commit on main, its successful push-event `check` job and the
version's dated changelog section. It creates a draft targeting that commit, uploads and verifies
the captured assets, then publishes. There is no preliminary tag push. Read-back verifies the tag
and every asset. The title is `Altitude <version>`; `-rc.N` is a prerelease and never latest.

Every attempt and external phase is recorded on the task. Interrupted publication reconciles
the task's recorded draft before retrying; it does not adopt foreign drafts or blindly repeat
uncertain writes. A repository/version ledger retains task ownership across daemon restarts;
only a definite create refusal with no remote side effect releases it. Existing conflicting
tags/releases and a tag with an active publisher refuse.
The grant cannot move/delete tags, replace assets, edit unrelated releases or change immutable
release settings. Owner, L3 or operator can revoke it; deadlines, a new task attempt or task
completion end it, and successful publication consumes it. Revocation cannot recall a request
already sent. A replacement grant preserves the original approval's historical identity and
deadline. See the [CLI contract](CLI.md#release-publication) for recovery and inspection.

The native permission rule is generated at launch/resume without editing user settings or
weakening inherited policy. A successful check does not prove the separate publish invocation
will be admitted. Actual Claude auto-mode publish acceptance and GitHub event ordering remain
unverified until the pending rc.2 owner's first use; a refusal stops publication and is reported.
Native macOS confirmation is also pending under [the native-runtime work](ROADMAP.md#native-macos-runtime)
(#225). Deterministic fixtures establish neither native engine acceptance nor a live publication.

### Operator by hand or hosted workflow

Pushing a `v0.*` tag runs `.github/workflows/release.yml` on a GitHub-hosted runner with GitHub's
own token and no other secrets; it never creates a tag. The job refuses to publish unless the
tag's commit is on `main`, that exact commit has a successful push `check` run of the self-hosted
workflow, and `CHANGELOG.md` has the version's dated section. It then builds the release files from
the tag, attests their build provenance and creates the GitHub release with that section as its
notes. A `-rc.N` tag is published as a prerelease; any other version becomes the latest release,
which the [one-command install](SETUP.md#install-the-application) fetches.

```sh
git tag v0.1.0 <approved SHA>
git push origin v0.1.0
```

Workers and manual commands use the operator's signed-in GitHub account. A tag ruleset restricts
that account, not the human or worker behind it; the tag alone does not establish who approved
publication. The release grant retains the operator's approval provenance for Altitude's supported
path and does not control arbitrary GitHub calls made with that account. Immutable releases keep
a published release's tag and files unchanged. The private repository's plan does not expose its
tag-ruleset configuration through the inspected API, so effective ruleset admission remains
first-use evidence. GitHub offers artifact attestations to private repositories only on Enterprise
plans, so the job attests once the repository is public and skips that step while it is private.
Release files cannot be downloaded without signing in while the repository is private.

A by-hand publication uses the same five built files and committed notes: create a draft with
`gh release create --draft --target <approved SHA>`, upload and verify every asset, then publish
with `gh release edit --draft=false`. Keep its tag, title and prerelease/latest treatment consistent
with the approved version. This path needs no hosted build or Actions billing and provides no hosted
build attestation; disclose that limit in its notes. Existing tag-triggered workflow behavior and
costs remain as configured. A manual publication introduces the tag only when the uploaded release
is ready; a resulting workflow encounters that already-published release.

A failed job publishes nothing; fix the cause and re-run the job. A release that published wrong
content is followed by a new version, never by moving its tag or replacing its files.

## Build the release files

From the clean, committed candidate checkout, with the locked web build tools available:

```sh
python3.12 scripts/build_release.py --version v0.1.0-rc.1 --output /tmp/altitude-release
```

The release workflow runs the same command with `--notes`, which also writes the version's changelog
section and refuses a version without one. The builder exports the exact Git revision, installs
frozen web dependencies and builds the UI, then emits the application archive, its SHA-256 file,
`install.py`, `install.sh` and `SHA256SUMS`. `install.sh` is `scripts/install.sh` with the version,
repository and the SHA-256 of the archive and `install.py` filled in; the unfilled template refuses
to run. The manifest records source identity and every packaged file hash. The archive contains the
CLI, Python daemon, built UI, personas, hooks, templates, schemas, the license and third-party
notices; users need no source build. Existing archive names are immutable. Building the files by
hand creates no tag, GitHub release or public publication.

Record archive checksum and install/update/recovery evidence alongside candidate checks. The initial
runtime target is Ubuntu 24.04 x86_64; on macOS, `install.sh` stops before downloading anything
until the native runtime and host validation exist. Deterministic fixtures do not establish
physical Mac, fresh-machine, browser trust or live provider compatibility. No public support claim
precedes that evidence.

For reproducible Linux installation evidence, dispatch the
[installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance) on main with
the selected source SHA as `source_ref`, or run the same harness in a
[local VM](DEVELOPMENT.md#local-vm-run) with `make installation-vm RESULTS=dir SOURCE=<sha>`. It builds two synthetic version labels from that same
commit and exercises real installation, service activation, HTTPS, update, failed activation recovery
and retained-data uninstall; the local VM also runs the built `install.sh` against a release server
inside the guest. This establishes no cross-release data migration or download from the published
GitHub release. After publication, `BASELINE=<tag>` repeats the run with the published release as the
baseline: its exact `install.sh` and archive install in the offline guest and update to the candidate
(the guest's own download from GitHub stays unexercised); with `RECOVERY=1` the candidate instead installs over the published release's failed installation after the documented cleanup. Record its run URL or `vm.json`, runner environment, artifact hashes and actual results separately
from required candidate checks. Availability of either entry point alone is not executed acceptance
or a new release gate.

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
