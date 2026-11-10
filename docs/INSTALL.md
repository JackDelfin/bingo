# Install or update Bingo

From the repository checkout, run **as your ordinary Linux user**:

```bash
bash ~/bingo/install.sh
```

Run that same command again to apply the checked-out bundled application release to the existing installation. It does **not** run `git pull` or download an installer script. Obtain/review a newer checkout separately when a new release is available.

For a managed desktop installation with Settings support, open **Bingo** from the applications menu, then use **Settings → Check for updates**. A newer published stable release offers an explicit **Install update** confirmation. This downloads a checksummed release from `JackDelfin/bingo`, retains its source in a private managed cache, and runs the preserving installer there; it does not rewrite your separate development checkout. Updates need internet and should be run between games. Nothing checks or installs automatically. See [Settings and release updates](SETTINGS.md) for local-host authorization, saved-data preservation, and recovery limits.

The product is **Bingo**. Existing internal resource and state names are retained for installation and saved-data compatibility.

## First installation

- Supported bootstrap package managers: Ubuntu/Debian with `apt-get` and interactive `sudo`. Ubuntu 24.04+ or Debian 12+ is the intended baseline, with rootless Podman working for your account. Do not run the installer or Podman with sudo.
- Internet is required for initial OS packages, public base images, and `npm ci`. Missing Python 3, Podman, curl, and Podman's rootless helper packages can be installed through ordinary interactive `sudo apt-get`. No unattended password handling or system/rootful containers are used. If rootless Podman cannot initialize (for example missing subordinate UID/GID ranges), the installer stops; an administrator must configure that account first.
- Use a machine supported by the Node 26, Mongo 8.0, and nginx images, with enough disk/RAM for two application builds. Mongo's CPU requirements still apply (including AVX on x86-64). Installation on every distribution/architecture is **not** certified by these tests.
- The default installation is `${XDG_DATA_HOME:-$HOME/.local/share}/venue-bingo`, **not the Git checkout**. Paths must be absolute, owned/safely parented, and cannot contain colons, control characters, or symlink components. Spaces are supported.
- Backend/frontend are built on the target architecture using pinned bundled source and Node 26 build images; no host Node installation or host `node_modules` is copied. The backend build runs its tests. New bootstrap Dockerfiles use fully qualified registry names; existing source Dockerfiles and the approved launcher are unchanged.
- Only the frontend publishes host TCP **3000**. Open `http://localhost:3000` on the host computer, or `http://BINGO-HOST-IP:3000` on another device on the same network. Mongo/backend have no host-published ports. Arrange firewall access on a trusted LAN separately; do not expose this application directly to the internet.
- Mongo uses the owned `venue-bingo-data` volume and `GLIBC_TUNABLES=glibc.pthread.rseq=1`. No game is created or reset. The bundled cards file is a **sample**, not a claim to contain your event's paper cards. Only the newly installed public sample `cards.csv` gets mode 0644 so the backend's `USER node` can read its read-only bind mount under default rootless mapping; its target/source parent directories remain private (0700). Existing files are never chmodded, chowned, or replaced by reruns.

Useful alternatives:

```bash
bash ~/bingo/install.sh --help
bash ~/bingo/install.sh --check
bash ~/bingo/install.sh --dir "$HOME/bingo with spaces"
bash ~/bingo/install.sh --offline
```

`--check` validates without changing installation/runtime resources or installing packages. On an existing installation it also verifies the actual API response, app image IDs, ownership, mounts/network/ports, and running database. `--offline` is supported **only** for an already-running, recognized installation with local prerequisites and base images; fresh installation offline is refused.

## Desktop startup

On a Linux desktop, open **Bingo** from your applications menu after installation. Pin it to your dock or favorites for one-click access. You do not need to open a terminal and type a start command each time.

The shortcut starts the existing installed database, backend and frontend if they are stopped, checks readiness, then requests `http://localhost:3000` in your default browser using `xdg-open`. A temporary startup terminal stays open during checks; it closes after browser dispatch and keeps an interactive startup error visible on failure. This is a browser-based application with a desktop shortcut, not a separate native window or an automatic login/boot service. A graphical login, a terminal emulator, a default browser, and `xdg-open` must be available on the target desktop. Other devices still connect over the trusted LAN.

Browser dispatch is not proof that a window appeared. Some browser openers remain running normally; the helper detaches them rather than waiting for the browser to close. Immediate launch errors are reported, but a later browser failure cannot be detected after detaching. If no window appears, open `http://localhost:3000` manually.

Normal install/update runs register the shortcut for your account. For an already managed installation, add or refresh only the shortcut without rebuilding, updating or starting any containers:

```bash
bash ~/bingo/install.sh --desktop-only
```

For a headless installation, skip desktop registration:

```bash
bash ~/bingo/install.sh --no-desktop
```

The app-menu entry and its local icon/helper are managed separately from the original `bingo.sh` launcher, source/configuration and saved data. The helper is stored with the installed app, so the shortcut does not need the checkout to remain in its original location. Do not move the installed app directory. No physical Desktop-folder shortcut or desktop-specific “trusted launcher” setting is installed automatically.

Clicking Bingo is **startup only**: it never runs the installer/updater, pulls/builds images, creates replacement containers, creates/resets a game, or removes volumes. All expected resources must already exist and match the recognized installation before anything is started. Missing, foreign or altered resources, a pending maintenance journal, and concurrent installation/update activity cause refusal rather than repair. If startup or readiness fails, the browser is not opened. Closing the browser does not stop the background services; clicking Bingo again reuses the installation.

If desktop registration fails after a successful app installation/update, the app and its data remain in place. Read the reported error, resolve the shortcut-path/ownership conflict, then retry `--desktop-only`; do not reinstall or delete the database. An unrelated existing shortcut or a changed managed file is not silently overwritten. `--check` never creates or refreshes desktop files.

## Reruns and existing installations

A private durable pointer in `$HOME/.local/state/venue-bingo/installation.json` identifies the original target, even if `XDG_DATA_HOME` changes later. An explicit `--dir` must agree with an existing pointer. Without a pointer, the installer checks the default location and the known legacy `$HOME/bingo` installation; checkout/release/QA trees are never adopted. An unknown launcher, incomplete resource set, foreign resource, changed recorded configuration/storage identity, or ambiguous target is refused rather than repaired. Storage identity is the validated `graphRoot`, `runRoot`, and `graphDriverName`; changing container/image counters or used bytes is not an identity change. Malformed or old whole-telemetry receipts are refused, not silently migrated.

The existing database must already be running. Reruns use the byte-identical approved offline updater, verified against its SHA-256 pin and the complete immutable payload manifest before execution. Both app candidates are built before publishing either production image tag. Updates are app-only: they retain the database container/volume, launcher, cards/configuration, source, and private files. There is no version-string-only skip: every update revalidates deployment invariants and API readiness. Reload both browsers after success.

Private `.bingo-update-*` backups and their standalone `rollback.sh` are retained in the installation. A live Mongo volume export is **not a guaranteed transaction-consistent or verified recovery backup**. The updater preserves data by not replacing/resetting the running database; it never imports an export automatically. Take a separate appropriate database backup before production maintenance.

## Failures and conservative recovery

All mutating runs share a per-user namespace lock and the installation-directory lock. Before pulls/builds/publication, a private journal is fsynced to `$HOME/.local/state/venue-bingo/operation.json`; it records intent and resource identities as they become available. A failed or interrupted operation leaves a pending/failure journal. **Rerunning then refuses** instead of guessing what partial resources it may adopt. Candidate images and any volumes are preserved, not deleted automatically. No owned-but-unexplained volume is adopted.

Do not delete a volume, reset Mongo, or blindly remove the journal to force a retry. Preserve the journal and `.bingo-update-*` backups, inspect the named target and exact resource identities, and have an administrator reconcile the partial operation. For a failed update, `.venue-install/last-update.log` records the frozen updater's controlled output; use its private rollback instructions if needed. Even when automatic rollback succeeds, the update exits nonzero and the failed journal requires investigation. There is deliberately no automatic cleanup or general repair mode.

### Manual app rollback with Podman 4.9 blank wildcard addresses

On affected Podman 4.9 inspections, the frontend's wildcard `HostIp` is the empty string rather than `0.0.0.0`. Normal checkout updates and their automatic rollback use the compatibility adapter. **The generated backup's standalone `rollback.sh` invokes its unchanged copied updater without that adapter and can refuse this schema**, reporting `Only the frontend may publish host port 3000`. Do not edit the backup's `rollback.sh`, `update.py`, metadata, or frozen payload to bypass the refusal.

After investigating the failed update, use the corrected checkout at `$HOME/bingo` to load `bootstrap.trusted_payload()`: it verifies the pinned updater and immutable payload, retaining the inspection adapter. Run as the **same ordinary user, without sudo**, with no concurrent installer/update/desktop startup. Replace the second argument below with the **one explicit absolute backup directory** reported by the updater; do not use a wildcard, guess a suffix, or point at the checkout. Review that backup's metadata and keep the original backup and failed journal.

<!-- podman49-manual-rollback -->
```bash
python3 -B -c 'import runpy,sys; module=runpy.run_path(sys.argv[1])["trusted_payload"](); sys.argv=[module.__file__, "--rollback", sys.argv[2]]; raise SystemExit(module.main())' "$HOME/bingo/installer/bootstrap.py" "/absolute/path/to/installation/.bingo-update-EXACT-SUFFIX"
```

This calls the verified pinned module's manual rollback with the adapter and the explicit backup. It revalidates ownership, images, mounts, network, ports and configuration before restoring old **app images/containers** and checking the API. It does not stop/recreate the database, import the volume export, reset games, or replace configuration. A refusal is not permission to relax those checks.

Even a successful app-only rollback **does not reconcile the installer's receipt, pointer, or failed operation journal**. Keep all of them unchanged, along with the original backup, for investigation; do not delete a receipt or journal to force the installer to run. The installer/desktop may continue refusing until the failed maintenance state is separately reconciled. This command is a manual app rollback, not a general installation-repair mode.

## Verification limits

The repository's installer tests use a fail-closed **fake Podman** and a strictly allowlisted isolated `PATH`; fake apt/sudo tests cannot reach a real package manager. They exercise fresh installation followed by the same command's offline update, private-file/database-resource preservation, locks, collisions, corruption, wrong targets, partial state, startup failure, and update failure with successful app-only rollback. The bundled updater also has its own normal/optimized fault and loopback-HTTP tests.

These tests do **not** establish real image-build, rootless-runtime, package installation, hardware/CPU, restart-after-reboot, or target-machine acceptance. The nonroot cards-access regression is a fake POSIX DAC simulation, not actual Podman UID/mount/SELinux proof. Real first-run installation, the actual image/user/rootless mapping, a real Mongo database, an actual offline router/LAN, iPad Safari, and HDMI/TV operation still require supervised acceptance on the intended machine. No such deployment was performed while preparing this installer.
