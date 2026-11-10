export interface ManagementSession {
  port: 8766;
  token: string;
}

const SESSION_KEY = 'bingo-management-session-v1';
const LOCAL_ORIGINS = new Set(['http://localhost:3000', 'http://127.0.0.1:3000']);

export type ManagementPhase = 'idle' | 'checking' | 'updating' | 'succeeded' | 'failed';
export interface ManagementStatus {
  currentVersion: string;
  latestVersion: string | null;
  phase: ManagementPhase;
  available: boolean;
  message: string;
}

export class ManagementClient {
  constructor(private session: ManagementSession, private fetcher: typeof fetch = fetch) {}

  async status(): Promise<ManagementStatus> {
    await this.attest();
    const value = await this.request('/status', { headers: this.authHeaders() });
    if (!isRecord(value) || !isVersion(value.currentVersion)
      || !(value.latestVersion === null || isVersion(value.latestVersion))
      || typeof value.available !== 'boolean' || typeof value.message !== 'string'
      || value.message.length > 240 || typeof value.phase !== 'string'
      || !['idle', 'checking', 'updating', 'succeeded', 'failed'].includes(value.phase)) throw unavailable();
    return {
      currentVersion: value.currentVersion, latestVersion: value.latestVersion,
      available: value.available, message: value.message, phase: value.phase as ManagementPhase,
    };
  }

  async check(): Promise<void> {
    await this.attest();
    await this.request('/check', { method: 'POST', headers: this.authHeaders(), body: '{}' });
  }

  async update(version: string): Promise<void> {
    if (!isVersion(version)) throw unavailable();
    await this.attest();
    await this.request('/update', {
      method: 'POST', headers: this.authHeaders(), body: JSON.stringify({ version, confirm: true }),
    });
  }

  private authHeaders(): Record<string, string> {
    return { 'X-Bingo-Token': this.session.token, 'Content-Type': 'application/json' };
  }

  private async attest(): Promise<void> {
    if (this.session.port !== 8766 || !/^[0-9a-f]{64}$/.test(this.session.token) || !crypto.subtle) throw unavailable();
    const challenge = hex(crypto.getRandomValues(new Uint8Array(16)));
    const value = await this.request('/identity', { headers: { 'X-Bingo-Challenge': challenge } });
    if (!isRecord(value) || typeof value.serviceSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.serviceSha256)
      || typeof value.proof !== 'string' || !/^[0-9a-f]{64}$/.test(value.proof)) throw unavailable();
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey('raw', encoder.encode(this.session.token), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const proof = await crypto.subtle.sign('HMAC', key, encoder.encode(`${challenge}.${value.serviceSha256}`));
    if (hex(new Uint8Array(proof)) !== value.proof) throw unavailable();
  }

  private async request(path: '/identity' | '/status' | '/check' | '/update', init: RequestInit): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await this.fetcher(`http://127.0.0.1:8766/v1${path}`, {
        ...init, signal: controller.signal, credentials: 'omit', cache: 'no-store', redirect: 'error', mode: 'cors',
      });
      if (!response.ok || !/^application\/json(?:;|$)/i.test(response.headers.get('Content-Type') ?? '')) throw unavailable();
      const text = await response.text();
      if (text.length > 8192) throw unavailable();
      return JSON.parse(text) as unknown;
    } catch {
      // Do not display untrusted response text, private target paths or tokens.
      throw unavailable();
    } finally {
      clearTimeout(timer);
    }
  }
}

export const APP_VERSION = 'v3.0.0';

function unavailable() {
  return new Error('Local updates are unavailable. Reopen Bingo from the applications menu to retry.');
}

function hex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isVersion(value: unknown): value is string {
  return typeof value === 'string' && /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value) && value.length < 40;
}

let captured = false;
let session: ManagementSession | null = null;

export function initializeManagementSession(): void {
  captured = true;
  const url = new URL(window.location.href);
  const blockedStorage = { getItem: () => null, setItem: () => {} };
  let storage: Pick<Storage, 'getItem' | 'setItem'> = blockedStorage;
  try { storage = window.sessionStorage; } catch { /* Keep capability in memory only. */ }
  try {
    session = readManagementSession(url, storage, () => window.history.replaceState(null, '', url.pathname + url.search));
  } catch {
    session = null;
  }
}

export function getManagementSession(): ManagementSession | null {
  if (!captured) initializeManagementSession();
  if (window.location.pathname.startsWith('/view/')) return null;
  return session;
}

export function readManagementSession(
  url: URL,
  storage: Pick<Storage, 'getItem' | 'setItem'>,
  clearFragment: () => void,
): ManagementSession | null {
  const fragment = /^#bingo-management=(8766)\.([0-9a-f]{64})$/.exec(url.hash);
  if (url.hash.startsWith('#bingo-management=')) clearFragment();
  if (!LOCAL_ORIGINS.has(url.origin) || url.username || url.password || url.pathname.startsWith('/view/')) return null;
  if (url.hash.startsWith('#bingo-management=') && !fragment) return null;
  if (fragment) {
    const session: ManagementSession = { port: 8766, token: fragment[2] };
    try {
      storage.setItem(SESSION_KEY, JSON.stringify(session));
    } catch {
      // An in-memory capability still works when session storage is blocked.
    }
    return session;
  }
  try {
    const value: unknown = JSON.parse(storage.getItem(SESSION_KEY) ?? 'null');
    if (typeof value !== 'object' || value === null || !('port' in value) || !('token' in value)) return null;
    if (value.port !== 8766 || typeof value.token !== 'string' || !/^[0-9a-f]{64}$/.test(value.token)) return null;
    return { port: 8766, token: value.token };
  } catch {
    return null;
  }
}
