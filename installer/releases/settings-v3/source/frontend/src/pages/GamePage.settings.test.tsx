import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import DialogProvider from '../dialog/DialogProvider';
import { INITIAL_STATE } from '../game/history';
import type { ServerGameState } from '../game/types';
import { FakeWebSocket } from '../test/fakeWebSocket';
import GamePage from './GamePage';

vi.mock('../api/client', () => ({ getPatterns: vi.fn().mockResolvedValue([['row']]) }));

class FakeAudio {
  static instances: FakeAudio[] = [];
  src = '';
  volume = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  play = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  pause = vi.fn();
  load = vi.fn();
  removeAttribute(name: string) { if (name === 'src') this.src = ''; }
  constructor() { FakeAudio.instances.push(this); }
}

beforeEach(() => {
  localStorage.clear();
  FakeWebSocket.reset();
  FakeAudio.instances = [];
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('Audio', FakeAudio);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) { this.setAttribute('open', ''); },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    },
  });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function renderGame(path = '/control/Test/secret', overrides: Partial<ServerGameState> = {}) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <DialogProvider>
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
async function openSettings() {
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  return within(screen.getByRole('dialog', { name: 'Settings' }));
}

test('controller settings include updates without enabling host updates from a remote browser', async () => {
  await renderGame();
  const settings = await openSettings();
  expect(settings.getByRole('heading', { name: 'Updates' })).toBeInTheDocument();
  expect(settings.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
});

test('controller settings share the inline session-only audio toggle without syncing or persisting opt-in', async () => {
  const first = await renderGame();
  const settings = await openSettings();
  const audio = settings.getByRole('checkbox', { name: 'Audio callouts' });
  expect(audio).not.toBeChecked();
  await userEvent.click(audio);
  expect(audio).toBeChecked();
  await userEvent.selectOptions(settings.getByRole('combobox', { name: 'Appearance' }), 'light');
  fireEvent.change(settings.getByRole('slider', { name: /Volume/ }), { target: { value: '65' } });
  expect(first.ws.pushes()).toHaveLength(0);
  expect(FakeAudio.instances).toHaveLength(0);
  expect(JSON.parse(localStorage.getItem('bingo.preferences.v1')!)).toEqual({ theme: 'light', volume: 65 });
  await userEvent.click(settings.getByRole('button', { name: 'Close settings' }));
  expect(screen.getByRole('checkbox', { name: 'Audio callouts' })).toBeChecked();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  const toggledSettings = await openSettings();
  expect(toggledSettings.getByRole('checkbox', { name: 'Audio callouts' })).not.toBeChecked();
  await userEvent.click(toggledSettings.getByRole('checkbox', { name: 'Audio callouts' }));
  first.unmount();
  await renderGame();
  expect(screen.getByRole('checkbox', { name: 'Audio callouts' })).not.toBeChecked();
  const reopened = await openSettings();
  expect(reopened.getByRole('combobox', { name: 'Appearance' })).toHaveValue('light');
  expect(reopened.getByRole('slider', { name: /Volume/ })).toHaveValue('65');
});

test('volume updates current and queued playback live without cancelling or overlapping calls', async () => {
  localStorage.setItem('bingo.preferences.v1', JSON.stringify({ theme: 'dark', volume: 35 }));
  const { ws } = await renderGame();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[1].pushId, revision: 2 }));
  const audio = FakeAudio.instances[0];
  expect(audio.volume).toBe(0.35);
  const ended = audio.onended;
  const settings = await openSettings();
  const volume = settings.getByRole('slider', { name: /Volume/ });
  fireEvent.change(volume, { target: { value: '0' } });
  expect(audio.volume).toBe(0);
  fireEvent.change(volume, { target: { value: '65' } });
  expect(audio.volume).toBe(0.65);
  expect(audio.pause).not.toHaveBeenCalled();
  expect(audio.onended).toBe(ended);
  expect(audio.play).toHaveBeenCalledTimes(1);
  expect(audio.src).toBe('/audio/calls/7.mp3');
  act(() => audio.onended?.());
  expect(audio.src).toBe('/audio/calls/16.mp3');
  expect(audio.volume).toBe(0.65);
  expect(audio.play).toHaveBeenCalledTimes(2);
  act(() => audio.onended?.());
  expect(FakeAudio.instances).toHaveLength(1);
  expect(audio.play).toHaveBeenCalledTimes(2);
  expect(ws.pushes()).toHaveLength(2);
});

test('Restore defaults changes only preferences, stops audio and keeps game-reset confirmation mandatory', async () => {
  localStorage.setItem('bingo.preferences.v1', JSON.stringify({ theme: 'light', volume: 25 }));
  localStorage.setItem('unrelated-preference', 'keep');
  const { ws } = await renderGame('/control/Test/secret', {
    eventHistory: [{ number: 9, patterns: [] }], eventPosition: 1,
  });
  await userEvent.click(screen.getByRole('checkbox', { name: 'Audio callouts' }));
  await userEvent.click(screen.getByRole('button', { name: '16' }));
  act(() => ws.receive({ type: 'ack', pushId: ws.pushes()[0].pushId, revision: 1 }));
  const audio = FakeAudio.instances[0];
  const ended = audio.onended;
  const settings = await openSettings();
  await userEvent.click(settings.getByRole('button', { name: 'Restore defaults' }));
  expect(settings.getByRole('combobox', { name: 'Appearance' })).toHaveValue('dark');
  expect(settings.getByRole('slider', { name: /Volume/ })).toHaveValue('100');
  expect(settings.getByRole('checkbox', { name: 'Audio callouts' })).not.toBeChecked();
  expect(settings.getByText(/reset always asks for confirmation/i)).toBeInTheDocument();
  expect(settings.queryByRole('checkbox', { name: /confirmation/i })).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem('bingo.preferences.v1')!)).toEqual({ theme: 'dark', volume: 100 });
  expect(localStorage.getItem('unrelated-preference')).toBe('keep');
  expect(audio.pause).toHaveBeenCalledTimes(1);
  act(() => ended?.());
  expect(audio.play).toHaveBeenCalledTimes(1);
  expect(ws.pushes()).toHaveLength(1);
  await userEvent.click(settings.getByRole('button', { name: 'Close settings' }));
  expect(screen.getByRole('button', { name: '9' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '16' })).toBeDisabled();
  expect(screen.getByRole('combobox', { name: 'Winning pattern' })).toHaveValue('any-line');
  await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
  const confirmation = screen.getByRole('alertdialog', { name: 'Reset the game' });
  await userEvent.click(within(confirmation).getByRole('button', { name: 'Cancel' }));
  expect(ws.pushes()).toHaveLength(1);
  expect(screen.getByRole('button', { name: '9' })).toBeDisabled();
});

test('audience exposes no Settings controls and incoming state never plays audio', async () => {
  const { ws } = await renderGame('/view/Test');
  expect(screen.queryByRole('button', { name: 'Settings', hidden: true })).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog', { hidden: true })).not.toBeInTheDocument();
  act(() => ws.receive({ type: 'state', state: {
    ...INITIAL_STATE, revision: 1, lastPushId: null,
    eventHistory: [{ number: 75, patterns: [] }], eventPosition: 1,
  } }));
  expect(FakeAudio.instances).toHaveLength(0);
  expect(ws.pushes()).toHaveLength(0);
});
