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
    for tool in curl openssl; do
        command -v "$tool" >/dev/null 2>&1 || stop "$tool is not installed." \
            "Install it first ($(package_hint "$tool")), then run this again."
    done
    [ "$system" != Linux ] || systemctl --user show-environment >/dev/null 2>&1 || stop "no systemd user manager is reachable." \
        "Altitude runs as a systemd user service. Run this from a login or SSH session where systemctl --user works."
    if [ "$system" = Darwin ]; then
        # macOS's own openssl is LibreSSL, which cannot check a certificate's host name.
        case "$(openssl version 2>/dev/null)" in
            "OpenSSL "[3-9]*) ;;
            *) stop "the openssl on PATH is $(openssl version 2>/dev/null || echo missing), not OpenSSL 3." \
                "Install it (brew install openssl@3), put it ahead of /usr/bin (export PATH=\"\$(brew --prefix openssl@3)/bin:\$PATH\", also in your shell profile), then run this again." ;;
        esac
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
import json, os, sys
result = json.load(open(sys.argv[1]))
home = os.path.expanduser("~")
tilde = lambda path: "~" + path[len(home):] if path.startswith(home + "/") else path
trust = result.get("trust") or {}
print(f"\nAltitude {result['version']} is installed and its service is {result['service']}.")
print(f"  Address: {result['url']}")
if trust.get("ca_sha256"):
    print(f"  Certificate authority: {tilde(trust['ca_cert'])}")
    print(f"  Its fingerprint: {trust['ca_sha256'].split('=', 1)[-1]}")
print("\nNext:")
EOF
    step=1
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) printf '  %s. Put alt on your PATH (add this line to your shell profile too):\n       export PATH="$HOME/.local/bin:$PATH"\n' "$step"
           step=$((step + 1)) ;;
    esac
    printf '  %s. Run: alt doctor\n' "$step"
    printf '  %s. Trust the certificate authority on each device, comparing the fingerprint above:\n       %s\n' \
        "$((step + 1))" "$REPOSITORY/blob/$VERSION/docs/SETUP.md#trust-https-on-each-device"
    printf '  %s. Open the address; First run walks you through the rest.\n' "$((step + 2))"
}

main "$@"
