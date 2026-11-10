#!/usr/bin/env bash
# One entry point: provision once, apply checked-out app updates on rerun.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    for arg in "$@"; do
        case "$arg" in
            -h|--help)
                printf '%s\n' 'Install Bingo; rerun the same command to update the checked-out release.' \
                    'Usage: bash install.sh [--dir ABSOLUTE_PATH] [--offline] [--check] [--desktop-only | --no-desktop]'
                exit 0 ;;
            --offline|--check|--desktop-only)
                printf '%s\n' 'Bingo refused: Missing Python 3; this mode never installs packages.' >&2
                exit 1 ;;
        esac
    done
    if (( EUID == 0 )) || [[ ! -t 0 ]] || ! command -v sudo >/dev/null || ! command -v apt-get >/dev/null; then
        printf '%s\n' 'Bingo refused: Missing Python 3. Run as an ordinary Ubuntu/Debian user in an interactive terminal, never sudo.' >&2
        exit 1
    fi
    # Trusted host metadata, not a downloaded script. No remote shell execution.
    source /etc/os-release
    if [[ ${ID:-} != ubuntu && ${ID:-} != debian ]]; then
        printf '%s\n' 'Bingo refused: automatic Python packages support Ubuntu/Debian only.' >&2
        exit 1
    fi
    printf '%s\n' 'Installing Python 3 with interactive sudo apt only; Podman always runs as your user.'
    sudo -- apt-get update
    sudo -- apt-get install python3
fi
exec python3 -B "$HERE/installer/bootstrap.py" "$@"
