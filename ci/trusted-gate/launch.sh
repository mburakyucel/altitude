#!/usr/bin/env bash
# Base-trusted root launcher. Candidate content is accepted only as a preverified inert tar stream.
set -euo pipefail
umask 077

CHECKOUT_ACTION_SHA=11d5960a326750d5838078e36cf38b85af677262
UPLOAD_ACTION_SHA=ea165f8d65b6e75b540449e92b4886f43607fa02
GATE_UID=23456
GATE_GID=23456
LOG_LIMIT=16777216
CLEANUP_STAGE=
CLEANUP_CGROUP=
CLEANUP_PARENT_CGROUP=

die() {
    printf 'trusted remote gate: %s\n' "$*" >&2
    exit 97
}

cleanup_outer() {
    local status=$?
    trap - EXIT
    set +e
    local cgroup_clean=1
    if [ -n "$CLEANUP_CGROUP" ] && [ -d "$CLEANUP_CGROUP" ] && \
        [ -n "$CLEANUP_PARENT_CGROUP" ] && [ -d "$CLEANUP_PARENT_CGROUP" ]; then
        kill_empty_remove_cgroup "$CLEANUP_CGROUP" "$CLEANUP_PARENT_CGROUP" || cgroup_clean=0
    fi
    if [ "$cgroup_clean" -eq 1 ] && [ -n "$CLEANUP_STAGE" ]; then
        case "$CLEANUP_STAGE" in
            /var/tmp/altitude-trusted-gate.*)
                if [ -d "$CLEANUP_STAGE" ] && [ ! -L "$CLEANUP_STAGE" ] && \
                    [ "$(/usr/bin/stat -Lc '%u' "$CLEANUP_STAGE")" -eq 0 ]; then
                    /usr/bin/rm -rf -- "$CLEANUP_STAGE"
                fi
                ;;
        esac
    fi
    if [ "$cgroup_clean" -ne 1 ]; then
        printf 'trusted remote gate: cgroup cleanup could not be proven\n' >&2
        status=97
    fi
    exit "$status"
}

cgroup_population() {
    /usr/bin/awk '
        $1 == "populated" && $2 ~ /^[01]$/ { count += 1; value = $2 }
        END { if (count == 1) print value; else exit 1 }
    ' "$1"
}

kill_empty_remove_cgroup() {
    local cgroup=$1 parent=$2 population wait_count=0
    [ -d "$cgroup" ] && [ -d "$parent" ] || return 1
    [ -r "$cgroup/cgroup.events" ] && [ -w "$cgroup/cgroup.kill" ] || return 1
    printf '%s\n' "$$" > "$parent/cgroup.procs" || return 1
    printf '1\n' > "$cgroup/cgroup.kill" || return 1
    while :; do
        population=$(cgroup_population "$cgroup/cgroup.events") || return 1
        [ "$population" = 0 ] && break
        [ "$wait_count" -lt 100 ] || return 1
        /usr/bin/sleep 0.05
        wait_count=$((wait_count + 1))
    done
    /usr/bin/rmdir "$cgroup" || return 1
}

require_sha() {
    case "$2" in
        *[!0-9a-f]*|'') die "$1 is not a lowercase hexadecimal SHA" ;;
    esac
    [ "${#2}" -eq 40 ] || die "$1 is not a full commit SHA"
}

require_sha256() {
    case "$2" in *[!0-9a-f]*|'') die "$1 is not a lowercase hexadecimal SHA-256" ;; esac
    [ "${#2}" -eq 64 ] || die "$1 is not a full SHA-256"
}

require_number() {
    case "$2" in *[!0-9]*|'') die "$1 is not an unsigned integer" ;; esac
}

require_positive_number() {
    require_number "$1" "$2"
    [ "$2" -gt 0 ] || die "$1 is not positive"
}

require_repository() {
    local owner name
    case "$2" in
        */*) ;;
        *) die "$1 is not owner/repository" ;;
    esac
    case "$2" in *[!A-Za-z0-9_.\/-]*|*/*/*|'') die "$1 contains invalid characters" ;; esac
    owner=${2%%/*}
    name=${2#*/}
    [ -n "$owner" ] && [ -n "$name" ] || die "$1 has an empty owner or repository"
}

require_mount_option() {
    case ",$1," in *",$2,"*) ;; *) die "$3 lacks mount option $2" ;; esac
}

forbid_mount_option() {
    case ",$1," in *",$2,"*) die "$3 unexpectedly has mount option $2" ;; *) ;; esac
}

normalized_mount_options() {
    printf '%s\n' "$1" | /usr/bin/tr ',' '\n' | /usr/bin/sort | /usr/bin/paste -sd, -
}

regular_input() {
    local path=$1
    [ -f "$path" ] && [ ! -L "$path" ] || die "input is not a regular non-symlink: $path"
    [ "$(/usr/bin/stat -Lc '%u' -- "$path")" = "${SUDO_UID:?}" ] || die "input owner is not the workflow user"
    local mode
    mode=$(/usr/bin/stat -Lc '%a' -- "$path")
    [ $((8#$mode & 8#022)) -eq 0 ] || die "input is group/world writable: $path"
}

bounded_input() {
    local path=$1 maximum=$2 size
    size=$(/usr/bin/stat -Lc '%s' -- "$path")
    [ "$size" -gt 0 ] && [ "$size" -le "$maximum" ] || die "input size is outside its bound: $path"
}

close_extra_fds() {
    local path fd
    for path in /proc/$$/fd/*; do
        fd=${path##*/}
        case "$fd" in
            0|1|2) ;;
            *[!0-9]*|'') ;;
            *) eval "exec ${fd}>&-" ;;
        esac
    done
}

mount_exactly() {
    local root=$1 attestation=$2
    local actual expected root_options usr_options dev_options shm_options proc_options gate_options
    actual=$(/usr/bin/findmnt -R -n -o TARGET -- "$root" | /usr/bin/sort)
    expected=$(printf '%s\n' "$root" "$root/dev" "$root/dev/shm" "$root/gate" "$root/proc" "$root/usr" | /usr/bin/sort)
    [ "$actual" = "$expected" ] || die "minimal-root mount set is not exact"
    /usr/bin/findmnt -n -o FSTYPE --target "$root" | /usr/bin/grep -qx tmpfs || die "minimal root is not tmpfs"
    /usr/bin/findmnt -n -o FSTYPE --target "$root/usr" | /usr/bin/grep -qx overlay || die "/usr is not an isolated overlay"
    /usr/bin/findmnt -n -o OPTIONS --target "$root/usr" | /usr/bin/grep -Eq '(^|,)ro(,|$)' || die "/usr is writable"
    /usr/bin/findmnt -n -o FSTYPE --target "$root/dev" | /usr/bin/grep -qx tmpfs || die "/dev is not private tmpfs"
    /usr/bin/findmnt -n -o FSTYPE --target "$root/dev/shm" | /usr/bin/grep -qx tmpfs || die "/dev/shm is not private tmpfs"
    /usr/bin/findmnt -n -o FSTYPE --target "$root/proc" | /usr/bin/grep -qx proc || die "/proc is not private procfs"
    [ "$stage/gate" -ef "$root/gate" ] || die "/gate is not the exact trusted bind source"

    root_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root")
    usr_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root/usr")
    dev_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root/dev")
    shm_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root/dev/shm")
    proc_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root/proc")
    gate_options=$(/usr/bin/findmnt -n -o OPTIONS --target "$root/gate")
    require_mount_option "$root_options" rw root
    require_mount_option "$root_options" nosuid root
    require_mount_option "$root_options" nodev root
    forbid_mount_option "$root_options" noexec root
    for option in ro nosuid nodev lowerdir=/usr; do require_mount_option "$usr_options" "$option" usr; done
    forbid_mount_option "$usr_options" rw usr
    for option in rw nosuid nodev noexec; do require_mount_option "$dev_options" "$option" dev; done
    for option in rw nosuid nodev noexec; do require_mount_option "$shm_options" "$option" dev-shm; done
    for option in rw nosuid nodev noexec hidepid=2; do require_mount_option "$proc_options" "$option" proc; done
    for option in rw nosuid nodev noexec; do require_mount_option "$gate_options" "$option" gate; done

    {
        printf 'schema=1\n'
        printf 'root\ttmpfs\t%s\n' "$(normalized_mount_options "$root_options")"
        printf 'usr\toverlay\t%s\n' "$(normalized_mount_options "$usr_options")"
        printf 'dev\ttmpfs\t%s\n' "$(normalized_mount_options "$dev_options")"
        printf 'dev-shm\ttmpfs\t%s\n' "$(normalized_mount_options "$shm_options")"
        printf 'proc\tproc\t%s\n' "$(normalized_mount_options "$proc_options")"
        printf 'gate\tbind\t%s\n' "$(normalized_mount_options "$gate_options")"
    } > "$attestation"
}

inner() {
    [ "$(/usr/bin/id -u)" -eq 0 ] || die "inner launcher is not root"
    local stage=${ALTITUDE_GATE_STAGE:?}
    local root=$stage/root
    case "$stage" in /var/tmp/altitude-trusted-gate.*) ;; *) die "unsafe stage" ;; esac
    [ -d "$stage" ] && [ ! -L "$stage" ] && [ "$(/usr/bin/stat -Lc '%u' "$stage")" -eq 0 ] || die "stage changed"
    [ "$(/usr/bin/sha256sum "$stage/input/candidate.tar" | /usr/bin/cut -d' ' -f1)" = "${ALTITUDE_GATE_CANDIDATE_ARCHIVE_SHA256:?}" ] || die "candidate archive changed"
    [ "$(/usr/bin/sha256sum "$stage/input/base-tests.tar" | /usr/bin/cut -d' ' -f1)" = "${ALTITUDE_GATE_BASE_ARCHIVE_SHA256:?}" ] || die "base-tests archive changed"
    [ "$(/usr/bin/sha256sum "$stage/trusted/pid1" | /usr/bin/cut -d' ' -f1)" = "${ALTITUDE_GATE_PID1_SHA256:?}" ] || die "PID 1 binary changed"

    /usr/bin/mount --make-rprivate /
    /usr/bin/mkdir -p "$root"
    /usr/bin/mount -t tmpfs -o mode=0755,nodev,nosuid,size=3G altitude-gate-root "$root"
    /usr/bin/mkdir -p "$root/usr" "$root/etc" "$root/dev/shm" "$root/proc" "$root/tmp" "$root/run" \
        "$root/home/altitude" "$root/root" "$root/work" \
        "$root/inputs" "$root/opt/gate" "$root/gate"
    /usr/bin/chmod 0755 "$root/etc" "$root/work" "$root/inputs" "$root/opt" "$root/opt/gate"
    /usr/bin/chmod 0700 "$root/root" "$root/gate"
    /usr/bin/chmod 1777 "$root/tmp"
    /usr/bin/chown "$GATE_UID:$GATE_GID" "$root/home/altitude"

    # A read-only overlay gives the test namespace distinct inodes for advisory
    # locks while retaining the runner image's immutable runtime contents.
    /usr/bin/mount -t overlay -o lowerdir=/usr,ro,nosuid,nodev altitude-gate-usr "$root/usr"
    /usr/bin/ln -s usr/bin "$root/bin"
    /usr/bin/ln -s usr/sbin "$root/sbin"
    /usr/bin/ln -s usr/lib "$root/lib"
    [ ! -d /usr/lib64 ] || /usr/bin/ln -s usr/lib64 "$root/lib64"

    /usr/bin/install -m 0644 /etc/ld.so.cache "$root/etc/ld.so.cache"
    /usr/bin/install -m 0644 /etc/nsswitch.conf "$root/etc/nsswitch.conf"
    printf 'altitude-gate:x:%s:%s:Altitude gate:/home/altitude:/usr/sbin/nologin\n' "$GATE_UID" "$GATE_GID" \
        > "$root/etc/passwd"
    printf 'altitude-gate:x:%s:\n' "$GATE_GID" > "$root/etc/group"

    /usr/bin/mount -t tmpfs -o mode=0755,nosuid,noexec,size=16M altitude-gate-dev "$root/dev"
    /usr/bin/mkdir -p "$root/dev/shm"
    /usr/bin/mknod -m 0666 "$root/dev/null" c 1 3
    /usr/bin/mknod -m 0666 "$root/dev/zero" c 1 5
    /usr/bin/mknod -m 0666 "$root/dev/full" c 1 7
    /usr/bin/mknod -m 0666 "$root/dev/random" c 1 8
    /usr/bin/mknod -m 0666 "$root/dev/urandom" c 1 9
    /usr/bin/mount -t tmpfs -o mode=1777,nodev,nosuid,noexec,size=64M altitude-gate-shm "$root/dev/shm"
    /usr/bin/mount -t proc -o hidepid=2,nosuid,nodev,noexec proc "$root/proc"

    /usr/bin/mount --bind "$stage/gate" "$root/gate"
    /usr/bin/mount -o remount,bind,rw,nosuid,nodev,noexec "$root/gate"
    /usr/bin/install -m 0444 "$stage/input/candidate.tar" "$root/inputs/candidate.tar"
    /usr/bin/install -m 0444 "$stage/input/base-tests.tar" "$root/inputs/base-tests.tar"
    /usr/bin/install -m 0555 "$stage/trusted/pid1" "$root/opt/gate/pid1"
    /usr/bin/install -m 0444 "$stage/trusted/trusted_runner.py" "$root/opt/gate/trusted_runner.py"
    /usr/bin/install -m 0444 "$stage/trusted/boundary_selftest.py" "$root/opt/gate/boundary_selftest.py"

    /usr/sbin/ip link set lo up
    /usr/bin/hostname altitude-test
    mount_exactly "$root" "$stage/gate/mounts.txt"

    # Hold a lock on the host lower inode across PID 1. The unprivileged
    # boundary probe must still be able to lock the overlay inode, proving that
    # candidate locks cannot contend with the runner's /usr inodes.
    local lower_usr_lock_fd
    exec {lower_usr_lock_fd}<>/usr/bin/python3
    /usr/bin/flock --exclusive --nonblock "$lower_usr_lock_fd" || die "cannot pin lower /usr lock probe"

    local current_netns current_pidns current_mntns current_ipcns current_utsns current_cgroupns
    current_netns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/net)
    current_pidns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/pid)
    current_mntns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/mnt)
    current_ipcns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/ipc)
    current_utsns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/uts)
    current_cgroupns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/cgroup)
    [ "$current_netns" != "${ALTITUDE_GATE_HOST_NETNS_INO:?}" ] || die "network namespace was not created"
    [ "$current_pidns" != "${ALTITUDE_GATE_HOST_PIDNS_INO:?}" ] || die "PID namespace was not created"
    [ "$current_mntns" != "${ALTITUDE_GATE_HOST_MNTNS_INO:?}" ] || die "mount namespace was not created"
    [ "$current_ipcns" != "${ALTITUDE_GATE_HOST_IPCNS_INO:?}" ] || die "IPC namespace was not created"
    [ "$current_utsns" != "${ALTITUDE_GATE_HOST_UTSNS_INO:?}" ] || die "UTS namespace was not created"
    [ "$current_cgroupns" != "${ALTITUDE_GATE_HOST_CGROUPNS_INO:?}" ] || die "cgroup namespace was not created"

    exec /usr/sbin/chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 \
        HOME=/home/altitude TMPDIR=/tmp XDG_RUNTIME_DIR=/run ALTITUDE_HOME=/home/altitude/.altitude \
        ALTITUDE_GATE_UID="$GATE_UID" ALTITUDE_GATE_GID="$GATE_GID" ALTITUDE_TRUSTED_REMOTE_TEST=1 \
        ALTITUDE_GATE_LOWER_LOCK_FD="$lower_usr_lock_fd" \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_ALLOW_PROTOCOL=file \
        GIT_PROTOCOL_FROM_USER=0 GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND=/bin/false \
        /opt/gate/pid1
}

read_number() {
    local file=$1 key=$2 value
    value=$(/usr/bin/awk -F= -v wanted="$key" '$1 == wanted && $2 ~ /^[0-9]+$/ {print $2}' "$file")
    [ -n "$value" ] && [ "$(printf '%s\n' "$value" | /usr/bin/wc -l)" -eq 1 ] || value=999
    printf '%s' "$value"
}

validate_result_schema() {
    local file=$1 line key value index=0
    local expected=(
        schema_version boundary_status
        base_status base_tests base_failures base_errors base_skipped
        base_expected_failures base_unexpected_successes base_successful
        candidate_status candidate_tests candidate_failures candidate_errors candidate_skipped
        candidate_expected_failures candidate_unexpected_successes candidate_successful
        log_truncated gate_ok
    )
    [ -f "$file" ] && [ ! -L "$file" ] || return 1
    [ "$(/usr/bin/stat -Lc '%u:%h' "$file")" = "0:1" ] || return 1
    while IFS= read -r line; do
        [ "$index" -lt "${#expected[@]}" ] || return 1
        key=${line%%=*}
        value=${line#*=}
        [ "$key" != "$line" ] && [ "$key" = "${expected[$index]}" ] || return 1
        case "$value" in *[!0-9]*|'') return 1 ;; esac
        index=$((index + 1))
    done < "$file"
    [ "$index" -eq "${#expected[@]}" ] || return 1
    [ "$(read_number "$file" schema_version)" -eq 1 ] || return 1
}

outer() {
    [ "$(/usr/bin/id -u)" -eq 0 ] || die "outer launcher requires root"
    exec </dev/null
    close_extra_fds
    trap cleanup_outer EXIT
    require_sha base "${ALTITUDE_GATE_BASE_SHA:?}"
    require_sha candidate "${ALTITUDE_GATE_CANDIDATE_SHA:?}"
    require_sha workflow "${ALTITUDE_GATE_WORKFLOW_SHA:?}"
    [ "$ALTITUDE_GATE_BASE_SHA" = "$ALTITUDE_GATE_WORKFLOW_SHA" ] || die "workflow and tested base SHAs differ"
    require_repository base-repository "${ALTITUDE_GATE_BASE_REPOSITORY:?}"
    require_repository candidate-repository "${ALTITUDE_GATE_CANDIDATE_REPOSITORY:?}"
    require_positive_number base-repository-id "${ALTITUDE_GATE_BASE_REPOSITORY_ID:?}"
    require_positive_number candidate-repository-id "${ALTITUDE_GATE_CANDIDATE_REPOSITORY_ID:?}"
    require_positive_number run-id "${ALTITUDE_GATE_RUN_ID:?}"
    require_positive_number run-attempt "${ALTITUDE_GATE_RUN_ATTEMPT:?}"
    require_positive_number check-run-id "${ALTITUDE_GATE_CHECK_RUN_ID:?}"
    require_number pull-request-id "${ALTITUDE_GATE_PULL_REQUEST_ID:?}"
    require_number pull-request-number "${ALTITUDE_GATE_PULL_REQUEST_NUMBER:?}"
    require_sha256 request-nonce "${ALTITUDE_GATE_REQUEST_NONCE:?}"
    [ "${ALTITUDE_GATE_WORKFLOW_PATH:?}" = ".github/workflows/trusted-remote-tests.yml" ] || die "workflow path changed"
    [ "${ALTITUDE_GATE_ARTIFACT_NAME:?}" = \
        "trusted-remote-evidence-$ALTITUDE_GATE_RUN_ID-$ALTITUDE_GATE_RUN_ATTEMPT-$ALTITUDE_GATE_REQUEST_NONCE" ] || \
        die "artifact name is not derived from the exact run tuple"
    case "${ALTITUDE_GATE_EVENT_NAME:?}:${ALTITUDE_GATE_EVENT_ACTION:?}" in
        pull_request_target:opened|pull_request_target:synchronize|pull_request_target:reopened|pull_request_target:ready_for_review|workflow_dispatch:retest) \
            [ "$ALTITUDE_GATE_PULL_REQUEST_ID" -gt 0 ] || die "PR evidence lacks a PR id"
            [ "$ALTITUDE_GATE_PULL_REQUEST_NUMBER" -gt 0 ] || die "PR evidence lacks a PR number" ;;
        push:push)
            [ "$ALTITUDE_GATE_PULL_REQUEST_ID" -eq 0 ] || die "push evidence unexpectedly names a PR id"
            [ "$ALTITUDE_GATE_PULL_REQUEST_NUMBER" -eq 0 ] || die "push evidence unexpectedly names a PR"
            [ "$ALTITUDE_GATE_BASE_REPOSITORY" = "$ALTITUDE_GATE_CANDIDATE_REPOSITORY" ] || die "push repositories differ"
            [ "$ALTITUDE_GATE_BASE_REPOSITORY_ID" = "$ALTITUDE_GATE_CANDIDATE_REPOSITORY_ID" ] || die "push repository ids differ"
            [ "$ALTITUDE_GATE_BASE_SHA" = "$ALTITUDE_GATE_CANDIDATE_SHA" ] || die "push SHAs differ" ;;
        *) die "event/action identity is not allowed" ;;
    esac
    require_sha256 expected-candidate "${ALTITUDE_GATE_EXPECTED_CANDIDATE_SHA256:?}"
    require_sha256 expected-base-tests "${ALTITUDE_GATE_EXPECTED_BASE_TESTS_SHA256:?}"
    require_sha256 expected-pid1 "${ALTITUDE_GATE_EXPECTED_PID1_SHA256:?}"
    local trusted=${ALTITUDE_GATE_TRUSTED_DIR:?}
    trusted=$(/usr/bin/realpath "$trusted")
    local candidate_archive=${ALTITUDE_GATE_CANDIDATE_ARCHIVE:?}
    local base_archive=${ALTITUDE_GATE_BASE_ARCHIVE:?}
    local pid1_bin=${ALTITUDE_GATE_PID1_BIN:?}
    local output_dir=${ALTITUDE_GATE_OUTPUT_DIR:?}
    regular_input "$candidate_archive"
    regular_input "$base_archive"
    regular_input "$pid1_bin"
    bounded_input "$candidate_archive" 536870912
    bounded_input "$base_archive" 134217728
    bounded_input "$pid1_bin" 4194304
    regular_input "$trusted/.github/workflows/trusted-remote-tests.yml"
    regular_input "$trusted/ci/trusted-gate/launch.sh"
    regular_input "$trusted/ci/trusted-gate/trusted_runner.py"
    regular_input "$trusted/ci/trusted-gate/boundary_selftest.py"
    regular_input "$trusted/ci/trusted-gate/verify_manifest.py"
    regular_input "$trusted/ci/trusted-gate/resolve_run_context.py"
    [ -d "$output_dir" ] && [ ! -L "$output_dir" ] || die "trusted output is not a directory"
    [ "$(/usr/bin/stat -Lc '%u' "$output_dir")" = "$SUDO_UID" ] || die "trusted output owner changed"
    local output_identity
    output_identity=$(/usr/bin/stat -Lc '%d:%i' "$output_dir")

    local stage
    stage=$(/usr/bin/mktemp -d /var/tmp/altitude-trusted-gate.XXXXXX)
    CLEANUP_STAGE=$stage
    /usr/bin/mkdir -m 0700 "$stage/input" "$stage/trusted" "$stage/gate" "$stage/root"
    /usr/bin/install -m 0444 "$candidate_archive" "$stage/input/candidate.tar"
    /usr/bin/install -m 0444 "$base_archive" "$stage/input/base-tests.tar"
    /usr/bin/install -m 0555 "$pid1_bin" "$stage/trusted/pid1"
    /usr/bin/install -m 0555 "$trusted/ci/trusted-gate/launch.sh" "$stage/trusted/launch.sh"
    /usr/bin/install -m 0444 "$trusted/.github/workflows/trusted-remote-tests.yml" "$stage/trusted/workflow.yml"
    /usr/bin/install -m 0444 "$trusted/ci/trusted-gate/trusted_runner.py" "$stage/trusted/trusted_runner.py"
    /usr/bin/install -m 0444 "$trusted/ci/trusted-gate/boundary_selftest.py" "$stage/trusted/boundary_selftest.py"
    /usr/bin/install -m 0444 "$trusted/ci/trusted-gate/verify_manifest.py" "$stage/trusted/verify_manifest.py"
    /usr/bin/install -m 0444 "$trusted/ci/trusted-gate/resolve_run_context.py" "$stage/trusted/resolve_run_context.py"
    local candidate_archive_sha base_archive_sha pid1_sha harness_sha
    local workflow_component launch_component runner_component boundary_component verifier_component resolver_component
    candidate_archive_sha=$(/usr/bin/sha256sum "$stage/input/candidate.tar" | /usr/bin/cut -d' ' -f1)
    base_archive_sha=$(/usr/bin/sha256sum "$stage/input/base-tests.tar" | /usr/bin/cut -d' ' -f1)
    pid1_sha=$(/usr/bin/sha256sum "$stage/trusted/pid1" | /usr/bin/cut -d' ' -f1)
    [ "$candidate_archive_sha" = "$ALTITUDE_GATE_EXPECTED_CANDIDATE_SHA256" ] || die "candidate archive digest changed"
    [ "$base_archive_sha" = "$ALTITUDE_GATE_EXPECTED_BASE_TESTS_SHA256" ] || die "base-tests archive digest changed"
    [ "$pid1_sha" = "$ALTITUDE_GATE_EXPECTED_PID1_SHA256" ] || die "PID 1 binary digest changed"
    workflow_component=$(/usr/bin/sha256sum "$stage/trusted/workflow.yml" | /usr/bin/cut -d' ' -f1)
    launch_component=$(/usr/bin/sha256sum "$stage/trusted/launch.sh" | /usr/bin/cut -d' ' -f1)
    runner_component=$(/usr/bin/sha256sum "$stage/trusted/trusted_runner.py" | /usr/bin/cut -d' ' -f1)
    boundary_component=$(/usr/bin/sha256sum "$stage/trusted/boundary_selftest.py" | /usr/bin/cut -d' ' -f1)
    verifier_component=$(/usr/bin/sha256sum "$stage/trusted/verify_manifest.py" | /usr/bin/cut -d' ' -f1)
    resolver_component=$(/usr/bin/sha256sum "$stage/trusted/resolve_run_context.py" | /usr/bin/cut -d' ' -f1)
    harness_sha=$(
        printf '%s  %s\n' \
            "$workflow_component" workflow.yml "$launch_component" launch.sh "$pid1_sha" pid1 \
            "$runner_component" trusted_runner.py "$boundary_component" boundary_selftest.py \
            "$verifier_component" verify_manifest.py "$resolver_component" resolve_run_context.py | \
            /usr/bin/sha256sum | /usr/bin/cut -d' ' -f1
    )

    local cgroup_root=/sys/fs/cgroup
    [ -f "$cgroup_root/cgroup.controllers" ] || die "cgroup v2 is required"
    local parent_cgroup cgroup
    parent_cgroup=$(/usr/bin/awk -F: '$1 == "0" {print $3}' /proc/self/cgroup)
    cgroup="$cgroup_root$parent_cgroup/altitude-gate-$$"
    /usr/bin/mkdir "$cgroup"
    CLEANUP_CGROUP=$cgroup
    CLEANUP_PARENT_CGROUP="$cgroup_root$parent_cgroup"
    [ -r "$cgroup/cgroup.events" ] && [ -w "$cgroup/cgroup.kill" ] || die "mandatory cgroup kill/empty controls are unavailable"
    printf '256\n' > "$cgroup/pids.max"
    printf '2147483648\n' > "$cgroup/memory.max"
    [ ! -f "$cgroup/memory.swap.max" ] || printf '0\n' > "$cgroup/memory.swap.max"
    printf '200000 100000\n' > "$cgroup/cpu.max"
    [ ! -f "$cgroup/memory.oom.group" ] || printf '1\n' > "$cgroup/memory.oom.group"
    printf '%s\n' "$$" > "$cgroup/cgroup.procs"

    local host_netns host_pidns host_mntns host_ipcns host_utsns host_cgroupns namespace_status
    host_netns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/net)
    host_pidns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/pid)
    host_mntns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/mnt)
    host_ipcns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/ipc)
    host_utsns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/uts)
    host_cgroupns=$(/usr/bin/stat -Lc '%i' /proc/self/ns/cgroup)
    set +e
    /usr/bin/timeout --foreground --kill-after=10s 20m /usr/bin/unshare --mount --pid --fork --kill-child=SIGKILL \
        --net --ipc --uts --cgroup --mount-proc=/proc /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
        ALTITUDE_GATE_MODE=inner ALTITUDE_GATE_STAGE="$stage" ALTITUDE_GATE_HOST_NETNS_INO="$host_netns" \
        ALTITUDE_GATE_HOST_PIDNS_INO="$host_pidns" ALTITUDE_GATE_CANDIDATE_ARCHIVE_SHA256="$candidate_archive_sha" \
        ALTITUDE_GATE_HOST_MNTNS_INO="$host_mntns" ALTITUDE_GATE_HOST_IPCNS_INO="$host_ipcns" \
        ALTITUDE_GATE_HOST_UTSNS_INO="$host_utsns" ALTITUDE_GATE_HOST_CGROUPNS_INO="$host_cgroupns" \
        ALTITUDE_GATE_BASE_ARCHIVE_SHA256="$base_archive_sha" ALTITUDE_GATE_PID1_SHA256="$pid1_sha" \
        /usr/bin/bash --noprofile --norc "$stage/trusted/launch.sh"
    namespace_status=$?
    set -e

    kill_empty_remove_cgroup "$cgroup" "$cgroup_root$parent_cgroup" || die "cgroup teardown could not be proven"
    CLEANUP_CGROUP=
    CLEANUP_PARENT_CGROUP=

    local result=$stage/gate/result.env
    local boundary_status=999 base_status=999 base_tests=0 base_failures=999 base_errors=999
    local base_skipped=0 base_expected_failures=0 base_unexpected_successes=999 base_successful=0
    local candidate_status=999 candidate_tests=0 candidate_failures=999 candidate_errors=999
    local candidate_skipped=0 candidate_expected_failures=0 candidate_unexpected_successes=999
    local candidate_successful=0 log_truncated=1 gate_ok=0
    if validate_result_schema "$result"; then
        boundary_status=$(read_number "$result" boundary_status)
        base_status=$(read_number "$result" base_status)
        base_tests=$(read_number "$result" base_tests)
        base_failures=$(read_number "$result" base_failures)
        base_errors=$(read_number "$result" base_errors)
        base_skipped=$(read_number "$result" base_skipped)
        base_expected_failures=$(read_number "$result" base_expected_failures)
        base_unexpected_successes=$(read_number "$result" base_unexpected_successes)
        base_successful=$(read_number "$result" base_successful)
        candidate_status=$(read_number "$result" candidate_status)
        candidate_tests=$(read_number "$result" candidate_tests)
        candidate_failures=$(read_number "$result" candidate_failures)
        candidate_errors=$(read_number "$result" candidate_errors)
        candidate_skipped=$(read_number "$result" candidate_skipped)
        candidate_expected_failures=$(read_number "$result" candidate_expected_failures)
        candidate_unexpected_successes=$(read_number "$result" candidate_unexpected_successes)
        candidate_successful=$(read_number "$result" candidate_successful)
        log_truncated=$(read_number "$result" log_truncated)
        gate_ok=$(read_number "$result" gate_ok)
    fi
    local output_log=$stage/gate/output.log
    [ -f "$output_log" ] || : > "$output_log"
    [ ! -L "$output_log" ] && [ "$(/usr/bin/stat -Lc '%u:%h' "$output_log")" = "0:1" ] || die "output log is not trusted"
    local mounts_file=$stage/gate/mounts.txt
    [ -f "$mounts_file" ] && [ ! -L "$mounts_file" ] && \
        [ "$(/usr/bin/stat -Lc '%u:%h' "$mounts_file")" = "0:1" ] || die "mount attestation is not trusted"
    local output_sha output_size mounts_sha mounts_size candidate_archive_size base_archive_size pid1_size overall
    output_sha=$(/usr/bin/sha256sum "$output_log" | /usr/bin/cut -d' ' -f1)
    output_size=$(/usr/bin/stat -Lc '%s' "$output_log")
    [ "$output_size" -le "$LOG_LIMIT" ] || die "output log exceeded its bound"
    mounts_sha=$(/usr/bin/sha256sum "$mounts_file" | /usr/bin/cut -d' ' -f1)
    mounts_size=$(/usr/bin/stat -Lc '%s' "$mounts_file")
    [ "$mounts_size" -gt 0 ] && [ "$mounts_size" -le 65536 ] || die "mount attestation size is invalid"
    candidate_archive_size=$(/usr/bin/stat -Lc '%s' "$stage/input/candidate.tar")
    base_archive_size=$(/usr/bin/stat -Lc '%s' "$stage/input/base-tests.tar")
    pid1_size=$(/usr/bin/stat -Lc '%s' "$stage/trusted/pid1")
    overall=1
    [ "$namespace_status" -eq 0 ] && [ "$boundary_status" -eq 0 ] && \
        [ "$base_status" -eq 0 ] && [ "$base_successful" -eq 1 ] && [ "$base_tests" -gt "$base_skipped" ] && \
        [ "$base_failures" -eq 0 ] && [ "$base_errors" -eq 0 ] && [ "$base_unexpected_successes" -eq 0 ] && \
        [ "$candidate_status" -eq 0 ] && [ "$candidate_successful" -eq 1 ] && [ "$candidate_tests" -gt "$candidate_skipped" ] && \
        [ "$candidate_failures" -eq 0 ] && [ "$candidate_errors" -eq 0 ] && \
        [ "$candidate_unexpected_successes" -eq 0 ] && [ "$log_truncated" -eq 0 ] && [ "$gate_ok" -eq 1 ] && overall=0

    local manifest=$stage/gate/manifest.json
    printf '{\n  "schema": 2,\n  "identity": {"base_repository": "%s", "base_repository_id": %s, "candidate_repository": "%s", "candidate_repository_id": %s, "event_name": "%s", "event_action": "%s", "pull_request_id": %s, "pull_request_number": %s, "run_id": %s, "run_attempt": %s, "check_run_id": %s, "workflow_path": "%s", "workflow_sha": "%s", "request_nonce": "%s", "artifact_name": "%s", "base_sha": "%s", "candidate_sha": "%s"},\n' \
        "$ALTITUDE_GATE_BASE_REPOSITORY" "$ALTITUDE_GATE_BASE_REPOSITORY_ID" \
        "$ALTITUDE_GATE_CANDIDATE_REPOSITORY" "$ALTITUDE_GATE_CANDIDATE_REPOSITORY_ID" \
        "$ALTITUDE_GATE_EVENT_NAME" "$ALTITUDE_GATE_EVENT_ACTION" "$ALTITUDE_GATE_PULL_REQUEST_ID" \
        "$ALTITUDE_GATE_PULL_REQUEST_NUMBER" \
        "$ALTITUDE_GATE_RUN_ID" "$ALTITUDE_GATE_RUN_ATTEMPT" "$ALTITUDE_GATE_CHECK_RUN_ID" \
        "$ALTITUDE_GATE_WORKFLOW_PATH" "$ALTITUDE_GATE_WORKFLOW_SHA" "$ALTITUDE_GATE_REQUEST_NONCE" \
        "$ALTITUDE_GATE_ARTIFACT_NAME" "$ALTITUDE_GATE_BASE_SHA" "$ALTITUDE_GATE_CANDIDATE_SHA" > "$manifest"
    printf '  "harness": {"sha256": "%s", "components": {"workflow.yml": "%s", "launch.sh": "%s", "pid1": "%s", "trusted_runner.py": "%s", "boundary_selftest.py": "%s", "verify_manifest.py": "%s", "resolve_run_context.py": "%s"}},\n' \
        "$harness_sha" "$workflow_component" "$launch_component" "$pid1_sha" "$runner_component" \
        "$boundary_component" "$verifier_component" "$resolver_component" >> "$manifest"
    printf '  "inputs": {"candidate_archive": {"sha256": "%s", "bytes": %s}, "base_tests_archive": {"sha256": "%s", "bytes": %s}, "pid1": {"sha256": "%s", "bytes": %s}},\n' \
        "$candidate_archive_sha" "$candidate_archive_size" "$base_archive_sha" "$base_archive_size" \
        "$pid1_sha" "$pid1_size" >> "$manifest"
    printf '  "actions": {"checkout": "%s", "upload_artifact": "%s"},\n' \
        "$CHECKOUT_ACTION_SHA" "$UPLOAD_ACTION_SHA" >> "$manifest"
    printf '  "limits": {"pids": 256, "memory_bytes": 2147483648, "cpu_quota": "200000 100000", "wall_seconds": 1200, "teardown_reserve_seconds": 100, "log_bytes": %s, "phase_seconds": {"extract_candidate": 60, "extract_base_copy": 60, "remove_candidate_tests": 20, "overlay_base_tests": 40, "boundary": 50, "base": 435, "candidate": 435}},\n' "$LOG_LIMIT" >> "$manifest"
    printf '  "commands": ["/usr/bin/python3 -I /opt/gate/boundary_selftest.py", "/usr/bin/python3 -I /opt/gate/trusted_runner.py --label base --root /work/base-suite/source --tests /work/base-suite/source/tests", "/usr/bin/python3 -I /opt/gate/trusted_runner.py --label candidate --root /work/candidate-suite/source --tests /work/candidate-suite/source/tests"],\n' >> "$manifest"
    printf '  "boundary_status": %s,\n  "base": {"status": %s, "tests": %s, "failures": %s, "errors": %s, "skipped": %s, "expected_failures": %s, "unexpected_successes": %s, "successful": %s},\n' \
        "$boundary_status" "$base_status" "$base_tests" "$base_failures" "$base_errors" \
        "$base_skipped" "$base_expected_failures" "$base_unexpected_successes" "$base_successful" >> "$manifest"
    printf '  "candidate": {"status": %s, "tests": %s, "failures": %s, "errors": %s, "skipped": %s, "expected_failures": %s, "unexpected_successes": %s, "successful": %s},\n' \
        "$candidate_status" "$candidate_tests" "$candidate_failures" "$candidate_errors" \
        "$candidate_skipped" "$candidate_expected_failures" "$candidate_unexpected_successes" \
        "$candidate_successful" >> "$manifest"
    printf '  "namespace_status": %s,\n  "log_truncated": %s,\n  "gate_ok": %s,\n  "output": {"sha256": "%s", "bytes": %s},\n  "mounts": {"sha256": "%s", "bytes": %s},\n  "status": %s\n}\n' \
        "$namespace_status" "$log_truncated" "$gate_ok" "$output_sha" "$output_size" \
        "$mounts_sha" "$mounts_size" "$overall" >> "$manifest"

    [ "$output_identity" = "$(/usr/bin/stat -Lc '%d:%i' "$output_dir")" ] && [ ! -L "$output_dir" ] || die "output directory changed"
    /usr/bin/install -o "$SUDO_UID" -g "$SUDO_GID" -m 0600 "$manifest" "$output_dir/manifest.json"
    /usr/bin/install -o "$SUDO_UID" -g "$SUDO_GID" -m 0600 "$output_log" "$output_dir/output.log"
    /usr/bin/install -o "$SUDO_UID" -g "$SUDO_GID" -m 0600 "$mounts_file" "$output_dir/mounts.txt"
    /usr/bin/rm -rf -- "$stage"
    CLEANUP_STAGE=
    return "$overall"
}

case "${ALTITUDE_GATE_MODE:-outer}" in
    inner) inner ;;
    outer) outer ;;
    *) die "unknown mode" ;;
esac
