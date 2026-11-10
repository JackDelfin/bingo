import { afterEach, describe, expect, test, vi } from 'vitest';
import { ManagementClient, readManagementSession } from './management';

afterEach(() => vi.unstubAllGlobals());

function storage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
  };
}

describe('local update capability', () => {
  test('consumes a desktop launch fragment on the fixed local app origin', () => {
    const local = storage();
    const clear = vi.fn();
    const result = readManagementSession(new URL(`http://localhost:3000/#bingo-management=8766.${'a'.repeat(64)}`), local, clear);
    expect(result !== null).toBe(true);
    expect(result?.port).toBe(8766);
    expect(clear).toHaveBeenCalledOnce();
    expect(readManagementSession(new URL('http://localhost:3000/'), local, vi.fn()) !== null).toBe(true);
  });

  test('refuses an invalid new fragment rather than reusing an old capability', () => {
    const local = storage();
    readManagementSession(new URL(`http://localhost:3000/#bingo-management=8766.${'a'.repeat(64)}`), local, vi.fn());
    const result = readManagementSession(new URL('http://localhost:3000/#bingo-management=8766.invalid'), local, vi.fn());
    expect(result === null).toBe(true);
  });

  test.each(['http://192.168.1.10:3000', 'https://localhost:3000', 'http://localhost:3001', 'http://localhost:3000/view/game'])('never enables host updates on %s', (base) => {
    const clear = vi.fn();
    expect(readManagementSession(new URL(`${base}#bingo-management=8766.${'a'.repeat(64)}`), storage(), clear) === null).toBe(true);
    expect(clear).toHaveBeenCalledOnce();
  });

  test('blocked or corrupt storage cannot break local play', () => {
    const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
    expect(readManagementSession(new URL('http://localhost:3000/'), blocked, vi.fn())).toBeNull();
    expect(readManagementSession(new URL(`http://localhost:3000/#bingo-management=8766.${'a'.repeat(64)}`), blocked, vi.fn()) !== null).toBe(true);
    const corrupt = storage();
    corrupt.setItem('bingo-management-session-v1', '{');
    expect(readManagementSession(new URL('http://localhost:3000/'), corrupt, vi.fn())).toBeNull();
  });
});

describe('attested local update requests', () => {
  test('proves the listener knows the capability before sending it', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (value: Uint8Array) => value.fill(1),
      subtle: {
        importKey: vi.fn(async () => ({})),
        sign: vi.fn(async () => new Uint8Array(32).fill(2).buffer),
      },
    });
    const session = { port: 8766 as const, token: 'a'.repeat(64) };
    const status = { currentVersion: 'v3.0.0', latestVersion: null, available: false, phase: 'idle', message: '' };
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const headers = new Headers(init?.headers);
      if (String(input).endsWith('/identity')) {
        expect(headers.has('X-Bingo-Token')).toBe(false);
        const challenge = headers.get('X-Bingo-Challenge');
        const serviceSha256 = 'b'.repeat(64);
        expect(challenge).toBe('01'.repeat(16));
        const proof = '02'.repeat(32);
        return new Response(JSON.stringify({ serviceSha256, proof }), { headers: { 'Content-Type': 'application/json' } });
      }
      expect(headers.get('X-Bingo-Token') === session.token).toBe(true);
      expect(init?.credentials).toBe('omit');
      expect(init?.redirect).toBe('error');
      expect(init?.cache).toBe('no-store');
      return new Response(JSON.stringify(status), { headers: { 'Content-Type': 'application/json' } });
    });
    const client = new ManagementClient(session, fetcher);
    expect(await client.status()).toEqual(status);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
