# Trusted remote test gate

Authoritative test evidence for Altitude is remote-only. Local `make test` is a developer diagnostic: it runs
candidate-controlled Make and Python in the operator's security context, so it cannot authorize a merge and must
never be called by `alt land`, L1/L2/L3 automation, or a landing fallback.

## Bootstrap trust

This first slice contains only the workflow, the base-trusted launcher and test selectors, focused tests, and this
contract. It deliberately does not change `alt land`. The bootstrap cannot attest itself: it requires independent
static review and a manual merge while the platform remains stopped. Only later changes may rely on the merged base
version of the harness. Landing integration is a separate slice and must remove every local and no-check fallback.

The workflow uses `pull_request_target`, so executable gate code comes from the PR's exact base SHA and not its
head. It also supports trusted `main` push runs and explicit dispatch retests after bootstrap. A dispatch supplies a
current PR number, fork repository, candidate SHA, expected current-main SHA, and a unique 64-hex request nonce; the
base-owned resolver checks those values against the current PR API response before candidate checkout. Permissions
are limited to `actions: read`, `contents: read`, and `pull-requests: read`; no repository secrets are referenced.
Both checkouts disable persisted credentials, LFS, submodules, and global safe-directory mutation.

The candidate is fetched through the base repository's immutable PR head ref and its resolved commit must equal the
API head SHA. It remains inert before containment. Candidate Makefiles, shell, C, hooks, local actions, dependencies,
and import paths are not executed by the privileged setup. Trusted Git, with system/global config disabled, packages
candidate source and base tests as tar data. Only base-owned PID-1 source is compiled.

Every action is pinned to a full commit SHA:

- `actions/checkout@11d5960a326750d5838078e36cf38b85af677262`;
- `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`.

## Remote containment

The base launcher fails closed unless the GitHub host provides root, cgroup v2 with writable controllers,
util-linux namespaces, chroot/mount tools, `ip`, a C compiler, and the expected Ubuntu `/usr` runtime. It creates
mount, PID, network, IPC, UTS, and cgroup namespaces and enforces:

- a private 3 GiB tmpfs root with no inherited workspace, home, `/root`, `/run`, `/tmp`, sysfs, or host `/dev`;
- a read-only nodev/nosuid overlay view of `/usr`, with distinct BSD-flock and POSIX-record-lock state from the lower
  host inode, plus copied minimal identity and loader files;
- a fresh `hidepid=2` procfs, a minimal `/dev`, bounded `/dev/shm`, and loopback as the only network interface;
- an exact six-mount allowlist whose normalized targets, filesystems, and required/forbidden options are recorded;
- a cgroup-v2 ceiling of 256 processes, 2 GiB memory, no swap, two CPUs of quota, and an absolute 20-minute deadline;
- fixed phase budgets totalling 1,100 seconds, reserving 100 seconds for mandatory teardown;
- `/dev/null` stdin and bounded pipes owned by trusted PID 1 for stdout/stderr, with no other child descriptors;
- empty supplementary groups, a dedicated UID/GID, empty capability bounding/inheritable/permitted/effective/ambient
  sets, `dumpable=0`, and `no_new_privs` before test commands;
- separate writable HOME, temporary, XDG, Altitude, and jobs state for the base-suite and candidate-suite phases;
- strict socket/socketpair domains limited to Unix and IPv4/IPv6, with a private network namespace containing only
  loopback; AF_VSOCK, legacy socketcall, and every io_uring syscall are denied; and
- denial of privileged namespace, mount, ptrace, keyring, BPF, perf, reboot, and kernel-loading operations.

The trusted PID 1 owns the log and result descriptors, applies the absolute and phase deadlines, kills command
process groups, and reaps descendants. The root parent requires `cgroup.kill`, proves the cgroup empty on success and
on every cleanup path, and refuses to discard its state when that proof fails. Candidate content is extracted only
inside the completed boundary by the unprivileged test UID.

## Selection and exact-pair evidence

No candidate runner is consulted. Fixed base-owned code directly runs:

1. boundary probes for identity, capabilities, mounts, devices, descriptors, BSD/POSIX lock separation, raw x32
   syscall rejection, socket-family restrictions, io_uring rejection, network isolation, fork, and reaping;
2. the exact base tests over one candidate source copy; and
3. candidate tests separately from a second candidate source copy.

Both suites must execute at least one non-skipped test. Zero-test and all-skipped results fail in PID 1, the trusted
runner, the root launcher, and the manifest verifier.

The root parent owns all result paths. The artifact name is
`trusted-remote-evidence-<run_id>-<run_attempt>-<request_nonce>`. Its strict manifest binds:

- base and candidate repository names and numeric ids, immutable PR id and current PR number, event/action, run
  id/attempt, Actions job
  (check-run) id, workflow path/SHA, request nonce, artifact name, and exact base/candidate SHAs;
- deterministic base-harness component hashes plus recomputed candidate archive, base-tests archive, and PID-1
  binary hashes and sizes;
- exact trusted commands, resource/phase limits, suite statuses and counts, and boundary status; and
- digest and size of the bounded output log and mandatory normalized mount attestation.

The base-owned verifier recomputes those digests from independent trusted inputs and rejects duplicate/unknown
schema fields, identity disagreement, mount-option disagreement, missing evidence, and all-skipped suites. This is
base-attached exact-pair run evidence. It is never a candidate-head check, and a green check attached to another
base, run attempt, job, workflow, artifact, nonce, or PR tuple is not reusable.

This is a containment and ordinary-correctness gate, not Byzantine semantic attestation. Candidate Python can try
to interfere with an in-process unittest framework or its text protocol; an ordinary test runner cannot
cryptographically prove program semantics against the program under test. The manifest records what the
base-owned supervisor observed. Code review remains responsible for deliberate semantic subversion. This does not
relax namespace, credential, filesystem, process, network, or resource containment.

## Later landing contract

This bootstrap does not implement landing. A later `alt land` change must fail closed while it revalidates through
the Actions and pull-request APIs:

- the exact workflow run id and attempt, named job and check-run id, successful conclusion, workflow SHA, and
  non-expired artifact id/name/digest;
- the downloaded manifest and all three evidence files against the current base repository/id/main SHA, immutable PR
  id and current PR number, current candidate repository/id/head SHA, dispatch nonce when applicable, and exact
  tested pair;
- that the tested base is an ancestor of the candidate; and
- that neither main nor the PR head changed immediately before publication.

This private personal repository has no branch protection, ruleset, or merge queue to serialize that publication.
Landing must therefore update main with an exact-base compare-and-swap fast-forward: after proving ancestry, push
the candidate SHA using a lease for exactly the tested base SHA. A moved base or head is a rejection and requires a
new remote run; a merge button or candidate-attached status cannot substitute for the evidence artifact. The
exact-base lease atomically eliminates a concurrent main-base update. GitHub provides no corresponding atomic CAS
over a fork PR head or an Actions rerun attempt, so landing must revalidate both immediately before publication.
Late movement of either is a provenance/revalidation event that can reject or supersede the evidence; it cannot
change the literal tested candidate commit selected for the leased push.

## Hosted-run assumptions

This bootstrap checkpoint is intentionally limited to shell syntax, strict C compilation, and focused unit/static
tests. It is not pushed, and its root boundary is not run locally. Independent review and the first hosted bootstrap
run must confirm Ubuntu 24.04 permits root cgroup creation/controller writes, read-only overlayfs, the exact
mount/chroot operations, `hidepid=2`, fixed numeric UID/GID, and action job API correlation while the current job is
running. Any unavailable prerequisite fails the check; it is not permission to weaken the boundary. The private
Actions control plane, pinned official actions, reviewed base harness, hosted kernel/hypervisor, and read-only `/usr`
runtime remain in the external trust base.
