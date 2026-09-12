/**
 * Canvas 2D renderer.
 *
 * Canvas rather than WebGL on purpose. The tree is ~1000 cards and ~1000 edges,
 * and viewport culling means a few hundred are ever drawn at once; measurements
 * on a comparable corpus put a WebGL build at 6 ms against a 16.7 ms budget, so
 * the extra machinery buys nothing here and costs a dependency plus a lot of
 * indirection.
 *
 * The renderer never computes geometry. Every position comes from the dataset.
 */

import { Camera } from "./camera";
import { AREA_COLOURS, EDGE, FLAGS, HIGHLIGHT, INK, tierTint } from "./theme";
import {
  EDGE_ALTERNATIVE,
  EDGE_POTENTIAL_GATE,
  type Dataset,
  type TechNode,
} from "./types";

/** Zoom thresholds. Below each, that much detail stops being drawn. */
const LOD_ICON = 0.14;
const LOD_TEXT = 0.42;
const LOD_ROW_LABEL = 0.06;
/** Below this, resting edges are not drawn at all. A thousand curves overlaid
 *  at full zoom-out read as a grey wash over the tree rather than as structure,
 *  and obscure the thing the overview is for: the shape of the rows. */
const LOD_IDLE_EDGES = 0.2;

/** Grid cell size for hit testing, in world units. */
const HIT_CELL = 512;

export interface Selection {
  /** Node index under the pointer, or null. */
  hovered: number | null;
  /** Node index pinned by a click, or null. */
  pinned: number | null;
  /** Node indices reachable backwards from the active node. */
  ancestors: Set<number>;
  /** Node indices reachable forwards from the active node. */
  descendants: Set<number>;
  /** When set, only these nodes are drawn: the isolate view. */
  isolated: Set<number> | null;
}

export function emptySelection(): Selection {
  return {
    hovered: null,
    pinned: null,
    ancestors: new Set(),
    descendants: new Set(),
    isolated: null,
  };
}

export class Renderer {
  private readonly context: CanvasRenderingContext2D;
  private atlas: HTMLImageElement | null = null;
  private readonly hitGrid = new Map<string, number[]>();
  private readonly maxTier: number;
  private dpr = 1;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly data: Dataset,
    private readonly camera: Camera,
  ) {
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("canvas 2d context unavailable");
    this.context = context;
    this.maxTier = Math.max(...data.nodes.map((n) => n.tier), 1);
    this.buildHitGrid();
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
    const { x, y } = this.camera.toWorld(screenX, screenY);
    const { card } = this.data.raw.canvas;
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
    this.drawRows(view, scale);
    this.drawBands(view, scale);
    this.drawEdges(selection, view);
    this.drawNodes(selection, view, scale);

    context.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  // -- layers ------------------------------------------------------------

  private drawRows(view: Bounds, scale: number): void {
    const context = this.context;
    const { width } = this.data.raw.canvas;

    for (const row of this.data.raw.rows) {
      if (row.y + row.h < view.y0 || row.y > view.y1) continue;
      const palette = AREA_COLOURS[row.group] ?? AREA_COLOURS.physics;

      context.fillStyle = INK.rowHeader;
      context.fillRect(0, row.y, width, row.h);

      // Accent stripe down the left edge carries the area even when the row
      // label has been dropped by LOD.
      context.fillStyle = palette.accent;
      context.globalAlpha = 0.55;
      context.fillRect(0, row.y, 6, row.h);
      context.globalAlpha = 1;

      if (scale >= LOD_ROW_LABEL) {
        context.fillStyle = palette.accent;
        context.font = `600 ${Math.round(26 / Math.max(scale, 0.25))}px system-ui, sans-serif`;
        context.textBaseline = "top";
        context.fillText(row.label, 22, row.y + 10);

        context.fillStyle = INK.textFaint;
        context.font = `${Math.round(20 / Math.max(scale, 0.25))}px system-ui, sans-serif`;
        const labelWidth = context.measureText(row.label).width;
        context.fillText(`${row.n}`, 34 + labelWidth, row.y + 12);
      }
    }
  }

  private drawBands(view: Bounds, scale: number): void {
    const context = this.context;
    const { height } = this.data.raw.canvas;

    for (const band of this.data.raw.bands) {
      if (band.x + band.w < view.x0 || band.x > view.x1) continue;
      context.fillStyle = tierTint("physics", band.t, this.maxTier);
      context.globalAlpha = 0.25;
      context.fillRect(band.x, 0, band.w, height);
      context.globalAlpha = 1;

      context.strokeStyle = INK.bandLine;
      context.lineWidth = 1 / scale;
      context.beginPath();
      context.moveTo(band.x, 0);
      context.lineTo(band.x, height);
      context.stroke();

      if (scale >= LOD_ROW_LABEL) {
        context.fillStyle = INK.bandLabel;
        context.font = `600 ${Math.round(22 / Math.max(scale, 0.25))}px system-ui, sans-serif`;
        context.textBaseline = "top";
        context.fillText(`TIER ${band.t}`, band.x + 12, 8);
      }
    }

    const repeatable = this.data.raw.repeatableBand;
    context.fillStyle = "#1a1420";
    context.globalAlpha = 0.6;
    context.fillRect(repeatable.x, 0, repeatable.w, height);
    context.globalAlpha = 1;
    if (scale >= LOD_ROW_LABEL) {
      context.fillStyle = INK.bandLabel;
      context.font = `600 ${Math.round(22 / Math.max(scale, 0.25))}px system-ui, sans-serif`;
      context.fillText("REPEATABLE", repeatable.x + 12, 8);
    }
  }

  private drawEdges(selection: Selection, view: Bounds): void {
    const context = this.context;
    const { card } = this.data.raw.canvas;
    const active = selection.pinned ?? selection.hovered;
    const { scale } = this.camera;

    // Edges are near-invisible until something is selected. With ~1000 of them
    // over 18 rows, drawing them all at full strength reads as a web rather
    // than as structure.
    const showAll = selection.isolated !== null;

    const drawIdle = showAll || (active === null && scale >= LOD_IDLE_EDGES);

    context.lineWidth = EDGE.width / scale;
    context.strokeStyle = EDGE.idle;
    context.beginPath();
    let drew = false;
    for (const [source, target, kind] of drawIdle ? this.data.raw.edges : []) {
      const from = this.data.nodes[source];
      const to = this.data.nodes[target];
      if (!from || !to) continue;
      if (selection.isolated && (!selection.isolated.has(source) || !selection.isolated.has(target)))
        continue;
      if (Math.max(from.x, to.x) < view.x0 || Math.min(from.x, to.x) > view.x1) continue;
      if (Math.max(from.y, to.y) < view.y0 || Math.min(from.y, to.y) > view.y1) continue;
      this.traceEdge(from, to, card, kind);
      drew = true;
    }
    if (drew) context.stroke();

    if (active === null) return;

    // Ancestry behind, descendants ahead, in two passes so colour stays clean.
    for (const [set, colour] of [
      [selection.ancestors, EDGE.ancestor],
      [selection.descendants, EDGE.descendant],
    ] as const) {
      context.strokeStyle = colour;
      context.lineWidth = EDGE.activeWidth / scale;
      context.beginPath();
      let any = false;
      for (const [source, target, kind] of this.data.raw.edges) {
        const inSet =
          (set.has(source) || source === active) && (set.has(target) || target === active);
        if (!inSet) continue;
        const from = this.data.nodes[source];
        const to = this.data.nodes[target];
        if (!from || !to) continue;
        this.traceEdge(from, to, card, kind);
        any = true;
      }
      if (any) context.stroke();
    }
  }

  private traceEdge(
    from: TechNode,
    to: TechNode,
    card: { w: number; h: number },
    kind: number,
  ): void {
    const context = this.context;
    const x0 = from.x + card.w;
    const y0 = from.y + card.h / 2;
    const x1 = to.x;
    const y1 = to.y + card.h / 2;

    // Style carries the edge kind, not colour, so hue stays unambiguously
    // "research area" across the whole canvas.
    const dash =
      kind === EDGE_ALTERNATIVE
        ? [4 / this.camera.scale, 6 / this.camera.scale]
        : kind === EDGE_POTENTIAL_GATE
          ? [12 / this.camera.scale, 8 / this.camera.scale]
          : [];
    context.setLineDash(dash);

    const midX = (x0 + x1) / 2;
    context.moveTo(x0, y0);
    context.bezierCurveTo(midX, y0, midX, y1, x1, y1);
  }

  private drawNodes(selection: Selection, view: Bounds, scale: number): void {
    const context = this.context;
    const { card } = this.data.raw.canvas;
    const atlas = this.data.raw.atlas;
    const active = selection.pinned ?? selection.hovered;
    const showText = scale >= LOD_TEXT;
    const showIcon = scale >= LOD_ICON;

    context.setLineDash([]);
    context.textBaseline = "top";

    for (let index = 0; index < this.data.nodes.length; index++) {
      const node = this.data.nodes[index]!;
      if (selection.isolated && !selection.isolated.has(index)) continue;
      if (node.x + card.w < view.x0 || node.x > view.x1) continue;
      if (node.y + card.h < view.y0 || node.y > view.y1) continue;

      const row = this.data.raw.rows[node.row];
      const palette = AREA_COLOURS[row?.group ?? node.area] ?? AREA_COLOURS.physics;

      const isActive = index === active;
      const isRelated =
        active !== null && (selection.ancestors.has(index) || selection.descendants.has(index));
      const dim = active !== null && !isActive && !isRelated;

      context.globalAlpha = dim ? HIGHLIGHT.dimmed : 1;

      context.fillStyle = palette.card;
      roundRect(context, node.x, node.y, card.w, card.h, 6);
      context.fill();

      context.lineWidth = (isActive ? 3 : node.dangerous ? 2.4 : 1.2) / scale;
      context.strokeStyle = isActive
        ? HIGHLIGHT.selected
        : selection.ancestors.has(index)
          ? HIGHLIGHT.ancestor
          : selection.descendants.has(index)
            ? HIGHLIGHT.descendant
            : node.dangerous
              ? FLAGS.dangerous
              : palette.border;
      context.stroke();

      if (showIcon && this.atlas && node.icon >= 0) {
        const sheetIndex = Math.floor(node.icon / atlas.perSheet);
        if (sheetIndex === 0) {
          const cell = node.icon % atlas.perSheet;
          const sx = (cell % atlas.perRow) * atlas.cell;
          const sy = Math.floor(cell / atlas.perRow) * atlas.cell;
          context.drawImage(
            this.atlas,
            sx,
            sy,
            atlas.cell,
            atlas.cell,
            node.x + 8,
            node.y + 8,
            card.h - 16,
            card.h - 16,
          );
        }
      }

      if (showText) {
        const textX = node.x + (showIcon ? card.h - 2 : 10);
        context.fillStyle = INK.text;
        context.font = "600 15px system-ui, sans-serif";
        wrapText(context, node.name, textX, node.y + 10, card.w - (textX - node.x) - 10, 18, 2);

        context.fillStyle = INK.textDim;
        context.font = "11px system-ui, sans-serif";
        const badges: string[] = [`T${node.tier}`];
        if (node.levels !== undefined) badges.push(node.levels < 0 ? "∞" : `×${node.levels}`);
        if (node.spilled) badges.push("spilled");
        if (node.variant) badges.push("variant");
        context.fillText(badges.join("  "), textX, node.y + card.h - 20);

        if (node.undrawable) {
          context.fillStyle = FLAGS.weightless;
          context.fillText("event", node.x + card.w - 44, node.y + card.h - 20);
        } else if (node.rare) {
          context.fillStyle = FLAGS.rare;
          context.fillText("rare", node.x + card.w - 38, node.y + card.h - 20);
        }
      }

      // Area dot: crisis rows are tinted by crisis, so the card has to say
      // which research area it actually belongs to.
      if (row?.group === "crisis" && showIcon) {
        const areaPalette = AREA_COLOURS[node.area] ?? AREA_COLOURS.physics;
        context.fillStyle = areaPalette.accent;
        context.beginPath();
        context.arc(node.x + card.w - 12, node.y + 12, 5, 0, Math.PI * 2);
        context.fill();
      }

      context.globalAlpha = 1;
    }
  }

  private buildHitGrid(): void {
    const { card } = this.data.raw.canvas;
    this.data.nodes.forEach((node, index) => {
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

function roundRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.arcTo(x + width, y, x + width, y + height, radius);
  context.arcTo(x + width, y + height, x, y + height, radius);
  context.arcTo(x, y + height, x, y, radius);
  context.arcTo(x, y, x + width, y, radius);
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
): void {
  const words = text.split(" ");
  let line = "";
  let lines = 0;
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (context.measureText(candidate).width > maxWidth && line) {
      context.fillText(line, x, y + lines * lineHeight);
      lines += 1;
      if (lines >= maxLines) {
        context.fillText("…", x + maxWidth - 8, y + (lines - 1) * lineHeight);
        return;
      }
      line = word;
    } else {
      line = candidate;
    }
  }
  if (line && lines < maxLines) context.fillText(line, x, y + lines * lineHeight);
}
