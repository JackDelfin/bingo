import { useEffect, useId, useRef, useState } from 'react';
import { APP_VERSION, getManagementSession, ManagementClient, type ManagementStatus } from './management';
import './updates.css';

const UNAVAILABLE = 'Local updates are unavailable. Reopen Bingo from the applications menu to retry.';

export default function UpdatesPanel() {
  const [client] = useState(() => {
    const session = getManagementSession();
    return session ? new ManagementClient(session) : null;
  });
  const [status, setStatus] = useState<ManagementStatus | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const operation = useRef(false);
  const mounted = useRef(true);
  const cancel = useRef<HTMLButtonElement>(null);
  const heading = useId();
  const confirmTitle = useId();
  const busy = pending || status?.phase === 'checking' || status?.phase === 'updating';

  useEffect(() => {
    mounted.current = true;
    if (!client) return () => { mounted.current = false; };
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await client.status();
        if (cancelled) return;
        setStatus(next);
        setError('');
        if (next.phase === 'checking' || next.phase === 'updating') timer = setTimeout(() => void poll(), 1000);
      } catch {
        if (cancelled) return;
        setError(UNAVAILABLE);
        setStatus((previous) => previous ? { ...previous, available: false, latestVersion: null, phase: 'failed' } : null);
      }
    };
    void poll();
    return () => { cancelled = true; mounted.current = false; clearTimeout(timer); };
  }, [client, refresh]);

  useEffect(() => { if (confirming) cancel.current?.focus(); }, [confirming]);

  const perform = async (kind: 'check' | 'update') => {
    if (!client || operation.current || busy || !status) return;
    const version = status.latestVersion;
    if (kind === 'update' && (!confirming || !status.available || !version)) return;
    operation.current = true;
    setPending(true);
    setConfirming(false);
    setError('');
    setStatus({ ...status, phase: kind === 'check' ? 'checking' : 'updating' });
    try {
      if (kind === 'check') await client.check();
      else await client.update(version!);
      if (mounted.current) setRefresh((value) => value + 1);
    } catch {
      if (mounted.current) {
        setError(UNAVAILABLE);
        setStatus({ ...status, available: false, latestVersion: null, phase: 'failed' });
      }
    } finally {
      operation.current = false;
      if (mounted.current) setPending(false);
    }
  };

  let message = '';
  if (status?.phase === 'checking') message = 'Checking GitHub for the latest stable release…';
  else if (status?.phase === 'updating') message = 'Installing update. Calling may pause briefly; keep Bingo open.';
  else if (status?.phase === 'succeeded') message = 'Update installed and verified. Reload Bingo to use it.';
  else if (status?.phase === 'failed') message = 'The update check or installation did not finish. Investigate before retrying; do not delete saved data or recovery records.';
  else if (status?.latestVersion) message = status.available ? `New release available: ${status.latestVersion}` : 'No newer stable release is available.';

  return (
    <section className="updates-panel" aria-labelledby={heading} aria-busy={busy}>
      <h3 id={heading}>Updates</h3>
      <p>Installed version: <strong>{status?.currentVersion ?? APP_VERSION}</strong></p>
      <p>Checks are manual. Internet is needed only to check and download; normal play works offline.</p>
      {!client && <p>To update, open Bingo from the applications menu on this computer. Remote phones, tablets and audience screens cannot update the host.</p>}
      <button type="button" disabled={!client || !status || busy || confirming} onClick={() => void perform('check')}>Check for updates</button>
      {client && error && <button type="button" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Retry local connection</button>}
      <p role="status" aria-live="polite">{error || message}</p>
      {status?.available && status.latestVersion && !confirming && status.phase !== 'succeeded' && (
        <button type="button" disabled={busy} onClick={() => setConfirming(true)}>Update to {status.latestVersion}</button>
      )}
      {confirming && <div className="update-confirmation" role="group" aria-labelledby={confirmTitle}>
        <h4 id={confirmTitle}>Update Bingo now?</h4>
        <p>Finish your current game first. Updating briefly restarts the app and can disconnect controllers. Saved games, patterns and configuration are preserved.</p>
        <p>The latest stable release updates Bingo’s managed local source checkout, not your separate development repository.</p>
        <div className="update-actions">
          <button type="button" ref={cancel} onClick={() => setConfirming(false)}>Cancel update</button>
          <button type="button" disabled={busy} onClick={() => void perform('update')}>Install update</button>
        </div>
      </div>}
      {status?.phase === 'succeeded' && <button type="button" onClick={() => window.location.reload()}>Reload Bingo</button>}
      <a href="https://github.com/JackDelfin/bingo/releases" target="_blank" rel="noreferrer">Release notes</a>
    </section>
  );
}
