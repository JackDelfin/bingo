const assert = require('node:assert/strict');
const test = require('node:test');

const firstRow = '1111100000000000000000000';

function validSelection(overrides = {}) {
  return { id: 'custom', name: 'Custom pattern', variants: [firstRow], ...overrides };
}

test('normalization trims the name and returns only independent pattern data', () => {
  const { normalizePatternSelection } = require('../dist/pattern-selection');
  const input = validSelection({ name: '  Caller’s pattern  ', code: 'not-public', _id: 'not-persisted' });
  const normalized = normalizePatternSelection(input);
  assert.deepEqual(normalized, validSelection({ name: 'Caller’s pattern' }));
  assert.notEqual(normalized, input);
  assert.notEqual(normalized.variants, input.variants);
  input.variants[0] = '1'.repeat(25);
  assert.deepEqual(normalized.variants, [firstRow]);
});

test('generic masks and inclusive field limits remain supported', () => {
  const { normalizePatternSelection } = require('../dist/pattern-selection');
  for (const variants of [[firstRow], Array(220).fill(firstRow), Array(700).fill(firstRow)]) {
    const selection = validSelection({ id: 'a'.repeat(60), name: 'x'.repeat(60), variants });
    assert.deepEqual(normalizePatternSelection(selection), selection);
  }
  const arbitrary = validSelection({ id: 'caller-pick-7', name: 'A', variants: ['1000000000000000000000000'] });
  assert.deepEqual(normalizePatternSelection(arbitrary), arbitrary, 'catalog membership is not a server concern');
});

const invalidSelections = [
  ['null', null], ['undefined', undefined], ['array', []], ['string', 'custom'], ['number', 7], ['empty object', {}],
  ['empty id', validSelection({ id: '' })],
  ['missing id', { name: 'Pattern', variants: [firstRow] }],
  ['non-string id', validSelection({ id: 1 })],
  ['uppercase id', validSelection({ id: 'Custom' })],
  ['underscore id', validSelection({ id: 'any_line' })],
  ['id with whitespace', validSelection({ id: ' custom' })],
  ['id with newline', validSelection({ id: 'custom\n' })],
  ['oversize id', validSelection({ id: 'a'.repeat(61) })],
  ['empty name', validSelection({ name: '' })],
  ['blank name', validSelection({ name: ' \n\t ' })],
  ['missing name', { id: 'custom', variants: [firstRow] }],
  ['non-string name', validSelection({ name: 1 })],
  ['oversize name', validSelection({ name: 'x'.repeat(61) })],
  ['empty variants', validSelection({ variants: [] })],
  ['missing variants', { id: 'custom', name: 'Pattern' }],
  ['non-array variants', validSelection({ variants: firstRow })],
  ['object variants', validSelection({ variants: { 0: firstRow, length: 1 } })],
  ['oversize variants', validSelection({ variants: Array(701).fill(firstRow) })],
  ['short mask', validSelection({ variants: ['1'.repeat(24)] })],
  ['long mask', validSelection({ variants: ['1'.repeat(26)] })],
  ['invalid mask character', validSelection({ variants: ['x'.repeat(25)] })],
  ['trailing newline mask', validSelection({ variants: [firstRow + '\n'] })],
  ['non-string mask', validSelection({ variants: [1] })],
  ['null mask', validSelection({ variants: [null] })],
  ['object mask', validSelection({ variants: [{ mask: firstRow }] })],
  ['array mask', validSelection({ variants: [[firstRow]] })],
  ['sparse variants', validSelection({ variants: Array(1) })],
  ['empty mask', validSelection({ variants: ['0'.repeat(25)] })],
  ['free-only mask', validSelection({ variants: ['0'.repeat(12) + '1' + '0'.repeat(12)] })],
  ['invalid later variant', validSelection({ variants: [firstRow, '0'.repeat(25)] })],
];

for (const [description, selection] of invalidSelections) {
  test(`rejects ${description}`, () => {
    const { normalizePatternSelection } = require('../dist/pattern-selection');
    assert.throws(() => normalizePatternSelection(selection), TypeError);
  });
}

test('legacy games default to all twelve straight lines in row-major order', () => {
  const { createDefaultPatternSelection } = require('../dist/pattern-selection');
  const selection = createDefaultPatternSelection();
  assert.equal(selection.id, 'any-line');
  assert.equal(selection.name, 'Any straight line');
  const expected = [];
  for (let row = 0; row < 5; row += 1) {
    expected.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) === row ? '1' : '0').join(''));
  }
  for (let column = 0; column < 5; column += 1) {
    expected.push(Array.from({ length: 25 }, (_, cell) => cell % 5 === column ? '1' : '0').join(''));
  }
  expected.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) === cell % 5 ? '1' : '0').join(''));
  expected.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) + cell % 5 === 4 ? '1' : '0').join(''));
  assert.deepEqual(selection.variants, expected);
  assert.equal(new Set(selection.variants).size, 12);
  for (const mask of selection.variants) {
    assert.match(mask, /^[01]{25}$/);
    assert.equal([...mask].filter(cell => cell === '1').length, 5);
  }
  selection.variants[0] = '0'.repeat(25);
  assert.deepEqual(createDefaultPatternSelection().variants, expected, 'defaults must not share a mutable array');
});
