import type { PatternSelection } from './types';

const mask = (predicate: (row: number, col: number) => boolean) =>
  Array.from({ length: 25 }, (_, index) => (predicate(Math.floor(index / 5), index % 5) ? '1' : '0')).join('');

/** Also used for legacy saved games with no selectedPattern field. */
export const DEFAULT_PATTERN: PatternSelection = {
  id: 'any-line',
  name: 'Any straight line',
  variants: [
    ...Array.from({ length: 5 }, (_, row) => mask((r) => r === row)),
    ...Array.from({ length: 5 }, (_, col) => mask((_r, c) => c === col)),
    mask((r, c) => r === c),
    mask((r, c) => r + c === 4),
  ],
};
