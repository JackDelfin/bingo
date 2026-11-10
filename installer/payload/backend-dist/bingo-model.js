"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.publicState = exports.STATE_FIELDS = void 0;
exports.buildStateUpdate = buildStateUpdate;
const mongoose = require("mongoose");
const pattern_selection_1 = require("./pattern-selection");
const Schema = mongoose.Schema;
const PatternSelectionSchema = new Schema({
    id: { type: String, required: true, validate: pattern_selection_1.isValidPatternId },
    name: { type: String, required: true, trim: true, minLength: 1, maxLength: 60 },
    variants: {
        type: [String], required: true, default: undefined, castNonArrays: false,
        validate: { validator: pattern_selection_1.isValidPatternVariants, message: 'Invalid pattern variants' },
    },
}, { _id: false, id: false, strict: true });
const BingoGame = new Schema({
    gameId: { type: String, required: true, match: /\w+/, index: true, unique: true, maxLength: 16 },
    code: { type: String, required: true, match: /[a-z0-9]+/, minLength: 6, maxLength: 32 },
    isPublic: { type: Boolean, default: true },
    eventHistory: { type: [Object], default: [] },
    eventPosition: { type: Number, default: 0 },
    bingo: { type: Boolean, default: false },
    validationResult: { type: Object, default: null },
    validatedPatterns: { type: [String], default: [] },
    selectedPattern: {
        type: PatternSelectionSchema, required: true, default: pattern_selection_1.createDefaultPatternSelection,
        // Prevent Mongoose's String/Array casts from accepting malformed raw selections.
        set: pattern_selection_1.normalizePatternSelection,
    },
    // Incremented on every accepted push; pushes must be based on the current revision
    revision: { type: Number, default: 0 },
    // Client-generated id of the last accepted push, lets a reconnecting controller tell whether its in-flight push landed
    lastPushId: { type: String, default: null },
}, { timestamps: true });
// Fields a controller is allowed to push; omitted fields are not overwritten.
exports.STATE_FIELDS = ['eventHistory', 'eventPosition', 'bingo', 'validationResult', 'validatedPatterns', 'selectedPattern'];
function buildStateUpdate(state, baseRevision, pushId) {
    const update = { revision: baseRevision + 1, lastPushId: pushId };
    for (const field of exports.STATE_FIELDS) {
        if (field in state) {
            update[field] = field === 'selectedPattern' ? (0, pattern_selection_1.normalizePatternSelection)(state[field]) : state[field];
        }
    }
    return update;
}
// Explicit allowlist: the controller's capability code is never public state.
// Kept outside server.ts so serialization can be tested without a live listener.
const publicState = (game) => {
    var _a, _b;
    return ({
        eventHistory: game.eventHistory,
        eventPosition: game.eventPosition,
        bingo: game.bingo,
        validationResult: game.validationResult,
        validatedPatterns: game.validatedPatterns,
        selectedPattern: game.selectedPattern == null
            ? (0, pattern_selection_1.createDefaultPatternSelection)()
            : (0, pattern_selection_1.normalizePatternSelection)(game.selectedPattern),
        revision: (_a = game.revision) !== null && _a !== void 0 ? _a : 0,
        lastPushId: (_b = game.lastPushId) !== null && _b !== void 0 ? _b : null,
    });
};
exports.publicState = publicState;
exports.default = mongoose.model('bingo', BingoGame);
//# sourceMappingURL=bingo-model.js.map