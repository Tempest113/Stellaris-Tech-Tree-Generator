/**
 * Shape of the dataset the pipeline emits.
 *
 * Field names are abbreviated in the file because it holds ~1000 records of
 * near-identical shape, where full key names would be a large fraction of the
 * bytes. They are expanded into readable names at load time so nothing
 * downstream has to remember what `ic` meant.
 */

export type Area = "physics" | "society" | "engineering" | "crisis";

export type NodeFlag =
  | "dangerous"
  | "rare"
  | "weightless"
  | "start"
  | "spilled"
  | "variant"
  | "perk-gated";

/** Index into `dataset.edgeKinds`. */
export const EDGE_PREREQUISITE = 0;
export const EDGE_ALTERNATIVE = 1;
export const EDGE_POTENTIAL_GATE = 2;

export interface RawNode {
  k: string;
  n: string;
  r: number;
  c: number;
  i: number;
  x: number;
  y: number;
  t: number;
  a: Area;
  g: string;
  ic: number;
  f?: NodeFlag[];
  $?: number;
  lv?: number;
}

export interface RawRow {
  i: number;
  group: Area;
  key: string;
  label: string;
  n: number;
  y: number;
  h: number;
  header: number;
}

export interface RawBand {
  t: number;
  s: number;
  e: number;
  x: number;
  w: number;
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
  canvas: {
    width: number;
    height: number;
    card: { w: number; h: number };
    columnPitch: number;
  };
  edgeKinds: string[];
  rows: RawRow[];
  bands: RawBand[];
  repeatableBand: { x: number; w: number };
  repeatableColumn: number;
  columns: number;
  nodes: RawNode[];
  /** `[sourceNodeIndex, targetNodeIndex, edgeKind]` */
  edges: [number, number, number][];
}

/** A node with its flags expanded and its adjacency resolved. */
export interface TechNode {
  key: string;
  name: string;
  row: number;
  column: number;
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
  weightless: boolean;
  isStart: boolean;
  spilled: boolean;
  /** Behind at least one ascension perk. Independent of `weightless`: a
   *  technology can be perk-gated and event-granted at once. */
  perkGated: boolean;
  /** A second slot for a technology a swap relocates; not its own technology. */
  variant: boolean;
  /** Indices of nodes this one depends on. */
  incoming: number[];
  /** Indices of nodes that depend on this one. */
  outgoing: number[];
}

export interface Dataset {
  raw: RawDataset;
  nodes: TechNode[];
  /** Every node index sharing a technology key, so variants light up together. */
  byKey: Map<string, number[]>;
}

export function expand(raw: RawDataset): Dataset {
  const flags = (node: RawNode, flag: NodeFlag) => node.f?.includes(flag) ?? false;

  const nodes: TechNode[] = raw.nodes.map((node) => ({
    key: node.k,
    name: node.n,
    row: node.r,
    column: node.c,
    x: node.x,
    y: node.y,
    tier: node.t,
    area: node.a,
    category: node.g,
    icon: node.ic,
    cost: node.$,
    levels: node.lv,
    dangerous: flags(node, "dangerous"),
    rare: flags(node, "rare"),
    weightless: flags(node, "weightless"),
    isStart: flags(node, "start"),
    spilled: flags(node, "spilled"),
    perkGated: flags(node, "perk-gated"),
    variant: flags(node, "variant"),
    incoming: [],
    outgoing: [],
  }));

  for (const [source, target] of raw.edges) {
    nodes[source]?.outgoing.push(target);
    nodes[target]?.incoming.push(source);
  }

  const byKey = new Map<string, number[]>();
  nodes.forEach((node, index) => {
    const existing = byKey.get(node.key);
    if (existing) existing.push(index);
    else byKey.set(node.key, [index]);
  });

  return { raw, nodes, byKey };
}
