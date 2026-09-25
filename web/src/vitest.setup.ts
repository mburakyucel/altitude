import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { presetVoiceBackend } from "./components/voiceBackend";

/** jsdom has no EventSource: the shell's change stream stays silent unless a test drives its own. */
class SilentEventSource extends EventTarget {
  static readonly CLOSED = 2;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {
    this.readyState = SilentEventSource.CLOSED;
  }
}

beforeEach(() => {
  vi.stubGlobal("EventSource", SilentEventSource);
  // Composers read the installation's voice backend once; tests name it instead (upload path by default).
  presetVoiceBackend("endpoint");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  // jsdom's default width is a desktop; a test that set a phone viewport gives it back.
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 1024 });
});
