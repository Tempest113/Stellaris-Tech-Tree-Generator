/**
 * Small hand-built datasets for the client tests.
 *
 * Only the fields the code under test reads are filled in. A node is written
 * as `[key, row, column]` with anything else as overrides.
 */

import type { RawDataset, RawNode } from "../types";

export function node(key: string, row: number, column: number, extra: Partial<RawNode> = {}): RawNode {
  return { k: key, n: key, r: row, c: column, i: 0, t: 1, a: "physics", g: "", ic: -1, ...extra };
}

export function dataset(nodes: RawNode[], edges: [number, number, number][] = [], extra: Partial<RawDataset> = {}): RawDataset {
  const rows = Math.max(...nodes.map((n) => n.r)) + 1;
  const columns = Math.max(...nodes.map((n) => n.c)) + 1;
  return {
    meta: { generated: "2026-09-13T00:00:00+00:00", sources: [], counts: {} },
    atlas: { sheets: [], cell: 56, perRow: 36, perSheet: 1296, size: 2048 },
    edgeKinds: ["prerequisite", "alternative", "potential-gate"],
    profiles: [
      { k: "regular", l: "Individualist", a: "regular", t: [] },
      { k: "hive", l: "Hive Mind", a: "hive", t: [] },
    ],
    authorities: [["regular", "Individualist"], ["hive", "Hive Mind"]],
    toggles: [],
    rows: Array.from({ length: rows }, (_, i) => ({ i, group: "physics", key: `row${i}`, label: `Row ${i}` })),
    bands: [{ t: 1, s: 0, e: columns - 1 }],
    repeatableColumn: columns - 1,
    columns,
    nodes,
    edges,
    ...extra,
  };
}
