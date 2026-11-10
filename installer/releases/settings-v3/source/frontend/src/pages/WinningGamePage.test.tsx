import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DialogProvider from '../dialog/DialogProvider';
import { INITIAL_STATE } from '../game/history';
import { FakeWebSocket } from '../test/fakeWebSocket';
import GamePage from './GamePage';

vi.mock('../api/client', () => ({ getPatterns: vi.fn().mockResolvedValue([['row', 'column', 'diag']]) }));

beforeEach(() => { FakeWebSocket.reset(); vi.stubGlobal('WebSocket', FakeWebSocket); });
afterEach(() => vi.unstubAllGlobals());
async function renderCaller() {
  render(<MemoryRouter initialEntries={['/control/Test/secret']}><DialogProvider><Routes><Route path="/control/:id/:code" element={<GamePage />} /></Routes></DialogProvider></MemoryRouter>);
  const ws = FakeWebSocket.latest();
  act(() => { ws.open(); ws.receive({ type: 'state', state: { ...INITIAL_STATE, revision: 0, lastPushId: null } }); });
  await act(async () => {});
  return ws;
}
function acknowledge(ws: FakeWebSocket, index: number, revision: number) {
  const push = ws.pushes()[index];
  act(() => ws.receive({ type: 'ack', pushId: push.pushId, revision }));
}

test('changes winning pattern and synchronizes it without requiring a bingo claim', async () => {
  const ws = await renderCaller();
  await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'full');
  const push = ws.pushes()[0];
  expect(push.state.selectedPattern.id).toBe('full');
  expect(push.state.bingo).toBe(false);
});

test('changing pattern after calls requires confirmation and preserves the calls', async () => {
  const ws = await renderCaller();
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  acknowledge(ws, 0, 1);
  await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'x');
  expect(screen.getByRole('alertdialog', { name: 'Change winning pattern' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(ws.pushes()).toHaveLength(1);
  await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'x');
  await userEvent.click(screen.getByRole('button', { name: 'Change pattern' }));
  expect(ws.pushes()[1].state.selectedPattern.id).toBe('x');
  expect(ws.pushes()[1].state.eventHistory).toEqual([{ number: 7, patterns: [] }]);
});

test('bingo verification is explicitly manual for physical paper cards', async () => {
  const ws = await renderCaller();
  await userEvent.click(screen.getByRole('button', { name: '7' }));
  acknowledge(ws, 0, 1);
  await userEvent.click(screen.getByRole('button', { name: 'Bingo' }));
  acknowledge(ws, 1, 2);
  expect(screen.getByText(/Check the physical card/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Winning pattern')).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Confirm winner' }));
  expect(ws.pushes()[2].state.eventHistory).toEqual([{ number: 7, patterns: [] }, { number: null, patterns: ['any-line'] }]);
});
