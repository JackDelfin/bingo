import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import DialogProvider from '../dialog/DialogProvider';
import HomePage from './HomePage';

vi.mock('../api/client', () => ({ getGameList: vi.fn().mockResolvedValue([]) }));
// Vitest stubs CSS imports; inject the actual stylesheet to exercise computed styles.
const { readFileSync } = await vi.importActual<{ readFileSync: (path: string, encoding: 'utf8') => string }>('node:fs');
const settingsStyles = readFileSync('src/settings/settings.css', 'utf8');

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true });
  Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: undefined });
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: undefined });
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
afterEach(() => vi.restoreAllMocks());

test('Home opens a named native settings dialog and restores trigger focus on close or Escape', async () => {
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  const trigger = screen.getByRole('button', { name: 'Settings' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await userEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Settings' });
  expect(dialog.tagName).toBe('DIALOG');
  const close = within(dialog).getByRole('button', { name: 'Close settings' });
  expect(close).toHaveFocus();
  await userEvent.click(close);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  await userEvent.click(trigger);
  // Browsers translate Escape into a native dialog cancel event; jsdom does not.
  fireEvent(dialog, new Event('cancel', { cancelable: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

test('Home settings contain local-update guidance without enabling remote updates', async () => {
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByRole('heading', { name: 'Updates' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
});

test('appearance and volume start dark at 100% and survive a page remount locally', async () => {
  const first = render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const appearance = screen.getByRole('combobox', { name: 'Appearance' });
  const volume = screen.getByRole('slider', { name: /Volume/ });
  expect(appearance).toHaveValue('dark');
  expect(document.documentElement).toHaveAttribute('data-bingo-theme', 'dark');
  expect(volume).toHaveValue('100');
  expect(screen.queryByRole('checkbox', { name: 'Audio callouts' })).not.toBeInTheDocument();
  await userEvent.selectOptions(appearance, 'light');
  fireEvent.change(volume, { target: { value: '35' } });
  expect(document.documentElement).toHaveAttribute('data-bingo-theme', 'light');
  expect(screen.getByText('35%')).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem('bingo.preferences.v1')!)).toEqual({ theme: 'light', volume: 35 });
  first.unmount();
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByRole('combobox', { name: 'Appearance' })).toHaveValue('light');
  expect(screen.getByRole('slider', { name: /Volume/ })).toHaveValue('35');
});

test.each([
  '{broken', 'null', '[]', '123', '{}',
  JSON.stringify({ theme: 'system', volume: 35 }),
  JSON.stringify({ theme: 'light', volume: '35' }),
  JSON.stringify({ theme: 'light', volume: -1 }),
  JSON.stringify({ theme: 'light', volume: 101 }),
  JSON.stringify({ theme: 'light', volume: 35.5 }),
  JSON.stringify({ theme: 'light', volume: 35, audio: true }),
])('invalid saved preferences %s fall back safely to defaults', async (stored) => {
  localStorage.setItem('bingo.preferences.v1', stored);
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByRole('combobox', { name: 'Appearance' })).toHaveValue('dark');
  expect(screen.getByRole('slider', { name: /Volume/ })).toHaveValue('100');
  expect(screen.getByText(/saved preferences could not be read/i)).toHaveAttribute('role', 'status');
});

test.each(['storage denied', 'quota exceeded'])('%s keeps preferences usable in memory and explains they are not saved', async (failure) => {
  if (failure === 'storage denied') {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
  } else {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Full', 'QuotaExceededError'); });
  }
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByRole('slider', { name: /Volume/ })).toHaveValue('100');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Appearance' }), 'light');
  fireEvent.change(screen.getByRole('slider', { name: /Volume/ }), { target: { value: '0' } });
  expect(document.documentElement).toHaveAttribute('data-bingo-theme', 'light');
  expect(screen.getByRole('slider', { name: /Volume/ })).toHaveValue('0');
  expect(screen.getByText(/preferences could not be saved.*this page only/i)).toHaveAttribute('role', 'status');
});

test('fullscreen can be entered and exited and follows browser fullscreen changes', async () => {
  const request = vi.fn().mockImplementation(async () => {
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: document.documentElement });
    fireEvent(document, new Event('fullscreenchange'));
  });
  const exit = vi.fn().mockImplementation(async () => {
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
    fireEvent(document, new Event('fullscreenchange'));
  });
  Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: request });
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exit });
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  await userEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }));
  expect(request).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: 'Exit fullscreen' }));
  expect(exit).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeEnabled();
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: document.documentElement });
  fireEvent(document, new Event('fullscreenchange'));
  expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();
  expect(localStorage.getItem('bingo.preferences.v1')).toBeNull();
});

test.each(['unsupported', 'rejected entry', 'rejected exit'])('fullscreen %s fails gracefully without changing preferences', async (failure) => {
  const rejected = vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError'));
  if (failure === 'rejected entry') Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: rejected });
  if (failure === 'rejected exit') {
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: document.documentElement });
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: rejected });
  }
  render(<HomePage />, { wrapper: DialogProvider });
  await screen.findByText('No game found');
  await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
  await userEvent.click(screen.getByRole('button', { name: failure === 'rejected exit' ? 'Exit fullscreen' : 'Enter fullscreen' }));
  expect(screen.getByRole('alert')).toHaveTextContent(failure === 'unsupported' ? /fullscreen is not supported/i : /could not change fullscreen/i);
  expect(screen.getByRole('slider', { name: /Volume/ })).toHaveValue('100');
  expect(screen.getByRole('button', { name: 'Close settings' })).toBeEnabled();
});

test('settings provide 44px touch targets, visible keyboard focus rules and a real light page appearance', async () => {
  const style = document.createElement('style');
  style.textContent = settingsStyles;
  document.head.append(style);
  try {
    render(<HomePage />, { wrapper: DialogProvider });
    await screen.findByText('No game found');
    const trigger = screen.getByRole('button', { name: 'Settings' });
    expect(getComputedStyle(trigger).minHeight).toBe('44px');
    await userEvent.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Settings' });
    const appearance = within(dialog).getByRole('combobox', { name: 'Appearance' });
    expect(getComputedStyle(appearance).minHeight).toBe('44px');
    expect(getComputedStyle(within(dialog).getByRole('slider', { name: /Volume/ })).minHeight).toBe('44px');
    expect(getComputedStyle(within(dialog).getByRole('button', { name: 'Close settings' })).minHeight).toBe('44px');
    expect(getComputedStyle(dialog).overflowY).toBe('auto');
    expect(settingsStyles).toMatch(/:focus-visible/);
    expect(settingsStyles).toMatch(/outline:\s*3px solid/);
    await userEvent.selectOptions(appearance, 'light');
    expect(getComputedStyle(document.body).backgroundColor).toBe('rgb(248, 250, 252)');
    expect(getComputedStyle(document.body).color).toBe('rgb(15, 23, 42)');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Restore defaults' }));
    expect(document.documentElement).toHaveAttribute('data-bingo-theme', 'dark');
  } finally { style.remove(); }
});
