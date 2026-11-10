import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { DEFAULT_PATTERN } from '../game/pattern-selection';
import WinningPattern from './WinningPattern';

// The library is independently tested; these fixtures test UI behavior.
vi.mock('../patterns/catalog', () => ({
  PATTERNS: [
    { id: 'any-line', name: 'Any straight line', category: 'Classic', description: 'Any row, column or diagonal.', variants: ['1111100000000000000000000', '1000010000100001000010000'] },
    { id: 't', name: 'Letter T', category: 'Letters', description: 'Top row and center column.', variants: ['1111100100001000010000100'] },
    { id: 'full', name: 'Full card / Blackout', category: 'Classic', description: 'Every number on the card.', variants: ['1111111111111111111111111'] },
  ],
}));

afterEach(() => vi.useRealTimers());

describe('WinningPattern', () => {
  test('caller can select full card without entering Bingo mode', async () => {
    const onChange = vi.fn();
    render(<WinningPattern selection={DEFAULT_PATTERN} readOnly={false} disabled={false} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'full');
    expect(onChange).toHaveBeenCalledWith({ id: 'full', name: 'Full card / Blackout', variants: ['1'.repeat(25)] });
  });

  test('viewer sees a diagram and name but no pattern-changing controls', () => {
    const onChange = vi.fn();
    render(<WinningPattern selection={DEFAULT_PATTERN} readOnly disabled={false} onChange={onChange} />);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Any straight line' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Winning pattern diagram/ }).querySelectorAll('[data-cell]')).toHaveLength(25);
    expect(screen.queryByRole('button', { name: /custom/i })).not.toBeInTheDocument();
  });

  test('shows one valid alternative at a time rather than an all-filled union', async () => {
    const selection = { id: 'any-line', name: 'Any straight line', variants: ['1111100000000000000000000', '1000010000100001000010000'] };
    const { container } = render(<WinningPattern selection={selection} readOnly={false} disabled={false} onChange={vi.fn()} />);
    expect(container.querySelectorAll('[data-cell][data-required="true"]')).toHaveLength(5);
    expect(screen.getByText(/Any one of 2 alternatives wins/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next example' }));
    expect(container.querySelector('[data-mask]')).toHaveAttribute('data-mask', selection.variants[1]);
  });

  test('custom editor saves a named, explicit mask and supports cancel without pushing', async () => {
    const onChange = vi.fn();
    render(<WinningPattern selection={DEFAULT_PATTERN} readOnly={false} disabled={false} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'custom');
    let editor = screen.getByRole('alertdialog', { name: 'Custom winning pattern' });
    await userEvent.click(within(editor).getByRole('button', { name: 'Cancel' }));
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'custom');
    editor = screen.getByRole('alertdialog', { name: 'Custom winning pattern' });
    await userEvent.clear(within(editor).getByLabelText('Pattern name'));
    await userEvent.type(within(editor).getByLabelText('Pattern name'), 'House special');
    await userEvent.click(within(editor).getByRole('button', { name: 'Clear squares' }));
    expect(within(editor).getByRole('button', { name: 'Save pattern' })).toBeDisabled();
    await userEvent.click(within(editor).getByRole('button', { name: 'Row 1, B' }));
    await userEvent.click(within(editor).getByRole('button', { name: 'Save pattern' }));
    expect(onChange).toHaveBeenCalledWith({ id: 'custom', name: 'House special', variants: ['1000000000001000000000000'] });
  });

  test('custom editor can add and remove alternative shapes', async () => {
    const onChange = vi.fn();
    render(<WinningPattern selection={DEFAULT_PATTERN} readOnly={false} disabled={false} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText('Winning pattern'), 'custom');
    const editor = screen.getByRole('alertdialog', { name: 'Custom winning pattern' });
    await userEvent.click(within(editor).getByRole('button', { name: 'Add alternative' }));
    expect(within(editor).getByText('Alternative 2 of 2')).toBeInTheDocument();
    await userEvent.click(within(editor).getByRole('button', { name: 'Remove alternative' }));
    expect(within(editor).getByText('Alternative 1 of 1')).toBeInTheDocument();
  });

  test('all changes are disabled while checking a claim', () => {
    render(<WinningPattern selection={DEFAULT_PATTERN} readOnly={false} disabled onChange={vi.fn()} />);
    expect(screen.getByLabelText('Winning pattern')).toBeDisabled();
  });

  test('audience cycles through alternatives, then cleans up timers on unmount', () => {
    vi.useFakeTimers();
    const selection = { id: 'any-line', name: 'Any straight line', variants: ['1111100000000000000000000', '1000010000100001000010000'] };
    const { container, unmount } = render(<WinningPattern selection={selection} readOnly disabled={false} onChange={vi.fn()} />);
    act(() => vi.advanceTimersByTime(3500));
    expect(container.querySelector('[data-mask]')).toHaveAttribute('data-mask', selection.variants[1]);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
