"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MAX_PATTERN_VARIANTS = void 0;
exports.isValidPatternId = isValidPatternId;
exports.isValidPatternVariants = isValidPatternVariants;
exports.normalizePatternSelection = normalizePatternSelection;
exports.createDefaultPatternSelection = createDefaultPatternSelection;
exports.MAX_PATTERN_VARIANTS = 700;
function isValidPatternId(input) {
    return typeof input === 'string' && input.length >= 1 && input.length <= 60 && !/[^a-z0-9-]/.test(input);
}
function isValidPatternVariants(input) {
    if (!Array.isArray(input) || input.length < 1 || input.length > exports.MAX_PATTERN_VARIANTS)
        return false;
    for (const mask of input) {
        if (typeof mask !== 'string' || mask.length !== 25 || /[^01]/.test(mask))
            return false;
        // An empty mask or the free center alone can never be a winning pattern.
        if (!/1/.test(mask.slice(0, 12) + mask.slice(13)))
            return false;
    }
    return true;
}
// Reject malformed input before Mongoose can coerce it. Select only public fields.
function normalizePatternSelection(input) {
    if (typeof input !== 'object' || input === null || Array.isArray(input)) {
        throw new TypeError('selectedPattern must be an object');
    }
    const selection = input;
    if (!isValidPatternId(selection.id))
        throw new TypeError('selectedPattern.id must be 1..60 lowercase letters, digits or hyphens');
    if (typeof selection.name !== 'string' || selection.name.trim().length < 1 || selection.name.trim().length > 60) {
        throw new TypeError('selectedPattern.name must be 1..60 characters after trimming');
    }
    if (!isValidPatternVariants(selection.variants)) {
        throw new TypeError('selectedPattern.variants must contain 1..700 binary 25-cell masks with a non-free cell');
    }
    return { id: selection.id, name: selection.name.trim(), variants: selection.variants.slice() };
}
// Factory: separate games must never share a mutable default selection.
function createDefaultPatternSelection() {
    const variants = [];
    for (let row = 0; row < 5; row += 1) {
        variants.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) === row ? '1' : '0').join(''));
    }
    for (let column = 0; column < 5; column += 1) {
        variants.push(Array.from({ length: 25 }, (_, cell) => cell % 5 === column ? '1' : '0').join(''));
    }
    variants.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) === cell % 5 ? '1' : '0').join(''));
    variants.push(Array.from({ length: 25 }, (_, cell) => Math.floor(cell / 5) + cell % 5 === 4 ? '1' : '0').join(''));
    return { id: 'any-line', name: 'Any straight line', variants };
}
//# sourceMappingURL=pattern-selection.js.map