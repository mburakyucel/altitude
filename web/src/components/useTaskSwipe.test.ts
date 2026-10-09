import { describe, expect, it } from "vitest";
import { completes, dragOffset, releaseVelocity } from "./useTaskSwipe";

const width = 390;

describe("swipe arithmetic", () => {
  it("follows the finger toward the other view and stops at one width", () => {
    expect(dragOffset(-80, width, 0, 2)).toBe(-80);
    expect(dragOffset(80, width, 1, 2)).toBe(80);
    expect(dragOffset(-600, width, 0, 2)).toBe(-width);
    expect(dragOffset(0, width, 0, 2)).toBe(0);
  });

  it("gives less and less past either end", () => {
    const short = dragOffset(60, width, 0, 2);
    const long = dragOffset(300, width, 0, 2);
    expect(short).toBeGreaterThan(0);
    expect(short).toBeLessThan(60);
    expect(long).toBeGreaterThan(short);
    expect(long).toBeLessThan(width * 0.55);
    expect(long - short).toBeLessThan(300 - 60);
    expect(dragOffset(-300, width, 1, 2)).toBeCloseTo(-long, 6);
  });

  it("completes a slow release from half the width and springs back before it", () => {
    expect(completes(-194, 0, width, 0, 2)).toBe(false);
    expect(completes(-195, 0, width, 0, 2)).toBe(true);
    expect(completes(195, 0, width, 1, 2)).toBe(true);
  });

  it("completes a fling in the drag's direction at any distance and springs back from a fling against it", () => {
    expect(completes(-30, -0.4, width, 0, 2)).toBe(true);
    expect(completes(-30, -0.39, width, 0, 2)).toBe(false);
    expect(completes(-300, 0.6, width, 0, 2)).toBe(false);
    expect(completes(60, 0.9, width, 1, 2)).toBe(true);
  });

  it("never completes past either end", () => {
    expect(completes(300, 2, width, 0, 2)).toBe(false);
    expect(completes(-300, -2, width, 1, 2)).toBe(false);
  });

  it("moves through three views in order, one step each way, and never wraps", () => {
    expect(dragOffset(-80, width, 0, 3)).toBe(-80);
    expect(dragOffset(-80, width, 1, 3)).toBe(-80);
    expect(dragOffset(80, width, 1, 3)).toBe(80);
    expect(dragOffset(80, width, 2, 3)).toBe(80);
    expect(dragOffset(-80, width, 2, 3)).toBeGreaterThan(-80);
    expect(dragOffset(-80, width, 2, 3)).toBeCloseTo(-dragOffset(80, width, 0, 3), 6);
    expect(completes(-195, 0, width, 0, 3)).toBe(true);
    expect(completes(-195, 0, width, 1, 3)).toBe(true);
    expect(completes(195, 0, width, 1, 3)).toBe(true);
    expect(completes(195, 0, width, 2, 3)).toBe(true);
    expect(completes(-300, -2, width, 2, 3)).toBe(false);
    expect(completes(300, 2, width, 0, 3)).toBe(false);
  });

  it("measures release speed over the last hundred milliseconds, the release included", () => {
    const samples = [{ x: 300, at: 0 }, { x: 260, at: 40 }, { x: 220, at: 80 }, { x: 180, at: 120 }, { x: 140, at: 160 }, { x: 130, at: 170 }];
    expect(releaseVelocity(samples)).toBeCloseTo(-90 / 90, 6);
  });

  it("measures a short flick over its whole length, from the touch to the release", () => {
    expect(releaseVelocity([{ x: 300, at: 0 }, { x: 220, at: 40 }])).toBeCloseTo(-2, 6);
    expect(releaseVelocity([{ x: 300, at: 0 }, { x: 260, at: 20 }, { x: 220, at: 40 }])).toBeCloseTo(-2, 6);
  });

  it("counts a pause before lifting as rest", () => {
    expect(releaseVelocity([{ x: 300, at: 0 }, { x: 100, at: 50 }, { x: 100, at: 200 }])).toBe(0);
    expect(releaseVelocity([{ x: 300, at: 0 }])).toBe(0);
    expect(releaseVelocity([])).toBe(0);
  });
});
