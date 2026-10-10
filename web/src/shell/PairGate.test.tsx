import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import PairGate from "./PairGate";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const remote = { local: false, https: true, check: true, certificate: { name: "Altitude CA 4F7K" } };

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

function renderGate() {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PairGate><h1>The app</h1></PairGate></QueryClientProvider>);
  return user;
}

function agent(value: string) {
  const spy = vi.spyOn(navigator, "userAgent", "get").mockReturnValue(value);
  onTestFinished(() => spy.mockRestore());
}

const step = (name: RegExp) => screen.getByRole("heading", { name }).closest("li") as HTMLElement;

describe("Pair this device", () => {
  it("on the computer running Altitude, needs no certificate step and pairs with a code", async () => {
    const calls = service({ local: true, https: false, check: false, certificate: null });
    const user = renderGate();
    expect(await screen.findByRole("button", { name: "Pair" })).toBeVisible();
    expect(screen.queryByText("This is the computer running Altitude.")).toBeNull();
    expect(within(step(/^Trust Altitude’s certificate/)).getByRole("status", { name: "Trusted" })).toBeVisible();
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
    expect(await screen.findByText("Not trusted yet. The usual missing step is the switch in Certificate Trust Settings.")).toBeVisible();
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
    expect(await screen.findByText("Not trusted yet. Install the certificate as a CA certificate.")).toBeVisible();
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

  it("on a desktop browser, explains importing the certificate and links the setup guide", async () => {
    service(remote);
    renderGate();
    expect(await screen.findByText("Not trusted yet. Import the certificate as a trusted authority, then restart the browser.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Setup guide" })).toHaveAttribute("href", expect.stringContaining("docs/SETUP.md#trust-https-on-each-device"));
    expect(screen.getByText(/Keychain Access › login/)).toBeVisible();
  });

  it("over plain HTTP elsewhere, says to pair on the computer running Altitude and hides the other steps", async () => {
    service({ local: false, https: false, check: false, certificate: null });
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
