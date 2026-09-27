#!/usr/bin/env bash
# Only on an explicitly disposable Ubuntu VM. Never run on a development host.
set -euo pipefail
if [[ $# != 4 || $1 != --disposable-vm ]]; then
    echo 'Usage: sudo bash scripts/test_installation_lifecycle.sh --disposable-vm BASELINE_DIR CANDIDATE_DIR RESULTS_DIR' >&2
    exit 2
fi
if [[ $EUID != 0 || -n ${ALTITUDE_ACTOR:-} ]]; then
    echo 'Requires root on a disposable VM, outside an Altitude worker.' >&2
    exit 2
fi
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || {
    echo 'This acceptance harness requires Ubuntu 24.04 x86_64.' >&2; exit 2;
}
baseline=$(realpath "$2")
candidate=$(realpath "$3")
mkdir -p "$4"
results=$(realpath "$4")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
scratch=$(mktemp -d /tmp/altitude-installation.XXXXXXXX)
account="alt-install-$$"
created=false
test_uid=''
cleanup() {
    outcome=$?
    lifecycle_exit=$outcome
    trap - EXIT
    set +e
    if $created; then
        # Only this newly created account is in scope, even after failed install.
        journalctl "_UID=$test_uid" --no-pager -n 400 > "$results/journal.log" 2>&1
        if [[ -d $scratch/$account/results ]]; then
            cp -a "$scratch/$account/results/." "$results/" || outcome=1
        fi
        timeout 30 systemctl stop "user@$test_uid.service" >> "$results/cleanup.log" 2>&1 || outcome=1
        timeout 15 loginctl disable-linger "$account" >> "$results/cleanup.log" 2>&1 || outcome=1
        timeout 15 userdel --remove "$account" >> "$results/cleanup.log" 2>&1 || outcome=1
        if id "$account" >/dev/null 2>&1; then outcome=1; fi
    fi
    rm -rf -- "$scratch"
    printf '{"lifecycle_exit": %s, "final_exit": %s}\n' "$lifecycle_exit" "$outcome" > "$results/cleanup.json"
    echo "Harness and cleanup exit status: $outcome" | tee -a "$results/cleanup.log"
    exit "$outcome"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP
chmod 755 "$scratch"
useradd --create-home --user-group --shell /bin/bash --base-dir "$scratch" "$account"
created=true
test_uid=$(id -u "$account")
test_home="$scratch/$account"
mkdir "$test_home/baseline" "$test_home/candidate" "$test_home/results"
cp -a "$baseline/." "$test_home/baseline/"
cp -a "$candidate/." "$test_home/candidate/"
cp "$script_dir/installation_lifecycle.py" "$test_home/"
chown -R "$account:$account" "$test_home"
timeout 30 loginctl enable-linger "$account"
timeout 30 systemctl start "user@$test_uid.service"
# No runner token, credentials, Python path or owner runtime enters the test.
timeout --signal=TERM --kill-after=10s 8m runuser -u "$account" -- env -i \
    HOME="$test_home" USER="$account" LOGNAME="$account" PATH=/usr/bin:/bin \
    XDG_RUNTIME_DIR="/run/user/$test_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$test_uid/bus" \
    /usr/bin/python3 "$test_home/installation_lifecycle.py" "$test_home/baseline" "$test_home/candidate" "$test_home/results"
