/**
 * Original 75-ball mask geometry. See PATTERN-SOURCES.md for the research,
 * readable 5×5 layouts, and house-rule caveats. This is not an exhaustive list
 * of possible patterns and does not imply regulatory approval of this app.
 *
 * All draw balls remain counted. The usual free center is index 12; retain
 * that cell in the mask whenever the actual shape includes it. A mask specifies
 * required cells only: extra marked cells never invalidate a winning shape.
 */

/** A variant is one complete winning mask; completing ANY ONE variant wins. */
export interface BingoPattern {
  id: string;
  name: string;
  category: string;
  description: string;
  /** Exactly 25 binary cells in row-major order; index 12 is the free center. */
  variants: string[];
}

/** Build a mask without trimming, transposing, or silently accepting bad rows. */
export function maskFromRows(rows: string[]): string {
  if (rows.length !== 5 || rows.some((row) => row.length !== 5 || /[^01]/.test(row))) {
    throw new Error('A pattern requires exactly five rows of five binary cells.');
  }
  return rows.join('');
}

/** Rotate one 5×5 mask 90 degrees clockwise, keeping the free center intact. */
export function rotateMask(mask: string): string {
  if (mask.length !== 25 || /[^01]/.test(mask)) {
    throw new Error('A mask requires exactly 25 binary cells.');
  }
  return Array.from({ length: 25 }, (_, index) => {
    const row = Math.floor(index / 5);
    const column = index % 5;
    return mask[(4 - column) * 5 + row];
  }).join('');
}

function fromCells(required: (row: number, column: number) => boolean): string {
  return Array.from({ length: 25 }, (_, index) =>
    required(Math.floor(index / 5), index % 5) ? '1' : '0',
  ).join('');
}

function unionMasks(...masks: string[]): string {
  return fromCells((row, column) => masks.some((mask) => mask[row * 5 + column] === '1'));
}

function rotations(mask: string): string[] {
  const result = new Set<string>();
  let current = mask;
  for (let turn = 0; turn < 4; turn++) {
    result.add(current);
    current = rotateMask(current);
  }
  return [...result];
}

/** Each result is the UNION of count distinct shapes, not count alternatives. */
function combinations(masks: string[], count: number): string[] {
  const result = new Set<string>();
  function visit(start: number, chosen: string[]): void {
    if (chosen.length === count) {
      result.add(unionMasks(...chosen));
      return;
    }
    for (let index = start; index <= masks.length - (count - chosen.length); index++) {
      visit(index + 1, [...chosen, masks[index]]);
    }
  }
  visit(0, []);
  return [...result];
}

function rectangles(height: number, width: number): string[] {
  const masks: string[] = [];
  for (let top = 0; top <= 5 - height; top++) {
    for (let left = 0; left <= 5 - width; left++) {
      masks.push(fromCells((row, column) =>
        row >= top && row < top + height && column >= left && column < left + width,
      ));
    }
  }
  return masks;
}

function preset(
  id: string,
  name: string,
  category: string,
  description: string,
  variants: string | string[],
): BingoPattern {
  return { id, name, category, description, variants: typeof variants === 'string' ? [variants] : variants };
}

const rows = Array.from({ length: 5 }, (_, target) => fromCells((row) => row === target));
const columns = Array.from({ length: 5 }, (_, target) => fromCells((_, column) => column === target));
const diagonals = [fromCells((r, c) => r === c), fromCells((r, c) => r + c === 4)];
const lines = [...rows, ...columns, ...diagonals];
const hardwayLines = lines.filter((mask) => mask[12] === '0');
const cornerCells = [0, 4, 24, 20].map((target) => fromCells((r, c) => r * 5 + c === target));
const corners = unionMasks(...cornerCells);
const stamps = rotations(maskFromRows(['11000', '11000', '00000', '00000', '00000']));
const blocks4 = rectangles(2, 2);
const blocks6 = [...rectangles(2, 3), ...rectangles(3, 2)];
const blocks8 = [...rectangles(2, 4), ...rectangles(4, 2)];
const outsideFrame = fromCells((r, c) => r === 0 || r === 4 || c === 0 || c === 4);
const insideFrame = fromCells((r, c) => r >= 1 && r <= 3 && c >= 1 && c <= 3 && (r !== 2 || c !== 2));
const smallDiamond = fromCells((r, c) => Math.abs(r - 2) + Math.abs(c - 2) === 1);
const cross = unionMasks(rows[2], columns[2]);
const x = unionMasks(...diagonals);
const uprightT = unionMasks(rows[0], columns[2]);
const seven = unionMasks(rows[0], diagonals[1]);
const tree = maskFromRows(['00100', '01110', '11111', '00100', '00100']);

// Explicit original block-letter layouts. Lettering is a house convention, not
// a claim that every jurisdiction uses this precise rasterization. T and X are
// deliberately exact full-line combinations, independently checked in Arrow.
const letterDefinitions: [string, string[], string][] = [
  ['a', ['11111', '10001', '11111', '10001', '10001'], 'Top bar, both side columns, and the middle crossbar together.'],
  ['b', ['11110', '10001', '11110', '10001', '11110'], 'Left spine and two rounded block bowls, with full four-cell bars on rows 1, 3, and 5.'],
  ['c', ['11111', '10000', '10000', '10000', '11111'], 'Top and bottom rows joined by the left column; the right side stays open.'],
  ['d', ['11110', '10001', '10001', '10001', '11110'], 'Left spine, four-cell top and bottom bars, and a right-hand outer curve.'],
  ['e', ['11111', '10000', '11111', '10000', '11111'], 'Three full horizontal bars joined by the left column.'],
  ['f', ['11111', '10000', '11110', '10000', '10000'], 'Full top bar, a four-cell middle bar, and the left column.'],
  ['g', ['11111', '10000', '10111', '10001', '11111'], 'An open upper-right bowl with a three-cell inward middle bar and closed lower-right side.'],
  ['h', ['10001', '10001', '11111', '10001', '10001'], 'Both outer columns joined by the complete middle row.'],
  ['i', ['11111', '00100', '00100', '00100', '11111'], 'Full top and bottom rows joined by the center column.'],
  ['j', ['11111', '00001', '00001', '10001', '01110'], 'Full top bar and right-hand stem curling into a three-cell bottom hook.'],
  ['k', ['10001', '10010', '11100', '10010', '10001'], 'Left spine with diagonal arms meeting at the center.'],
  ['l', ['10000', '10000', '10000', '10000', '11111'], 'The entire left column and bottom row together.'],
  ['m', ['10001', '11011', '10101', '10001', '10001'], 'Two outer stems with inward diagonals meeting at the center.'],
  ['n', ['10001', '11001', '10101', '10011', '10001'], 'Both outer columns and the top-left to bottom-right diagonal together.'],
  ['o', ['11111', '10001', '10001', '10001', '11111'], 'A square block O using the complete outside frame; this is an intentional geometric alias.'],
  ['p', ['11111', '10001', '11111', '10000', '10000'], 'Left spine with a full-width upper bowl and no lower-right stem.'],
  ['q', ['01110', '10001', '10101', '10010', '01101'], 'Rounded block bowl with a center-to-bottom-right diagonal tail.'],
  ['r', ['11110', '10001', '11110', '10010', '10001'], 'Left spine and upper bowl with a diagonal leg down to the bottom-right corner.'],
  ['s', ['11111', '10000', '11111', '00001', '11111'], 'Three complete horizontal bars, with upper-left and lower-right joins.'],
  ['t', ['11111', '00100', '00100', '00100', '00100'], 'Complete the entire top row and center column TOGETHER.'],
  ['u', ['10001', '10001', '10001', '10001', '11111'], 'Both outer columns joined by the full bottom row; top stays open.'],
  ['v', ['10001', '10001', '10001', '01010', '00100'], 'Outer stems for three rows then an inward diagonal taper to the bottom-center tip.'],
  ['w', ['10001', '10001', '10101', '10101', '01010'], 'Two outer stems and a raised center stroke ending in two lower inner tips.'],
  ['x', ['10001', '01010', '00100', '01010', '10001'], 'Complete BOTH full corner-to-corner diagonals: nine cells including the free center, not just four corners plus center.'],
  ['y', ['10001', '01010', '00100', '00100', '00100'], 'Two diagonal upper arms merge at the free center into a centered lower stem.'],
  ['z', ['11111', '00010', '00100', '01000', '11111'], 'Full top and bottom rows joined by the top-right to bottom-left diagonal.'],
];

/**
 * Stable preset ids. Default: any-line. Alternatives are OR; unions required
 * within an alternative are AND. There are no wild-ball or ignored-call rules.
 */
export const PATTERNS: BingoPattern[] = [
  preset('any-line', 'Any straight line', 'Lines',
    'Complete any ONE of 12 full lines: five horizontal, five vertical, or two corner-to-corner diagonals. The alternatives are not combined.', lines),
  preset('full', 'Full card / blackout / coverall', 'Classic',
    'Cover the entire 5×5 card, including the already-free center (24 numbered cells). One fixed mask; all draw balls remain counted.', '1'.repeat(25)),
  preset('row', 'Any horizontal row', 'Lines',
    'Complete any ONE of the five full horizontal rows. The center row includes the free center; rows are alternatives, not combined.', rows),
  preset('column', 'Any vertical column', 'Lines',
    'Complete any ONE of the five full vertical columns. The center column includes the free center; columns are alternatives.', columns),
  preset('diag', 'Either diagonal', 'Lines',
    'Complete EITHER full corner-to-corner diagonal. These are two alternatives; choose Letter X to require both together.', diagonals),
  preset('corners', 'Four corners', 'Classic',
    'Complete all four extreme corner cells together. The center is not part of this fixed four-cell mask.', corners),
  preset('any-three-corners', 'Any three corners', 'Classic',
    'Complete any THREE of the four extreme corner cells together. Four alternative masks; the fourth corner is not required.', combinations(cornerCells, 3)),
  preset('inside-corners', 'Small / inside four corners', 'Classic',
    'Complete the four corners of the central 3×3 square: row/column pairs (2,2), (2,4), (4,2), (4,4), counting from 1. Fixed house layout.',
    maskFromRows(['00000', '01010', '00000', '01010', '00000'])),
  preset('two-lines', 'Any two lines / double line bingo', 'Lines',
    'Complete TWO distinct standard lines together. Each of 66 alternatives unions a pair of rows, columns, or full diagonals; intersections may be shared. Corners and stamps do not count as lines.', combinations(lines, 2)),
  preset('three-lines', 'Any three lines / triple line bingo', 'Lines',
    'Complete THREE distinct standard lines together. Each of 220 alternatives unions three rows, columns, or full diagonals; intersections may be shared. No corner or stamp substitutions.', combinations(lines, 3)),
  preset('hardway', 'Hardway straight line', 'Hardway',
    'Complete any ONE full row or column that does NOT pass through the free center. Eight alternatives; center row, center column, and diagonals are excluded.', hardwayLines),
  preset('two-hardway-lines', 'Two hardway lines', 'Hardway',
    'Complete TWO distinct non-center rows or columns together. Each of 28 alternatives unions two hardway lines; crossings may be shared and no free-center line is allowed.', combinations(hardwayLines, 2)),
  preset('three-hardway-lines', 'Three hardway lines', 'Hardway',
    'Complete THREE distinct non-center rows or columns together. Each of 56 alternatives unions three hardway lines; no line may use the free center.', combinations(hardwayLines, 3)),
  preset('line-or-corners', 'Any line OR four corners', 'Combinations',
    'Complete EITHER one of the 12 standard lines OR all four extreme corners. Thirteen alternatives; this does not require a line and corners together.', [...lines, corners]),
  preset('line-and-corners', 'Any line AND four corners', 'Combinations',
    'Complete one full standard line AND all four extreme corners together. Each of 12 alternatives is their union; shared corner cells count once.', lines.map((line) => unionMasks(line, corners))),
  preset('postage-stamp', 'Any corner postage stamp', 'Blocks & stamps',
    'Complete any ONE 2×2 block touching an extreme corner. Four alternative corner positions; this is not a block anywhere on the card.', stamps),
  preset('double-postage-stamp', 'Double postage stamp', 'Blocks & stamps',
    'Complete any TWO distinct 2×2 corner stamps together. Six alternative pairs; the stamps are disjoint and require eight cells in each mask.', combinations(stamps, 2)),
  preset('triple-postage-stamp', 'Triple postage stamp', 'Blocks & stamps',
    'Complete any THREE distinct 2×2 corner stamps together. Four alternative triples, each requiring twelve cells; no overlap between stamps.', combinations(stamps, 3)),
  preset('four-postage-stamps', 'Four corner postage stamps', 'Blocks & stamps',
    'Complete ALL FOUR 2×2 corner stamps together. One fixed sixteen-cell mask; the middle row and middle column are not required.', combinations(stamps, 4)),
  preset('block-4', 'Block of four / four-pack anywhere', 'Blocks & stamps',
    'Complete any ONE contiguous 2×2 square anywhere on the card. Sixteen placements are alternatives; the free center may be included.', blocks4),
  preset('block-6', 'Block of six / six-pack anywhere', 'Blocks & stamps',
    'Complete any ONE contiguous 2×3 or 3×2 rectangle. Twenty-four alternative placements include both orientations; the free center may be used.', blocks6),
  preset('block-8', 'Block of eight / eight-pack anywhere', 'Blocks & stamps',
    'Complete any ONE contiguous 2×4 or 4×2 rectangle. Sixteen alternative placements include both orientations; this is not a hollow 3×3 frame.', blocks8),
  preset('block-9', 'Block of nine / nine-pack anywhere', 'Blocks & stamps',
    'Complete any ONE contiguous 3×3 square. Nine alternative placements; all include the free center, which remains in the geometry.', rectangles(3, 3)),
  preset('hardway-block-4', 'Hardway block of four', 'Hardway',
    'Complete any ONE contiguous 2×2 square that excludes the free center. Twelve alternative placements; all four required cells are numbered.', blocks4.filter((mask) => mask[12] === '0')),
  preset('hardway-block-6', 'Hardway six-pack', 'Hardway',
    'Complete any ONE contiguous 2×3 or 3×2 rectangle that excludes the free center. Twelve alternative placements; no free-center rectangle is allowed.', blocks6.filter((mask) => mask[12] === '0')),
  preset('hardway-block-8', 'Hardway block of eight', 'Hardway',
    'Complete any ONE contiguous 2×4 or 4×2 rectangle that excludes the free center. Eight alternative placements; all eight cells are numbered.', blocks8.filter((mask) => mask[12] === '0')),
  preset('corner-6', 'Corner six-pack', 'Blocks & stamps',
    'Complete any ONE 2×3 or 3×2 rectangle anchored at an extreme corner. Eight alternative corner/orientation combinations, all excluding the free center.',
    blocks6.filter((mask) => [0, 4, 20, 24].some((index) => mask[index] === '1'))),
  preset('outside-frame', 'Outside / large picture frame', 'Frames & diamonds',
    'Complete the sixteen cells on the outside border of the card. One fixed hollow 5×5 frame; no interior cells or free center are required.', outsideFrame),
  preset('inside-frame', 'Inside / small picture frame', 'Frames & diamonds',
    'Complete the eight-cell border of the central 3×3 square. One fixed hollow frame; neither the free center nor the outside border is required.', insideFrame),
  preset('broken-frame', 'Broken picture frame (no corners)', 'Frames & diamonds',
    'House version: complete the outside border EXCEPT its four extreme corners. One fixed twelve-cell mask; other broken-frame gaps require a custom pattern.',
    fromCells((r, c) => (r === 0 || r === 4 || c === 0 || c === 4) && !((r === 0 || r === 4) && (c === 0 || c === 4)))),
  preset('diamond', 'Large hollow diamond / baseball diamond', 'Frames & diamonds',
    'Complete the eight-cell diamond outline whose tips are the midpoints of the card edges. One fixed mask; the free center is not in this hollow outline.',
    fromCells((r, c) => Math.abs(r - 2) + Math.abs(c - 2) === 2)),
  preset('small-diamond', 'Small hollow diamond', 'Frames & diamonds',
    'Complete the four cells immediately above, below, left, and right of the free center. One fixed house outline; the free center itself is not required.', smallDiamond),
  preset('full-diamond', 'Full / filled diamond', 'Frames & diamonds',
    'Complete the filled thirteen-cell diamond with tips at the four edge midpoints. One fixed mask includes the free center and all cells inside the outline.',
    fromCells((r, c) => Math.abs(r - 2) + Math.abs(c - 2) <= 2)),
  preset('cross', 'Cross / plus', 'Shapes',
    'Complete the entire middle row AND center column together. One fixed nine-cell plus sign includes the free center; it is not a church cross.', cross),
  preset('church-cross', 'Church cross (four orientations)', 'Shapes',
    'House version: a full center-column stem with a full crossbar on row 2, not row 3. Any ONE of four 90-degree rotations wins; all include the free center.',
    rotations(unionMasks(columns[2], rows[1]))),
  preset('x-frame', 'X AND outside frame', 'Combinations',
    'Complete BOTH full diagonals AND the entire outside frame together. One fixed union of twenty-one cells, including the free center; neither component wins alone.', unionMasks(x, outsideFrame)),
  preset('cross-frame', 'Cross AND outside frame', 'Combinations',
    'Complete the entire middle row, center column, AND outside frame together. One fixed twenty-one-cell union; neither the cross nor frame alone is enough.', unionMasks(cross, outsideFrame)),
  preset('corners-cross', 'Four corners AND cross', 'Combinations',
    'Complete all four extreme corners AND the entire middle row and center column together. One fixed thirteen-cell union, including the free center.', unionMasks(corners, cross)),
  ...letterDefinitions.map(([id, letterRows, detail]) => preset(
    id,
    id === 'x' ? 'Letter X / both diagonals' : `Letter ${id.toUpperCase()} (upright)`,
    'Letters',
    `${detail} Fixed upright 5×5 block-letter layout; other house lettering may differ.`,
    maskFromRows(letterRows),
  )),
  preset('crazy-t', 'Crazy T / T in any orientation', 'Letters',
    'Complete a full edge row or column AND its perpendicular center stem together. Any ONE of the four 90-degree rotations of the upright T wins; not their union.', rotations(uprightT)),
  preset('crazy-l', 'Crazy L / L in any orientation', 'Letters',
    'Complete two full adjacent card edges together, forming a large L. Any ONE of four 90-degree rotations wins; separate orientations are alternatives.', rotations(unionMasks(columns[0], rows[4]))),
  preset('crazy-h', 'Crazy H / H in either orientation', 'Letters',
    'Complete an upright H or its 90-degree rotation: two opposite outer edges and a middle crossbar. Two distinct alternatives after symmetric rotations are deduplicated.', rotations(unionMasks(columns[0], columns[4], rows[2]))),
  preset('7', 'Number 7 (upright diagonal)', 'Numbers',
    'Complete the full top row AND the top-right to bottom-left diagonal together. Fixed upright nine-cell seven; straight right-stem house versions are different.', seven),
  preset('crazy-7', 'Crazy 7 / seven in any orientation', 'Numbers',
    'Complete the diagonal-stem seven in any ONE of four 90-degree rotations. Each alternative unions a full edge and a full diagonal; no reflected extra shapes.', rotations(seven)),
  preset('arrow', 'Arrow (four orientations)', 'Shapes',
    'Filled arrowhead of widths 1, 3, 5 in the top three rows, then a two-cell center shaft. Any ONE of four rotations wins. Its upright mask also resembles the Christmas tree.', rotations(tree)),
  preset('kite', 'Small kite / crazy kite', 'Shapes',
    'Complete a 2×2 corner square AND the diagonal tail through the free center to the opposite corner. Any ONE of four rotations wins; seven cells per alternative.',
    rotations(maskFromRows(['11000', '11000', '00100', '00010', '00001']))),
  preset('large-kite', 'Large kite / crazy giant kite', 'Shapes',
    'Complete a 3×3 corner square AND the two remaining diagonal cells to the opposite corner. Any ONE of four rotations wins; eleven cells including the free center.',
    rotations(maskFromRows(['11100', '11100', '11100', '00010', '00001']))),
  preset('bow-tie', 'Bow tie (horizontal)', 'Shapes',
    'Complete two filled side triangles joined at the center: row widths 2, 4, 5, 4, 2 across both wings. One fixed horizontal mask; Hourglass is the vertical orientation.',
    maskFromRows(['10001', '11011', '11111', '11011', '10001'])),
  preset('hourglass', 'Hourglass / vertical bow tie', 'Shapes',
    'Complete filled top and bottom triangles meeting at the free center. Rows contain 5, 3, 1, 3, 5 cells. One fixed vertical mask; not the union of two orientations.',
    maskFromRows(['11111', '01110', '00100', '01110', '11111'])),
  preset('butterfly', 'Butterfly (corner-wing house layout)', 'Shapes',
    'House version: four 2×2 corner wings AND the free center, leaving the other middle-row and middle-column cells empty. One fixed mask; other butterfly silhouettes differ.',
    maskFromRows(['11011', '11011', '00100', '11011', '11011'])),
  preset('heart', 'Heart (filled house layout)', 'Shapes',
    'House version: two upper lobes at columns 2 and 4, two full rows, a three-cell taper, and a bottom-center tip. One fixed filled silhouette; outlines are different games.',
    maskFromRows(['01010', '11111', '11111', '01110', '00100'])),
  preset('christmas-tree', 'Christmas tree (upright)', 'Shapes',
    'Complete centered foliage of widths 1, 3, 5 across rows 1–3 and a center trunk on rows 4–5. One fixed upright eleven-cell mask; the free center is included.', tree),
  preset('checkerboard', 'Checkerboard (corner cells included)', 'Shapes',
    'Complete alternating cells starting with the top-left corner. One fixed thirteen-cell checkerboard includes all four extreme corners and the free center.', fromCells((r, c) => (r + c) % 2 === 0)),
  preset('inverse-checkerboard', 'Inverse checkerboard', 'Shapes',
    'Complete the twelve cells omitted by the corner-start checkerboard. One fixed complementary mask excludes all four extreme corners and the free center.', fromCells((r, c) => (r + c) % 2 === 1)),
  preset('railroad', 'Railroad tracks (adjacent full lines)', 'Lines',
    'Complete TWO ADJACENT full rows or TWO ADJACENT full columns together. Eight alternative pairs; this follows the adjacent-track convention, not separated outer rails.',
    [...rows.slice(0, 4).map((row, index) => unionMasks(row, rows[index + 1])), ...columns.slice(0, 4).map((column, index) => unionMasks(column, columns[index + 1]))]),
  preset('layer-cake', 'Layer cake', 'Shapes',
    'Complete the full top, middle, AND bottom rows together, leaving rows 2 and 4 unrequired. One fixed fifteen-cell mask, including the free center.', unionMasks(rows[0], rows[2], rows[4])),
  preset('stair', 'Staircase (thin house layout)', 'Shapes',
    'House version: a nine-cell connected staircase steps right then down from the top-left to bottom-right corner. Any ONE of four rotations wins; thick staircases require a custom pattern.',
    rotations(maskFromRows(['11000', '01100', '00110', '00011', '00001']))),
  preset('pyramid', 'Small pyramid / crazy pyramid', 'Shapes',
    'Complete a filled triangle of widths 1, 3, 5 in the bottom three rows, with apex at the free center. Any ONE of four 90-degree rotations wins; not a whole-card triangle.',
    rotations(maskFromRows(['00000', '00000', '00100', '01110', '11111']))),
  preset('top-or-bottom-line', 'Top line OR bottom line', 'Lines',
    'Complete EITHER the full top row OR the full bottom row. Two alternatives, not both rows together; no other row or column is eligible.', [rows[0], rows[4]]),
  preset('top-or-bottom-half', 'Top OR bottom half (middle row included)', 'Shapes',
    'House version: complete the top THREE rows OR the bottom THREE rows. Two fifteen-cell alternatives both include the entire middle row and free center; a 2-row half is a different game.',
    [fromCells((r) => r <= 2), fromCells((r) => r >= 2)]),
  preset('window-pane', 'Window pane (frame and cross)', 'Combinations',
    'House version: complete the outside frame AND the center cross, outlining four small panes. One fixed twenty-one-cell mask, intentionally identical to Cross AND outside frame.', unionMasks(outsideFrame, cross)),
  preset('tic-tac-toe', 'Tic-tac-toe / hash grid', 'Shapes',
    'Complete rows 2 and 4 AND columns 2 and 4 together, counting from 1. One fixed sixteen-cell hash grid; the free center is not part of the grid lines.', unionMasks(rows[1], rows[3], columns[1], columns[3])),
  preset('small-diamond-corners', 'Small diamond AND four corners', 'Combinations',
    'Complete the four cells immediately around the free center AND all four extreme corners together. One fixed eight-cell union; the free center itself is not required.', unionMasks(smallDiamond, corners)),
];

export function getPattern(id: string): BingoPattern | undefined {
  return PATTERNS.find((pattern) => pattern.id === id);
}
