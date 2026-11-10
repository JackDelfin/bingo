#!/usr/bin/env bash
# Rootless local-network deployment of the unmodified React Bingo application.
set -Eeuo pipefail
BASE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LABEL=org.reactbingo.venue

fail() { printf 'Bingo: %s\n' "$*" >&2; exit 1; }
command -v podman >/dev/null || fail 'Install Podman first; see VENUE-SETUP.md.'
command -v curl >/dev/null || fail 'Install curl first; see VENUE-SETUP.md.'
podman info >/dev/null 2>&1 || fail 'Podman is not usable by this user.'

ensure_container() {
    local name=$1
    shift
    if podman container exists "$name"; then
        [[ $(podman inspect --format '{{ index .Config.Labels "org.reactbingo.venue" }}' "$name") == 1 ]] || fail "Container name already in use: $name"
        podman start "$name" >/dev/null
    else
        podman run -d --pull=never --name "$name" --label "$LABEL=1" \
            --network venue-bingo --restart unless-stopped "$@" >/dev/null
    fi
}

case "${1:-start}" in
    start)
        for image in docker.io/library/mongo:8.0 localhost/reactbingo-backend:venue localhost/reactbingo-frontend:venue; do
            podman image exists "$image" || fail "Missing image $image. Load images.tar.gz as described in VENUE-SETUP.md."
        done
        [[ -r "$BASE/reactbingo/nginx.conf" && -r "$BASE/reactbingo/backend/cards.csv" ]] || fail 'Keep the supplied reactbingo source folder beside bingo.sh.'
        if podman network exists venue-bingo; then
            [[ $(podman network inspect --format '{{ index .Labels "org.reactbingo.venue" }}' venue-bingo) == 1 ]] || fail 'Network name already in use: venue-bingo'
        else
            podman network create --label "$LABEL=1" venue-bingo >/dev/null
        fi
        if podman volume exists venue-bingo-data; then
            [[ $(podman volume inspect --format '{{ index .Labels "org.reactbingo.venue" }}' venue-bingo-data) == 1 ]] || fail 'Volume name already in use: venue-bingo-data'
        else
            podman volume create --label "$LABEL=1" venue-bingo-data >/dev/null
        fi
        # Tested workaround for MongoDB's startup failure on the venue Ubuntu 7.0 kernel.
        ensure_container venue-bingo-db --network-alias db -v venue-bingo-data:/data/db \
            --env GLIBC_TUNABLES=glibc.pthread.rseq=1 docker.io/library/mongo:8.0
        ensure_container venue-bingo-backend --network-alias backend \
            -v "$BASE/reactbingo/backend/cards.csv:/app/cards.csv:ro,Z" localhost/reactbingo-backend:venue
        ensure_container venue-bingo-frontend -p 0.0.0.0:3000:80 \
            -v "$BASE/reactbingo/nginx.conf:/etc/nginx/nginx.conf:ro,Z" localhost/reactbingo-frontend:venue
        for attempt in {1..60}; do
            if curl --fail --silent --max-time 3 http://127.0.0.1:3000/api/list >/dev/null; then
                printf 'Bingo is ready: http://localhost:3000\nTV display: http://localhost:3000/view/VenueBingo\n'
                exit 0
            fi
            sleep 1
        done
        fail 'Startup did not become ready. Check: podman logs venue-bingo-backend'
        ;;
    stop)
        for name in venue-bingo-frontend venue-bingo-backend venue-bingo-db; do
            if podman container exists "$name"; then
                [[ $(podman inspect --format '{{ index .Config.Labels "org.reactbingo.venue" }}' "$name") == 1 ]] || fail "Container name already in use: $name"
            fi
        done
        for name in venue-bingo-frontend venue-bingo-backend venue-bingo-db; do
            if podman container exists "$name"; then podman stop "$name" >/dev/null; fi
        done
        printf 'Bingo stopped. Saved games remain in the venue-bingo-data volume.\n'
        ;;
    *) fail 'Usage: ./bingo.sh start|stop' ;;
esac