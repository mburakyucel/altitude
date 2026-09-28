#!/usr/bin/env bash
# Only on an explicitly disposable Ubuntu VM. Never run on a development host.
# PHASE reboot-install keeps the installed account for the machine to restart; reboot-verify then
# checks it and removes it. PHASE bootstrap points the release host at this machine's loopback for the
# duration of the test. Each phase writes to RESULTS_DIR/PHASE.
set -euo pipefail
if [[ ($# != 5 && $# != 6) || $1 != --disposable-vm || ! ${6:-all} =~ ^(all|bootstrap|reboot-install|reboot-verify)$ ]]; then
    echo 'Usage: sudo bash scripts/test_installation_lifecycle.sh --disposable-vm BASELINE_DIR CANDIDATE_DIR RESULTS_DIR SOURCE_COMMIT [bootstrap|reboot-install|reboot-verify]' >&2
    exit 2
fi
[[ $5 =~ ^[0-9a-f]{40}$ ]] || { echo 'Supply the full selected source commit.' >&2; exit 2; }
if [[ $EUID != 0 || -n ${ALTITUDE_ACTOR:-} ]]; then
    echo 'Requires root on a disposable VM, outside an Altitude worker.' >&2
    exit 2
fi
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || {
    echo 'This acceptance harness requires Ubuntu 24.04 x86_64.' >&2; exit 2;
}
phase=${6:-all}
baseline=$(realpath "$2")
candidate=$(realpath "$3")
mkdir -p "$4"
results=$(realpath "$4")
[[ $phase == all ]] || { results="$results/$phase"; mkdir -p "$results"; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
created=false
redirected=false
keep=false
test_uid=''
# The account left installed across the restart; only root can write it.
kept=/var/lib/altitude-installation-reboot-account
if [[ $phase == reboot-verify ]]; then
    account=$(cat "$kept" 2>/dev/null) || { echo 'No installed reboot-install account to verify.' >&2; exit 2; }
    scratch=$(dirname -- "$(getent passwd "$account" | cut -d: -f6)")
    [[ $account =~ ^alt-install-[0-9]+$ && $scratch =~ ^/var/tmp/altitude-installation\.[A-Za-z0-9]+$ &&
       -d $scratch && ! -L $scratch && $(stat -c %u "$scratch") == 0 ]] ||
        { echo "The recorded account $account is not a harness account." >&2; exit 2; }
    rm -f -- "$kept"
else
    [[ ! -e $kept ]] || { echo "A reboot-install account is still pending: $(cat "$kept")" >&2; exit 2; }
    # /var/tmp survives a restart; /tmp does not.
    scratch=$(mktemp -d /var/tmp/altitude-installation.XXXXXXXX)
    account="alt-install-$$"
fi
cleanup() {
    outcome=$?
    lifecycle_exit=$outcome
    trap - EXIT
    set +e
    restore_exit=0
    if $redirected; then
        cp -- "$scratch/hosts" /etc/hosts || restore_exit=1
        sysctl -qw "net.ipv4.ip_unprivileged_port_start=$port_floor" || restore_exit=1
    fi
    copy_exit=0
    linger_exit=0
    manager_exit=0
    delete_exit=0
    if $created; then
        # Only this harness's account is in scope, even after failed install.
        journalctl "_UID=$test_uid" --no-pager -n 400 > "$results/journal.log" 2>&1
        if [[ -d $scratch/$account/results ]]; then
            for evidence in "$scratch/$account/results/"*.json "$scratch/$account/results/"*.log; do
                [[ -f $evidence ]] || continue
                install -m 644 "$evidence" "$results/" || copy_exit=1
            done
        fi
        if $keep && (( outcome == 0 && copy_exit == 0 )) && (umask 077; printf '%s\n' "$account" > "$kept"); then
            echo "Left $account installed for reboot-verify" | tee -a "$results/cleanup.log"
            exit 0
        fi
        # The account could not be recorded for reboot-verify, so it goes now.
        $keep && { outcome=1; rm -f -- "$kept"; }
        timeout 15 loginctl disable-linger "$account" >> "$results/cleanup.log" 2>&1
        linger_exit=$?
        timeout 30 systemctl stop "user@$test_uid.service" >> "$results/cleanup.log" 2>&1
        manager_exit=$?
        timeout 15 userdel --remove "$account" >> "$results/cleanup.log" 2>&1
        delete_exit=$?
        if (( copy_exit || linger_exit || manager_exit || delete_exit || restore_exit )); then outcome=1; fi
        if id "$account" >/dev/null 2>&1; then outcome=1; fi
    fi
    rm -rf -- "$scratch"
    printf '{"lifecycle_exit": %s, "restore_redirect_exit": %s, "copy_evidence_exit": %s, "disable_linger_exit": %s, "stop_manager_exit": %s, "delete_account_exit": %s, "final_exit": %s}\n' \
        "$lifecycle_exit" "$restore_exit" "$copy_exit" "$linger_exit" "$manager_exit" "$delete_exit" "$outcome" > "$results/cleanup.json"
    echo "Harness and cleanup exit status: $outcome" | tee -a "$results/cleanup.log"
    exit "$outcome"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP
test_home="$scratch/$account"
if [[ $phase == reboot-verify ]]; then
    created=true
    test_uid=$(id -u "$account")
else
    chmod 755 "$scratch"
    useradd --create-home --user-group --shell /bin/bash --base-dir "$scratch" "$account"
    created=true
    test_uid=$(id -u "$account")
    mkdir "$test_home/baseline" "$test_home/candidate" "$test_home/results"
    cp -a "$baseline/." "$test_home/baseline/"
    cp -a "$candidate/." "$test_home/candidate/"
    cp "$script_dir/installation_lifecycle.py" "$test_home/"
    chown -R "$account:$account" "$test_home"
    timeout 30 loginctl enable-linger "$account"
    timeout 30 systemctl start "user@$test_uid.service"
fi
if [[ $phase == bootstrap ]]; then
    # The test account serves the release over HTTPS on 127.0.0.1:443 under the release's own host name.
    host=$(sed -n "s|^REPOSITORY='https://\([^/':]*\)/.*|\1|p" "$test_home/baseline/install.sh")
    [[ $host =~ ^[A-Za-z0-9.-]+$ ]] || { echo 'The baseline install.sh names no release host.' >&2; exit 2; }
    port_floor=$(sysctl -n net.ipv4.ip_unprivileged_port_start)
    cp -- /etc/hosts "$scratch/hosts"
    redirected=true
    printf '127.0.0.1 %s\n' "$host" >> /etc/hosts
    sysctl -qw net.ipv4.ip_unprivileged_port_start=443
fi
# No runner token, credentials, Python path or owner runtime enters the test.
timeout --signal=TERM --kill-after=10s 8m runuser -u "$account" -- env -i \
    HOME="$test_home" USER="$account" LOGNAME="$account" PATH=/usr/bin:/bin \
    XDG_RUNTIME_DIR="/run/user/$test_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$test_uid/bus" \
    /usr/bin/python3 "$test_home/installation_lifecycle.py" "$test_home/baseline" "$test_home/candidate" "$test_home/results" "$5" "$phase"
[[ $phase == reboot-install ]] && keep=true
exit 0
