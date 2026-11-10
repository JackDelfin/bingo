# Controller audio release v2

This is the default release selected by `installer/bootstrap.py` for both fresh
installs and existing-install updates through `bash "$HOME/bingo/install.sh"`.
No separate audio install or update command is needed. Desktop registration
refreshes the installed helper after an update; `--no-desktop` defers that refresh
until an explicit `--desktop-only` run.

## Frozen inputs

- `payload/frontend-dist/` is an exact copy of the finished
  `source/frontend/dist/`, including all 75 bundled MP3 callouts and metadata.
- The remaining payload files, updater, assets, build recipes and backend source
  are byte-identical copies of the historical top-level installer inputs.
- Frontend source starts with the historical source and overlays only
  `src/game/useNumberCallouts.ts`, `src/pages/GamePage.tsx`, `src/index.css`,
  `src/pages/GamePage.callouts.test.tsx`, `scripts/` and `public/audio/` from the
  finished controller-audio source. Generator/script provenance is retained.
- No historical tree or source distribution is replaced. Do not edit a released
  tree in place; create another version and update the mutable selector/pins.

## SHA-256 authorities

- Current `payload/files.sha256`:
  `fd50b3c289286e0bc596dd63d33838743ce105017aaa1f61f2ae0346c6e087cd`
- Current `fresh-files.json`:
  `1037dbb623cc1cbbadec1d2d49b5f7e9ba6849e87007e8b5ea887c8bebf85d2e`
- Preserved `payload/release-tools/update.py`:
  `7e671ccaa85ee0b4e7853a4140e5845ae7f5e3905e7553d910e73bf8f0865bff`
- Recognized historical payload manifest (receipt/journal migration only):
  `198ba57eaf14633986011dbe03d6876c743d198312693295a843f55fe0b50aa8`

The bootstrap and installed desktop helper recognize only the historical and
current receipt/journal pins. A failed current-release verification never falls
back to the historical payload. New receipts and journals record the selected
current pin. Membership/checksum, ownership, locking, storage identity and exact
configured/actual port checks remain required.

Strict-fake installer/desktop tests verify packaging and command flow, not a real
Podman build, deployment, physical desktop click, audio playback or laptop setup.
