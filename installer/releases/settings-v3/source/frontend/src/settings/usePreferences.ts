import { useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';
export interface Preferences { theme: Theme; volume: number }
export const PREFERENCES_KEY = 'bingo.preferences.v1';
const DEFAULTS: Preferences = { theme: 'dark', volume: 100 };

function isPreferences(value: unknown): value is Preferences {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).length === 2
    && (record.theme === 'dark' || record.theme === 'light')
    && typeof record.volume === 'number' && Number.isInteger(record.volume)
    && record.volume >= 0 && record.volume <= 100;
}

function loadPreferences(): { preferences: Preferences; storageError: string | null } {
  try {
    const stored = localStorage.getItem(PREFERENCES_KEY);
    if (stored === null) return { preferences: { ...DEFAULTS }, storageError: null };
    const parsed: unknown = JSON.parse(stored);
    if (isPreferences(parsed)) return { preferences: parsed, storageError: null };
  } catch {
    // Corrupt data and unavailable storage both keep the app usable.
  }
  return { preferences: { ...DEFAULTS }, storageError: 'Saved preferences could not be read. Using dark appearance and 100% volume.' };
}

/** Device-only preferences: never include audio opt-in or synchronized game data. */
export function usePreferences() {
  const [local, setLocal] = useState(loadPreferences);
  const { preferences, storageError } = local;
  useEffect(() => {
    document.documentElement.dataset.bingoTheme = preferences.theme;
  }, [preferences.theme]);
  const update = (next: Preferences) => {
    let error: string | null = null;
    try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(next)); } catch {
      error = 'Preferences could not be saved. Changes apply to this page only.';
    }
    setLocal({ preferences: next, storageError: error });
  };
  return {
    ...preferences,
    storageError,
    setTheme: (theme: Theme) => update({ ...preferences, theme }),
    setVolume: (volume: number) => update({ ...preferences, volume }),
    restoreDefaults: () => update({ ...DEFAULTS }),
  };
}

export type PreferenceControls = ReturnType<typeof usePreferences>;
