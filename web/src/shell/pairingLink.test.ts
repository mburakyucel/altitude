import { describe, expect, it, onTestFinished, vi } from "vitest";
import { takePairingLink } from "./pairingLink";

function visit(path: string) {
  const original = window.location.href;
  const state = window.history.state;
  window.history.replaceState({ page: "fictional" }, "", path);
  onTestFinished(() => window.history.replaceState(state, "", original));
}

describe("takePairingLink", () => {
  it.each(["ABCD-2345", "abcd-2345", "ABCD2345", "abcd2345", "ABCD%2D2345"])("takes a valid fictional code %s once and cleans the same history entry", (token) => {
    visit(`/projects/demo?view=tasks#pair=${token}`);
    const entries = window.history.length;
    const replace = vi.spyOn(window.history, "replaceState");
    onTestFinished(() => replace.mockRestore());
    const link = takePairingLink();
    expect(link && "code" in link ? link.code.replace("-", "") : link).toBe("ABCD2345");
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/projects/demo?view=tasks");
    expect(window.history.length).toBe(entries);
    expect(replace).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(window.history.state)).not.toContain("2345");
    expect(takePairingLink()).toBeNull();
  });

  it.each(["", "short", "ABCD-23456", "ABCD--2345", "ABCD_2345", "ABCD-23I5", "ABCD-23O5", "ABCD-2305", "ABCD-2315", "%E0%A4%A", "ABCD-2345&extra=1", "ABCD-2345#extra", "%20ABCD-2345"])("strips malformed pairing token %s without retaining it", (token) => {
    visit(`/projects/demo#pair=${token}`);
    expect(takePairingLink()).toEqual({ invalid: true });
    expect(window.location.hash).toBe("");
    expect(takePairingLink()).toBeNull();
  });

  it.each(["/projects/demo#details", "/projects/demo#pair", "/projects/demo#Pair=ABCD-2345", "/projects/demo#section&pair=ABCD-2345", "/projects/demo?pair=ABCD-2345#details", "/projects/demo"])("preserves ordinary URL %s", (path) => {
    visit(path);
    const replace = vi.spyOn(window.history, "replaceState");
    onTestFinished(() => replace.mockRestore());
    expect(takePairingLink()).toBeNull();
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(path);
    expect(replace).not.toHaveBeenCalled();
  });

  it("never writes the capability to browser storage, history state, or a request", () => {
    visit("/projects/demo?view=tasks#pair=ABCD-2345");
    const store = vi.spyOn(Storage.prototype, "setItem");
    const replace = vi.spyOn(window.history, "replaceState");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    onTestFinished(() => { store.mockRestore(); replace.mockRestore(); });
    expect(takePairingLink()).toEqual({ code: "ABCD-2345" });
    expect(store).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(JSON.stringify(replace.mock.calls)).not.toContain("ABCD-2345");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
