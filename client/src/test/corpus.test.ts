/**
 * Invariants of every view of the built dataset.
 *
 * Skipped when `public/data/dataset.json` has not been built. Hand-built
 * fixtures pin the rules; this pins that the real tree obeys them under every
 * empire profile and in isolated lineages -- where traces once ran to cards
 * that were not drawn.
 */

import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CARD_HEIGHT, CARD_WIDTH } from "../geometry";
import { EDGE_POTENTIAL_GATE, applyView, expand, type Dataset, type RawDataset } from "../types";

const PATH = new URL("../../public/data/dataset.json", import.meta.url);
const built = existsSync(PATH);
const raw = built ? (JSON.parse(readFileSync(PATH, "utf-8")) as RawDataset) : undefined;

/** Everything a card needs and everything that needs it, as `main.ts` isolates it. */
function lineage(data: Dataset, start: number): Set<number> {
  const keep = new Set<number>([start]);
  for (const direction of ["incoming", "outgoing"] as const) {
    const queue = [start];
    const seen = new Set<number>();
    while (queue.length) {
      for (const next of data.nodes[queue.pop()!]![direction]) {
        if (seen.has(next)) continue;
        seen.add(next);
        keep.add(next);
        queue.push(next);
      }
    }
  }
  return keep;
}

function problems(data: Dataset): string[] {
  const found: string[] = [];
  const { geometry, edges } = data.view;
  const visible = data.nodes.map((n, i) => [n, i] as const).filter(([n]) => !n.hidden);

  for (const [source, target, kind] of edges) {
    if (data.nodes[source]!.hidden || data.nodes[target]!.hidden) {
      found.push(`edge ${data.nodes[source]!.key} -> ${data.nodes[target]!.key} touches a hidden card`);
    }
    if (kind !== EDGE_POTENTIAL_GATE && data.nodes[source]!.column >= data.nodes[target]!.column) {
      found.push(`prerequisite ${data.nodes[source]!.key} is not left of ${data.nodes[target]!.key}`);
    }
  }
  const occupied = new Map<string, string>();
  for (const [n] of visible) {
    if (n.x < 0 || n.y < 0 || n.x + CARD_WIDTH > geometry.width || n.y + CARD_HEIGHT > geometry.height) {
      found.push(`${n.key} lies outside the canvas`);
    }
    if (!geometry.rows[n.row]!.shown) found.push(`${n.key} is in a row that is not shown`);
    const spot = `${n.x},${n.y}`;
    if (occupied.has(spot)) found.push(`${n.key} overlaps ${occupied.get(spot)}`);
    occupied.set(spot, n.key);
  }
  return found;
}

describe.skipIf(!built)("the built dataset", () => {
  it("is consistent with every empire, and with none chosen", () => {
    const data = expand(raw!);
    for (let profile = -1; profile < raw!.profiles.length; profile++) {
      applyView(data, profile < 0 ? null : profile, null);
      expect(problems(data), raw!.profiles[profile]?.k ?? "all").toEqual([]);
    }
  });

  it("isolates every lineage into a compact, consistent tree", () => {
    const data = expand(raw!);
    const failures: string[] = [];
    for (const profile of [null, 0, raw!.profiles.length - 1]) {
      applyView(data, profile, null);
      const whole = data.view.geometry.width;
      const shown = data.nodes.flatMap((n, i) => (n.hidden ? [] : [i]));
      for (const index of shown) {
        applyView(data, profile, null);
        const keep = lineage(data, index);
        applyView(data, profile, keep);
        const found = problems(data);
        if (data.nodes.some((n, i) => !n.hidden !== keep.has(i))) found.push("visible cards differ from the lineage");
        if (data.view.geometry.width > whole) found.push("isolated tree is wider than the whole");
        failures.push(...found.map((f) => `${data.nodes[index]!.key}: ${f}`));
      }
    }
    expect(failures.slice(0, 10)).toEqual([]);
  });
});
