#!/usr/bin/env bash
# Serve the wireframes over HTTP so the viewer opens on a phone on the same network.
# It serves the repository root, not this folder: wireframes.css imports web/design/tokens.css from
# two levels up, and a board without its tokens is not the board. Override the port with PORT=.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
port=${PORT:-8899}
ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' | head -1)
echo "here:  http://localhost:$port/${here#"$root"/}/"
[ -n "$ip" ] && echo "phone: http://$ip:$port/${here#"$root"/}/"
echo "Ctrl-C to stop."
exec python3 -m http.server "$port" --bind 0.0.0.0 --directory "$root"
