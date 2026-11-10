import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { getManagementSession, ManagementClient } from './management';
import UpdatesPanel from './UpdatesPanel';
import SettingsPanel from '../settings/SettingsPanel';

vi.mock('./management', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./management')>();
  return { ...actual, getManagementSession: vi.fn() };
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(getManagementSession).mockReturnValue(null);
});

test('remote controllers get guidance, never probe localhost or check releases', () => {
  const status = vi.spyOn(ManagementClient.prototype, 'status');
  const check = vi.spyOn(ManagementClient.prototype, 'check');
  render(<UpdatesPanel />);
  expect(screen.getByRole('heading', { name: 'Updates' })).toBeInTheDocument();
  expect(screen.getByText(/open Bingo from the applications menu on this computer/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
  expect(status).not.toHaveBeenCalled();
  expect(check).not.toHaveBeenCalled();
});

test('checks only on request and requires explicit install confirmation', async () => {
  vi.mocked(getManagementSession).mockReturnValue({ port: 8766, token: 'a'.repeat(64) });
  const idle = { currentVersion: 'v3.0.0', latestVersion: null, available: false, phase: 'idle' as const, message: '' };
  const available = { ...idle, latestVersion: 'v3.1.0', available: true };
  const status = vi.spyOn(ManagementClient.prototype, 'status').mockResolvedValue(idle);
  const check = vi.spyOn(ManagementClient.prototype, 'check').mockResolvedValue();
  const update = vi.spyOn(ManagementClient.prototype, 'update').mockResolvedValue();
  render(<UpdatesPanel />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Check for updates' })).toBeEnabled());
  expect(check).not.toHaveBeenCalled();
  status.mockResolvedValue(available);
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Update to v3.1.0' })).toBeEnabled());
  expect(check).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Update to v3.1.0' }));
  expect(screen.getByText(/finish your current game/i)).toBeInTheDocument();
  expect(update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel update' }));
  expect(update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Update to v3.1.0' }));
  status.mockResolvedValue({ ...available, phase: 'updating' });
  const install = screen.getByRole('button', { name: 'Install update' });
  fireEvent.click(install);
  fireEvent.click(install);
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update).toHaveBeenCalledWith('v3.1.0');
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
});

test('Settings mounts updates only when open and stops status polling on close', async () => {
  vi.mocked(getManagementSession).mockReturnValue({ port: 8766, token: 'a'.repeat(64) });
  const status = vi.spyOn(ManagementClient.prototype, 'status').mockResolvedValue({
    currentVersion: 'v3.0.0', latestVersion: null, available: false, phase: 'checking', message: '',
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute('open', ''); },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true, value: function (this: HTMLDialogElement) {
      this.removeAttribute('open'); this.dispatchEvent(new Event('close'));
    },
  });
  const preferences = { theme: 'dark' as const, volume: 100, storageError: null,
    setTheme: vi.fn(), setVolume: vi.fn(), restoreDefaults: vi.fn() };
  render(<SettingsPanel preferences={preferences}><UpdatesPanel /></SettingsPanel>);
  expect(status).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  await waitFor(() => expect(status).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Close settings' }));
  await new Promise((resolve) => setTimeout(resolve, 1100));
  expect(status).toHaveBeenCalledOnce();
});
