/**
 * Turn placement into pixels.
 *
 * The pipeline decides where a card belongs -- its row, its column, and its
 * order within that cell -- and this is the one formula that turns that into
 * positions. It lives in the browser because the answer depends on the empire
 * profile: a profile hides cards, and the cards around them close up. The
 * previous attempt kept a copy of the row and band formulas on both sides of
 * the wire, the two drifted apart, and cards drew nowhere near their panels;
 * so there is exactly one copy, here.
 *
 * Row heights vary: a row is as tall as its busiest column needs, because most
 * cells hold four or five cards while a few hold dozens. One Blokkats cell holds
 * 37 technologies, which on its own would set that row to 3,468 px and the whole
 * canvas to 23,188 px tall against 10,230 wide -- an awkward shape on any
 * landscape screen, and mostly empty space. So a cell taller than `MAX_STACK`
 * wraps into sub-columns, and a logical column is then as wide as the widest
 * cell in it. Columns keep their logical indices; only the pixel mapping
 * changes, so a card can still only move right of what it depends on, never
 * left.
 *
 * A row with nothing visible in it is left out entirely.
 */

import type { Dataset } from "./types";

/** Card size. Width is set by name length -- the 95th percentile technology name
 *  is about 34 characters, which fits at this width without truncation. */
export const CARD_WIDTH = 230;
export const CARD_HEIGHT = 78;

/** Horizontal gap between columns. Generous because edge routing needs the
 *  channel, and because adjacent columns are dependency steps the eye should
 *  read as distinct. */
export const COLUMN_GAP = 90;

/** Vertical gap between stacked cards in one cell. */
const CARD_GAP = 14;

/** Space above a row for its label, and below it before the next row starts.
 *  The label is drawn in world units and never taller than this space, which is
 *  what stops it spilling onto the cards when zoomed out; the header is sized
 *  so it stays readable down to roughly a fifth of full scale. */
export const ROW_HEADER = 64;
export const ROW_GUTTER = 34;

/** Extra separation between research areas, and before the crisis bands. */
const AREA_GAP = 40;
const CRISIS_GAP = 72;

/** Left margin before column 0, and top margin above the first row. */
const MARGIN_X = 40;
const MARGIN_Y = 40;

/** Cards stacked in one cell before wrapping into another sub-column. Chosen
 *  against the corpus: the median cell holds 4 and the 95th percentile 14, so 8
 *  leaves four fifths of cells untouched while cutting the tallest row by 78%. */
const MAX_STACK = 8;

/** Gap between sub-columns inside one cell. Tighter than COLUMN_GAP because
 *  sub-columns are the same dependency step, not consecutive ones, and should
 *  read as one group. */
const SUBCOLUMN_GAP = 24;

const SUBCOLUMN_PITCH = CARD_WIDTH + SUBCOLUMN_GAP;
const CARD_PITCH = CARD_HEIGHT + CARD_GAP;

export interface RowBox {
  y: number;
  h: number;
  /** Visible cards in the row. */
  count: number;
  /** False for a row with nothing visible, which takes no space. */
  shown: boolean;
}

export interface Span {
  x: number;
  w: number;
}

export interface Geometry {
  rows: RowBox[];
  /** Left edge of every logical column; traces turn in the gap before it. */
  columnX: number[];
  /** One span per tier band, in the order of `dataset.bands`. */
  bands: Span[];
  repeatable: Span;
  width: number;
  height: number;
}

/** Position every visible node, and size every row and band around them. */
export function computeGeometry(data: Dataset): Geometry {
  const { raw, nodes } = data;

  // Visible cards per (row, column) cell, in the pipeline's order.
  const cells = new Map<string, number[]>();
  nodes.forEach((node, index) => {
    if (node.hidden) return;
    const key = `${node.row}:${node.column}`;
    const members = cells.get(key);
    if (members) members.push(index);
    else cells.set(key, [index]);
  });
  for (const members of cells.values()) {
    members.sort((a, b) => nodes[a]!.order - nodes[b]!.order);
  }

  const subcolumns = new Array<number>(raw.columns).fill(1);
  const stacks = new Array<number>(raw.rows.length).fill(0);
  const counts = new Array<number>(raw.rows.length).fill(0);
  for (const members of cells.values()) {
    const { row, column } = nodes[members[0]!]!;
    subcolumns[column] = Math.max(subcolumns[column]!, Math.ceil(members.length / MAX_STACK));
    stacks[row] = Math.max(stacks[row]!, Math.min(members.length, MAX_STACK));
    counts[row] = counts[row]! + members.length;
  }

  const columnX: number[] = [];
  const columnWidths: number[] = [];
  let cursor = MARGIN_X;
  for (let column = 0; column < raw.columns; column++) {
    const width = subcolumns[column]! * SUBCOLUMN_PITCH - SUBCOLUMN_GAP;
    columnX.push(cursor);
    columnWidths.push(width);
    cursor += width + COLUMN_GAP;
  }
  const width = cursor - COLUMN_GAP + MARGIN_X;

  const rows: RowBox[] = [];
  cursor = MARGIN_Y;
  let previousGroup: string | null = null;
  for (const row of raw.rows) {
    const count = counts[row.i] ?? 0;
    if (count === 0) {
      rows[row.i] = { y: cursor, h: 0, count: 0, shown: false };
      continue;
    }
    if (previousGroup !== null && row.group !== previousGroup) {
      cursor += row.group === "crisis" ? CRISIS_GAP : AREA_GAP;
    }
    previousGroup = row.group;
    const stack = Math.max(stacks[row.i] ?? 1, 1);
    const h = ROW_HEADER + stack * CARD_PITCH - CARD_GAP + ROW_GUTTER;
    rows[row.i] = { y: cursor, h, count, shown: true };
    cursor += h;
  }

  for (const members of cells.values()) {
    members.forEach((index, position) => {
      const node = nodes[index]!;
      const subcolumn = Math.floor(position / MAX_STACK);
      node.x = columnX[node.column]! + subcolumn * SUBCOLUMN_PITCH;
      node.y = rows[node.row]!.y + ROW_HEADER + (position % MAX_STACK) * CARD_PITCH;
    });
  }

  const span = (start: number, end: number): Span => ({
    x: columnX[start]! - COLUMN_GAP / 2,
    w: columnX[end]! + columnWidths[end]! - columnX[start]! + COLUMN_GAP,
  });

  return {
    rows,
    columnX,
    bands: raw.bands.map((band) => span(band.s, band.e)),
    repeatable: span(raw.repeatableColumn, raw.repeatableColumn),
    width,
    height: cursor + MARGIN_Y,
  };
}
