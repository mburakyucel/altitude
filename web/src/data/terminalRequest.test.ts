import { describe, expect, it } from "vitest";
import { terminalRequest } from "./terminalRequest";

// A review found `/api//terminal…` passed a prefix check while altd, which drops empty segments, served it.
describe("the dev proxy keeps terminal requests away from altd", () => {
  it("recognises every spelling altd routes to the terminal", () => {
    for (const url of ["/api/terminal/atlas/open", "/api//terminal/atlas/input", "//api/terminal-access",
      "/api/%74erminal/atlas/close", "/api/./terminal/atlas/stream?id=x", "/api/x/../terminal-access"]) {
      expect(terminalRequest(url), url).toBe(true);
    }
  });

  it("passes everything else through", () => {
    for (const url of ["/api/overview", "/api/task/atlas/terminal-notes", "/digest.wav", "/"]) {
      expect(terminalRequest(url), url).toBe(false);
    }
  });
});
