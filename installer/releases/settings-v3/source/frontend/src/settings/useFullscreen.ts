import { useEffect, useState } from 'react';

/** Browser-owned state, never a persisted preference or part of game sync. */
export function useFullscreen() {
  const [active, setActive] = useState(() => Boolean(document.fullscreenElement));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const changed = () => setActive(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);

  const toggle = async () => {
    if (busy) return;
    const exiting = Boolean(document.fullscreenElement);
    if (exiting ? !document.exitFullscreen : !document.documentElement.requestFullscreen || document.fullscreenEnabled === false) {
      setError('Fullscreen is not supported in this browser. You can still use all game controls.');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      if (exiting) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
      setActive(Boolean(document.fullscreenElement));
    } catch {
      setError('Could not change fullscreen. Try again or use your browser’s fullscreen controls.');
    } finally {
      setBusy(false);
    }
  };
  return { active, busy, error, toggle };
}
