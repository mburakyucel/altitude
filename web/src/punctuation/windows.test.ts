import { describe, expect, it } from "vitest";
import { planWindows } from "./windows";

const LIMITS = { core: 48, context: 8, tokens: 126 };

function check(counts: number[]) {
  const windows = planWindows(counts, LIMITS);
  const cores = windows.flatMap((w) => Array.from({ length: w.coreEnd - w.coreStart }, (_, i) => w.coreStart + i));
  expect(cores).toEqual(counts.map((_, i) => i));
  for (const w of windows) {
    expect(w.coreStart - w.start).toBeLessThanOrEqual(LIMITS.context);
    expect(w.end - w.coreEnd).toBeLessThanOrEqual(LIMITS.context);
    expect(w.coreEnd - w.coreStart).toBeLessThanOrEqual(LIMITS.core);
    expect(counts.slice(w.start, w.end).reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(LIMITS.tokens);
  }
  return windows;
}

describe("planWindows", () => {
  it("returns nothing for no words and one window for a short text", () => {
    expect(planWindows([], LIMITS)).toEqual([]);
    expect(check([1, 2, 1])).toEqual([{ start: 0, coreStart: 0, coreEnd: 3, end: 3 }]);
  });

  it("slides 48-word cores with 8 words of context on each side", () => {
    expect(check(Array<number>(120).fill(1))).toEqual([
      { start: 0, coreStart: 0, coreEnd: 48, end: 56 },
      { start: 40, coreStart: 48, coreEnd: 96, end: 104 },
      { start: 88, coreStart: 96, coreEnd: 120, end: 120 },
    ]);
  });

  it("shrinks windows to stay inside the token budget", () => {
    const windows = check(Array.from({ length: 200 }, (_, i) => 1 + ((i * 7) % 31)));
    expect(windows.length).toBeGreaterThan(200 / 48);
  });
});
