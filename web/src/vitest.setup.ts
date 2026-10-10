import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { HostCapture } from "./components/hostCapture";
import { forgetVisits } from "./components/visitMemory";
import { presetVoiceBackend } from "./components/voiceBackend";
import { punctuationFixture } from "./components/voiceTest";

// findBy and waitFor wait out a busy host as the test timeout does (vite.config.ts).
configure({ asyncUtilTimeout: 10_000 });

// The bundled punctuation model runs only in a real browser (web/e2e); unit tests use a fixture.
vi.mock("./punctuation", () => ({ loadPunctuator: () => punctuationFixture.load() }));

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
  // Composers read the installation's voice backend once; tests name it instead (host voice by default).
  presetVoiceBackend("host");
  punctuationFixture.reset();
});

afterEach(() => {
  cleanup();
  // Every memory router starts at the same entry key; what a test's pages kept must not reach the next test.
  forgetVisits();
  // A capture left sending must not hold one of the page's two slots for the next test.
  HostCapture.retained.forEach((capture) => capture.cancel());
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  // jsdom's default width is a desktop; a test that set a phone viewport gives it back.
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 1024 });
});
