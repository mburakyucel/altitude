import { afterEach, describe, expect, it, vi } from "vitest";
import { clockText, dateText, dayText, exactTime, localTimes, stampText, timeTokens } from "./observed";

// Expectations come from the runner's own zone and locale; the walkthrough (local-time.pw.ts) pins a
// non-UTC zone and asserts the exact text.
const local = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const hhmm = (iso: string) => new Date(iso).toTimeString().slice(0, 5);
const zoned = (iso: string) => new Date(iso).toLocaleTimeString([], { timeStyle: "long" });
const anchor = Date.parse("2026-10-11T01:55:00Z");
const found = (text: string, at = anchor) => timeTokens(text, at).map(({ text, at }) => ({ text, at }));
const shown = (text: string, at = anchor) => timeTokens(text, at).map((token) => token.shown);

describe("written times", () => {
  it.each([
    ["since 2026-10-10T23:58:42+00:00.", "2026-10-10T23:58:42+00:00", "2026-10-10T23:58:42.000Z"],
    ["at 2026-10-10T23:58Z", "2026-10-10T23:58Z", "2026-10-10T23:58:00.000Z"],
    ["at 2026-10-10T16:58:42.123-07:00 then", "2026-10-10T16:58:42.123-07:00", "2026-10-10T23:58:42.123Z"],
    ["on 2026-10-10 23:58 UTC.", "2026-10-10 23:58 UTC", "2026-10-10T23:58:00.000Z"],
    ["on 2026-10-10T23:58:42Z UTC", "2026-10-10T23:58:42Z UTC", "2026-10-10T23:58:42.000Z"],
    ["September 24 19:17 UTC", "September 24 19:17 UTC", "2026-09-24T19:17:00.000Z"],
    ["Oct 10, 23:58Z", "Oct 10, 23:58Z", "2026-10-10T23:58:00.000Z"],
  ])("reads a dated time in %j", (text, token, at) => {
    expect(found(text)).toEqual([{ text: token, at }]);
  });

  it.each([
    ["restart since 23:58Z.", "23:58Z", "2026-10-10T23:58:00Z"],
    ["probe at04:10:43Z", "04:10:43Z", "2026-10-11T04:10:43Z"],
    ["window 23:58 UTC", "23:58 UTC", "2026-10-10T23:58:00Z"],
    ["deadline 03:30 GMT", "03:30 GMT", "2026-10-11T03:30:00Z"],
    ["seen 08:13:36UTC", "08:13:36UTC", "2026-10-11T08:13:36Z"],
  ])("keeps a UTC clock in %j a clock", (text, token, instant) => {
    expect(found(text)).toEqual([{ text: token, at: hhmm(instant) }]);
    expect(shown(text)).toEqual([local(instant)]);
  });

  it("shows a dated time as a clock on the text's own local day and dated on another", () => {
    const sameDay = new Date(anchor - 30 * 60_000).toISOString();
    expect(shown(`since ${sameDay}`)).toEqual([local(sameDay)]);
    expect(shown("since 2026-10-08T12:00:00Z")).toEqual([`${new Date("2026-10-08T12:00:00Z").toLocaleDateString([], { month: "short", day: "numeric" })}, ${local("2026-10-08T12:00:00Z")}`]);
  });

  it("never invents a day for a clock written without one, past or future", () => {
    const morning = Date.parse("2026-10-10T10:00:00Z");
    expect(shown("resumes at 18:00Z", morning)).toEqual([local("2026-10-10T18:00:00Z")]);
    expect(shown("failed at 02:00Z", morning)).toEqual([local("2026-10-10T02:00:00Z")]);
    expect(timeTokens("resumes at 18:00Z", morning)[0]?.exact).toBe(zoned("2026-10-10T18:00:00Z"));
  });

  it("takes a clock's offset from the text's own time across daylight saving", () => {
    expect(shown("at 23:58Z", Date.parse("2026-01-15T23:00:00Z"))).toEqual([local("2026-01-15T23:58:00Z")]);
    expect(shown("at 23:58Z", Date.parse("2026-07-15T23:00:00Z"))).toEqual([local("2026-07-15T23:58:00Z")]);
  });

  it.each([
    ["quiet 07:20–07:45 UTC", "07:20–07:45 UTC", "–", "2026-10-11T07:20:00Z", "2026-10-11T07:45:00Z"],
    ["quiet 07:20/07:45 UTC", "07:20/07:45 UTC", "/", "2026-10-11T07:20:00Z", "2026-10-11T07:45:00Z"],
    ["overnight 23:30 - 00:30 UTC", "23:30 - 00:30 UTC", " - ", "2026-10-10T23:30:00Z", "2026-10-11T00:30:00Z"],
  ])("reads both ends of the range in %j, the end after the start", (text, token, separator, start, end) => {
    expect(found(text)).toEqual([{ text: token, at: hhmm(start) }]);
    expect(shown(text)).toEqual([`${local(start)}${separator}${local(end)}`]);
    expect(timeTokens(text, anchor)[0]?.exact).toBe(`${zoned(start)} – ${zoned(end)}`);
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
    "runs at 18:43, 19:05 UTC",
    "runs at 18:43,19:05 and 19:30 UTC",
    "runs at 18:43 or 19:05 UTC",
    "from 18:43 to 19:05 UTC",
    "runs at 18:43,  19:05 UTC",
    "runs at 18:43 ,19:05 UTC",
    "see logs/2026-10-10T23:58Z.txt",
    "see run-23:58Z.log",
    "C:\\logs\\2026-10-10T23:58Z",
  ])("leaves %j alone", (text) => {
    expect(found(text)).toEqual([]);
  });

  it("converts plain text for a line that shows a string", () => {
    expect(localTimes("resets 2026-10-11T01:25:00+00:00; window 07:20–07:45 UTC", anchor))
      .toBe(`resets ${local("2026-10-11T01:25:00Z")}; window ${local("2026-10-11T07:20:00Z")}–${local("2026-10-11T07:45:00Z")}`);
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
