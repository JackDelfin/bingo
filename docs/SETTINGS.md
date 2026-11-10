# Bingo settings

Open **Settings** on the home page or a game's controller page. The audience
screen intentionally has no settings or update controls.

## Preferences on this device

| Setting | Default | Behavior |
| --- | --- | --- |
| Appearance | Dark | Dark or light; remembered in this browser when local storage is available. |
| Volume | 100% | Applies to Bingo's audio callouts on this device, including queued calls. Remembered locally. |
| Audio callouts | Off | Controller only. Opt in for the current controller session; never saved or sent to audience screens. |
| Fullscreen | Browser-controlled | Enter or exit fullscreen where supported. A refusal leaves the game running and displays guidance. |

**Restore defaults** restores dark appearance, 100% volume and audio off. It
never clears numbers, changes the winning pattern or resets a game. Game reset
always requires its separate confirmation.

Preferences are not shared with other controllers. If local storage is blocked,
the controls still work for the current page; Bingo explains that it cannot save
them. A reload starts audio off even when other preferences were remembered.

Use Escape or **Close** to dismiss Settings. The dialog traps keyboard focus
while open and returns focus to its Settings button when closed.

## Updates

Use updates on the **computer hosting Bingo**, launched through its **Bingo**
applications-menu shortcut. A remote phone, tablet or audience screen cannot
update the host. If local updates are unavailable, close and reopen Bingo from
that shortcut; ordinary play does not require the local update service.

1. Finish the game and arrange a maintenance break.
2. Open **Settings → Updates → Check for updates**.
3. Review the version and release notes. Checking alone never installs a release.
4. If a newer release is available, choose **Update to [version]**, review the
   confirmation, then choose **Install update**. Canceling leaves the installation
   alone.
5. Follow the reported result. After a successful update, reload the page.

Internet is needed for release checks and downloads, not for normal LAN play.
Checks are manual; opening Settings does not automatically contact GitHub. The
release source is the latest stable release of
[`JackDelfin/bingo`](https://github.com/JackDelfin/bingo/releases/latest).

Update only between games: the application services can restart during an
update. Updates use the preserving installer rather than replacing the database
or doing a destructive `git reset` in your working copy. Keep any failure logs,
maintenance journal and backups; do not delete a journal or database volume to
force a retry. See [installation and recovery](INSTALL.md) for preserving-update
limitations and recovery guidance.
