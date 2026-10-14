import { Link } from "react-router";
import { useMachine } from "../data/api";
import type { Machine } from "../data/api";
import { Command } from "./Onboarding";
import { StatusMark } from "./StatusMark";

export function networkSummary(network: Machine["network"]) {
  return network && !("error" in network) ? `${network.url} · ${network.listener_scope === "all" ? "All interfaces" : network.listener_scope === "local" ? "This computer only" : network.listener_scope === "private" ? "Private listener" : "Network listener"}` : "Could not read service address";
}

/** Serving metadata comes from the hosting daemon; a browser origin is never a service reading. */
export function HostingCard({ detail = false }: { detail?: boolean }) {
  const machine = useMachine();
  const network = machine.data?.network;
  if (machine.isPending) return <div className="settings-card"><StatusMark label="Reading service address…" /></div>;
  if (machine.isError || !network || "error" in network) return <div className="settings-card" role="alert">
    <p>Could not read service address.</p>
    {network && "error" in network ? <p className="text-meta text-muted">{network.error}</p> : null}
    <button type="button" className="btn" onClick={() => void machine.refetch()}>Retry</button>
  </div>;
  return <>
    <section className="settings-card hosting-card" aria-label="Where Altitude runs">
      <h2>Where Altitude runs</h2>
      <dl className="settings-network">
        <dt>Hosting</dt><dd>{network.computer}<span className="block text-meta text-muted">{machine.data?.deployment === "container" ? "Altitude runs inside a Linux container. The host computer’s name is not available here." : "The computer running Altitude"}</span></dd>
        <dt>Open</dt><dd><a className="link" href={network.url}>{network.url}</a></dd>
        <dt>Listening</dt><dd>{network.listener_scope === "all" ? "All interfaces" : network.listener_scope === "local" ? "Only the hosting computer" : network.listener_scope === "private" ? "Selected private address" : "Selected network address"}</dd>
      </dl>
      {network.listener_scope === "all" && network.scope === "local" ? <p className="text-meta text-muted">The certificate URL uses localhost, which opens on the hosting computer. Listening on all interfaces does not make that URL usable on another device.</p> : null}
      {machine.data?.deployment === "container" ? <p className="text-meta text-muted">The host launcher controls port publishing. Reachability of the configured URL from another device is unverified.</p> : network.listener_scope !== "local" ? <p className="text-meta text-muted">Other-device reachability is unverified; the selected address and port must be reachable.</p> : !detail ? <p className="text-meta text-muted">For this computer, no VPN is needed.</p> : null}
      {!detail ? <Link className="link" to="/settings/network">Use another device…</Link> : null}
    </section>
    {detail ? <NetworkGuidance packaged={network.packaged} container={machine.data?.deployment === "container"} local={network.listener_scope === "local"} /> : null}
  </>;
}

function NetworkGuidance({ packaged, container, local }: { packaged: boolean; container: boolean; local: boolean }) {
  return <div className="network-guidance">
    <section className="settings-card"><h2>Another device</h2><p>{local
      ? "Altitude listens only on the hosting computer, so other devices can’t reach it yet. Change its address under Use another device… below."
      : "Open the URL above on the other device, then trust Altitude’s certificate and pair it there."} On a phone, localhost means the phone.</p></section>
    <section className="settings-card"><h2>Away from home</h2><p>Connect your devices through a private VPN, such as WireGuard, and set Altitude’s address to the hosting computer’s VPN address (Use another device… below). The hosting computer must be available.</p>
      <p className="text-meta text-muted">A VPN changes neither Altitude’s address, certificate trust nor pairing.</p>
      <a className="link" href="https://www.wireguard.com/quickstart/" target="_blank" rel="noopener noreferrer">WireGuard documentation</a>
    </section>
    <details className="settings-card network-instructions"><summary className="link">Use another device…</summary>
      {packaged ? <>
        <ol className="pair-instructions">
          <li>Arrange a private connection, then choose a private address assigned to the hosting computer. Replace <code>&lt;private-address&gt;</code> below with that address.</li>
          <li>In a terminal on the hosting computer, preview the change. Preview changes nothing.<Command text="alt service configure --host <private-address>" /></li>
          <li>Review the current and proposed URLs, retained CA fingerprint, certificate action and recovery command. To apply, run the action printed by the preview:<Command text="alt service configure --host <private-address> --apply" /></li>
          <li>Open the verified URL printed after applying. Trust the matching certificate and pair the other browser.</li>
        </ol>
        <p>Changing the address replaces the old URL and retains the configured port. A health check on the host does not prove another device can reach it.</p>
        <p>To return to access only on the hosting computer, preview and then apply:</p>
        <Command text="alt service configure --host 127.0.0.1" />
        <Command text="alt service configure --host 127.0.0.1 --apply" />
      </> : <p>{container ? "For a container deployment, use its launcher’s documented private-address configuration. Host folders and tools are not shared with the container." : "For a source deployment, configure the service address through the source deployment instructions on the hosting computer."} <a className="link" href="https://github.com/mburakyucel/altitude/blob/main/docs/SETUP.md">Deployment setup guide</a></p>}
      <p className="text-meta text-muted">If the host’s address changes or its VPN goes away, the URL may stop working. Use the hosting terminal to restore local-only access.</p>
    </details>
  </div>;
}
