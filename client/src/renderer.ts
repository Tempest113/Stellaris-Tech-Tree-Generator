/**
 * Canvas 2D renderer.
 *
 * Canvas rather than WebGL on purpose. The tree is ~1000 cards and ~1000 edges,
 * and viewport culling means a few hundred are ever drawn at once; measurements
 * on a comparable corpus put a WebGL build at 6 ms against a 16.7 ms budget, so
 * the extra machinery buys nothing here and costs a dependency plus a lot of
 * indirection.
 *
 * The renderer never computes geometry. Every position comes from the current
 * view (see `geometry.ts`); what is decided here is only how those positions
 * are drawn.
 *
 * Two coordinate spaces, and the rule for which a thing lives in:
 *
 * - World space for everything attached to the tree -- rows, cards, traces and
 *   the row labels. A world-space label can be sized to the box it sits in, so
 *   it shrinks with the tree instead of spilling over the cards as the view
 *   zooms out.
 * - Screen space for the tier header only. Tier bands run the full height of
 *   the canvas, so their labels are pinned to the top of the view where they
 *   are always visible, and fitted to each band's on-screen width.
 */

import { Camera } from "./camera";
import {
  CARD_HEIGHT,
  CARD_WIDTH,
  COLUMN_GAP,
  ROW_GUTTER,
  ROW_HEADER,
  type RowBox,
} from "./geometry";
import { AREA_ACCENT, EDGE, FLAG, FONT, HIGHLIGHT, INK, rowAccent, withAlpha } from "./theme";
import {
  EDGE_ALTERNATIVE,
  EDGE_POTENTIAL_GATE,
  type Dataset,
  type RawRow,
  type TechNode,
} from "./types";

const card = { w: CARD_WIDTH, h: CARD_HEIGHT };

/** Zoom thresholds. Below each, that much detail stops being drawn. */
const LOD_ICON = 0.14;
const LOD_TEXT = 0.42;

/** Smallest on-screen text size, in px, still worth drawing. */
const MIN_LABEL_PX = 6;

/** Height of the fixed tier header, in screen pixels. */
export const TIER_HEADER_PX = 26;

/** Grid cell size for hit testing, in world units. */
const HIT_CELL = 512;

/** Card anatomy, in world units. */
const SIDE_BAR = 3;
const ICON = 52;
const ICON_X = 12;
const BADGE_RADIUS = 11;

/** Corner markers: rare, dangerous and a crisis row's area dot, right to left. */
const MARKER = 9;
const MARKER_GAP = 5;

/** Corner cut on a trace, in world units. */
const CHAMFER = 14;

/** Row band inset from the canvas edge, and corner radius. */
const BAND_INSET = 16;
const BAND_RADIUS = 10;

/** Row label: largest font the row header can hold, and its preferred screen size. */
const ROW_LABEL_MAX_FONT = 30;
const ROW_LABEL_SCREEN_PX = 12;

export interface Selection {
  /** Node index under the pointer, or null. */
  hovered: number | null;
  /** Node index pinned by a click, or null. */
  pinned: number | null;
  /** Node indices reachable backwards from the active node. */
  ancestors: Set<number>;
  /** Node indices reachable forwards from the active node. */
  descendants: Set<number>;
}

export function emptySelection(): Selection {
  return {
    hovered: null,
    pinned: null,
    ancestors: new Set(),
    descendants: new Set(),
  };
}

export class Renderer {
  private readonly context: CanvasRenderingContext2D;
  private atlas: HTMLImageElement | null = null;
  private readonly hitGrid = new Map<string, number[]>();
  /** Every resting trace, one path per edge kind, built once per view. */
  private idlePaths: Path2D[];
  private dpr = 1;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly data: Dataset,
    private readonly camera: Camera,
  ) {
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("canvas 2d context unavailable");
    this.context = context;
    this.buildHitGrid();
    this.idlePaths = this.buildPaths(() => true);
  }

  /** Rebuild what depends on the current view: hit testing and resting traces. */
  refresh(): void {
    this.buildHitGrid();
    this.idlePaths = this.buildPaths(() => true);
  }

  setAtlas(image: HTMLImageElement): void {
    this.atlas = image;
  }

  resize(): void {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { clientWidth, clientHeight } = this.canvas;
    this.canvas.width = Math.floor(clientWidth * this.dpr);
    this.canvas.height = Math.floor(clientHeight * this.dpr);
    this.camera.setViewport({ width: clientWidth, height: clientHeight });
  }

  /** Node index at a screen position, or null. */
  pick(screenX: number, screenY: number): number | null {
    if (screenY < TIER_HEADER_PX) return null;
    const { x, y } = this.camera.toWorld(screenX, screenY);
    const bucket = this.hitGrid.get(cellKey(x, y));
    if (!bucket) return null;
    // Later nodes in a cell draw on top, so search backwards.
    for (let i = bucket.length - 1; i >= 0; i--) {
      const index = bucket[i]!;
      const node = this.data.nodes[index]!;
      if (x >= node.x && x <= node.x + card.w && y >= node.y && y <= node.y + card.h) {
        return index;
      }
    }
    return null;
  }

  draw(selection: Selection): void {
    const context = this.context;
    const { scale } = this.camera;

    context.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    context.fillStyle = INK.background;
    context.fillRect(0, 0, this.canvas.width, this.canvas.height);

    context.translate(this.camera.x, this.camera.y);
    context.scale(scale, scale);

    const view = this.camera.visibleBounds(400);
    this.drawTierColumns(view);
    this.drawRows(view, scale);
    this.drawEdges(selection, scale);
    this.drawNodes(selection, view, scale);

    context.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawTierHeader();
  }

  // -- background --------------------------------------------------------

  /** Alternating stripes, one per tier band, so a band reads as a column. */
  private drawTierColumns(view: Bounds): void {
    const context = this.context;
    const { height, bands, repeatable } = this.data.view.geometry;
    bands.forEach((band, index) => {
      if (band.w <= 0 || band.x + band.w < view.x0 || band.x > view.x1) return;
      context.fillStyle = index % 2 ? "rgba(255,255,255,0.038)" : "rgba(255,255,255,0.014)";
      context.fillRect(band.x, 0, band.w, height);
    });
    context.fillStyle = withAlpha(FLAG.rare, 0.05);
    context.fillRect(repeatable.x, 0, repeatable.w, height);
  }

  /** Each row as a rounded band washed in its area's colour, with its label. */
  private drawRows(view: Bounds, scale: number): void {
    const context = this.context;
    const { width, rows } = this.data.view.geometry;

    for (const row of this.data.raw.rows) {
      const box = rows[row.i];
      if (!box?.shown) continue;
      if (box.y + box.h < view.y0 || box.y > view.y1) continue;
      const accent = rowAccent(row.group, row.colour);
      const top = box.y + 4;
      const bottom = box.y + box.h - ROW_GUTTER / 2;

      roundRect(context, BAND_INSET, top, width - 2 * BAND_INSET, bottom - top, BAND_RADIUS);
      context.fillStyle = withAlpha(accent, 0.09);
      context.fill();
      context.lineWidth = 1 / scale;
      context.strokeStyle = withAlpha(accent, 0.22);
      context.stroke();

      this.drawRowLabel(row, box, accent, scale);
    }
  }

  /**
   * The row's name and population, as a pill in the row header.
   *
   * Sized in world units against the header it sits in. It tends toward a
   * fixed screen size as the view zooms out, stops growing once it fills the
   * header, and is dropped when it becomes too small to read. It therefore can
   * never overlap a card, which a label sized in screen space does as soon as
   * the header shrinks below the text.
   */
  private drawRowLabel(row: RawRow, box: RowBox, accent: string, scale: number): void {
    const context = this.context;
    const font = Math.min(ROW_LABEL_MAX_FONT, Math.max(12, ROW_LABEL_SCREEN_PX / scale));
    if (font * scale < MIN_LABEL_PX) return;

    const text = row.label.toUpperCase();
    const count = String(box.count);
    const height = font * 1.6;
    const padding = font * 0.6;

    context.textBaseline = "middle";
    context.font = `600 ${font}px ${FONT.display}`;
    setLetterSpacing(context, font * 0.12);
    const textWidth = context.measureText(text).width;
    setLetterSpacing(context, 0);
    context.font = `${font * 0.8}px ${FONT.data}`;
    const countWidth = context.measureText(count).width;

    const x = BAND_INSET + 12;
    const y = box.y + (ROW_HEADER - height) / 2 + 2;
    roundRect(context, x, y, padding * 2 + textWidth + padding + countWidth, height, 4);
    context.fillStyle = withAlpha(INK.background, 0.6);
    context.fill();
    context.lineWidth = 1 / scale;
    context.strokeStyle = withAlpha(accent, 0.55);
    context.stroke();

    context.fillStyle = INK.muted;
    context.fillText(count, x + padding + textWidth + padding, y + height / 2 + 1);
    context.font = `600 ${font}px ${FONT.display}`;
    setLetterSpacing(context, font * 0.12);
    context.fillStyle = accent;
    context.fillText(text, x + padding, y + height / 2 + 1);
    setLetterSpacing(context, 0);
  }

  /**
   * Tier labels, pinned to the top of the view.
   *
   * Each label is fitted to its band's visible width -- "TIER 6", then "T6",
   * then "6", then nothing -- and clipped to the band besides, so a narrow band
   * far zoomed out can never push its label into its neighbour.
   */
  private drawTierHeader(): void {
    const context = this.context;
    const { scale, x: offset } = this.camera;
    const viewWidth = this.canvas.width / this.dpr;
    const bar = TIER_HEADER_PX;

    context.fillStyle = withAlpha(INK.background, 0.94);
    context.fillRect(0, 0, viewWidth, bar);
    context.fillStyle = INK.line;
    context.fillRect(0, bar - 1, viewWidth, 1);

    const { bands, repeatable } = this.data.view.geometry;
    const entries = [
      ...this.data.raw.bands.map((b, index) => ({
        ...bands[index]!,
        labels: [`TIER ${b.t}`, `T${b.t}`, `${b.t}`],
      })),
      {
        ...repeatable,
        labels: ["REPEATABLE", "REPEAT", "R"],
      },
    ];

    context.font = `600 11px ${FONT.display}`;
    context.textBaseline = "middle";
    entries.forEach((entry, index) => {
      if (entry.w <= 0) return;
      const left = offset + entry.x * scale;
      const right = left + entry.w * scale;
      if (right < 0 || left > viewWidth) return;

      if (index % 2) {
        context.fillStyle = "rgba(255,255,255,0.035)";
        context.fillRect(left, 0, right - left, bar - 1);
      }
      context.fillStyle = INK.line;
      context.fillRect(Math.round(left), 0, 1, bar - 1);

      const visibleLeft = Math.max(left, 0);
      const visibleRight = Math.min(right, viewWidth);
      const room = visibleRight - visibleLeft - 16;
      setLetterSpacing(context, 1.5);
      const label = entry.labels.find((l) => context.measureText(l).width <= room);
      if (label) {
        context.save();
        context.beginPath();
        context.rect(left, 0, right - left, bar);
        context.clip();
        context.fillStyle = index === entries.length - 1 ? FLAG.rare : INK.muted;
        context.fillText(label, visibleLeft + 8, bar / 2);
        context.restore();
      }
      setLetterSpacing(context, 0);
    });
  }

  // -- traces ------------------------------------------------------------

  private drawEdges(selection: Selection, scale: number): void {
    const context = this.context;
    const active = selection.pinned ?? selection.hovered;

    const resting = this.idlePaths;
    context.lineWidth = EDGE.width / scale;
    context.strokeStyle = EDGE.idle;
    context.globalAlpha = active === null ? EDGE.idleAlpha : EDGE.mutedAlpha;
    this.strokeByKind(resting, scale);
    context.globalAlpha = 1;

    if (active !== null) {
      // Ancestry behind, descendants ahead, in two passes so colour stays clean.
      for (const [set, colour] of [
        [selection.ancestors, EDGE.ancestor],
        [selection.descendants, EDGE.descendant],
      ] as const) {
        const paths = this.buildPaths(
          (s, t) => (set.has(s) || s === active) && (set.has(t) || t === active),
        );
        context.strokeStyle = colour;
        context.lineWidth = EDGE.activeWidth / scale;
        this.strokeByKind(paths, scale);
      }
    }
    context.setLineDash([]);
  }

  private strokeByKind(paths: Path2D[], scale: number): void {
    paths.forEach((path, kind) => {
      this.context.setLineDash(
        kind === EDGE_ALTERNATIVE
          ? [4 / scale, 4 / scale]
          : kind === EDGE_POTENTIAL_GATE
            ? [10 / scale, 6 / scale]
            : [],
      );
      this.context.stroke(path);
    });
  }

  /** One path per edge kind, holding every edge the filter keeps. */
  private buildPaths(keep: (source: number, target: number) => boolean): Path2D[] {
    const paths = [new Path2D(), new Path2D(), new Path2D()];
    for (const [source, target, kind] of this.data.view.edges) {
      if (!keep(source, target)) continue;
      const from = this.data.nodes[source];
      const to = this.data.nodes[target];
      if (from && to) this.trace(paths[kind] ?? paths[0]!, from, to);
    }
    return paths;
  }

  /**
   * A circuit-board trace from the right of one card to the left of another.
   *
   * Leaves horizontally, turns in the channel just before the target's column,
   * and arrives horizontally, with the corners cut at 45 degrees. Every trace
   * into a column turns in the same channel, so traces into one column merge
   * into a single trunk and branch off it, instead of each picking its own
   * turning point and fanning out.
   */
  private trace(path: Path2D, from: TechNode, to: TechNode): void {
    const { columnX } = this.data.view.geometry;
    const columnGap = COLUMN_GAP;
    const x1 = from.x + card.w;
    const y1 = from.y + card.h / 2;
    const x2 = to.x;
    const y2 = to.y + card.h / 2;
    const dy = y2 - y1;
    const direction = Math.sign(dy);
    const channel = (columnX[to.column] ?? to.x) - columnGap / 2;

    path.moveTo(x1, y1);
    if (channel >= x1 + CHAMFER) {
      if (Math.abs(dy) < 1) {
        path.lineTo(x2, y2);
        return;
      }
      const cut = Math.min(CHAMFER, Math.abs(dy) / 2);
      path.lineTo(channel - cut, y1);
      path.lineTo(channel, y1 + cut * direction);
      path.lineTo(channel, y2 - cut * direction);
      path.lineTo(channel + cut, y2);
      path.lineTo(x2, y2);
      return;
    }

    // A backward trace: one of the few potential-gates reaching down from a
    // higher tier. Out into the channel on the source's right, across in the
    // gap just above the target card, and into the target from its left.
    const out = x1 + columnGap / 2;
    const above = to.y - 7;
    path.lineTo(out, y1);
    path.lineTo(out, above);
    path.lineTo(channel, above);
    path.lineTo(channel, y2);
    path.lineTo(x2, y2);
  }

  // -- cards -------------------------------------------------------------

  private drawNodes(selection: Selection, view: Bounds, scale: number): void {
    const context = this.context;
    const active = selection.pinned ?? selection.hovered;
    const showText = scale >= LOD_TEXT;
    const showIcon = scale >= LOD_ICON;
    // Never thinner than a device pixel, or the bar vanishes zoomed out.
    const barWidth = Math.max(SIDE_BAR, 1.5 / scale);

    context.setLineDash([]);

    for (let index = 0; index < this.data.nodes.length; index++) {
      const node = this.data.nodes[index]!;
      if (node.hidden) continue;
      if (node.x + card.w < view.x0 || node.x > view.x1) continue;
      if (node.y + card.h < view.y0 || node.y > view.y1) continue;

      const isActive = index === active;
      const isAncestor = selection.ancestors.has(index);
      const isDescendant = selection.descendants.has(index);
      const dim = active !== null && !isActive && !isAncestor && !isDescendant;
      context.globalAlpha = dim ? HIGHLIGHT.dimmed : 1;

      roundRect(context, node.x, node.y, card.w, card.h, 4);
      context.fillStyle = INK.card;
      context.fill();
      context.lineWidth = (isActive || isAncestor || isDescendant ? 2 : 1) / scale;
      context.strokeStyle = isActive
        ? INK.text
        : isAncestor
          ? HIGHLIGHT.ancestor
          : isDescendant
            ? HIGHLIGHT.descendant
            : INK.line;
      context.stroke();

      // The side bar carries what the row cannot: danger, then rarity, and
      // otherwise the card's own research area.
      context.fillStyle = node.dangerous
        ? FLAG.dangerous
        : node.rare
          ? FLAG.rare
          : (AREA_ACCENT[node.area] ?? INK.line);
      roundRect(context, node.x, node.y, barWidth, card.h, 2);
      context.fill();

      const iconY = node.y + (card.h - ICON) / 2;
      if (showIcon && this.atlas && node.icon >= 0) {
        this.drawAtlas(node.icon, node.x + ICON_X, iconY, ICON);
      }
      if (showIcon && node.perkBadge !== undefined) {
        this.drawPerkBadge(node, node.x + ICON_X + ICON - 7, iconY + ICON - 5, scale);
      }

      const markers = showIcon ? this.drawMarkers(node) : 0;
      if (showText) this.drawCardText(node, showIcon, markers);

      context.globalAlpha = 1;
    }
  }

  /**
   * Markers in the card's top-right corner, returning the width they take.
   *
   * Rarity and danger are the side bar's colour, and colour alone must not
   * carry them, so each also gets a shape: a diamond for rare, a warning
   * triangle for dangerous. Crisis rows are tinted by crisis, so a card there
   * also has a dot in its research area's colour.
   */
  private drawMarkers(node: TechNode): number {
    const context = this.context;
    let right = node.x + card.w - 8;
    const cy = node.y + 12;
    const row = this.data.raw.rows[node.row];
    if (row?.group === "crisis") {
      context.fillStyle = AREA_ACCENT[node.area] ?? INK.muted;
      context.beginPath();
      context.arc(right - 4, cy, 4, 0, Math.PI * 2);
      context.fill();
      right -= 8 + MARKER_GAP;
    }
    if (node.dangerous) {
      const half = MARKER / 2;
      context.fillStyle = FLAG.dangerous;
      context.beginPath();
      context.moveTo(right - half, cy - half);
      context.lineTo(right, cy + half);
      context.lineTo(right - MARKER, cy + half);
      context.closePath();
      context.fill();
      context.fillStyle = INK.card;
      context.fillRect(right - half - 0.75, cy - 1.5, 1.5, 3);
      context.fillRect(right - half - 0.75, cy + 2.5, 1.5, 1.5);
      right -= MARKER + MARKER_GAP;
    }
    if (node.rare) {
      const half = MARKER / 2;
      context.fillStyle = FLAG.rare;
      context.beginPath();
      context.moveTo(right - half, cy - half);
      context.lineTo(right, cy);
      context.lineTo(right - half, cy + half);
      context.lineTo(right - MARKER, cy);
      context.closePath();
      context.fill();
      right -= MARKER + MARKER_GAP;
    }
    return node.x + card.w - 8 - right;
  }

  private drawCardText(node: TechNode, withIcon: boolean, markers = 0): void {
    const context = this.context;
    const textX = node.x + (withIcon ? ICON_X + ICON + 10 : 14);
    const right = node.x + card.w - 10;

    context.textBaseline = "top";
    context.fillStyle = INK.text;
    context.font = `600 14px ${FONT.display}`;
    // The name's first line keeps clear of the corner markers.
    wrapText(context, node.name, textX, node.y + 11, right - textX, 17, 2, right - textX - markers);

    const baseline = node.y + card.h - 21;
    context.font = `600 11px ${FONT.display}`;
    context.fillStyle = INK.muted;
    let tier = `T${node.tier}`;
    if (node.levels !== undefined) tier += node.levels < 0 ? " ∞" : ` ×${node.levels}`;
    context.fillText(tier, textX, baseline);
    let cursor = textX + context.measureText(tier).width + 8;

    // The tag says how an undrawable technology arrives; failing that, with no
    // profile chosen, a variant says it is one presentation of a technology
    // among several. A profile shows only the presentation it gets.
    const variant = node.variant && this.data.view.profile === null;
    const flag = node.tag ?? (variant ? "Variant" : "");
    let flagX = right;
    if (flag) {
      context.fillStyle = node.tag ? FLAG.rare : INK.muted;
      flagX = right - context.measureText(flag).width;
      context.fillText(flag, flagX, baseline);
    }

    if (node.cost) {
      context.font = `11px ${FONT.data}`;
      const cost = node.cost.toLocaleString();
      if (cursor + context.measureText(cost).width < flagX - 6) {
        context.fillStyle = INK.muted;
        context.fillText(cost, cursor, baseline);
        cursor += context.measureText(cost).width;
      }
    }
  }

  /**
   * The ascension perk behind a technology, as a badge on its icon's corner.
   *
   * Solid ring for a gate the technology declares, dashed for one it inherits
   * through a prerequisite, so the chain after a gated technology is visibly
   * gated without claiming the gate sits on every card.
   */
  private drawPerkBadge(node: TechNode, cx: number, cy: number, scale: number): void {
    const context = this.context;
    context.beginPath();
    context.arc(cx, cy, BADGE_RADIUS, 0, Math.PI * 2);
    context.fillStyle = INK.background;
    context.fill();
    context.lineWidth = 1.5 / scale;
    context.strokeStyle = FLAG.rare;
    context.setLineDash(node.perkInherited ? [3 / scale, 2 / scale] : []);
    context.stroke();
    context.setLineDash([]);

    if (this.atlas && node.perkBadge !== undefined && node.perkBadge >= 0) {
      context.save();
      context.beginPath();
      context.arc(cx, cy, BADGE_RADIUS - 2, 0, Math.PI * 2);
      context.clip();
      const size = (BADGE_RADIUS - 1) * 2;
      this.drawAtlas(node.perkBadge, cx - size / 2, cy - size / 2, size);
      context.restore();
    } else {
      context.fillStyle = FLAG.rare;
      context.font = `700 13px ${FONT.display}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText("✦", cx, cy + 1);
      context.textAlign = "left";
    }
  }

  private drawAtlas(slot: number, x: number, y: number, size: number): void {
    const atlas = this.data.raw.atlas;
    // Only the first sheet is loaded; the corpus fits on one.
    if (!this.atlas || Math.floor(slot / atlas.perSheet) !== 0) return;
    const cell = slot % atlas.perSheet;
    const sx = (cell % atlas.perRow) * atlas.cell;
    const sy = Math.floor(cell / atlas.perRow) * atlas.cell;
    this.context.drawImage(this.atlas, sx, sy, atlas.cell, atlas.cell, x, y, size, size);
  }

  private buildHitGrid(): void {
    this.hitGrid.clear();
    this.data.nodes.forEach((node, index) => {
      if (node.hidden) return;
      const x0 = Math.floor(node.x / HIT_CELL);
      const x1 = Math.floor((node.x + card.w) / HIT_CELL);
      const y0 = Math.floor(node.y / HIT_CELL);
      const y1 = Math.floor((node.y + card.h) / HIT_CELL);
      for (let cx = x0; cx <= x1; cx++) {
        for (let cy = y0; cy <= y1; cy++) {
          const key = `${cx}:${cy}`;
          const bucket = this.hitGrid.get(key);
          if (bucket) bucket.push(index);
          else this.hitGrid.set(key, [index]);
        }
      }
    });
  }
}

interface Bounds {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

function cellKey(x: number, y: number): string {
  return `${Math.floor(x / HIT_CELL)}:${Math.floor(y / HIT_CELL)}`;
}

/** `letterSpacing` where the browser supports it; a no-op elsewhere. */
function setLetterSpacing(context: CanvasRenderingContext2D, px: number): void {
  (context as CanvasRenderingContext2D & { letterSpacing?: string }).letterSpacing = `${px}px`;
}

function roundRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.arcTo(x + width, y, x + width, y + height, r);
  context.arcTo(x + width, y + height, x, y + height, r);
  context.arcTo(x, y + height, x, y, r);
  context.arcTo(x, y, x + width, y, r);
  context.closePath();
}

function wrapText(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines: number,
  firstLineWidth = maxWidth,
): void {
  const words = text.split(" ");
  let line = "";
  let lines = 0;
  const width = () => (lines === 0 ? firstLineWidth : maxWidth);
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (context.measureText(candidate).width > width() && line) {
      if (lines === maxLines - 1) {
        context.fillText(ellipsise(context, `${line} ${word}`, width()), x, y + lines * lineHeight);
        return;
      }
      context.fillText(ellipsise(context, line, width()), x, y + lines * lineHeight);
      lines += 1;
      line = word;
    } else {
      line = candidate;
    }
  }
  if (line && lines < maxLines) {
    context.fillText(ellipsise(context, line, width()), x, y + lines * lineHeight);
  }
}

/** Trim ``text`` with an ellipsis until it fits ``maxWidth``. */
function ellipsise(context: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (context.measureText(text).width <= maxWidth) return text;
  let end = text.length;
  while (end > 1 && context.measureText(`${text.slice(0, end)}…`).width > maxWidth) end--;
  return `${text.slice(0, end).trimEnd()}…`;
}
