#!/usr/bin/env bash
# Run as the existing rootless Podman user, never with sudo.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$HERE/update.py" "$@"
