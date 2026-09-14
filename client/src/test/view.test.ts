/**
 * Geometry and per-profile views, against small hand-built datasets.
 */

import { describe, expect, it } from "vitest";
import { CARD_WIDTH, ROW_HEADER } from "../geometry";
import { applyView, expand } from "../types";
import { dataset, node } from "./fixtures";

describe("computeGeometry", () => {
  it("stacks a cell's cards in the pipeline's order", () => {
    const data = expand(dataset([node("b", 0, 0, { i: 1 }), node("a", 0, 0, { i: 0 })]));
    const [b, a] = data.nodes;
    expect(a!.x).toBe(b!.x);
    expect(a!.y).toBeLessThan(b!.y);
    expect(a!.y).toBe(data.view.geometry.rows[0]!.y + ROW_HEADER);
  });

  it("keeps columns left to right", () => {
    const data = expand(dataset([node("a", 0, 0), node("b", 0, 1), node("c", 0, 2)]));
    const xs = data.nodes.map((n) => n.x);
    expect(xs[0]).toBeLessThan(xs[1]!);
    expect(xs[1]! - xs[0]!).toBeGreaterThanOrEqual(CARD_WIDTH);
    expect(xs[1]).toBeLessThan(xs[2]!);
  });

  it("wraps a tall cell into sub-columns and widens its column", () => {
    const cell = Array.from({ length: 9 }, (_, i) => node(`t${i}`, 0, 0, { i }));
    const data = expand(dataset([...cell, node("next", 0, 1)]));
    const ninth = data.nodes[8]!;
    expect(ninth.x).toBeGreaterThan(data.nodes[0]!.x);
    expect(ninth.y).toBe(data.nodes[0]!.y);
    expect(data.nodes[9]!.x).toBeGreaterThanOrEqual(ninth.x + CARD_WIDTH);
  });

  it("gives a row with nothing visible no height", () => {
    const raw = dataset([node("a", 0, 0), node("b", 1, 0, { hp: "1" }), node("c", 2, 0)]);
    const data = expand(raw);
    applyView(data, 0, null);
    const rows = data.view.geometry.rows;
    expect(rows[1]!.shown).toBe(false);
    expect(rows[2]!.y).toBe(rows[0]!.y + rows[0]!.h);
    // With every empire, the row is back.
    applyView(data, null, null);
    expect(data.view.geometry.rows[1]!.shown).toBe(true);
  });

  it("collapses empty columns only in a compact view", () => {
    const raw = dataset([node("a", 0, 0), node("gap", 1, 1), node("c", 0, 2)]);
    const data = expand(raw);
    const whole = data.view.geometry.width;
    applyView(data, null, new Set([0, 2]));
    expect(data.view.geometry.width).toBeLessThan(whole);
    expect(data.nodes[2]!.x - data.nodes[0]!.x).toBeLessThan(2 * CARD_WIDTH + 200);
    expect(data.nodes[2]!.y).toBe(data.nodes[0]!.y);
  });
});

describe("applyView", () => {
  it("hides what a profile never gets, and nothing with every empire", () => {
    const data = expand(dataset([node("a", 0, 0), node("hive_only", 0, 1, { hp: "1" })]));
    applyView(data, 0, null);
    expect(data.nodes.map((n) => n.hidden)).toEqual([false, true]);
    applyView(data, 1, null);
    expect(data.nodes.map((n) => n.hidden)).toEqual([false, false]);
    applyView(data, null, null);
    expect(data.nodes.map((n) => n.hidden)).toEqual([false, false]);
  });

  it("presents a slot under the name, badge and tag its profile sees", () => {
    const raw = dataset([
      node("t", 0, 0, {
        n: "Default",
        tg: "Special Project",
        pb: 3,
        pv: [{ m: "2", n: "Hive Name", ic: 7, sw: "t_hive" }],
        bv: [{ m: "2", pi: 1, pb: 4 }],
        tv: [{ m: "2", tg: "Combat" }],
      }),
    ]);
    const data = expand(raw);
    const [t] = data.nodes;
    applyView(data, 1, null);
    expect([t!.name, t!.icon, t!.swap, t!.perkBadge, t!.perkInherited, t!.tag]).toEqual([
      "Hive Name", 7, "t_hive", 4, true, "Combat",
    ]);
    applyView(data, 0, null);
    expect([t!.name, t!.perkBadge, t!.perkInherited, t!.tag]).toEqual(["Default", 3, false, "Special Project"]);
  });

  it("clears a tag a profile does not get", () => {
    const data = expand(dataset([node("t", 0, 0, { tg: "Event", tv: [{ m: "1" }] })]));
    applyView(data, 0, null);
    expect(data.nodes[0]!.tag).toBeUndefined();
  });

  it("draws edges only between visible cards", () => {
    const raw = dataset(
      [node("a", 0, 0), node("b", 0, 1, { hp: "1" }), node("c", 0, 2)],
      [[0, 1, 0], [1, 2, 0], [0, 2, 1]],
    );
    const data = expand(raw);
    applyView(data, 0, null);
    expect(data.view.edges).toEqual([[0, 2, 1]]);
    expect(data.nodes[2]!.incoming).toEqual([0]);
    applyView(data, null, new Set([0, 2]));
    expect(data.view.edges).toEqual([[0, 2, 1]]);
  });
});

describe("presets", () => {
  const raw = () =>
    dataset([node("a", 0, 0, { hp: "4" }), node("b", 0, 1, { hp: "c" })], [], {
      profiles: [
        { k: "regular", l: "Individualist", a: "regular", t: [], s: "arcade" },
        { k: "hive", l: "Hive Mind", a: "hive", t: [], s: "arcade" },
        { k: "regular", l: "Individualist", a: "regular", t: [], s: "vanilla" },
        { k: "hive", l: "Hive Mind", a: "hive", t: [], s: "vanilla" },
      ],
      presets: [
        { k: "arcade", l: "Arcade" },
        { k: "vanilla", l: "Vanilla" },
      ],
      defaultPreset: "arcade",
    });

  it("with every empire, hides a slot only where every empire of the preset loses it", () => {
    const data = expand(raw());
    expect(data.view.preset).toBe("arcade");
    expect(data.nodes.map((n) => n.hidden)).toEqual([false, false]);
    applyView(data, null, null, "vanilla");
    // `a` is lost by one vanilla empire only; `b` by both.
    expect(data.nodes.map((n) => n.hidden)).toEqual([false, true]);
    applyView(data, 2, null);
    expect(data.nodes.map((n) => n.hidden)).toEqual([true, true]);
  });
});
