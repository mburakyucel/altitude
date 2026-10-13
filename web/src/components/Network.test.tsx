import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

const network = { computer: "Atlas host", host: "127.0.0.1", port: 8890, url: "https://127.0.0.1:8890", scope: "local", listener_scope: "local", state: "running", packaged: true };
function service(value: unknown = network) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    const data = url === "/api/machine" ? { altitude_repository: "fixture/altitude", network: value }
      : url.includes("/api/overview") ? { projects: [], queue: [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [] }
        : url.includes("/api/voice") ? { backend: "browser", selection: "fixture", host: { state: "unavailable", reason: "fixture" } }
          : { devices: [] };
    return new Response(JSON.stringify(data), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
describe("Network", () => {
  it.each(["0.0.0.0", "::"])("keeps the parsed wildcard listener separate from its localhost certificate URL (%s)", async host => {
    service({ ...network, host, url: "https://localhost:8890", listener_scope: "all" });
    renderApp({ route: "/settings/network" });
    expect(await screen.findByText("All interfaces")).toBeVisible();
    expect(screen.getByRole("link", { name: "https://localhost:8890" })).toBeVisible();
    expect(screen.getByText(/The certificate URL uses localhost/)).toBeVisible();
    expect(screen.getByText(/Other-device reachability is unverified/)).toBeVisible();
    expect(screen.queryByText("Only the hosting computer")).toBeNull();
  });
  it("shows the actual host, URL and explicit preview/apply actions without making writes", async () => {
    const calls = service();
    const { user } = renderApp({ route: "/settings/network" });
    expect(await screen.findByText("Atlas host")).toBeVisible();
    expect(screen.getByRole("link", { name: network.url })).toHaveAttribute("href", network.url);
    expect(screen.getByText("Only the hosting computer")).toBeVisible();
    await user.click(screen.getByText("Use another device…"));
    expect(screen.getByText("alt service configure --host <private-address>")).toBeVisible();
    expect(screen.getByText("alt service configure --host <private-address> --apply")).toBeVisible();
    expect(screen.getByText("alt service configure --host 127.0.0.1 --apply")).toBeVisible();
    expect(screen.getByText(/A VPN changes neither Altitude’s address, certificate trust nor pairing/)).toBeVisible();
    expect(calls.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it("renders address failures explicitly and retries without guessing browser origin", async () => {
    const calls = service({ error: "Service record unavailable" });
    const { user } = renderApp({ route: "/settings/network" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not read service address");
    expect(screen.getByText("Service record unavailable")).toBeVisible();
    expect(screen.queryByRole("link", { name: /^https?:/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.mock.calls.filter(([url]) => url === "/api/machine")).toHaveLength(2));
  });

  it("uses deployment instructions for source installs without showing packaged apply", async () => {
    service({ ...network, packaged: false });
    const { user } = renderApp({ route: "/settings/network" });
    await screen.findByText("Atlas host");
    await user.click(screen.getByText("Use another device…"));
    expect(screen.getByText(/For a source deployment, configure/)).toBeVisible();
    expect(screen.queryByText("alt service configure --host <private-address> --apply")).toBeNull();
  });
});
