import { describe, expect, test } from 'vitest';
import { getPattern, maskFromRows, PATTERNS, rotateMask } from './catalog';

const mask = (...rows: string[]) => rows.join('');
const cells = (predicate: (row: number, column: number) => boolean) =>
  Array.from({ length: 25 }, (_, index) => predicate(Math.floor(index / 5), index % 5) ? '1' : '0').join('');
const union = (...masks: string[]) =>
  cells((row, column) => masks.some((entry) => entry[row * 5 + column] === '1'));
const standardRows = Array.from({ length: 5 }, (_, row) => cells((r) => r === row));
const standardColumns = Array.from({ length: 5 }, (_, column) => cells((_, c) => c === column));
const standardDiagonals = [cells((r, c) => r === c), cells((r, c) => r + c === 4)];
const standardLines = [...standardRows, ...standardColumns, ...standardDiagonals];
const requiredPattern = (id: string) => {
  const pattern = getPattern(id);
  expect(pattern, `missing preset ${id}`).toBeDefined();
  return pattern!;
};


// These literals intentionally do not use catalog helpers: a broken helper must
// not silently change both the implementation and its expected geometry.
describe('core 75-ball pattern geometry', () => {
  test('uses any-line as the available default and rejects unknown ids', () => {
    expect(getPattern('any-line')).toBe(PATTERNS.find((pattern) => pattern.id === 'any-line'));
    expect(getPattern('not-a-pattern')).toBeUndefined();
  });

  test('X requires BOTH complete diagonals, including the free center', () => {
    expect(getPattern('x')?.variants).toEqual([
      mask('10001', '01010', '00100', '01010', '10001'),
    ]);
    expect(getPattern('x')?.variants[0].split('').filter((cell) => cell === '1')).toHaveLength(9);
  });

  test('upright T is a full top bar and center column, not alternatives', () => {
    expect(getPattern('t')?.variants).toEqual([
      mask('11111', '00100', '00100', '00100', '00100'),
    ]);
  });

  test('full coverall includes every cell and corners includes only four corners', () => {
    expect(getPattern('full')?.variants).toEqual(['1'.repeat(25)]);
    expect(getPattern('corners')?.variants).toEqual([
      mask('10001', '00000', '00000', '00000', '10001'),
    ]);
  });
});

describe('mask helpers', () => {
  test('maskFromRows preserves exactly five binary rows in row-major order', () => {
    const rows = ['10001', '01010', '00100', '01010', '10001'];
    const original = [...rows];
    expect(maskFromRows(rows)).toBe(rows.join(''));
    expect(rows).toEqual(original);
  });

  test('maskFromRows rejects wrong row counts, widths, and nonbinary characters', () => {
    const invalid = [
      [], ['11111'], ['11111', '11111', '11111', '11111'],
      Array<string>(6).fill('11111'),
      ['1111', '11111', '11111', '11111', '111111'],
      ['1111x', '11111', '11111', '11111', '11111'],
      ['1111\n', '11111', '11111', '11111', '11111'],
    ];
    for (const rows of invalid) expect(() => maskFromRows(rows)).toThrow();
  });

  test('rotation is clockwise, preserves the center, and cycles after four turns', () => {
    const input = mask('11000', '00000', '00100', '00000', '00000');
    const once = mask('00001', '00001', '00100', '00000', '00000');
    expect(rotateMask(input)).toBe(once);
    expect(rotateMask(rotateMask(rotateMask(rotateMask(input))))).toBe(input);
    expect(rotateMask(input)[12]).toBe('1');
  });

  test.each(['', '1'.repeat(24), '1'.repeat(26), 'x'.repeat(25), `${'1'.repeat(24)}\n`])(
    'rotation rejects invalid masks: %j',
    (input) => expect(() => rotateMask(input)).toThrow(),
  );
});

describe('catalog coverage and mask integrity', () => {
  const requiredIds = [
    'any-line', 'full', 'row', 'column', 'diag', 'corners', 'any-three-corners', 'inside-corners',
    'two-lines', 'three-lines', 'hardway', 'two-hardway-lines', 'three-hardway-lines',
    'line-or-corners', 'line-and-corners', 'postage-stamp', 'double-postage-stamp',
    'triple-postage-stamp', 'four-postage-stamps', 'block-4', 'block-6', 'block-8', 'block-9',
    'hardway-block-4', 'hardway-block-6', 'hardway-block-8', 'corner-6',
    'outside-frame', 'inside-frame', 'broken-frame', 'diamond', 'small-diamond', 'full-diamond',
    'cross', 'church-cross', 'x-frame', 'cross-frame', 'corners-cross',
    ...'abcdefghijklmnopqrstuvwxyz'.split(''),
    'crazy-t', 'crazy-l', 'crazy-h', '7', 'crazy-7', 'arrow', 'kite', 'large-kite',
    'bow-tie', 'hourglass', 'butterfly', 'heart', 'christmas-tree', 'checkerboard',
    'inverse-checkerboard', 'railroad', 'layer-cake', 'stair', 'pyramid',
    'top-or-bottom-line', 'top-or-bottom-half', 'window-pane', 'tic-tac-toe', 'small-diamond-corners',
  ];

  test('exposes the broad named preset catalog with stable unique ids', () => {
    expect(PATTERNS.map((pattern) => pattern.id).sort()).toEqual([...requiredIds].sort());
    expect(new Set(PATTERNS.map((pattern) => pattern.id)).size).toBe(PATTERNS.length);
  });

  test('every variant is binary row-major 5×5, nontrivial, and unique within its preset', () => {
    for (const pattern of PATTERNS) {
      expect(pattern.id).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
      expect(pattern.name.length).toBeGreaterThan(2);
      expect(pattern.category.length).toBeGreaterThan(2);
      expect(pattern.description.length).toBeGreaterThan(35);
      expect(pattern.variants.length, pattern.id).toBeGreaterThan(0);
      expect(new Set(pattern.variants).size, pattern.id).toBe(pattern.variants.length);
      for (const variant of pattern.variants) {
        expect(variant, pattern.id).toMatch(/^[01]{25}$/);
        expect(variant.length, pattern.id).toBe(25);
        expect([...variant].some((cell, index) => index !== 12 && cell === '1'), pattern.id).toBe(true);
      }
    }
  });

  test.each([
    ['any-line', 12], ['row', 5], ['column', 5], ['diag', 2], ['two-lines', 66],
    ['three-lines', 220], ['hardway', 8], ['two-hardway-lines', 28], ['three-hardway-lines', 56],
    ['line-or-corners', 13], ['line-and-corners', 12], ['any-three-corners', 4],
    ['postage-stamp', 4], ['double-postage-stamp', 6], ['triple-postage-stamp', 4],
    ['four-postage-stamps', 1], ['block-4', 16], ['block-6', 24], ['block-8', 16], ['block-9', 9],
    ['hardway-block-4', 12], ['hardway-block-6', 12], ['hardway-block-8', 8], ['corner-6', 8],
    ['crazy-t', 4], ['crazy-l', 4], ['crazy-h', 2], ['crazy-7', 4], ['church-cross', 4],
    ['arrow', 4], ['kite', 4], ['large-kite', 4], ['railroad', 8], ['stair', 4], ['pyramid', 4],
  ])('%s has %i distinct winning alternatives', (id, count) => {
    expect(requiredPattern(String(id)).variants).toHaveLength(Number(count));
  });
});

describe('geometric families and combined-win semantics', () => {
  test('line alternatives are exactly the standard rows, columns, and diagonals', () => {
    expect(new Set(requiredPattern('any-line').variants)).toEqual(new Set(standardLines));
    expect(new Set(requiredPattern('row').variants)).toEqual(new Set(standardRows));
    expect(new Set(requiredPattern('column').variants)).toEqual(new Set(standardColumns));
    expect(new Set(requiredPattern('diag').variants)).toEqual(new Set(standardDiagonals));
  });

  test('two- and three-line masks each require a UNION of distinct complete lines', () => {
    const pairs = new Set<string>();
    const triples = new Set<string>();
    for (let first = 0; first < standardLines.length; first++) {
      for (let second = first + 1; second < standardLines.length; second++) {
        pairs.add(union(standardLines[first], standardLines[second]));
        for (let third = second + 1; third < standardLines.length; third++) {
          triples.add(union(standardLines[first], standardLines[second], standardLines[third]));
        }
      }
    }
    expect(new Set(requiredPattern('two-lines').variants)).toEqual(pairs);
    expect(new Set(requiredPattern('three-lines').variants)).toEqual(triples);
    expect(requiredPattern('two-lines').variants).not.toContain(standardRows[0]);
  });

  test('hardway alternatives never pass through the free center', () => {
    for (const id of ['hardway', 'two-hardway-lines', 'three-hardway-lines', 'hardway-block-4', 'hardway-block-6', 'hardway-block-8']) {
      expect(requiredPattern(id).variants.every((entry) => entry[12] === '0'), id).toBe(true);
    }
    expect(new Set(requiredPattern('hardway').variants)).toEqual(new Set(standardLines.filter((entry) => entry[12] === '0')));
  });

  test('stamp combinations union whole, disjoint 2×2 corner stamps', () => {
    const stamps = [
      mask('11000', '11000', '00000', '00000', '00000'),
      mask('00011', '00011', '00000', '00000', '00000'),
      mask('00000', '00000', '00000', '00011', '00011'),
      mask('00000', '00000', '00000', '11000', '11000'),
    ];
    expect(new Set(requiredPattern('postage-stamp').variants)).toEqual(new Set(stamps));
    const counts = [['double-postage-stamp', 8], ['triple-postage-stamp', 12], ['four-postage-stamps', 16]] as const;
    for (const [id, count] of counts) {
      for (const variant of requiredPattern(id).variants) {
        expect([...variant].filter((cell) => cell === '1')).toHaveLength(count);
        for (const stamp of stamps) {
          const overlap = [...stamp].filter((cell, index) => cell === '1' && variant[index] === '1').length;
          expect([0, 4]).toContain(overlap);
        }
      }
    }
  });

  test.each([
    ['block-4', [[2, 2]]], ['block-6', [[2, 3], [3, 2]]],
    ['block-8', [[2, 4], [4, 2]]], ['block-9', [[3, 3]]],
  ] as const)('%s covers all contiguous rectangular placements', (id, dimensions) => {
    const expected = new Set<string>();
    for (const [height, width] of dimensions) {
      for (let top = 0; top <= 5 - height; top++) {
        for (let left = 0; left <= 5 - width; left++) {
          expected.add(cells((r, c) => r >= top && r < top + height && c >= left && c < left + width));
        }
      }
    }
    expect(new Set(requiredPattern(id).variants)).toEqual(expected);
  });

  test('frame combinations contain BOTH constituent shapes within each single mask', () => {
    const frame = requiredPattern('outside-frame').variants[0];
    expect(requiredPattern('x-frame').variants).toEqual([union(frame, requiredPattern('x').variants[0])]);
    expect(requiredPattern('cross-frame').variants).toEqual([union(frame, requiredPattern('cross').variants[0])]);
  });

  test('checkerboard and inverse partition all 25 cells without overlap', () => {
    const board = requiredPattern('checkerboard').variants[0];
    const inverse = requiredPattern('inverse-checkerboard').variants[0];
    expect(board).toBe(mask('10101', '01010', '10101', '01010', '10101'));
    expect(inverse).toBe(mask('01010', '10101', '01010', '10101', '01010'));
    expect([...board].every((cell, index) => cell !== inverse[index])).toBe(true);
  });

  test.each([
    ['outside-frame', ['11111', '10001', '10001', '10001', '11111']],
    ['inside-frame', ['00000', '01110', '01010', '01110', '00000']],
    ['diamond', ['00100', '01010', '10001', '01010', '00100']],
    ['small-diamond', ['00000', '00100', '01010', '00100', '00000']],
    ['full-diamond', ['00100', '01110', '11111', '01110', '00100']],
    ['cross', ['00100', '00100', '11111', '00100', '00100']],
    ['7', ['11111', '00010', '00100', '01000', '10000']],
    ['kite', ['11000', '11000', '00100', '00010', '00001']],
    ['arrow', ['00100', '01110', '11111', '00100', '00100']],
    ['christmas-tree', ['00100', '01110', '11111', '00100', '00100']],
    ['bow-tie', ['10001', '11011', '11111', '11011', '10001']],
    ['hourglass', ['11111', '01110', '00100', '01110', '11111']],
    ['butterfly', ['11011', '11011', '00100', '11011', '11011']],
    ['heart', ['01010', '11111', '11111', '01110', '00100']],
    ['layer-cake', ['11111', '00000', '11111', '00000', '11111']],
    ['stair', ['11000', '01100', '00110', '00011', '00001']],
    ['pyramid', ['00000', '00000', '00100', '01110', '11111']],
  ])('%s has an explicit, reproducible first 5×5 mask', (id, rows) => {
    expect(requiredPattern(String(id)).variants[0]).toBe((rows as string[]).join(''));
  });

  test('all crazy-T alternatives are rotations of the upright T, with no reflected/partial masks', () => {
    const expected = new Set<string>();
    let current = requiredPattern('t').variants[0];
    for (let turn = 0; turn < 4; turn++) {
      expected.add(current);
      current = rotateMask(current);
    }
    expect(new Set(requiredPattern('crazy-t').variants)).toEqual(expected);
  });
});
