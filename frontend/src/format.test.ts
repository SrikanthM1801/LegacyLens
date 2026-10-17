import { compact, fmtDay, niceTicks, pct, plural, titleCase } from "./format";

describe("format", () => {
  it("compacts numbers like axis ticks and stat tiles", () => {
    expect(compact(0)).toBe("0");
    expect(compact(999)).toBe("999");
    expect(compact(1000)).toBe("1K");
    expect(compact(1249)).toBe("1.2K");
    expect(compact(12_900)).toBe("13K");
    expect(compact(4_200_000)).toBe("4.2M");
  });

  it("builds round ticks from zero that cover the max", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(130)).toEqual([0, 50, 100, 150]);
    expect(niceTicks(23)).toEqual([0, 10, 20, 30]);
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
    for (const max of [1, 3, 9, 41, 130, 999, 12_345]) {
      const t = niceTicks(max);
      expect(t[0]).toBe(0);
      expect(t[t.length - 1]).toBeGreaterThanOrEqual(max);
    }
  });

  it("formats small things", () => {
    expect(pct(0.739)).toBe("74%");
    expect(pct(null)).toBe("n/a");
    expect(plural(1, "question")).toBe("1 question");
    expect(plural(3, "question")).toBe("3 questions");
    expect(titleCase("no_answer")).toBe("No answer");
    expect(fmtDay("2026-10-17")).toMatch(/Oct\s17/);
  });
});
