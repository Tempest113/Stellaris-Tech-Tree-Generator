import { afterEach, describe, expect, it, vi } from "vitest";
import { linkHash, readLink } from "../link";

function at(hash: string): void {
  vi.stubGlobal("window", { location: { hash } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("links", () => {
  it("round-trips the view", () => {
    const state = { empire: "hive-bio-ships", preset: "arcade", tech: "tech_titans", isolate: "tech_titans" };
    at(linkHash(state));
    expect(readLink()).toEqual(state);
  });

  it("leaves out what is not set", () => {
    expect(linkHash({ empire: "all", preset: null, tech: null, isolate: null })).toBe("#empire=all");
    expect(linkHash({ empire: null, preset: null, tech: null, isolate: null })).toBe("");
  });

  it("reads a bare or empty hash as saying nothing", () => {
    at("");
    expect(readLink()).toEqual({ empire: null, preset: null, tech: null, isolate: null });
    at("#tech=tech_lasers_1");
    expect(readLink()).toEqual({ empire: null, preset: null, tech: "tech_lasers_1", isolate: null });
  });
});
