import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import PairGate from "./PairGate";
import type { Trust } from "../data/api";
import { takePairingLink } from "./pairingLink";
import type { PairingLink } from "./pairingLink";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const remote = { local: false, https: true, check: true, command: false, certificate: { name: "Altitude CA 4F7K" } };

type Answer = "trusted" | "untrusted" | "refused" | "retry" | "expired";

/** The pairing API as a remote browser sees it: each trust check takes the next answer, by default a refused
 * connection and then, as Altitude records a refusal, "untrusted"; /api/pair records its body. */
function service(trust: Record<string, unknown>, answers: Answer[] = []) {
  const calls = { challenges: 0, checks: [] as string[], pairs: [] as unknown[] };
  let paired = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/access") return json({ paired, device: paired ? "d1" : null, trust });
    if (path === "/api/trust" && init?.method === "POST") return json({ challenge: `c${++calls.challenges}` });
    if (path.startsWith("/api/trust/")) {
      calls.checks.push(path.slice("/api/trust/".length));
      expect(init?.cache).toBe("no-store");
      const challenge = path.slice("/api/trust/".length);
      const answer = answers.shift() ?? (calls.checks.filter((check) => check === challenge).length > 1 ? "untrusted" : "refused");
      if (answer === "trusted") return json({ trusted: true });
      if (answer === "untrusted") return json({ trusted: false });
      if (answer === "retry") return json({ retry: true });
      if (answer === "expired") return json({ error: "unknown" }, 404);
      throw new TypeError("Failed to fetch");
    }
    if (path === "/api/pair") {
      const body = JSON.parse(String(init?.body));
      calls.pairs.push(body);
      if (body.code !== "ABCD-2345") return json({ error: "That code is not right. 4 tries left." }, 403);
      paired = true;
      return json({ paired: true });
    }
    return json({}, 404);
  }));
  return calls;
}

function renderGate(pairingLink: PairingLink = null, strict = false) {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const gate = <QueryClientProvider client={client}><PairGate pairingLink={pairingLink ? { ...pairingLink } : null}><h1>The app</h1></PairGate></QueryClientProvider>;
  render(strict ? <StrictMode>{gate}</StrictMode> : gate);
  return user;
}

function agent(value: string) {
  const spy = vi.spyOn(navigator, "userAgent", "get").mockReturnValue(value);
  onTestFinished(() => spy.mockRestore());
}

const step = (name: RegExp) => screen.getByRole("heading", { name }).closest("li") as HTMLElement;

const installLink: PairingLink = { code: "ABCD-2345" };
const localHttps = { local: true, https: true, check: true, command: true, certificate: { name: "Altitude CA 4F7K" } };
const recovery = "Run ~/.local/bin/alt pair on the computer running Altitude for a new code.";
const transportRecovery = "Could not confirm pairing. Reopen the install link, or run ~/.local/bin/alt pair for a new code.";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** Keep the application's access/trust/pair API real while controlling only fixture HTTP replies. */
function linkService(trust: Trust = localHttps, answers: Answer[] = trust === localHttps ? ["trusted", "trusted"] : []) {
  service(trust, answers);
  const original = globalThis.fetch;
  const fixture = {
    paired: false,
    inaccessible: false,
    accessWait: null as Promise<void> | null,
    pairReply: null as (() => Promise<Response>) | null,
    accessReads: 0,
    pairs: [] as { code: string; standalone: boolean }[],
    requests: [] as { path: string; method: string; body?: BodyInit | null }[],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    fixture.requests.push({ path, method: init?.method ?? "GET", body: init?.body });
    if (path === "/api/access") {
      fixture.accessReads++;
      if (fixture.accessWait) await fixture.accessWait;
      if (fixture.inaccessible) throw new TypeError("Failed to fetch");
      return json({ paired: fixture.paired, device: fixture.paired ? "fictional-device" : null, trust });
    }
    if (path === "/api/pair") {
      fixture.pairs.push(JSON.parse(String(init?.body)));
      if (fixture.pairReply) return fixture.pairReply();
      fixture.paired = true;
      return json({ paired: true });
    }
    return original(input, init);
  }));
  return fixture;
}

describe("Install pairing link", () => {
  it("waits for access, submits once under StrictMode, and never displays or stores the code while pairing", async () => {
    const fixture = linkService();
    const access = deferred<void>();
    const pair = deferred<Response>();
    fixture.accessWait = access.promise;
    fixture.pairReply = () => pair.promise;
    const store = vi.spyOn(Storage.prototype, "setItem");
    onTestFinished(() => store.mockRestore());
    renderGate(installLink, true);
    expect(document.querySelector('main[aria-busy="true"]')).toBeInTheDocument();
    expect(fixture.pairs).toHaveLength(0);
    await act(async () => access.resolve());
    expect(await screen.findByText("Pairing…")).toBeVisible();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    expect(document.body.textContent).not.toContain("ABCD-2345");
    expect(document.querySelector('input[value="ABCD-2345"]')).toBeNull();
    expect(fixture.pairs).toEqual([{ code: "ABCD-2345", standalone: false }]);
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fixture.pairs).toHaveLength(1);
    fixture.paired = true;
    await act(async () => pair.resolve(json({ paired: true })));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
    fixture.paired = false;
    await act(async () => window.dispatchEvent(new Event("altitude:unpaired")));
    expect(await screen.findByLabelText("Pairing code")).toHaveValue("");
    expect(fixture.pairs).toHaveLength(1);
    expect(store).not.toHaveBeenCalled();
    for (const request of fixture.requests) {
      expect(request.path).not.toContain("2345");
      if (request.path !== "/api/pair") expect(String(request.body)).not.toContain("2345");
    }
  });

  it("keeps the cleaned link in page memory while access is unreachable and redeems after Retry", async () => {
    const fixture = linkService();
    fixture.inaccessible = true;
    const original = window.location.href;
    const originalState = window.history.state;
    window.history.replaceState(null, "", "/projects/demo#pair=ABCD-2345");
    onTestFinished(() => window.history.replaceState(originalState, "", original));
    const link = takePairingLink();
    expect(window.location.hash).toBe("");
    const user = renderGate(link);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach Altitude.");
    expect(fixture.pairs).toHaveLength(0);
    fixture.inaccessible = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
  });

  it.each<[string, Answer[], string]>([
    ["untrusted", ["untrusted", "trusted"] as Answer[], "Not trusted yet"],
    ["unknown", ["retry", "retry", "retry", "trusted"] as Answer[], "Couldn’t check."],
  ])("waits while remote trust is %s and submits once after Check again", async (_state, answers, message) => {
    const fixture = linkService(remote, answers);
    const user = renderGate(installLink, true);
    expect(await screen.findByText(message)).toBeVisible();
    expect(fixture.pairs).toHaveLength(0);
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    expect(document.body.textContent).not.toContain("ABCD-2345");
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
    const pairAt = fixture.requests.findIndex((request) => request.path === "/api/pair");
    const checks = fixture.requests.slice(0, pairAt).filter((request) => request.path.startsWith("/api/trust/"));
    expect(checks.length).toBeGreaterThan(0);
  });

  it("waits for an in-flight managed trust challenge before sending the capability", async () => {
    const fixture = linkService(remote, ["trusted"]);
    const original = globalThis.fetch;
    const trust = deferred<void>();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/trust/")) await trust.promise;
      return original(input, init);
    }));
    renderGate(installLink);
    expect(await screen.findByRole("status", { name: "Checking…" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(0);
    await act(async () => trust.resolve());
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
  });

  it("keeps the link pending until an external certificate is confirmed", async () => {
    const fixture = linkService({ ...remote, check: false, certificate: null });
    const user = renderGate(installLink);
    expect(await screen.findByText(/Altitude can’t check this automatically\./)).toBeVisible();
    expect(fixture.pairs).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
    expect(fixture.requests.some((request) => request.path.startsWith("/api/trust"))).toBe(false);
  });

  it("never redeems over remote HTTP", async () => {
    const fixture = linkService({ local: false, https: false, check: false, command: false, certificate: null });
    renderGate(installLink);
    expect(await screen.findByText("This Altitude serves plain HTTP. Pair on the computer running it.")).toBeVisible();
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fixture.pairs).toHaveLength(0);
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
  });

  it("does not automatically redeem on loopback HTTP but preserves manual pairing", async () => {
    const fixture = linkService({ ...localHttps, https: false });
    const user = renderGate(installLink);
    const field = await screen.findByLabelText("Pairing code");
    expect(field).toHaveValue("");
    expect(fixture.pairs).toHaveLength(0);
    await user.type(field, "EFGH-6789");
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toEqual([{ code: "EFGH-6789", standalone: false }]);
  });

  it("discards a link when already paired and cannot reuse it after pairing is removed", async () => {
    const fixture = linkService();
    fixture.paired = true;
    renderGate(installLink, true);
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(0);
    fixture.paired = false;
    await act(async () => window.dispatchEvent(new Event("altitude:unpaired")));
    expect(await screen.findByLabelText("Pairing code")).toHaveValue("");
    expect(screen.getByText("This device is no longer paired. Pair it again to continue.")).toBeVisible();
    expect(fixture.pairs).toHaveLength(0);
  });

  it("reports a malformed link without burning an attempt and typing clears its error", async () => {
    const fixture = linkService();
    const user = renderGate({ invalid: true });
    expect(await screen.findByRole("alert")).toHaveTextContent(`This pairing link is not valid. ${recovery}`);
    const field = screen.getByLabelText("Pairing code");
    expect(field).toHaveValue("");
    expect(fixture.pairs).toHaveLength(0);
    await user.type(field, "E");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    [410, "This code has expired or was already used. Make a new one.", "This pairing link has expired or was already used."],
    [403, "That code is not right. 4 tries left.", "That code is not right. 4 tries left."],
    [403, "Pairing is denied.", "Pairing is denied."],
  ])("discards a refused link (%s), leaves a blank manual field and never retries", async (status, error, shown) => {
    const fixture = linkService();
    fixture.pairReply = async () => json({ error }, status);
    const user = renderGate(installLink, true);
    expect(await screen.findByRole("alert")).toHaveTextContent(`${shown} ${recovery}`);
    const field = screen.getByLabelText("Pairing code");
    expect(field).toHaveValue("");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fixture.pairs).toHaveLength(1);
    fixture.pairReply = null;
    await user.type(field, "EFGH-6789");
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs.map((pair) => pair.code)).toEqual(["ABCD-2345", "EFGH-6789"]);
  });

  it("observes a successful pairing cookie after a lost response and opens the app without retrying", async () => {
    const fixture = linkService();
    fixture.pairReply = async () => {
      fixture.paired = true;
      throw new TypeError("Failed to fetch");
    };
    renderGate(installLink, true);
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(fixture.pairs).toHaveLength(1);
    expect(fixture.accessReads).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([false, true])("rechecks access before recovery after transport failure (access unavailable: %s)", async (accessUnavailable) => {
    const fixture = linkService();
    const access = deferred<void>();
    fixture.pairReply = async () => {
      fixture.accessWait = access.promise;
      fixture.inaccessible = accessUnavailable;
      throw new TypeError("Failed to fetch");
    };
    const user = renderGate(installLink);
    await waitFor(() => expect(fixture.accessReads).toBe(2));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    await act(async () => access.resolve());
    expect(await screen.findByRole("alert")).toHaveTextContent(transportRecovery);
    const field = screen.getByLabelText("Pairing code");
    expect(field).toHaveValue("");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fixture.pairs).toHaveLength(1);
    await user.type(field, "E");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disposes a pending page link and does not restore it from the cleaned URL", async () => {
    const fixture = linkService(remote, ["untrusted", "trusted"]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const original = window.location.href;
    const originalState = window.history.state;
    window.history.replaceState(null, "", "/#pair=ABCD-2345");
    onTestFinished(() => window.history.replaceState(originalState, "", original));
    const view = render(<QueryClientProvider client={client}><PairGate pairingLink={takePairingLink()}><h1>The app</h1></PairGate></QueryClientProvider>);
    expect(await screen.findByText("Not trusted yet")).toBeVisible();
    view.unmount();
    renderGate(takePairingLink());
    expect(await screen.findByLabelText("Pairing code")).toHaveValue("");
    expect(fixture.pairs).toHaveLength(0);
  });

  it("never sends a disposed link when an outstanding trust check finishes", async () => {
    const fixture = linkService(remote, ["trusted"]);
    const original = globalThis.fetch;
    const trust = deferred<void>();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/trust/")) await trust.promise;
      return original(input, init);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><PairGate pairingLink={{ ...installLink }}><h1>The app</h1></PairGate></QueryClientProvider>);
    expect(await screen.findByRole("status", { name: "Checking…" })).toBeVisible();
    view.unmount();
    await act(async () => trust.resolve());
    expect(fixture.pairs).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "The app" })).toBeNull();
  });
});

describe("Pair this device", () => {
  it("probes HTTPS even on the host and keeps code entry absent until the probe succeeds", async () => {
    const calls = service({ ...remote, local: true, guide: "https://example.test/v1/docs/SETUP.md" }, ["untrusted", "trusted"]);
    const user = renderGate();
    expect(await screen.findByText("Not trusted yet")).toBeVisible();
    expect(calls.challenges).toBe(1);
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    expect(screen.getByText(/If they differ, delete the download and stop/)).toBeVisible();
    expect(screen.getByText("full SHA-256")).toBeVisible();
    expect(screen.getByRole("link", { name: "Certificate guide" })).toHaveAttribute("href", "https://example.test/v1/docs/SETUP.md");
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByLabelText("Pairing code")).toBeVisible();
    expect(calls.pairs).toEqual([]);
  });
  it("on the computer running Altitude, needs no certificate step and pairs with a code", async () => {
    const calls = service({ local: true, https: false, check: false, command: false, certificate: null });
    const user = renderGate();
    expect(await screen.findByRole("button", { name: "Pair" })).toBeVisible();
    expect(screen.queryByText("This is the computer running Altitude.")).toBeNull();
    expect(screen.queryByRole("heading", { name: /^Trust Altitude’s certificate/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Download/ })).toBeNull();
    const field = screen.getByLabelText("Pairing code");
    await user.type(field, "2222-2222");
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not right. 4 tries left.");
    await user.clear(field);
    expect(screen.queryByRole("alert")).toBeNull();
    await user.type(field, "ABCD-2345");
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(calls.challenges).toBe(0);
    expect(calls.pairs.at(-1)).toEqual({ code: "ABCD-2345", standalone: false });
  });

  it("on an iPhone, explains the profile, rechecks when the page returns and pairs with the trusted challenge", async () => {
    agent(IPHONE);
    const calls = service(remote, ["untrusted", "trusted"]);
    const user = renderGate();
    await screen.findByText("Not trusted yet");
    expect(screen.queryByText("You opened Altitude’s HTTPS address.")).toBeNull();
    expect(await screen.findByText("The usual missing step is the switch in Certificate Trust Settings.")).toBeVisible();
    const trust = step(/^Trust Altitude’s certificate/);
    expect(within(trust).getByText("Not trusted yet")).toBeVisible();
    expect(within(trust).getByRole("link", { name: "Download the profile" })).toHaveAttribute("href", "/api/certificate/altitude.mobileconfig");
    expect(within(trust).getByText(/Settings › General › About › Certificate Trust Settings › “Altitude CA 4F7K”/)).toBeVisible();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    // Coming back from Settings checks again by itself.
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(await within(trust).findByRole("status", { name: "Trusted" })).toBeVisible();
    expect(within(trust).queryByRole("link", { name: "Download the profile" })).toBeNull();
    await user.type(screen.getByLabelText("Pairing code"), "ABCD-2345");
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(calls.pairs).toEqual([{ code: "ABCD-2345", standalone: false }]);
  });

  it("shows Checking… while a check runs and Check again finishes it", async () => {
    agent("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36");
    // The refused second certificate rejects the fetch; the next attempt reads the refusal Altitude recorded.
    const calls = service(remote, ["refused", "untrusted", "trusted"]);
    const user = renderGate();
    expect(await screen.findByText("Install the certificate as a CA certificate.")).toBeVisible();
    expect(calls.checks).toEqual(["c1", "c1"]);
    const trust = step(/^Trust Altitude’s certificate/);
    expect(within(trust).getByRole("link", { name: "Download the certificate" })).toHaveAttribute("href", "/api/certificate/altitude.crt");
    let release = () => {};
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/trust/")) await new Promise<void>((resolve) => { release = resolve; });
      return original(input, init);
    }));
    await user.click(within(trust).getByRole("button", { name: "Check again" }));
    expect(await within(trust).findByRole("status", { name: "Checking…" })).toBeVisible();
    expect(within(trust).getByRole("button", { name: "Check again" })).toBeDisabled();
    release();
    expect(await within(trust).findByRole("status", { name: "Trusted" })).toBeVisible();
    expect(screen.getByLabelText("Pairing code")).toBeVisible();
  });

  it("tries again on an ordinary connection, starts over on an expired challenge, and gives up after three", async () => {
    const calls = service(remote, ["retry", "expired", "trusted"]);
    renderGate();
    expect(await within(await waitFor(() => step(/^Trust Altitude’s certificate/))).findByRole("status", { name: "Trusted" })).toBeVisible();
    expect(calls.checks).toEqual(["c1", "c1", "c2"]);
  });

  it("says it couldn't check, never that the device is untrusted, when the answers run out or every request fails", async () => {
    const calls = service(remote, ["retry", "retry", "retry", "refused", "refused", "refused"]);
    const user = renderGate();
    expect(await screen.findByText("Couldn’t check.")).toBeVisible();
    expect(screen.queryByText(/Not trusted yet\./)).toBeNull();
    expect(calls.checks).toHaveLength(3);
    // Failed requests alone, a network interruption or a refusal Altitude never heard about, never mean untrusted.
    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(calls.checks).toHaveLength(6));
    expect(await screen.findByText("Couldn’t check.")).toBeVisible();
    expect(screen.queryByText(/Not trusted yet\./)).toBeNull();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
  });

  it("on another desktop, shows the steps for the system and browser chosen and links the setup guide", async () => {
    service(remote);
    const user = renderGate();
    expect(await screen.findByText("Import it as a trusted authority in this browser or system.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Certificate guide" })).toHaveAttribute("href", expect.stringContaining("docs/SETUP.md#trust-https-on-each-device"));
    expect(screen.queryByText("~/.local/bin/alt trust")).toBeNull();
    const chooser = screen.getByLabelText("Steps for");
    await user.selectOptions(chooser, "mac");
    expect(screen.getByText(/In Keychain Access, open “Altitude CA 4F7K”, expand Trust/)).toBeVisible();
    await user.selectOptions(chooser, "linux");
    expect(screen.getByText("chrome://certificate-manager")).toBeVisible();
    expect(screen.getByText(/the certificate viewer shows its fingerprints/)).toBeVisible();
    await user.selectOptions(chooser, "linux-firefox");
    expect(screen.getByText("Tick Trust this CA to identify websites, then OK.")).toBeVisible();
    expect(screen.queryByText("chrome://certificate-manager")).toBeNull();
  });

  it("on the hosting computer, offers alt trust first and keeps the steps by hand behind a disclosure", async () => {
    service(localHttps, ["untrusted"]);
    const user = renderGate();
    expect(await screen.findByText("~/.local/bin/alt trust")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Download the certificate" })).not.toBeVisible();
    await user.click(screen.getByText("Trust it by hand instead"));
    expect(screen.getByRole("link", { name: "Download the certificate" })).toBeVisible();
    expect(screen.getByLabelText("Steps for")).toBeVisible();
  });

  it("over plain HTTP elsewhere, says to pair on the computer running Altitude and hides the other steps", async () => {
    service({ local: false, https: false, check: false, command: false, certificate: null });
    renderGate();
    expect(await screen.findByText("This Altitude serves plain HTTP. Pair on the computer running it.")).toBeVisible();
    expect(screen.queryByRole("heading", { name: /^Trust Altitude’s certificate/ })).toBeNull();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
  });

  it("with an externally supplied certificate, asks for a Private tab check and pairs without a challenge", async () => {
    const calls = service({ ...remote, check: false, certificate: null });
    const user = renderGate();
    expect(await screen.findByText(/Altitude can’t check this automatically\./)).toBeVisible();
    expect(screen.queryByLabelText("Pairing code")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(within(step(/^Trust Altitude’s certificate/)).getByRole("status", { name: "Trusted" })).toBeVisible();
    await user.type(screen.getByLabelText("Pairing code"), "ABCD-2345");
    await user.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByRole("heading", { name: "The app" })).toBeVisible();
    expect(calls.challenges).toBe(0);
    expect(calls.pairs).toEqual([{ code: "ABCD-2345", standalone: false }]);
  });
});
