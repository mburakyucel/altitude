import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { StatusMark, BusyLabel } from "../components/StatusMark";
import { checkTrust, pairDevice, readAccess, UNPAIRED_EVENT } from "../data/api";
import type { Access, Trust } from "../data/api";
import { forgetVisits } from "../components/visitMemory";
import { BrandMark } from "./BrandMark";
import "./pairing.css";

const SETUP_GUIDE = "https://github.com/mburakyucel/altitude/blob/main/docs/SETUP.md#trust-https-on-each-device";

type Mark = "done" | "pending" | "checking" | "problem";
const marks: Record<Mark, string> = { done: "✓", pending: "", checking: "", problem: "!" };
const spoken: Record<Mark, string> = { done: "done", pending: "to do", checking: "checking", problem: "needs attention" };

/** iPhone and iPad (iPadOS reports a Mac with touch), Android, or any other browser. */
type Kind = "apple" | "android" | "desktop";
function deviceKind(): Kind {
  const agent = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1)) return "apple";
  return /Android/.test(agent) ? "android" : "desktop";
}

/** A code's characters by the server's rule: letters and digits only, uppercased. A mistyped O or 0 stays, so the
 * server can say the code is not right. */
const codeChars = (text: string) => text.toUpperCase().replace(/[^\p{L}\p{N}]/gu, "");

/** The code as the field shows it: at most eight characters, with the dash after the fourth. */
function formatCode(text: string): string {
  const chars = codeChars(text).slice(0, 8);
  return chars.length < 4 ? chars : `${chars.slice(0, 4)}-${chars.slice(4)}`;
}

const untrusted: Record<Kind, string> = {
  apple: "Not trusted yet. The usual missing step is the switch in Certificate Trust Settings.",
  android: "Not trusted yet. Install the certificate as a CA certificate.",
  desktop: "Not trusted yet. Import the certificate as a trusted authority, then restart the browser.",
};

function Step({ mark, title, aside, children }: { mark: Mark; title: string; aside?: ReactNode; children?: ReactNode }) {
  return <li className="pair-step">
    <span className="pair-mark" data-mark={mark} aria-hidden>{mark === "checking" ? <span className="spinner" /> : marks[mark]}</span>
    <div className="pair-step-body">
      <div className="pair-step-head"><h2>{title}<span className="sr-only"> — {spoken[mark]}</span></h2>{aside}</div>
      {children}
    </div>
  </li>;
}

/** Where to download the certificate and how this kind of device installs and trusts it. The SHA-256 to compare
 * comes from the computer running Altitude, never from this page, which a forged one could imitate. */
function Instructions({ kind, name }: { kind: Kind; name: string }) {
  if (kind === "apple") {
    return <ol className="pair-instructions">
      <li><a className="btn btn-primary" href="/api/certificate/altitude.mobileconfig">Download the profile</a></li>
      <li>Open Settings › Profile Downloaded. It must list one Certificate, “{name}”. Tap More Details: the SHA-256 must end
        with the 8 pairs <code>alt pair</code> shows on your computer. Anything else: tap Remove and stop. Otherwise tap Install.</li>
      <li>Turn it on: Settings › General › About › Certificate Trust Settings › “{name}”.</li>
      <li>Come back here; this checks itself.</li>
    </ol>;
  }
  if (kind === "android") {
    return <ol className="pair-instructions">
      <li><a className="btn btn-primary" href="/api/certificate/altitude.crt">Download the certificate</a></li>
      <li>Install it: Settings › Security › Encryption &amp; credentials › Install a certificate › CA certificate (names vary).
        Its name must be “{name}” and its SHA-256 must end with the 8 pairs <code>alt pair</code> shows on your computer; otherwise stop.</li>
      <li>Come back here; this checks itself.</li>
    </ol>;
  }
  return <ol className="pair-instructions">
    <li><a className="btn btn-primary" href="/api/certificate/altitude.crt">Download the certificate</a></li>
    <li>Check its name is “{name}” and its SHA-256 ends with the 8 pairs <code>alt pair</code> shows. Import it as a trusted
      authority in this browser or system (macOS: Keychain Access › login, set Secure Sockets Layer to Always Trust), then
      restart the browser and reopen this page.</li>
    <li><a className="link" href={SETUP_GUIDE} target="_blank" rel="noopener noreferrer">Setup guide</a></li>
  </ol>;
}

type TrustState = { status: "checking" | "untrusted" | "unknown" | "manual" | "trusted" };

/** Trust Altitude's certificate: checked as the page opens, whenever it becomes visible again, and on Check again.
 * The computer running Altitude is trusted as it is; an externally supplied certificate is confirmed by hand. */
function useTrust(trust: Trust): [TrustState, () => void, () => void] {
  const automatic = trust.https && !trust.local && trust.check && !!trust.certificate;
  const [state, setState] = useState<TrustState>(() => trust.local ? { status: "trusted" } : automatic ? { status: "checking" } : { status: "manual" });
  const live = useRef({ mounted: true, running: false, trusted: false });
  const check = useCallback(async () => {
    const current = live.current;
    if (!automatic || current.running || current.trusted) return;
    current.running = true;
    setState({ status: "checking" });
    const answer = await checkTrust().catch(() => "unknown" as const);
    current.running = false;
    if (!current.mounted) return;
    current.trusted = answer === "trusted";
    setState({ status: answer });
  }, [automatic]);
  useEffect(() => {
    const current = live.current;
    current.mounted = true;
    void check();
    const visible = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      current.mounted = false;
      document.removeEventListener("visibilitychange", visible);
    };
  }, [check]);
  return [state, () => void check(), () => setState({ status: "trusted" })];
}

const pill: Record<TrustState["status"], { label: string; tone?: string }> = {
  checking: { label: "Checking…" }, untrusted: { label: "Not trusted yet" }, manual: { label: "Not trusted yet" },
  unknown: { label: "Couldn’t check", tone: "danger" }, trusted: { label: "Trusted", tone: "ok" },
};
const trustMark: Record<TrustState["status"], Mark> = { checking: "checking", untrusted: "pending", manual: "pending", unknown: "problem", trusted: "done" };

/** Pair this device (SPEC.md §3.16): the one screen an unpaired browser gets. */
function PairScreen({ trust, removed, onPaired }: { trust: Trust; removed: boolean; onPaired: () => void }) {
  const [kind] = useState(deviceKind);
  const [trusted, check, confirm] = useTrust(trust);
  const [code, setCode] = useState("");
  const [state, setState] = useState<{ status: "idle" | "pairing" } | { status: "failed"; error: string }>({ status: "idle" });
  const secure = trust.https || trust.local;
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setState({ status: "pairing" });
    try {
      await pairDevice(code);
      onPaired();
    } catch (error) {
      setState({ status: "failed", error: (error as Error).message });
    }
  };
  // The field owns the dash: every change is reformatted in place and the caret keeps its place among the characters.
  const onType = (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.target;
    let chars = codeChars(input.value);
    let caret = codeChars(input.value.slice(0, input.selectionStart ?? input.value.length)).length;
    if ((event.nativeEvent as InputEvent).inputType?.startsWith("delete") && input.value === code.replace("-", "")) {
      // A deletion that took only the dash takes the fourth character instead; a paste of the same code is not one.
      chars = chars.slice(0, 3) + chars.slice(4);
      caret = 3;
    }
    const next = formatCode(chars);
    const at = Math.min(caret < 4 ? caret : caret + 1, next.length);
    input.value = next;
    input.setSelectionRange(at, at);
    if (next === code) return;
    setCode(next);
    if (state.status === "failed") setState({ status: "idle" });
  };
  const name = trust.certificate?.name ?? "";
  const status = pill[trusted.status];
  return <main className="pair-screen">
    <div className="pair-brand"><BrandMark size={28} />Altitude</div>
    <section className="pair-card" aria-labelledby="pair-title">
      <h1 id="pair-title">Pair this device</h1>
      {removed ? <p role="status" className="pair-removed">This device is no longer paired. Pair it again to continue.</p> : null}
      <ol className="pair-steps">
        <Step mark={secure ? "done" : "problem"} title="HTTPS address">
          {trust.https || trust.local ? null : <p className="pair-lead">This Altitude serves plain HTTP. Pair on the computer running it.</p>}
        </Step>
        {secure ? <Step mark={trustMark[trusted.status]} title="Trust Altitude’s certificate"
          aside={<span className="chip pair-pill" data-tone={status.tone}>{trusted.status === "checking" ? <StatusMark label={status.label} /> : trusted.status === "trusted" ? <StatusMark busy={false} label={status.label} /> : status.label}</span>}>
          {trusted.status === "trusted" ? null : trusted.status === "manual" ? <>
            <p className="pair-lead">Altitude can’t check this automatically. Open this address in a new Private tab; if it loads without a warning, tap Continue.</p>
            <button type="button" className="btn btn-primary" onClick={confirm}>Continue</button>
          </> : <>
            <Instructions kind={kind} name={name} />
            {trusted.status === "untrusted" ? <p role="status" className="text-meta">{untrusted[kind]}</p> : null}
            {trusted.status === "unknown" ? <p role="alert" className="text-meta text-danger">Couldn’t check.</p> : null}
            <button type="button" className="btn" disabled={trusted.status === "checking"} onClick={check}>Check again</button>
          </>}
        </Step> : null}
        {secure && trusted.status === "trusted" ? <Step mark={state.status === "pairing" ? "checking" : state.status === "failed" ? "problem" : "pending"} title="Pair">
          <form className="pair-form" onSubmit={(event) => void onSubmit(event)}>
            <p className="pair-lead">Enter the code from <code>alt pair</code>, or from Settings › Devices on a paired device.</p>
            <label className="pair-field">Pairing code
              <input value={code} onChange={onType}
                placeholder="ABCD-2345" autoComplete="one-time-code" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
                required disabled={state.status === "pairing"} autoFocus />
            </label>
            {state.status === "failed" ? <p role="alert" className="text-meta text-danger">{state.error}</p> : null}
            <button type="submit" className="btn btn-primary" disabled={state.status === "pairing" || !code}>
              <BusyLabel busy={state.status === "pairing"} label="Pair" working="Pairing…" />
            </button>
            <p className="text-meta text-muted">Each code works once, for 10 minutes. This device stays paired until you remove it in Settings.</p>
          </form>
        </Step> : null}
      </ol>
    </section>
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
      client.setQueryData<Access>(["access"], (known) => known && { ...known, paired: false, device: null });
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
    return <PairScreen trust={access.data.trust} removed={removed} onPaired={() => {
      // Replies cached while unpaired were refusals; the app starts fresh as the paired device, without kept drafts.
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "access" });
      forgetVisits();
      setRemoved(false);
      void access.refetch();
    }} />;
  }
  return children;
}
