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

During active preview development, L3 proposes a patch release whenever validated fixes have
accumulated on main, at least daily, and the operator approves its version and SHA. Several patch
releases in one day are reasonable when fixes warrant them; there is no weekly wait or mandatory
overnight observation period. Every candidate passes the complete gate and targeted validation.
Higher-risk lifecycle, permissions or storage changes include an observation interval chosen for
their failure modes and recorded in the readiness record. A checkpoint can conclude that no release
is ready. There is no scheduled publication, release branch or freeze of unrelated development.

Tags are the source version authority: `v0.MINOR.PATCH`, beginning with `v0.1.0`. Every release
increments PATCH, whatever it contains. MINOR increments, resetting PATCH, only when the operator
explicitly instructs it. Breaking behavior is described even during `0.x`. Candidate labels use
`v0.1.0-rc.1`, incrementing `rc.N` when the candidate changes. Before tag publication, the candidate
label is just a label in the readiness record. Never move a published tag. The web package's private
build metadata is not a separate product version.

## Candidate and release gates

This repository's required GitHub-hosted PR `check` runs the full `make check` gate with candidate
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
   deterministic gate on the final SHA before publishing it. The operator publishes it, or asks a
   task owner to and grants it ([publish a release](#publish-a-release)). Use the same source for
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
recorded ref heads reach, so re-checking shortly before publication costs seconds. A recorded head
this clone lacks (pruned since, or recorded by another clone) is skipped and counted as
`since_heads_missing`, which only widens the scan. GitHub keeps pull-request refs (`refs/pull/*`)
that a normal clone does not fetch and that become visible with the repository; scan a temporary
`git clone --mirror` of the repository with `scripts/audit_history.py --repo <mirror>` to cover
them. Inspect the
findings file directly; the audit record (task report, issue #233) carries the cutoff, the rules, the
counts and the conclusion, never the matches. Regular expressions and a word list find shaped and
known material, binary blobs get only path and size checks, and objects no ref reaches are not
scanned: a clean run is evidence within that coverage, not a guarantee. Bring any remediation
(history rewrite, credential rotation, file removal, visibility change) to the operator as a
decision; the audit itself changes nothing.

## Publish a release

Pushing a `v0.*` tag runs `.github/workflows/release.yml` on a GitHub-hosted runner with GitHub's
own token and no other secrets; it never creates a tag. The job refuses to publish unless the tag's
commit is on `main`, that exact commit has a successful push `check` run of
`.github/workflows/hosted-checks.yml`, and `CHANGELOG.md` has the version's dated section. It then
builds the release files from the tag, attests their build provenance and creates the GitHub release
with that section as its notes. A `-rc.N` tag is published as a prerelease; any other version
becomes the latest release, which the [one-command install](SETUP.md#install-the-application)
fetches.

```sh
git tag <version> <approved SHA>
git push origin <version>
```

A tag ruleset lets only the operator's GitHub account create, move or delete `v*` tags, and immutable
releases keep a published release's tag and files unchanged. Workers and granted commands use that
same signed-in account, so the ruleset limits the account, not the person or task behind it; the
operator's approval is their recorded decision, not the tag itself. GitHub offers artifact attestations to private repositories only on Enterprise
plans, so the job attests once the repository is public and skips that step while it is private.
Release files cannot be downloaded without signing in while the repository is private.

A failed job publishes nothing; fix the cause and re-run the job. A release that published wrong
content is followed by a new version, never by moving its tag or replacing its files.

### Publication by a granted task owner

When the operator asks a task to publish an approved version and SHA, the owner asks for an
[operator grant](CLI.md#operator-grant) naming the repository, version, SHA and these steps, then runs
each step with `alt task run`. This path needs no hosted build or Actions billing and provides no
hosted build attestation, so its notes say so.

1. Build the files from the approved commit into a new directory in the task folder:
   `python3.12 scripts/build_release.py --version <version> --source <SHA> --output <dir> --notes <dir>-notes.md`.
2. Create a draft that targets the approved commit, with every file attached:
   `gh release create <version> --draft --target <SHA> --title 'Altitude <version>' --notes-file <dir>-notes.md`
   plus `--prerelease` for `-rc.N`, followed by the files in `<dir>`.
3. Read the draft back and compare every asset's name and size with the build, then publish it with
   `gh release edit <version> --draft=false`. GitHub creates the tag with the release, so no tag is
   pushed before the files are in place. The tag still starts the release workflow, which fails
   because the release already exists and changes nothing; that failed run is expected on this path.
4. Read back the published release: its tag's commit is the approved SHA and every asset's digest
   matches `SHA256SUMS`.

The owner stops and reports at any mismatch, at an existing tag or release for that version that
does not match, or when a guard refuses a step; it never moves a tag, deletes a release or replaces
a file. A step GitHub itself refuses, such as a ruleset or billing limit, is the operator's to take.

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

Record archive checksum and install/update/recovery evidence alongside candidate checks. The
runtime targets are Ubuntu 24.04 x86_64 and macOS 15 or newer on Apple silicon. `make installation-vm`
runs a release's install, update, recovery and uninstall in a disposable Linux VM, and
[`make installation-mac`](DEVELOPMENT.md#macos-installation-lane) runs them on the Mac under a
throwaway home. Deterministic fixtures do not establish fresh-machine, browser trust or live
provider compatibility. No public support claim precedes that evidence.

For reproducible Linux installation evidence, dispatch the
[installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance) on main with
the selected source SHA as `source_ref`, or run the same harness in a
[local VM](DEVELOPMENT.md#local-vm-run) with `make installation-vm RESULTS=dir SOURCE=<sha>`. It builds two synthetic version labels from that same
commit and exercises real installation, service activation, HTTPS, update, failed activation recovery
and retained-data uninstall; the local VM also runs the built `install.sh` against a release server
inside the guest. This establishes no cross-release data migration or download from the published
GitHub release. After publication, `BASELINE=<tag>` repeats the run with the published release as the
baseline: its exact `install.sh` and archive install in the offline guest and update to the candidate; with `RECOVERY=1` the candidate instead installs over the published release's failed installation after the documented cleanup.
With `PUBLIC=1` the guest instead installs `releases/latest` and the baseline through setup's command
from GitHub itself, and the baseline's daemon must offer the latest release and `alt update` install it. Record its run URL or `vm.json`, runner environment, artifact hashes and actual results separately
from required candidate checks. Availability of either entry point alone is not executed acceptance
or a new release gate.

On an Apple silicon Mac, `make installation-macos-vm RESULTS=dir SOURCE=<sha>` runs the built
`install.sh` through its public command in fresh macOS guests ([macOS VM run](DEVELOPMENT.md#macos-vm-run)):
each missing prerequisite must stop it with its documented fix and nothing changed, and with them
present the release installs, updates to newer stable releases, recovers from a failing one, starts
again at login after a restart and uninstalls. Record its `macos-vm.json` files with the candidate's
evidence. It establishes no physical second Mac, browser/device certificate trust or download from
GitHub.

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
