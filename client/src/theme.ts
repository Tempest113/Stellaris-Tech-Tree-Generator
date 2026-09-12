/**
 * Colour.
 *
 * Two signals share the background: research area sets the hue, tier sets how
 * saturated it is. Keeping hue and saturation on separate meanings means one
 * can be read without decoding the other, which matters because a reader
 * usually wants one or the other, not both at once.
 *
 * Crisis rows get their own hue rather than an area hue, because a crisis chain
 * spans all three research areas and tinting it by any one of them would be a
 * lie. The per-card area dot carries the area instead.
 *
 * Edges carry meaning in their dash pattern, not their colour, so that hue
 * stays unambiguously "research area" everywhere on the canvas. That also keeps
 * the three edge kinds distinguishable for a red-green colourblind reader.
 */

import type { Area } from "./types";

export interface AreaPalette {
  /** Row background, darkest tier. */
  rowLow: string;
  /** Row background, highest tier. */
  rowHigh: string;
  /** Card fill. */
  card: string;
  /** Card border. */
  border: string;
  /** Row label and area dot. */
  accent: string;
}

export const AREA_COLOURS: Record<Area, AreaPalette> = {
  physics: {
    rowLow: "#0e1a2b",
    rowHigh: "#16304f",
    card: "#1b2c44",
    border: "#2f5580",
    accent: "#6fb3ff",
  },
  society: {
    rowLow: "#101f16",
    rowHigh: "#173a25",
    card: "#1a3325",
    border: "#2f6b45",
    accent: "#6ddb95",
  },
  engineering: {
    rowLow: "#241608",
    rowHigh: "#452a0e",
    card: "#38240f",
    border: "#7a4d1c",
    accent: "#ffae5c",
  },
  crisis: {
    rowLow: "#22101c",
    rowHigh: "#3f1a33",
    card: "#331526",
    border: "#7a2c58",
    accent: "#ff7fc4",
  },
};

export const INK = {
  background: "#0a0c10",
  text: "#e8ecf2",
  textDim: "#8e99a8",
  textFaint: "#5c6675",
  rowHeader: "#0d1117",
  bandLine: "#1b222c",
  bandLabel: "#4d5866",
};

export const EDGE = {
  /** Edges are barely there until something is selected. */
  idle: "rgba(150,165,185,0.055)",
  ancestor: "#ffd479",
  descendant: "#79c8ff",
  width: 1.6,
  activeWidth: 2.6,
};

export const HIGHLIGHT = {
  selected: "#ffffff",
  ancestor: "#ffd479",
  descendant: "#79c8ff",
  dimmed: 0.18,
};

/** Flag treatments. Dangerous and weightless matter more than rare here. */
export const FLAGS = {
  /** In a Gigastructures build 74% of technologies are rare, so it is a weak
   *  signal and gets the quietest treatment. */
  rare: "#c9a227",
  dangerous: "#ff5f5f",
  /** Never drawn from the research pool: granted by event or ascension. */
  weightless: "#9b7fe0",
};

/**
 * Blend a row's background between its low and high tint by tier.
 *
 * Tier 5 holds 383 of 978 technologies, so an even spread across the declared
 * 0-9 range would leave most of the tree in one shade. The ramp is normalised
 * against the tier range that actually carries content.
 */
export function tierTint(area: Area, tier: number, maxTier: number): string {
  const palette = AREA_COLOURS[area] ?? AREA_COLOURS.physics;
  const t = maxTier > 0 ? Math.min(1, Math.max(0, tier / maxTier)) : 0;
  return mix(palette.rowLow, palette.rowHigh, t);
}

function mix(a: string, b: string, t: number): string {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const c = pa.map((v, i) => Math.round(v + ((pb[i] ?? v) - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function parseHex(hex: string): number[] {
  const value = hex.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}
