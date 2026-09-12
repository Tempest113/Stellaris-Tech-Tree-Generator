/**
 * Colour and type.
 *
 * Carried over from the first version of the tree: neutral surfaces, with the
 * research area the only saturated colour on the canvas, so hue always means
 * "area" and nothing else.
 *
 * Area lives on the row. A row is washed in its area's colour, which reads at
 * any zoom level, and a card is left plain so its text stays legible. The
 * card's side bar says the one thing the row cannot: whether this technology
 * is dangerous or rare.
 *
 * Crisis rows take a hue per crisis rather than an area hue, because a crisis
 * chain spans all three research areas and tinting it by any one would be a lie.
 *
 * Edges carry their kind in the dash pattern, not in colour, which keeps the
 * three kinds distinguishable for a red-green colourblind reader.
 */

export const INK = {
  background: "#141414",
  panel: "#1b1b1e",
  card: "#212125",
  line: "#38363c",
  text: "#eef0f2",
  muted: "#8f8b94",
};

export const AREA_ACCENT: Record<string, string> = {
  physics: "#53a8e2",
  society: "#63c78a",
  engineering: "#e0a458",
};

/** Keyed by crisis row key, from `config/rows.toml`. */
export const CRISIS_ACCENT: Record<string, string> = {
  blokkats: "#52d97e",
  sirens: "#b07be0",
  aeternum: "#d8bd5a",
  compound: "#5fc2c8",
  katzen: "#e07fae",
};

/** Fallback for a crisis row the palette does not know yet. */
export const STUB = "#726e75";

export const FLAG = {
  rare: "#a07be0",
  dangerous: "#e05c5c",
};

export const EDGE = {
  /** Resting traces: present, but quieter than any card. */
  idle: "#5e5a66",
  idleAlpha: 0.7,
  /** Resting traces while something is selected, so the lineage stands out. */
  mutedAlpha: 0.12,
  ancestor: "#ffd479",
  descendant: "#79c8ff",
  width: 1.5,
  activeWidth: 2.4,
};

export const HIGHLIGHT = {
  ancestor: "#ffd479",
  descendant: "#79c8ff",
  dimmed: 0.25,
};

export const FONT = {
  display: '"Chakra Petch", "Segoe UI", system-ui, sans-serif',
  data: 'ui-monospace, "Cascadia Mono", "JetBrains Mono", monospace',
};

export function rowAccent(group: string, key: string): string {
  if (group === "crisis") return CRISIS_ACCENT[key] ?? STUB;
  return AREA_ACCENT[group] ?? STUB;
}

/** `#rrggbb` at an alpha, as an `rgba()` string. */
export function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
