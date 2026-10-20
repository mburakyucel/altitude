import { describe, expect, it } from "vitest";
import { clockText, stampText } from "./observed";
import { durationText, newsAgo, refusal, runCost, runTime, runWhen, tokensText, zoned } from "./projectAgents";

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

describe("project agent words", () => {
  it("writes budgets and limits in hours and minutes", () => {
    expect([30, 60, 70, 120, 600].map(durationText)).toEqual(["30 min", "1 h", "1 h 10 min", "2 h", "10 h"]);
    expect(durationText(0)).toBe("0 min");
  });

  it("adds the machine's zone after each schedule time only when the browser's offset differs", () => {
    const clock = { zone: "Istanbul", offset: 180 };
    expect(zoned("Weekdays at 07:00 and Fridays at 17:00", clock, 180)).toBe("Weekdays at 07:00 and Fridays at 17:00");
    expect(zoned("Weekdays at 07:00 and Fridays at 17:00", clock, 60)).toBe("Weekdays at 07:00 Istanbul and Fridays at 17:00 Istanbul");
    expect(zoned("Next: tomorrow at 07:00", clock, 0)).toBe("Next: tomorrow at 07:00 Istanbul");
    expect(zoned("Running, 2 of 20 min", clock, 0)).toBe("Running, 2 of 20 min");
    expect(zoned("Weekdays at 07:00", undefined, 0)).toBe("Weekdays at 07:00");
  });

  it("says how long ago news is with a space before the unit", () => {
    expect(newsAgo(hoursAgo(2))).toBe("2 h ago");
    expect(newsAgo(hoursAgo(50))).toBe("2 d ago");
  });

  it("names a run by its day, and a late run by when it ran", () => {
    const today = new Date(); today.setHours(7, 0, 0, 0);
    const yesterday = new Date(today.valueOf() - 86_400_000);
    const older = new Date(today.valueOf() - 6 * 86_400_000);
    expect(runTime(today.toISOString())).toBe(`Today ${clockText(today.toISOString())}`);
    expect(runTime(yesterday.toISOString())).toBe(`Yesterday ${clockText(yesterday.toISOString())}`);
    expect(runTime(older.toISOString())).toBe(stampText(older.toISOString()));
    const started = new Date(older.valueOf() + 134 * 60_000).toISOString();
    expect(runWhen({ at: older.toISOString(), started, late: true })).toBe(`${stampText(older.toISOString())}, ran at ${clockText(started)}`);
    expect(runWhen({ at: older.toISOString(), started, late: false })).toBe(stampText(older.toISOString()));
  });

  it("writes a run's minutes and tokens, empty before it used any", () => {
    expect([950, 820_000, 4_100_000].map(tokensText)).toEqual(["950", "820k", "4.1M"]);
    expect(runCost({ minutes: 6, tokens: 4_100_000 })).toBe("6 min · 4.1M tokens");
    expect(runCost({ minutes: null, tokens: null })).toBe("");
  });

  it("puts a refusal under its verb unless the server already did", () => {
    expect(refusal("Not saved", "The name is taken.")).toBe("Not saved: The name is taken.");
    expect(refusal("Not saved", "Not turned on: the agents changed in another window.")).toBe("Not turned on: the agents changed in another window.");
  });
});
