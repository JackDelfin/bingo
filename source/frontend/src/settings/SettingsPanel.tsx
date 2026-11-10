import { useId, useRef, useState, type ReactNode } from 'react';
import type { PreferenceControls, Theme } from './usePreferences';
import { useFullscreen } from './useFullscreen';
import './settings.css';

export interface SettingsPanelProps {
  preferences: PreferenceControls;
  audio?: { enabled: boolean; setEnabled: (enabled: boolean) => void; error?: string | null };
  children?: ReactNode;
}

/** Native modal semantics provide browser focus containment and Escape support. */
export default function SettingsPanel({ preferences, audio, children }: SettingsPanelProps) {
  const fullscreen = useFullscreen();
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const volumeId = useId();
  const audioHelpId = useId();
  const close = () => dialog.current?.close();
  return (
    <>
      <button type="button" className="settings-trigger" ref={trigger} onClick={() => {
        dialog.current?.showModal();
        setOpen(true);
        closeButton.current?.focus();
      }}>Settings</button>
      <dialog className="settings-panel" ref={dialog} aria-labelledby={titleId}
        onCancel={(event) => { event.preventDefault(); close(); }}
        onClose={() => { setOpen(false); trigger.current?.focus(); }}>
        <header className="settings-header">
          <h2 id={titleId}>Settings</h2>
          <button type="button" ref={closeButton} onClick={close} aria-label="Close settings">Close</button>
        </header>
        {audio && <div className="settings-audio">
          <label className="settings-toggle">
            <input type="checkbox" checked={audio.enabled} aria-describedby={audioHelpId}
              onChange={(event) => audio.setEnabled(event.target.checked)} />
            Audio callouts
          </label>
          <p id={audioHelpId}>Plays on this controller only. Works offline. Off at the start of each controller session; never saved.</p>
          {audio.error && <p role="alert">{audio.error}</p>}
        </div>}
        <label className="settings-field">Appearance
          <select value={preferences.theme} onChange={(event) => preferences.setTheme(event.target.value as Theme)}>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>
        <label className="settings-field" htmlFor={volumeId}>Volume
          <output htmlFor={volumeId}>{preferences.volume}%</output>
          <input id={volumeId} type="range" min="0" max="100" step="1" value={preferences.volume}
            aria-valuetext={`${preferences.volume}%`}
            onChange={(event) => preferences.setVolume(Number(event.target.value))} />
        </label>
        {preferences.storageError && <p role="status">{preferences.storageError}</p>}
        <button type="button" disabled={fullscreen.busy} onClick={() => void fullscreen.toggle()}>
          {fullscreen.active ? 'Exit fullscreen' : 'Enter fullscreen'}
        </button>
        {fullscreen.error && <p role="alert">{fullscreen.error}</p>}
        <p className="settings-safety">Game reset always asks for confirmation to protect called numbers and awarded patterns.</p>
        <button type="button" onClick={() => {
          audio?.setEnabled(false);
          preferences.restoreDefaults();
        }}>Restore defaults</button>
        <p className="settings-help">Restores dark appearance, 100% volume and audio off. Never resets the game or winning pattern.</p>
        {open && children}
      </dialog>
    </>
  );
}
