import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import DialogProvider from '../dialog/DialogProvider';
import { INITIAL_STATE } from '../game/history';
import type { ServerGameState } from '../game/types';
import { FakeWebSocket } from '../test/fakeWebSocket';
import GamePage from './GamePage';

vi.mock('../api/client', () => ({ getPatterns: vi.fn().mockResolvedValue([['row']]) }));

class FakeAudio {
  static instances: FakeAudio[] = [];
  static nextPlay: (() => Promise<void>) | undefined;
  src = '';
  preload = '';
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  play = vi.fn<() => Promise<void>>().mockImplementation(() => {
    const next = FakeAudio.nextPlay;
    FakeAudio.nextPlay = undefined;
    return next?.() ?? Promise.resolve();
  });
  pause = vi.fn();
  load = vi.fn();
  removeAttribute(name: string) {
    if (name === 'src') this.src = '';
  }
  constructor() {
    FakeAudio.instances.push(this);
  }
}

beforeEach(() => {
  FakeWebSocket.reset();
  FakeAudio.instances = [];
  FakeAudio.nextPlay = undefined;
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('Audio', FakeAudio);
});
afterEach(() => vi.unstubAllGlobals());

async function renderGame(path = '/control/Test/secret', overrides: Partial<ServerGameState> = {}) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <DialogProvider>
        <Link to="/view/Test">Audience test route</Link>
        <Link to="/control/Other/test-code">Another controller</Link>
        <Routes>
          <Route path="/control/:id/:code" element={<GamePage />} />
          <Route path="/view/:id" element={<GamePage />} />
        </Routes>
      </DialogProvider>
    </MemoryRouter>,
  );
  const ws = FakeWebSocket.latest();
  act(() => {
    ws.open();
    ws.receive({ type: 'state', state: { ...INITIAL_STATE, revision: 0, lastPushId: null, ...overrides } });
  });
  await act(async () => {});
  return { ws, ...view };
}

test('controller can opt in to offline audio for newly called numbers', async () => {
  const { ws } = await renderGame();
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  expect(toggle).not.toBeChecked();
  expect(FakeAudio.instances).toHaveLength(0);
  await userEvent.click(toggle);
  expect(toggle).toBeChecked();
  expect(ws.pushes()).toHaveLength(0);
  expect(FakeAudio.instances).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: '7' }));
  expect(FakeAudio.instances).toHaveLength(1);
  expect(FakeAudio.instances[0].src).toBe('/audio/calls/7.mp3');
  expect(FakeAudio.instances[0].play).toHaveBeenCalledTimes(1);
  expect(ws.pushes()[0].state.eventHistory).toEqual([{ number: 7, patterns: [] }]);
});

test('rapid calls play in order without overlapping', async () => {
  const { ws } = await renderGame();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));

  expect(FakeAudio.instances).toHaveLength(1);
  const audio = FakeAudio.instances[0];
  expect(audio.src).toBe('/audio/calls/7.mp3');
  expect(audio.play).toHaveBeenCalledTimes(1);
  act(() => audio.onended?.());
  expect(audio.src).toBe('/audio/calls/16.mp3');
  expect(audio.play).toHaveBeenCalledTimes(2);
  act(() => audio.onended?.());
  expect(audio.play).toHaveBeenCalledTimes(2);
});

test('turning audio off stops the current clip and discards queued calls', async () => {
  await renderGame();
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  const audio = FakeAudio.instances[0];
  const staleEnded = audio.onended;
  await userEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  expect(audio.pause).toHaveBeenCalledTimes(1);
  expect(audio.src).toBe('');
  act(() => staleEnded?.());
  expect(audio.play).toHaveBeenCalledTimes(1);

  await userEvent.click(toggle);
  expect(audio.play).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: '75' }));
  expect(FakeAudio.instances[0].src).toBe('/audio/calls/75.mp3');
  expect(audio.play).toHaveBeenCalledTimes(2);
});

test('leaving the controller stops playback and invalidates late media events', async () => {
  const { unmount } = await renderGame();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  const audio = FakeAudio.instances[0];
  const staleEnded = audio.onended;
  unmount();
  expect(audio.pause).toHaveBeenCalledTimes(1);
  act(() => staleEnded?.());
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test.each(['Undo', 'Bingo', 'Reset'])('%s stops pending callouts without turning the option off', async (control) => {
  await renderGame();
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  const audio = FakeAudio.instances[0];
  const staleEnded = audio.onended;
  await userEvent.click(screen.getByRole('button', { name: control }));
  expect(toggle).toBeChecked();
  expect(audio.pause).toHaveBeenCalledTimes(1);
  act(() => staleEnded?.());
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test.each(['rejected', 'media error', 'thrown'])('%s playback fails safely without losing the number', async (failure) => {
  const { ws } = await renderGame();
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  await userEvent.click(toggle);
  if (failure === 'rejected') FakeAudio.nextPlay = () => Promise.reject(new DOMException('Blocked', 'NotAllowedError'));
  if (failure === 'thrown') FakeAudio.nextPlay = () => { throw new Error('Unavailable'); };
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  if (failure === 'media error') act(() => FakeAudio.instances[0].onerror?.());

  expect(toggle).not.toBeChecked();
  expect(screen.getByRole('alert')).toHaveTextContent(/audio could not play/i);
  expect(screen.getByRole('button', { name: '7' })).toBeDisabled();
  expect(ws.pushes()[0].state.eventHistory).toEqual([{ number: 7, patterns: [] }]);
  await userEvent.click(toggle);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '75' }));
  expect(FakeAudio.instances[0].src).toBe('/audio/calls/75.mp3');
});

test.each(['Audience test route', 'Another controller'])('changing route to %s stops audio and resets opt-in', async (link) => {
  await renderGame();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  const audio = FakeAudio.instances[0];
  await userEvent.click(screen.getByRole('link', { name: link }));
  expect(audio.pause).toHaveBeenCalledTimes(1);
  const toggle = screen.queryByRole('checkbox', { name: 'Audio callouts' });
  if (link === 'Another controller') expect(toggle).not.toBeChecked();
  else expect(toggle).not.toBeInTheDocument();
});

test('a rejected old play promise cannot disable a new playback session', async () => {
  await renderGame();
  let rejectOld!: (reason: Error) => void;
  FakeAudio.nextPlay = () => new Promise<void>((_resolve, reject) => { rejectOld = reject; });
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  await userEvent.click(toggle);
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '75' }));
  await act(async () => rejectOld(new DOMException('Stopped', 'AbortError')));
  expect(toggle).toBeChecked();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(FakeAudio.instances[0].src).toBe('/audio/calls/75.mp3');
});

test('audio stays silent for saved state, remote updates, duplicates and redo', async () => {
  const { ws } = await renderGame('/control/Test/secret', {
    eventHistory: [{ number: 1, patterns: [] }], eventPosition: 1,
  });
  expect(FakeAudio.instances).toHaveLength(0);
  await userEvent.click(screen.getByRole('button', { name: '2' }));
  expect(FakeAudio.instances).toHaveLength(0);
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  act(() => ws.receive({ type: 'state', state: {
    ...INITIAL_STATE, revision: 2, lastPushId: null,
    eventHistory: [{ number: 1, patterns: [] }, { number: 16, patterns: [] }], eventPosition: 2,
  } }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  expect(FakeAudio.instances).toHaveLength(0);
  await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes().at(-1)!.pushId, revision: 3 }));
  await userEvent.click(screen.getByRole('button', { name: 'Redo' }));
  expect(FakeAudio.instances).toHaveLength(0);
});

test('audience has no audio toggle and never plays incoming numbers', async () => {
  const { ws } = await renderGame('/view/Test');
  expect(screen.queryByRole('checkbox', { name: 'Audio callouts' })).not.toBeInTheDocument();
  act(() => ws.receive({ type: 'state', state: {
    ...INITIAL_STATE, revision: 1, lastPushId: null,
    eventHistory: [{ number: 75, patterns: [] }], eventPosition: 1,
  } }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  expect(FakeAudio.instances).toHaveLength(0);
  expect(ws.pushes()).toHaveLength(0);
});

test('a number blocked during a bingo check is not announced', async () => {
  await renderGame('/control/Test/secret', { bingo: true });
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  expect(FakeAudio.instances).toHaveLength(0);
});

test.each(['reset', 'undo queued call', 'undo current call', 'bingo'])('remote %s cancels invalidated playback without changing opt-in', async (correction) => {
  const { ws } = await renderGame();
  const toggle = screen.getByRole('checkbox', { name: 'Audio callouts' });
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[1].pushId, revision: 2 }));
  const audio = FakeAudio.instances[0];
  const staleEnded = audio.onended;
  const remote: ServerGameState = { ...ws.pushes()[1].state, revision: 3, lastPushId: null };
  if (correction === 'reset') Object.assign(remote, { eventHistory: [], eventPosition: 0 });
  if (correction === 'undo queued call') remote.eventPosition = 1;
  if (correction === 'undo current call') Object.assign(remote, { eventHistory: [{ number: 16, patterns: [] }], eventPosition: 1 });
  if (correction === 'bingo') remote.bingo = true;
  act(() => ws.receive({ type: 'state', state: remote }));

  expect(toggle).toBeChecked();
  expect(audio.pause).toHaveBeenCalledTimes(1);
  expect(audio.src).toBe('');
  act(() => staleEnded?.());
  expect(audio.play).toHaveBeenCalledTimes(1);
  expect(ws.pushes()).toHaveLength(2);
});

test('remote additions do not cancel valid queued calls or announce remote numbers', async () => {
  const { ws } = await renderGame();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[1].pushId, revision: 2 }));
  const audio = FakeAudio.instances[0];
  const remote: ServerGameState = {
    ...ws.pushes()[1].state, revision: 3, lastPushId: null,
    eventHistory: [...ws.pushes()[1].state.eventHistory, { number: 75, patterns: [] }], eventPosition: 3,
  };
  act(() => ws.receive({ type: 'state', state: remote }));
  expect(audio.pause).not.toHaveBeenCalled();
  expect(audio.src).toBe('/audio/calls/7.mp3');
  act(() => audio.onended?.());
  expect(audio.src).toBe('/audio/calls/16.mp3');
  act(() => audio.onended?.());
  expect(audio.play).toHaveBeenCalledTimes(2);
  expect(ws.pushes()).toHaveLength(2);
});
