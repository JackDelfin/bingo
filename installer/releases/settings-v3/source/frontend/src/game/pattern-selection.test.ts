import { describe, expect, test } from 'vitest';
import { INITIAL_STATE } from './history';
import { gameReducer } from './reducer';
import { DEFAULT_PATTERN } from './pattern-selection';
import type { PatternSelection } from './types';

const full: PatternSelection = { id: 'full', name: 'Full card / Blackout', variants: ['1'.repeat(25)] };

describe('selected winning pattern', () => {
  test('defaults to all twelve straight-line alternatives, not their union', () => {
    expect(INITIAL_STATE.selectedPattern).toEqual(DEFAULT_PATTERN);
    expect(DEFAULT_PATTERN.variants).toHaveLength(12);
    expect(new Set(DEFAULT_PATTERN.variants).size).toBe(12);
    expect(DEFAULT_PATTERN.variants.every((mask) => /^[01]{25}$/.test(mask))).toBe(true);
  });

  test('changing pattern preserves called numbers and undo history', () => {
    const drawn = gameReducer(INITIAL_STATE, { type: 'draw', number: 7 });
    const changed = gameReducer(drawn, { type: 'selectPattern', pattern: full });
    expect(changed.selectedPattern).toEqual(full);
    expect(changed.eventHistory).toEqual(drawn.eventHistory);
    expect(changed.eventPosition).toBe(1);
    expect(gameReducer(changed, { type: 'undo' }).selectedPattern).toEqual(full);
  });

  test('reset clears calls and awards but keeps the chosen winning pattern', () => {
    const selected = gameReducer(INITIAL_STATE, { type: 'selectPattern', pattern: full });
    const drawn = gameReducer(selected, { type: 'draw', number: 7 });
    expect(gameReducer(drawn, { type: 'reset' })).toEqual({ ...INITIAL_STATE, selectedPattern: full });
  });

  test('cannot change the winning pattern while a claim is being checked', () => {
    const checking = { ...INITIAL_STATE, bingo: true };
    expect(gameReducer(checking, { type: 'selectPattern', pattern: full })).toBe(checking);
  });
});
