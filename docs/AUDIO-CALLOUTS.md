# Audio callouts

On a game's **controller** page, turn on **Audio callouts** below the Undo, Redo,
and Reset buttons. New numbers you click will be spoken as their Bingo letter
and number, for example “B, seven” or “O, seventy-five.”

- **Off by default.** Opening or reloading a game starts with audio off. Enabling
  it does not replay the saved game or change anything on the board.
- **This device only.** Sound comes from the phone, tablet, or computer running
  that controller tab. Connect that device to the speakers you want to use.
  Other controllers and read-only audience screens remain silent.
- **Works without internet.** All 75 English callouts are bundled with Bingo.
  The browser downloads them from the same Bingo server over your local network;
  no speech service, browser speech voice, account, or extra software is needed.
  The Bingo server and local network must still be available.
- **No overlapping voices.** Rapid new calls play in order. Switching audio off,
  Undo, Redo, Reset, beginning a Bingo check, or leaving the game stops playback
  and clears any pending announcements. Undo/redo, loading a game, and changes
  from another controller do not announce numbers.
- **Saved games are unchanged.** Audio is a local controller preference, not a
  saved or synchronized game setting. Changing the option sends no game update.

If audio cannot play, Bingo turns the option off and shows a message; the number
is still called and synchronized normally. Check device volume, browser/tab
muting, speaker connections, and browser media permissions, then enable the
option again. Re-enabling does not replay the failed or pending calls.

## Implementation and verification

`source/frontend/src/game/useNumberCallouts.ts` owns sequential HTML audio
playback and cancellation. `GamePage.tsx` announces only local number-button
calls and scopes playback to one controller route. Bundled assets live in
`source/frontend/public/audio/calls/`; their metadata records synthesis inputs,
voice, and hashes. Build-time generation uses local eSpeak NG and FFmpeg; neither
is a runtime requirement.

Frontend tests cover opt-in behavior, queued calls, cancellation, playback
failures, stale playback promises, route changes, and silent audience/reload/sync
behavior. Browser media decoding/playback checks are separate from an actual
speaker or tablet test. Hardware volume, Bluetooth/HDMI routing, and iPad Safari
must be checked on the event's actual equipment.
