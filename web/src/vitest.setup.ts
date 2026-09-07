import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { setStarting } from "./shell/starting";

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  setStarting(null);
  // jsdom's default width is a desktop; a test that set a phone viewport gives it back.
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 1024 });
});
