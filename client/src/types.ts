/**
 * Shape of the dataset the pipeline emits.
 *
 * Field names are abbreviated in the file because it holds ~1000 records of
 * near-identical shape, where full key names would be a large fraction of the
 * bytes. They are expanded into readable names at load time so nothing
 * downstream has to remember what `ic` meant.
 */

import { computeGeometry, type Geometry } from "./geometry";

export type Area = "physics" | "society" | "engineering" | "crisis";

export type NodeFlag =
  | "dangerous"
  | "rare"
  | "undrawable"
  | "start"
  | "spilled"
  | "variant"
  | "gated"
  | "perk-inherited";

/** Index into `dataset.edgeKinds`. */
export const EDGE_PREREQUISITE = 0;
export const EDGE_ALTERNATIVE = 1;
export const EDGE_POTENTIAL_GATE = 2;

/** An in-place swap a slot is presented as, for the profiles in `m`. */
export interface RawPresentation {
  /** Hex mask over `dataset.profiles`. */
  m: string;
  n: string;
  ic: number;
  sw: string;
}

export interface RawNode {
  k: string;
  n: string;
  r: number;
  c: number;
  /** Order within its (row, column) cell. */
  i: number;
  t: number;
  a: Area;
  g: string;
  ic: number;
  /** Swap name, on a slot presenting a relocating `technology_swap`. */
  sw?: string;
  /** Atlas slot of the perk badge; -1 when the perk ships no art. */
  pb?: number;
  /** How an undrawable technology arrives, as a card tag: "Event", "Starting",
   *  "Observation Insight", or a manual override. */
  tg?: string;
  f?: NodeFlag[];
  $?: number;
  lv?: number;
  /** Hex mask over `dataset.profiles`: where this slot is not shown. */
  hp?: string;
  pv?: RawPresentation[];
}

export interface RawRow {
  i: number;
  group: Area;
  key: string;
  label: string;
}

export interface RawBand {
  t: number;
  s: number;
  e: number;
}

export interface RawProfile {
  /** Stable key, e.g. "hive-bio-ships-wilderness". */
  k: string;
  l: string;
  /** "regular", "hive" or "machine". */
  a: string;
  /** Toggles switched on. */
  t: string[];
}

export interface RawDataset {
  meta: {
    generated: string;
    sources: { key: string; name: string; version: string | null }[];
    counts: Record<string, number>;
  };
  /** `size` is the sheet's pixel width and height; `perRow * cell` is smaller,
   *  so CSS background scaling needs the real figure. */
  atlas: { sheets: string[]; cell: number; perRow: number; perSheet: number; size: number };
  edgeKinds: string[];
  /** Every empire an empire can be created as; masks index this list. */
  profiles: RawProfile[];
  authorities: [string, string][];
  toggles: [string, string][];
  rows: RawRow[];
  bands: RawBand[];
  repeatableColumn: number;
  columns: number;
  nodes: RawNode[];
  /** `[sourceNodeIndex, targetNodeIndex, edgeKind]`, indices into `nodes`, for
   *  the all-empires view. */
  edges: [number, number, number][];
}

export interface Presentation {
  mask: bigint;
  name: string;
  icon: number;
  swap?: string;
}

/** A node with its flags expanded and its adjacency resolved. */
export interface TechNode {
  key: string;
  /** The swap this slot is presented as, if any. Details are keyed by it. */
  swap?: string;
  name: string;
  row: number;
  column: number;
  /** Order within its (row, column) cell. */
  order: number;
  /** Set by the geometry for the current view. */
  x: number;
  y: number;
  tier: number;
  area: Area;
  category: string;
  icon: number;
  cost?: number;
  levels?: number;
  dangerous: boolean;
  rare: boolean;
  /** Never offered for research on its own merits. */
  undrawable: boolean;
  isStart: boolean;
  spilled: boolean;
  /** Behind at least one gate -- a perk, tradition, origin or civic --
   *  declared or inherited. */
  gated: boolean;
  /** The card badge's perk gate is inherited through a prerequisite. */
  perkInherited: boolean;
  /** How an undrawable technology arrives, in a word or two for the card. */
  tag?: string;
  /** Atlas slot for the card's perk badge: -1 for a perk without art,
   *  `undefined` for no badge at all. */
  perkBadge?: number;
  /** A second slot for a technology a swap relocates; not its own technology. */
  variant: boolean;
  /** Profiles in which this slot is not shown. */
  hiddenIn: bigint;
  /** Not shown in the current view. */
  hidden: boolean;
  /** How the slot is presented with no profile chosen. */
  base: Presentation;
  presentations: Presentation[];
  /** Indices of nodes this one depends on, in the current view. */
  incoming: number[];
  /** Indices of nodes that depend on this one, in the current view. */
  outgoing: number[];
}

export interface View {
  /** Index into `profiles`, or null for every empire at once. */
  profile: number | null;
  edges: [number, number, number][];
  geometry: Geometry;
}

export interface Dataset {
  raw: RawDataset;
  nodes: TechNode[];
  /** Every node index sharing a technology key, so variants light up together. */
  byKey: Map<string, number[]>;
  /** Technology-level dependencies, `[sourceKey, targetKey, edgeKind]`. */
  dependencies: [string, string, number][];
  view: View;
}

export function mask(hex: string | undefined): bigint {
  return hex ? BigInt(`0x${hex}`) : 0n;
}

export function profileBit(profile: number): bigint {
  return 1n << BigInt(profile);
}

export function expand(raw: RawDataset): Dataset {
  const flags = (node: RawNode, flag: NodeFlag) => node.f?.includes(flag) ?? false;

  const nodes: TechNode[] = raw.nodes.map((node) => {
    const base: Presentation = { mask: 0n, name: node.n, icon: node.ic, swap: node.sw };
    return {
      key: node.k,
      swap: node.sw,
      name: node.n,
      row: node.r,
      column: node.c,
      order: node.i,
      x: 0,
      y: 0,
      tier: node.t,
      area: node.a,
      category: node.g,
      icon: node.ic,
      cost: node.$,
      levels: node.lv,
      dangerous: flags(node, "dangerous"),
      rare: flags(node, "rare"),
      undrawable: flags(node, "undrawable"),
      isStart: flags(node, "start"),
      spilled: flags(node, "spilled"),
      gated: flags(node, "gated"),
      tag: node.tg,
      perkInherited: flags(node, "perk-inherited"),
      perkBadge: node.pb,
      variant: flags(node, "variant"),
      hiddenIn: mask(node.hp),
      hidden: false,
      base,
      presentations: (node.pv ?? []).map((p) => ({
        mask: mask(p.m),
        name: p.n,
        icon: p.ic,
        swap: p.sw,
      })),
      incoming: [],
      outgoing: [],
    };
  });

  const byKey = new Map<string, number[]>();
  nodes.forEach((node, index) => {
    const existing = byKey.get(node.key);
    if (existing) existing.push(index);
    else byKey.set(node.key, [index]);
  });

  const seen = new Set<string>();
  const dependencies: [string, string, number][] = [];
  for (const [source, target, kind] of raw.edges) {
    const edge: [string, string, number] = [nodes[source]!.key, nodes[target]!.key, kind];
    const id = edge.join("|");
    if (seen.has(id)) continue;
    seen.add(id);
    dependencies.push(edge);
  }

  const data: Dataset = {
    raw,
    nodes,
    byKey,
    dependencies,
    view: { profile: null, edges: [], geometry: undefined as unknown as Geometry },
  };
  applyProfile(data, null);
  return data;
}

/**
 * Show the tree as one profile sees it, or as every empire does.
 *
 * Hides what the profile never gets, presents each slot under the name the
 * profile knows it by, rewires dependencies between the slots left visible,
 * and closes up the gaps.
 */
export function applyProfile(data: Dataset, profile: number | null): void {
  const bit = profile === null ? 0n : profileBit(profile);

  for (const node of data.nodes) {
    node.hidden = profile !== null && (node.hiddenIn & bit) !== 0n;
    const shown =
      profile === null ? undefined : node.presentations.find((p) => (p.mask & bit) !== 0n);
    const presentation = shown ?? node.base;
    node.name = presentation.name;
    node.icon = presentation.icon;
    node.swap = presentation.swap;
  }

  let edges: [number, number, number][];
  if (profile === null) {
    edges = data.raw.edges;
  } else {
    edges = [];
    const visible = (key: string) =>
      (data.byKey.get(key) ?? []).filter((index) => !data.nodes[index]!.hidden);
    for (const [sourceKey, targetKey, kind] of data.dependencies) {
      const sources = visible(sourceKey);
      if (sources.length === 0) continue;
      for (const target of visible(targetKey)) {
        for (const source of sources) edges.push([source, target, kind]);
      }
    }
  }

  for (const node of data.nodes) {
    node.incoming = [];
    node.outgoing = [];
  }
  for (const [source, target] of edges) {
    data.nodes[source]?.outgoing.push(target);
    data.nodes[target]?.incoming.push(source);
  }

  data.view = { profile, edges, geometry: computeGeometry(data) };
}
