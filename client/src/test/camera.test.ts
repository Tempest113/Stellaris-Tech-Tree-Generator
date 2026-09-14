import { describe, expect, it } from "vitest";
import { fitArea } from "../camera";

describe("fitArea", () => {
  const tree = { width: 12000, height: 13000 };

  it("fits a tall tree beside a narrow dock on a landscape screen", () => {
    const area = fitArea({ width: 1920, height: 1080 }, tree, { header: 26, dock: { left: 12, top: 38, right: 332, bottom: 300 } });
    expect(area.left).toBeGreaterThan(332);
    expect(area.top).toBeLessThan(38);
  });

  it("fits below a dock that spans a phone's width", () => {
    const area = fitArea({ width: 375, height: 812 }, tree, { header: 26, dock: { left: 12, top: 38, right: 363, bottom: 250 } });
    expect(area.left).toBe(0);
    expect(area.top).toBeGreaterThan(250);
  });

  it("keeps clear of the panel", () => {
    const area = fitArea({ width: 1920, height: 1080 }, tree, { header: 26, dock: null }, 420, 0);
    expect(area.right).toBe(1500);
  });
});
