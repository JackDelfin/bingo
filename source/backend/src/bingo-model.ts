import * as mongoose from 'mongoose';
import { createDefaultPatternSelection, isValidPatternId, isValidPatternVariants, normalizePatternSelection } from './pattern-selection';
const Schema = mongoose.Schema

const PatternSelectionSchema = new Schema({
  id: { type: String, required: true, validate: isValidPatternId },
  name: { type: String, required: true, trim: true, minLength: 1, maxLength: 60 },
  variants: {
    type: [String], required: true, default: undefined, castNonArrays: false,
    validate: { validator: isValidPatternVariants, message: 'Invalid pattern variants' },
  },
}, { _id: false, id: false, strict: true });

const BingoGame = new Schema({
  gameId: { type: String, required: true, match: /\w+/, index: true, unique: true, maxLength: 16 },
  code: { type: String, required: true, match: /[a-z0-9]+/, minLength: 6, maxLength: 32},
  isPublic: { type: Boolean, default: true },
  eventHistory: { type: [Object], default: [] },
  eventPosition: { type: Number, default: 0 },
  bingo: { type: Boolean, default: false },
  validationResult: { type: Object, default: null },
  validatedPatterns: { type: [String], default: [] },
  selectedPattern: {
    type: PatternSelectionSchema, required: true, default: createDefaultPatternSelection,
    // Prevent Mongoose's String/Array casts from accepting malformed raw selections.
    set: normalizePatternSelection,
  },
  // Incremented on every accepted push; pushes must be based on the current revision
  revision: { type: Number, default: 0 },
  // Client-generated id of the last accepted push, lets a reconnecting controller tell whether its in-flight push landed
  lastPushId: { type: String, default: null },
}, { timestamps: true });

// Fields a controller is allowed to push; omitted fields are not overwritten.
export const STATE_FIELDS = ['eventHistory', 'eventPosition', 'bingo', 'validationResult', 'validatedPatterns', 'selectedPattern'];

export function buildStateUpdate(state: { [key: string]: unknown }, baseRevision: number, pushId: string): { [key: string]: unknown } {
  const update: { [key: string]: unknown } = { revision: baseRevision + 1, lastPushId: pushId };
  for (const field of STATE_FIELDS) {
    if (field in state) {
      update[field] = field === 'selectedPattern' ? normalizePatternSelection(state[field]) : state[field];
    }
  }
  return update;
}

// Explicit allowlist: the controller's capability code is never public state.
// Kept outside server.ts so serialization can be tested without a live listener.
export const publicState = (game: any) => ({
  eventHistory: game.eventHistory,
  eventPosition: game.eventPosition,
  bingo: game.bingo,
  validationResult: game.validationResult,
  validatedPatterns: game.validatedPatterns,
  selectedPattern: game.selectedPattern == null
    ? createDefaultPatternSelection()
    : normalizePatternSelection(game.selectedPattern),
  revision: game.revision ?? 0,
  lastPushId: game.lastPushId ?? null,
});

export default mongoose.model('bingo', BingoGame);
