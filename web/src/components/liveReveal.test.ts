import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveReveal } from "./liveReveal";

/* Live dictation flows in at a steady pace (SPEC.md §3.6); these walk the pace frame by frame. */

function reduceMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduce && query === "(prefers-reduced-motion: reduce)", media: query }));
}

const frames = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

describe("useLiveReveal", () => {
  beforeEach(() => {
    reduceMotion(false);
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "setTimeout"] });
  });
  afterEach(() => vi.useRealTimers());

  it("flows each answer in over about the time until the next, then holds the whole text", () => {
    const { result, rerender } = renderHook(({ text }) => useLiveReveal(text), { initialProps: { text: null as string | null } });
    expect(result.current).toBeNull();
    const first = "check the build and the tests";
    rerender({ text: first });
    expect(result.current).toBe("");
    const shown: string[] = [];
    for (let at = 0; at < 480; at += 60) { frames(60); shown.push(result.current!); }
    // Every frame shows a growing prefix of the words heard, never all of them at once.
    shown.forEach((text, index) => {
      expect(first.startsWith(text)).toBe(true);
      if (index) expect(text.length).toBeGreaterThan(shown[index - 1]!.length);
    });
    expect(shown[0]!.length).toBeLessThan(first.length / 4);
    frames(100);
    expect(result.current).toBe(first);
  });

  it("swaps a revised word in place without taking back what has flowed in, then continues", () => {
    const { result, rerender } = renderHook(({ text }) => useLiveReveal(text), { initialProps: { text: "fix the tets" as string | null } });
    frames(1000);
    expect(result.current).toBe("fix the tets");
    rerender({ text: "fix the tests, then ship it" });
    expect(result.current).toBe("fix the test");
    frames(60);
    expect(result.current!.length).toBeGreaterThan("fix the test".length);
    expect("fix the tests, then ship it".startsWith(result.current!)).toBe(true);
    frames(1000);
    expect(result.current).toBe("fix the tests, then ship it");
    rerender({ text: "fix the tests" });
    expect(result.current).toBe("fix the tests");
  });

  it("paces to the answers' own rhythm and never slower than a steady minimum", () => {
    const { result, rerender } = renderHook(({ text }) => useLiveReveal(text), { initialProps: { text: "a" as string | null } });
    frames(1000);
    // A one-letter backlog still arrives within a tenth of a second at the minimum pace.
    rerender({ text: "ab" });
    frames(90);
    expect(result.current).toBe("ab");
    // A long backlog (a replay catching up) arrives within the expected gap, not letter by letter at speech pace.
    const replay = "ab " + "word ".repeat(80).trim();
    rerender({ text: replay });
    frames(1300);
    expect(result.current).toBe(replay);
  });

  it("clears with the capture, and the next capture flows from its own start", () => {
    const { result, rerender } = renderHook(({ text }) => useLiveReveal(text), { initialProps: { text: "first words" as string | null } });
    frames(1000);
    rerender({ text: null });
    expect(result.current).toBeNull();
    rerender({ text: "second" });
    expect(result.current).toBe("");
    frames(1000);
    expect(result.current).toBe("second");
  });

  it("under reduced motion shows every answer whole at once", () => {
    reduceMotion(true);
    const { result, rerender } = renderHook(({ text }) => useLiveReveal(text), { initialProps: { text: "check the build" as string | null } });
    expect(result.current).toBe("check the build");
    rerender({ text: "check the build and the tests" });
    expect(result.current).toBe("check the build and the tests");
  });
});
