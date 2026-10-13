#!/bin/sh
# Install Altitude @VERSION@ for the current user.
#
#   curl --proto '=https' --tlsv1.2 -fsSL @REPOSITORY@/releases/latest/download/install.sh | sh
#
# scripts/build_release.py fills in the version and the SHA-256 of every file this script downloads,
# so nothing it fetches runs unless it matches the release it was built with. Nothing runs as root.
# The whole script is one function called on its last line, so a partial download does nothing.
set -eu

VERSION='@VERSION@'
REPOSITORY='@REPOSITORY@'
ARCHIVE="altitude-$VERSION.tar.gz"
ARCHIVE_SHA256='@ARCHIVE_SHA256@'
INSTALLER_SHA256='@INSTALLER_SHA256@'

stop() {
    printf 'Altitude was not installed: %s\n' "$1" >&2
    [ -z "${2:-}" ] || printf '  %s\n' "$2" >&2
    exit 1
}

python_version() {
    "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null
}

# Python 3.12 or newer from PATH, or where python.org and Homebrew put it on a Mac. Apple's
# /usr/bin/python3 without the command line tools opens an installer dialog, so it is not run then.
find_python() {
    for candidate in python3.14 python3.13 python3.12 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        path=$(command -v "$candidate" 2>/dev/null) || continue
        if [ "$system" = Darwin ] && [ "$path" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$path" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
            printf '%s\n' "$path"
            return 0
        fi
    done
    return 1
}

# The newest Python found when none is recent enough, for the message.
older_python() {
    for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        path=$(command -v "$candidate" 2>/dev/null) || continue
        if [ "$system" = Darwin ] && [ "$path" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        python_version "$path" && return 0
    done
    return 1
}

# Match platform.openssl() before downloading any application code. Homebrew's keg-only tool
# is used directly by Altitude; the installing shell and saved service PATH stay unchanged.
find_openssl() {
    if [ "$system" != Darwin ]; then command -v openssl; return; fi
    for candidate in openssl /opt/homebrew/opt/openssl@3/bin/openssl /usr/local/opt/openssl@3/bin/openssl; do
        path=$(command -v "$candidate" 2>/dev/null) || continue
        version=$("$path" version 2>/dev/null) || continue
        case "$version" in
            "OpenSSL "[3-9].* | "OpenSSL "[1-9][0-9]*.*) printf '%s\n' "$path"; return 0 ;;
        esac
    done
    return 1
}

# How to install a package with this system's own package manager; never another distribution's command.
package_hint() {
    if command -v apt-get >/dev/null 2>&1; then printf 'sudo apt install %s' "$1"
    elif command -v dnf >/dev/null 2>&1; then printf 'sudo dnf install %s' "$1"
    elif command -v pacman >/dev/null 2>&1; then printf 'sudo pacman -S %s' "$([ "$1" = python3 ] && echo python || echo "$1")"
    elif command -v zypper >/dev/null 2>&1; then printf 'sudo zypper install %s' "$1"
    elif command -v brew >/dev/null 2>&1; then printf 'brew install %s' "$1"
    else printf "with your distribution's package manager"
    fi
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d ' ' -f 1
    else
        shasum -a 256 "$1" | cut -d ' ' -f 1
    fi
}

fetch() {
    curl -fsSL --proto '=https' --tlsv1.2 --retry 2 -o "$workdir/$1" "$REPOSITORY/releases/download/$VERSION/$1" ||
        stop "could not download $1 from the $VERSION release." "Check the network connection and run the command again."
    [ "$(sha256 "$workdir/$1")" = "$2" ] ||
        stop "$1 does not match the checksum this $VERSION installer was built with; nothing was run." \
            "Run the command again; if it repeats, report it at $REPOSITORY/issues."
}

main() {
    case "$VERSION" in @*) stop "this is the release template; download install.sh from a published release." ;; esac
    system=$(uname -s)
    machine=$(uname -m)
    case "$system" in
        Linux | Darwin) ;;
        *) stop "Altitude $VERSION runs on Linux with a systemd user manager or on macOS 15 or newer; this machine runs $system." ;;
    esac
    [ "$(id -u)" != 0 ] || stop "run this as the account that will use Altitude, not as root." \
        "Altitude installs into your home directory and needs no administrator rights."
    if [ "$system" = Darwin ]; then
        macos=$(sw_vers -productVersion 2>/dev/null) || macos=unknown
        [ "${macos%%.*}" -ge 15 ] 2>/dev/null || stop "Altitude $VERSION needs macOS 15 or newer; this Mac runs macOS $macos." \
            "Update macOS in System Settings > General > Software Update, then run this again."
        python_fix="Install it first (with Homebrew: brew install python@3.12), then run this again."
    else
        python_fix="Install it first ($(package_hint python3)), then run this again."
    fi
    python=$(find_python) || stop "Python 3.12 or newer was not found ($(older_python || echo 'no python3'))." "$python_fix"
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 ||
        stop "no SHA-256 tool (sha256sum or shasum) is installed." "Install coreutils, then run this again."
    command -v curl >/dev/null 2>&1 || stop "curl is not installed." \
        "Install it first ($(package_hint curl)), then run this again."
    if [ "$system" = Darwin ]; then
        find_openssl >/dev/null || stop "OpenSSL 3 or newer was not found on PATH or in Homebrew's standard locations." \
            "Install it (brew install openssl@3), then run this again; no shell-profile change is needed."
    else
        find_openssl >/dev/null || stop "openssl is not installed." \
            "Install it first ($(package_hint openssl)), then run this again."
    fi
    [ "$system" != Linux ] || systemctl --user show-environment >/dev/null 2>&1 || stop "no systemd user manager is reachable." \
        "Altitude runs as a systemd user service. Run this from a login or SSH session where systemctl --user works."
    if [ "$system" = Darwin ]; then
        launchctl print "gui/$(id -u)" >/dev/null 2>&1 || stop "no logged-in desktop session of this account is reachable." \
            "Altitude runs as a LaunchAgent in your desktop session. Log in to this Mac's desktop, then run this again."
    fi
    case "$system/$machine" in
        Linux/x86_64 | Darwin/arm64) ;;
        *) printf 'Altitude is not yet validated on %s %s; please report anything that does not work at %s/issues.\n' \
               "$([ "$system" = Darwin ] && echo macOS || echo Linux)" "$machine" "$REPOSITORY" ;;
    esac

    # macOS mktemp ignores TMPDIR without a template.
    workdir=$(mktemp -d "${TMPDIR:-/tmp}/altitude-install.XXXXXXXX")
    trap 'rm -rf "$workdir"' EXIT
    printf 'Installing Altitude %s ...\n' "$VERSION"
    fetch "$ARCHIVE" "$ARCHIVE_SHA256"
    fetch install.py "$INSTALLER_SHA256"
    "$python" -B "$workdir/install.py" --archive "$workdir/$ARCHIVE" --sha256 "$ARCHIVE_SHA256" > "$workdir/result.json" ||
        stop "the installer stopped with the message above." "Your existing data and any previous installation are kept."

    "$python" - "$workdir/result.json" <<'EOF'
import json, os, shlex, sys
result = json.load(open(sys.argv[1]))
home = os.path.expanduser("~")
tilde = lambda path: "~" + path[len(home):] if path.startswith(home + "/") else path
trust = result.get("trust") or {}
print(f"\nOpen Altitude: {result['url']}")
print(f"Altitude {result['version']} is installed. Its web app is hosted by a background process on this computer ({result['service']}).")
print("You can close this terminal; the service runs independently of it and the browser tab.")
network = result.get("network") or {}
if network.get("listener_scope") == "all":
    print("The service listens on all interfaces. The localhost certificate URL opens on this computer; other-device reachability is unverified.")
elif network.get("listener_scope") == "local":
    print("This URL opens only on this computer. No VPN is needed here.")
else:
    print("Other-device reachability depends on the configured address and port; it is not verified here.")
print("Expect a certificate warning until this browser trusts Altitude's CA. The browser setup screen guides trust and pairing.")
print("A warning-bypassed setup page cannot authenticate itself. Compare its certificate name and full SHA-256 independently with this terminal; stop on mismatch.")
if trust.get("ca_sha256"):
    print(f"  Certificate name: {trust['ca_name']}")
    print(f"  Public CA file: {tilde(trust['ca_cert'])}")
    print(f"  SHA-256: {trust['ca_sha256'].split('=', 1)[-1]}")
print("Import the matching CA, then reopen this exact URL in a new private window without a warning before entering a pairing code.")
print(f"Pair every browser, including this computer: run {shlex.quote(os.path.join(home, '.local/bin/alt'))} pair when setup asks.")
print(result.get("lifecycle", "Altitude runs as your account's background service."))
if result.get("lingering"):
    print(f"\n{result['lingering']['detail']}")
print("Control automatic updates in Settings → About; reinstall preserves your preference. Closing a tab does not stop Altitude.")
print("Use alt service stop to stop the app. alt uninstall removes the command and service, retains settings, certificates and data, and keeps application resources needed by registered project hooks.")
print("Troubleshooting: alt doctor explains the service and readiness; add --json for automation.")
EOF
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) printf '\nTo use alt by name in new shells, add this line to your shell profile:\n  export PATH="$HOME/.local/bin:$PATH"\n' ;;
    esac
    printf '\nCertificate instructions for this release:\n  %s\n' "$REPOSITORY/blob/$VERSION/docs/SETUP.md#trust-https-on-each-device"
}

main "$@"
