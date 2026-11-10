# Offline update of an existing venue installation

**App-only update, not initial installation. Creating this release has not updated
your server.** Run it on the existing venue computer as the same ordinary user
that owns its rootless Podman deployment. **Never use `sudo`.**

## Requirements

- Existing Bash, Python 3.9+, curl and working rootless Podman. Uses Podman 4.9
  operations, not Podman-5-only features. No downloads, npm, package installation
  or image pulls occur.
- Existing installation, normally `~/bingo`, with unchanged `bingo.sh`,
  `reactbingo/nginx.conf`, `reactbingo/backend/cards.csv` and private links file.
  Two exact supported launcher SHA-256 byte variants:
  - `aa510ee4de96bf13123c6d59ed99f9bc8ba3e410fed7c689abdbf8be635bf0d2`:
    supplied repaired staging fixture, without a final LF.
  - `57fb6cf504447205e3114fdf6d3ddc6f64da0092ab7b445b7cc907dead385340`:
    the same bytes plus one final LF, matching the historical authorized successful
    repaired venue SHA-256 read-back (session message 19469).
  No whitespace normalization or unknown launcher acceptance is performed. Any
  other bytes require independent review and an explicitly approved release pin;
  they are refused, never overwritten or automatically accepted. No launcher is deployed.
- Existing containers `venue-bingo-db`, `venue-bingo-backend`,
  `venue-bingo-frontend`, network `venue-bingo` and volume `venue-bingo-data`.
  Each must have ownership label `org.reactbingo.venue=1` (exact string value).
  Missing/foreign resources are refused, not adopted or created.
- Each app must have **exactly one bind mount**: existing
  `reactbingo/backend/cards.csv` to `/app/cards.csv` for backend, and existing
  `reactbingo/nginx.conf` to `/etc/nginx/nginx.conf` for frontend. Extra bind,
  volume or tmpfs mounts/destinations are unsupported and refused before any
  export/build/tag/stop, including rollback revalidation. Normal Podman inspection
  fields are allowed; absent optional mount flags are not invented or required.
- Local images `localhost/reactbingo-backend:venue`,
  `localhost/reactbingo-frontend:venue`, `docker.io/library/mongo:8.0`.
  App tags must match installed app image IDs. The database must already be running
  with its existing volume/network and `GLIBC_TUNABLES=glibc.pthread.rseq=1`.
  A mismatch is refused; the updater never repairs/recreates the database.
- Space for a raw copy of the Mongo volume, the staged release and new image
  layers. Do not prune images while a rollback backup is still needed.

## Update between games

1. **Pause calling and all game changes throughout update and verification.**
   Leave Mongo running. Do not run `bingo.sh stop`, other launchers, another update
   or direct Podman changes concurrently. The updater locks against other copies
   of itself, not unrelated commands.
2. Transfer and extract the release to a **new directory outside** `~/bingo`, never
   over the installation. Trust its source: bundled checksums detect transfer
   damage, not malicious replacement of both files and checksums.
3. Run with the actual extracted path and an explicit, absolute existing install
   path (quote paths containing spaces):

   ```bash
   bash /absolute/path/to/release/release-tools/install-update.sh "$HOME/bingo"
   ```

4. Require exit status **0** and `Update completed; both apps verified`.
   Nonzero means failed update, even if automatic rollback restored the old apps.
5. **Reload both browsers**: caller phone/tablet and TV/audience viewer. Use the
   existing saved game and existing controller/viewer links. Confirm called
   numbers and pattern display before resuming. Do not create a replacement game,
   clear browser storage or share the private controller capability URL.

## Preservation and rollback

Before state-changing Podman commands, the updater verifies `files.sha256`, exact
payload membership, required entry files, the COPY-only Dockerfiles, all ownership
labels, image IDs, existing launcher/config, mounts/network and frontend-only port
3000. Initial refusal makes **zero state-changing Podman calls**. Ownership/config
are checked again before switching and before rollback; never relabel a foreign
resource to bypass a refusal.

A unique mode-700 `~/bingo/.bingo-update-<suffix>/` contains staged files, original
image tag/ID metadata, config hashes, `database-volume.tar` and standalone
`rollback.sh`/`update.py`. Unique backup image tags retain the old image IDs.
The volume export and backup tags are verified **before disturbing either app**.

Both derived images build on the venue computer with `--pull=never --network=none`,
using its existing images/dependencies and COPYing only `backend-dist/` to
`/app/dist/` and `frontend-dist/` to `/usr/share/nginx/html/`. No native binaries or
new dependencies are supplied. Both builds finish before either `:venue` tag
changes. Only owned frontend/backend containers are replaced; keeping renamed old
containers is avoided to prevent backend network-alias conflicts.

The original launcher starts the new apps. It idempotently starts the
**already-running** database; it never stops/removes/recreates it or changes its
kernel workaround. Image IDs, ownership, running state, original DB identity and
`/api/list` readiness are read back before success. Independently of the unchanged
launcher's weaker check, the updater requires a **nonredirect HTTP 200** and actual
JSON matching backend `GET /list`: exactly `{"games": [strings]}` (an empty `games`
array is valid). Redirects, HTML, malformed JSON and other JSON shapes fail.
The same validator gates update, automatic rollback and standalone rollback;
each probe uses a 3-second curl limit, a 4-second process timeout and a 1 MiB
response limit. The standalone helper retries at most 60 times with 1-second
delays. Response bodies are never printed. No database import/migration/
reset/new-game API is called. Source, launcher, config, cards, saved-game volume
and private links are not overwritten.

Backup/build failure leaves existing apps and `:venue` tags alone (staging/extra
image tags may remain). Failure after switching automatically attempts to restore
old tags and recreate/verify apps using the existing launcher. Successful rollback
still returns nonzero. Failed/blocked rollback is reported without claiming
restoration. Do not delete the database/volume or substitute a new game.

For manual rollback, pause callers and run **one exact backup path** printed by the
updater; replace `<suffix>`, do not use a wildcard:

```bash
bash "$HOME/bingo/.bingo-update-<suffix>/rollback.sh"
```

The saved helper works without the extracted release directory. It preflights all
resource labels, original database identity, config hashes and old image IDs
before any tagging/stopping. Missing apps after a failed update are allowed;
foreign same-named apps are not. It restores old tags, recreates **only apps** with
the original mounts/network/port and verifies readiness. Manual rollback issues
**no database start/stop/remove/run or volume-changing command**. Reload both
browsers afterward. Keep backups/image tags until venue verification is complete.

**Backup limitation:** Mongo stays running during the requested raw volume export.
That is **not a guaranteed application-consistent snapshot, verified recovery or
logical `mongodump`**. The unchanged live database is the preservation strategy;
the export is never automatically restored. Backups contain private game data and
capabilities: keep them private; never distribute/upload them with the release.
Power loss, host failure, image pruning and forced `SIGKILL` recovery are not
promised.

## Release assembly contract (maintainer)

Parent supplies real, tested compiled output. Expected root:

```text
files.sha256
backend-dist/server.js              # plus ALL other backend build output
frontend-dist/index.html            # plus ALL frontend assets/public files
release-tools/install-update.sh
release-tools/update.py
release-tools/Dockerfile.backend
release-tools/Dockerfile.frontend
release-tools/README.md
release-tools/tests/test_update.py  # these three tests optional at runtime
release-tools/tests/fake_podman.py
release-tools/tests/bingo.sh         # non-private pinned launcher test fixture
```

Only optional root documents: `LICENSE`, `README.md`, `RELEASE.md`, also
checksummed. No other root trees/files or extra release-tools entries. Exclude
`.git`, `node_modules`, Python caches, image archives, DB exports and private links.
Only regular files/directories; no symlinks, hard links or special files. Both
Dockerfiles must stay byte-for-byte COPY-only.

`files.sha256` lists **every regular payload file except itself exactly once**:
lowercase SHA-256, two spaces, canonical relative path; no `./`, `..`, absolute
paths, backslashes or duplicates. Generate after all release files are final,
on the build computer, with `RELEASE` pointing at the assembled release:

```bash
python3 - "$RELEASE" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve(strict=True)
rows = []
for path in sorted(root.rglob('*')):
    if path.is_file() and path != root / 'files.sha256':
        if path.is_symlink():
            raise SystemExit('Refusing symlink in release')
        rows.append(hashlib.sha256(path.read_bytes()).hexdigest()
                    + '  ' + path.relative_to(root).as_posix())
(root / 'files.sha256').write_text('\n'.join(rows) + '\n')
PY
```

Never regenerate checksums at the venue to bypass a mismatch. Verify the outer
archive/checksum separately before extracting; this is not a secure extractor.

## Tests and limits

From repository root (all Podman calls use an isolated fake, never real containers):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s release-tools/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 python3 -m unittest discover -s release-tools/tests -v
bash -n release-tools/install-update.sh release-tools/tests/bingo.sh
```

Coverage includes payload/checksum, foreign/missing resources, Podman 4.9 lowercase
network labels, config/image mismatch, exact app mounts before update/rollback,
both exact launcher pins and unknown-byte refusal, backup-before-stop, offline flags,
private file preservation, app-only success, readiness/startup failure, automatic
rollback and standalone manual rollback. Real curl readiness regressions use only
an isolated ephemeral loopback HTTP server via an exact readiness-URL wrapper;
negative standalone retry delays are mocked, not waited out. Fake binaries use an
isolated executable whitelist with no real runtime fallback. Security guards raise
explicitly, never `assert`.

No live/remote venue update was performed. Fake-runtime tests do **not** establish
real Podman 4.9 compatibility, physical offline operation, Mongo recovery, saved-game
browser behavior, iPad Safari, HDMI or venue Wi-Fi acceptance. The builder must
separately test real derived image builds (Docker verifies only the build path,
not Podman transactions). The operator must verify the existing game and both
browsers on site.
