import { afterEach, describe, expect, it, vi } from "vitest";
import { clockText, dateText, dayText, exactTime, stampText, timeTokens } from "./observed";

// Expectations come from the runner's own zone and locale; the walkthrough (local-time.pw.ts) pins a
// non-UTC zone and asserts the exact text.
const local = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const anchor = Date.parse("2026-10-11T01:55:00Z");
const found = (text: string, at = anchor) => timeTokens(text, at).map(({ text, at, dated, until }) => ({ text, at, dated, until }));

describe("written times", () => {
  it.each([
    ["since 2026-10-10T23:58:42+00:00.", "2026-10-10T23:58:42+00:00", "2026-10-10T23:58:42.000Z"],
    ["at 2026-10-10T23:58Z", "2026-10-10T23:58Z", "2026-10-10T23:58:00.000Z"],
    ["at 2026-10-10T16:58:42.123-07:00 then", "2026-10-10T16:58:42.123-07:00", "2026-10-10T23:58:42.000Z"],
    ["on 2026-10-10 23:58 UTC.", "2026-10-10 23:58 UTC", "2026-10-10T23:58:00.000Z"],
    ["on 2026-10-10T23:58:42Z UTC", "2026-10-10T23:58:42Z UTC", "2026-10-10T23:58:42.000Z"],
    ["September 24 19:17 UTC", "September 24 19:17 UTC", "2026-09-24T19:17:00.000Z"],
    ["Oct 10, 23:58Z", "Oct 10, 23:58Z", "2026-10-10T23:58:00.000Z"],
  ])("reads a dated time in %j", (text, token, at) => {
    expect(found(text)).toEqual([{ text: token, at, dated: true, until: undefined }]);
  });

  it.each([
    ["restart since 23:58Z.", "23:58Z", "2026-10-10T23:58:00.000Z"],
    ["probe at04:10:43Z", "04:10:43Z", "2026-10-11T04:10:43.000Z"],
    ["window 23:58 UTC", "23:58 UTC", "2026-10-10T23:58:00.000Z"],
    ["deadline 03:30 GMT", "03:30 GMT", "2026-10-11T03:30:00.000Z"],
    ["seen 08:13:36UTC", "08:13:36UTC", "2026-10-10T08:13:36.000Z"],
  ])("places a UTC clock in %j on its day", (text, token, at) => {
    expect(found(text)).toEqual([{ text: token, at, dated: false, until: undefined }]);
  });

  it("reads both ends of a UTC range", () => {
    expect(found("quiet 07:20–07:45 UTC")).toEqual([
      { text: "07:20–07:45 UTC", at: "2026-10-10T07:20:00.000Z", dated: false, until: "2026-10-10T07:45:00.000Z" },
    ]);
  });

  it("takes a clock's day from the text's own time, not today", () => {
    expect(found("restart since 23:58Z", Date.parse("2026-03-01T00:10:00Z"))[0]?.at).toBe("2026-02-28T23:58:00.000Z");
    expect(found("resets at 02:00Z", Date.parse("2026-03-01T00:10:00Z"))[0]?.at).toBe("2026-03-01T02:00:00.000Z");
  });

  it.each([
    "a bare 23:58 has no zone",
    "17:12 PDT is already named",
    "fingerprint 6D:C0:12:00:29Z",
    "host 10.0.0.1:8080Z",
    "file.py:12:34Z",
    "UTC+2 at 12:00 UTC+2",
    "2026-10-10 alone",
    "2026-10-10T23:58:42 without an offset",
    "at 25:10Z and 23:61 UTC",
    "zone 12:30 zones",
  ])("leaves %j alone", (text) => {
    expect(found(text)).toEqual([]);
  });
});

describe("one format family", () => {
  afterEach(() => vi.useRealTimers());

  it("shows a clock today, a date otherwise, a year when not this one", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 10, 18, 0));
    const today = new Date(2026, 9, 10, 16, 58).toISOString();
    const earlier = new Date(2026, 9, 9, 8, 14).toISOString();
    const older = new Date(2025, 11, 31, 8, 14).toISOString();
    expect(stampText(today)).toBe(local(today));
    expect(stampText(earlier)).toBe(`${new Date(earlier).toLocaleDateString([], { month: "short", day: "numeric" })}, ${local(earlier)}`);
    expect(stampText(older)).toContain("2025");
    expect(clockText(today)).toBe(local(today));
    expect(dayText(today)).toBe("Today");
    expect(dayText(earlier)).toBe("Yesterday");
    expect(dateText("2036-09-25T04:00:00+00:00")).toContain("2036");
  });

  it("never shows ISO, Z or an offset", () => {
    for (const text of [stampText("2026-10-10T23:58:42+00:00"), exactTime("2026-10-10T23:58:42+00:00"), clockText("2026-10-10T23:58:42Z")]) {
      expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}|T\d{2}:|\+00:00|\dZ\b/);
    }
  });

  it("is empty for a missing or unparseable time", () => {
    for (const value of [null, undefined, "", "soon"]) expect([stampText(value), clockText(value), dateText(value), dayText(value), exactTime(value)]).toEqual(["", "", "", "", ""]);
  });
});
