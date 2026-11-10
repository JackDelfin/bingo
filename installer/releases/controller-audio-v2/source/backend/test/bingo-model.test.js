const assert = require('node:assert/strict');
const test = require('node:test');
const { default: BingoGame } = require('../dist/bingo-model');
const { createDefaultPatternSelection } = require('../dist/pattern-selection');

const selectedPattern = { id: 'custom', name: 'Caller pattern', variants: ['1111100000000000000000000'] };
const gameData = { gameId: 'test-game', code: 'secret123' };

test('controller updates include a normalized selection and existing revision metadata only', () => {
  const { buildStateUpdate, STATE_FIELDS } = require('../dist/bingo-model');
  assert.ok(STATE_FIELDS.includes('selectedPattern'));
  const update = buildStateUpdate({
    bingo: true,
    selectedPattern: { ...selectedPattern, name: ' Caller pattern ' },
    code: 'replace-secret', gameId: 'other-game', revision: 999, lastPushId: 'forged',
    'selectedPattern.id': 'forged',
  }, 6, 'push-7');
  assert.deepEqual(update, { revision: 7, lastPushId: 'push-7', bingo: true, selectedPattern });
});

test('old-client pushes omit the selection update and preserve the saved choice', async () => {
  const { buildStateUpdate, publicState } = require('../dist/bingo-model');
  const game = new BingoGame({ ...gameData, selectedPattern });
  const update = buildStateUpdate({ eventPosition: 4, bingo: false }, 0, 'legacy-push');
  assert.equal('selectedPattern' in update, false);
  game.set(update);
  await game.validate();
  assert.deepEqual(publicState(game).selectedPattern, selectedPattern);
  assert.equal(game.revision, 1);
});

test('controller update preparation rejects explicitly malformed selections rather than dropping them', () => {
  const { buildStateUpdate } = require('../dist/bingo-model');
  assert.equal(typeof buildStateUpdate, 'function');
  for (const value of [null, undefined, [], {}, { ...selectedPattern, variants: [] }, { ...selectedPattern, variants: ['0'.repeat(25)] }]) {
    assert.throws(() => buildStateUpdate({ selectedPattern: value }, 0, 'bad-push'), TypeError);
  }
});

function plainSelection(game) {
  return game.toObject().selectedPattern;
}

test('public state for old plain saved games always includes the default and never the code', () => {
  const { publicState } = require('../dist/bingo-model');
  const game = { ...gameData, eventHistory: [], eventPosition: 0, bingo: false, validationResult: null, validatedPatterns: [] };
  const state = publicState(game);
  assert.deepEqual(state, {
    eventHistory: [], eventPosition: 0, bingo: false, validationResult: null, validatedPatterns: [],
    selectedPattern: createDefaultPatternSelection(), revision: 0, lastPushId: null,
  });
  assert.equal('code' in state, false);
  assert.equal('gameId' in state, false);
  state.selectedPattern.variants[0] = '1'.repeat(25);
  assert.deepEqual(publicState(game).selectedPattern, createDefaultPatternSelection());
});

test('public state preserves saved choices and revision metadata without leaking subdocument internals', () => {
  const { publicState } = require('../dist/bingo-model');
  const game = new BingoGame({ ...gameData, selectedPattern, revision: 7, lastPushId: 'push-7' });
  const state = publicState(game);
  assert.deepEqual(state.selectedPattern, selectedPattern);
  assert.equal(state.revision, 7);
  assert.equal(state.lastPushId, 'push-7');
  assert.equal('code' in state, false);
  assert.equal('_id' in state.selectedPattern, false);
  state.selectedPattern.variants[0] = '1'.repeat(25);
  assert.deepEqual(plainSelection(game), selectedPattern);
  const plain = publicState({ ...gameData, selectedPattern: { ...selectedPattern, code: 'injected', _id: 'hidden' } });
  assert.deepEqual(plain.selectedPattern, selectedPattern);
});

test('new and hydrated legacy games receive independent persisted defaults', async () => {
  const first = new BingoGame(gameData);
  const second = new BingoGame(gameData);
  const legacy = BingoGame.hydrate(gameData);
  for (const game of [first, second, legacy]) {
    await game.validate();
    assert.deepEqual(plainSelection(game), createDefaultPatternSelection());
  }
  first.selectedPattern.variants[0] = '1'.repeat(25);
  assert.deepEqual(plainSelection(second), createDefaultPatternSelection());
  assert.deepEqual(plainSelection(legacy), createDefaultPatternSelection());
});

test('explicit strict subdocument persists only normalized public pattern fields with no _id', async () => {
  const game = new BingoGame({ ...gameData, selectedPattern: { ...selectedPattern, name: '  Caller pattern  ', code: 'hidden', extra: true, _id: 'ignored' } });
  await game.validate();
  assert.deepEqual(plainSelection(game), selectedPattern);
  const reloaded = BingoGame.hydrate(game.toObject());
  assert.deepEqual(plainSelection(reloaded), selectedPattern);
  assert.equal(BingoGame.schema.path('selectedPattern').schema.options.strict, true);
  assert.equal(BingoGame.schema.path('selectedPattern').schema.options._id, false);
});

const invalid = [
  null, [], 'custom', {},
  { ...selectedPattern, id: 'Uppercase' },
  { ...selectedPattern, id: 123 },
  { ...selectedPattern, name: '  ' },
  { ...selectedPattern, name: 'x'.repeat(61) },
  { ...selectedPattern, variants: [] },
  { ...selectedPattern, variants: selectedPattern.variants[0] },
  { ...selectedPattern, variants: Array(701).fill(selectedPattern.variants[0]) },
  { ...selectedPattern, variants: ['1'.repeat(24)] },
  { ...selectedPattern, variants: ['x'.repeat(25)] },
  { ...selectedPattern, variants: ['0'.repeat(25)] },
  { ...selectedPattern, variants: ['0'.repeat(12) + '1' + '0'.repeat(12)] },
];

for (const [index, selection] of invalid.entries()) {
  test(`schema rejects invalid selectedPattern case ${index + 1} without a database`, async () => {
    const game = new BingoGame({ ...gameData, selectedPattern: selection });
    await assert.rejects(game.validate(), /selectedPattern/);
  });
}

test('nested document edits cannot bypass field and mask validators', async () => {
  for (const [field, value] of [
    ['id', 'Bad_ID'], ['name', '  '], ['name', 'x'.repeat(61)],
    ['variants', []], ['variants', ['0'.repeat(25)]],
  ]) {
    const game = new BingoGame({ ...gameData, selectedPattern });
    game.selectedPattern[field] = value;
    await assert.rejects(game.validate(), /selectedPattern/);
  }
});
