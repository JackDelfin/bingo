# Winning-pattern update — 75-ball manual bingo

This update adds a synchronized **PLAYING FOR** panel in the bottom-right of the flashboard. The private caller can change it; audience browsers only display it.

## Choosing the next game

1. Open your existing private caller link. Public game-list links are audience-only.
2. Use the **Winning pattern** dropdown in the bottom-right.
3. Choose a preset, check its highlighted squares, and announce the rule to players.
4. The audience screen updates automatically. Patterns with alternatives cycle through examples; **any one** alternative wins, not the union of all examples.

There are **88 presets**, including full card/coverall, T, X, single/double/triple lines, corners, postage stamps, blocks, inside/outside frames, diamonds, crosses, arrow, kite, bow tie, heart, checkerboards, stairs, pyramid, alphabet letters and rotated variants. This is a broad catalog, not a claim to include every house rule or every type of bingo. It is for 75-ball, 5×5 cards with a free center, not 90-ball or 80-ball bingo. Exact diagrams, not pattern names alone, define the displayed rule. Research and per-pattern caveats are in `source/PATTERN-SOURCES.md`.

## Custom patterns

Choose **Custom pattern…**, give it a name, and tap required squares. You can clear/fill the grid, rotate it clockwise, and add up to twelve alternatives. The center stays free. Empty/free-center-only patterns cannot be saved. Save, then approve the mid-game confirmation if one appears.

Custom patterns are saved with the game and synchronized just like presets. They are not a global library shared among separate games.

## Calls and winners

- Changing the pattern keeps all called numbers and undo/redo history. Once any ball has been called, a confirmation is required.
- All calls in the current game count toward the chosen pattern. This is not a separate-call-range progressive-game system. For a fresh round, deliberately reset the board and confirm the reset.
- Pattern changes are disabled during a BINGO check.
- **Check winning paper cards manually.** The application does not know your venue's physical card set. Confirming a winner records the caller's decision; it does not certify a paper card.
- Old saved games open with **Any straight line** unless a pattern was already selected. Existing private caller links still apply.

## Installing later on the venue computer

This package is an **update to the existing repaired rootless-Podman installation**, not a fresh install. No venue access or feature deployment was performed during development.

Use `INSTALL.md` when you have the venue computer. Extract into a NEW directory outside the current `~/bingo` installation. Do not unpack source over the existing installation and do not use `sudo podman`.

The installer derives new application images from the images already installed on that computer, using precompiled architecture-independent JavaScript/static files. It does not need npm, Node, image pulls or an internet connection. It retains the database container/volume, MongoDB kernel workaround, launcher, nginx configuration and original installed source. Its checks deliberately refuse unfamiliar or changed installations rather than guessing.

Reload both caller and audience browsers after the update. Test with a disposable game before event use. Keep caller links and the installer-created private backup private.

## Still needs an on-site check

The actual venue update, physical iPad Safari, HDMI/TV readability and the router with its WAN unplugged have not been verified. A working local network is required even without internet. Do not expose the private-LAN app to the public internet.
