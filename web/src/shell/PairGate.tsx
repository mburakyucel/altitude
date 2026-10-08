import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { pairDevice, readAccess, UNPAIRED_EVENT } from "../data/api";
import type { Access } from "../data/api";
import { Command } from "../components/Onboarding";
import { BrandMark } from "./BrandMark";
import "./pairing.css";

/** A pairing link (`/pair?code=…`, printed by `alt pair`) fills in and submits its code once. It is read as the
 * page loads, before the router starts, and leaves the address bar and history at once. */
let linkedCode = "";
if (window.location.pathname === "/pair") {
  linkedCode = new URLSearchParams(window.location.search).get("code") ?? "";
  window.history.replaceState(window.history.state, "", "/");
}

/** Pair this device (SPEC.md §3.16): the one screen an unpaired browser gets. */
function PairScreen({ removed, onPaired }: { removed: boolean; onPaired: () => void }) {
  const [linked] = useState(linkedCode);
  const [code, setCode] = useState(linked);
  const [state, setState] = useState<{ status: "idle" | "pairing" } | { status: "failed"; error: string }>({ status: "idle" });
  const submit = async (value: string) => {
    setState({ status: "pairing" });
    try {
      await pairDevice(value);
      onPaired();
    } catch (error) {
      setState({ status: "failed", error: (error as Error).message });
    }
  };
  useEffect(() => {
    const once = linkedCode;
    linkedCode = "";
    if (once) void submit(once);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- a linked code is submitted once, on arrival
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(code);
  };
  return <main className="pair-screen">
    <div className="pair-brand"><BrandMark size={28} />Altitude</div>
    <form className="pair-card" onSubmit={onSubmit} aria-labelledby="pair-title">
      <h1 id="pair-title">Pair this device</h1>
      {removed ? <p role="status" className="pair-removed">This device is no longer paired. Pair it again to continue.</p> : null}
      <p className="pair-lead">Altitude opens only on devices you pair. On the computer running Altitude, in a terminal or over SSH, run:</p>
      <Command text="alt pair" />
      <p className="text-meta text-muted">For a container deployment, run this inside the container shell. Host tools and sign-ins do not configure the container.</p>
      <p className="pair-lead">Then type the code it shows. A device that is already paired can also make a code in Settings › Devices.</p>
      <label className="pair-field">Pairing code
        <input value={code} onChange={(event) => { setCode(event.target.value); if (state.status === "failed") setState({ status: "idle" }); }}
          placeholder="ABCD-2345" autoComplete="one-time-code" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
          maxLength={12} required disabled={state.status === "pairing"} autoFocus={!linked} />
      </label>
      {state.status === "failed" ? <p role="alert" className="text-meta text-danger">{state.error}</p> : null}
      <button type="submit" className="btn btn-primary" disabled={state.status === "pairing" || !code.trim()}>
        {state.status === "pairing" ? "Pairing…" : "Pair"}
      </button>
      <p className="text-meta text-muted">Each code works once, for 10 minutes. This device stays paired until you remove it in Settings.</p>
      <p className="text-meta text-muted">Did the browser warn about the certificate before showing this page? Pair only after it opens without a warning. For a container, export its public certificate with the host launcher's certificate command. For a native installation, use Set up a device in Settings › Devices on a trusted, paired browser, or run <code>alt tls-share</code> on the computer hosting Altitude. For localhost, run <code>alt doctor</code> there and import its public <code>ca_cert</code> file using your browser's certificate settings.</p>
    </form>
  </main>;
}

/** Shows the app to a paired browser and the pairing screen to any other. Any API reply of 401 means this
 * browser lost its pairing (removed in Settings, or its cookie expired), so it returns here at once. */
export default function PairGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const access = useQuery({ queryKey: ["access"], queryFn: readAccess, staleTime: Infinity, refetchOnWindowFocus: false, retry: 1 });
  const [removed, setRemoved] = useState(false);
  useEffect(() => {
    const unpaired = () => {
      if (client.getQueryData<Access>(["access"])?.paired) setRemoved(true);
      client.setQueryData<Access>(["access"], { paired: false, device: null });
    };
    window.addEventListener(UNPAIRED_EVENT, unpaired);
    return () => window.removeEventListener(UNPAIRED_EVENT, unpaired);
  }, [client]);
  if (access.isPending) return <main className="pair-screen" aria-busy="true"><div className="pair-brand"><BrandMark size={28} />Altitude</div></main>;
  if (access.isError) {
    return <main className="pair-screen">
      <div className="pair-brand"><BrandMark size={28} />Altitude</div>
      <p role="alert" className="pair-card">Could not reach Altitude.{" "}
        <button type="button" className="link" onClick={() => void access.refetch()}>Retry</button></p>
    </main>;
  }
  if (!access.data.paired) {
    return <PairScreen removed={removed} onPaired={() => {
      // Replies cached while unpaired were refusals; the app starts fresh as the paired device.
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "access" });
      setRemoved(false);
      void access.refetch();
    }} />;
  }
  return children;
}
