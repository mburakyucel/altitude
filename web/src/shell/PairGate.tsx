import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { StatusMark, BusyLabel } from "../components/StatusMark";
import { Command } from "../components/Onboarding";
import { ApiError, checkTrust, pairDevice, readAccess, UNPAIRED_EVENT } from "../data/api";
import type { Access, Trust } from "../data/api";
import { forgetVisits } from "../components/visitMemory";
import { BrandMark } from "./BrandMark";
import type { PairingLink } from "./pairingLink";
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

/** Where a desktop browser keeps the authorities it trusts: macOS's keychain, which Safari, Chrome, Edge and Firefox
 * read, or on Linux Chrome's or Firefox's own store. The visitor can pick another when the browser's report is wrong. */
const desktops = {
  "mac": "Mac",
  "linux": "Linux · Chrome or Chromium",
  "linux-firefox": "Linux · Firefox",
  "other": "Another system",
} as const;
type Desktop = keyof typeof desktops;
function desktopKind(): Desktop {
  const agent = navigator.userAgent;
  const firefox = /Firefox\//.test(agent);
  if (/Macintosh/.test(agent)) return "mac";
  if (/Linux|X11/.test(agent)) return firefox ? "linux-firefox" : /Chrome\/|Chromium\//.test(agent) ? "linux" : "other";
  return "other";
}

/** A code's characters by the server's rule: letters and digits only, uppercased. A mistyped O or 0 stays, so the
 * server can say the code is not right. */
const codeChars = (text: string) => text.toUpperCase().replace(/[^\p{L}\p{N}]/gu, "");

/** The code as the field shows it: at most eight characters, with the dash after the fourth. */
function formatCode(text: string): string {
  const chars = codeChars(text).slice(0, 8);
  return chars.length < 4 ? chars : `${chars.slice(0, 4)}-${chars.slice(4)}`;
}

/** A phone's usual missing step, which its instructions do not make obvious. A desktop's list already ends with it. */
const untrusted: Partial<Record<Kind, string>> = {
  apple: "The usual missing step is the switch in Certificate Trust Settings.",
  android: "Install the certificate as a CA certificate.",
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
function Instructions({ kind, name, guide, installLink, here }: { kind: Kind; name: string; guide: string; installLink: boolean; here: boolean }) {
  const [desktop, setDesktop] = useState(desktopKind);
  const system = kind === "desktop" ? desktop : kind;
  const compare = <>Compare its name, “{name}”, and its <strong>full SHA-256</strong> with the installer or <code>alt doctor</code> on the hosting computer. If they differ, delete the download and stop.</>;
  const reopen = <li>{installLink ? <>Restart the browser, then reopen the original private link from the installer. It must open without a certificate warning. If it has expired, run <code>~/.local/bin/alt pair</code> for a new code.</>
    : <>Restart the browser and reopen this page. It must open without a certificate warning; if a warning remains, don’t pair.</>}</li>;
  const steps = <ol className="pair-instructions">
    <li><a className="btn btn-primary" href={kind === "apple" ? "/api/certificate/altitude.mobileconfig" : "/api/certificate/altitude.crt"}>{kind === "apple" ? "Download the profile" : "Download the certificate"}</a></li>
    {system === "mac" ? <li>In Finder, select altitude.crt in Downloads and press Space. {compare}</li>
      : system === "linux" ? <li>In a terminal, type this command yourself: <code>openssl x509 -in ~/Downloads/altitude.crt -noout -subject -fingerprint -sha256</code>. {compare}</li>
      : system === "linux-firefox" ? null : <li>{compare}</li>}
    {system === "apple" ? <>
      <li>Open Settings › Profile Downloaded. It must list one Certificate, “{name}”. Tap More Details to compare the SHA-256, then tap Install.</li>
      <li>Turn it on: Settings › General › About › Certificate Trust Settings › “{name}”.</li>
    </> : system === "android" ? <li>Install it: Settings › Security › Encryption &amp; credentials › Install a certificate › CA certificate (names vary).</li>
      : system === "mac" ? <>
        <li>Double-click altitude.crt and add it to the login keychain.</li>
        <li>In Keychain Access, open “{name}”, expand Trust and set Secure Sockets Layer (SSL) to Always Trust. Close the window and enter your password.</li>
      </> : system === "linux" ? <>
        <li>Open this address in a new tab: <Command text="chrome://certificate-manager" /></li>
        <li>Choose Local certificates › Custom › Installed by you › Trusted Certificates › Import, then altitude.crt.</li>
      </> : system === "linux-firefox" ? <>
        <li>In Firefox, open Settings › Privacy &amp; Security › Certificates › View Certificates › Authorities › Import, then choose altitude.crt.</li>
        <li>Click View. {compare}</li>
        <li>Tick Trust this CA to identify websites, then OK.</li>
      </> : <li>Import it as a trusted authority in this browser or system.</li>}
    {reopen}
  </ol>;
  const chooser = kind === "desktop" ? <label className="pair-choose">Steps for
    <select value={desktop} onChange={(event) => setDesktop(event.target.value as Desktop)}>
      {Object.entries(desktops).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
  </label> : null;
  return <>
    <p className="pair-lead">A page opened past a certificate warning cannot prove it is Altitude. Check the certificate against the hosting computer before trusting it.</p>
    {kind === "desktop" && here ? <>
      <ol className="pair-instructions">
        <li>On the computer running Altitude, run: <Command text="~/.local/bin/alt trust" />It shows the certificate’s name and full SHA-256, then asks before trusting it in this computer’s browsers.</li>
        {reopen}
      </ol>
      <details className="pair-manual"><summary className="link">Trust it by hand instead</summary>{chooser}{steps}</details>
    </> : <>{chooser}{steps}</>}
    <p><a className="link" href={guide} target="_blank" rel="noopener noreferrer">Certificate guide</a></p>
  </>;
}

type TrustState = { status: "checking" | "untrusted" | "unknown" | "manual" | "trusted" };

/** Trust Altitude's certificate: checked as the page opens, whenever it becomes visible again, and on Check again.
 * Host location never establishes HTTPS trust; an externally supplied certificate is confirmed by hand. */
function useTrust(trust: Trust): [TrustState, () => void, () => void] {
  const automatic = trust.https && trust.check && !!trust.certificate;
  const [state, setState] = useState<TrustState>(() => !trust.https && trust.local ? { status: "trusted" } : automatic ? { status: "checking" } : { status: "manual" });
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
function PairScreen({ trust, guide = SETUP_GUIDE, removed, onPaired, pairingLink }: { trust: Trust; guide?: string; removed: boolean; onPaired: () => void; pairingLink: PairingLink }) {
  const [kind] = useState(deviceKind);
  const [trusted, check, confirm] = useTrust(trust);
  const [code, setCode] = useState("");
  const pending = useRef(pairingLink);
  const live = useRef(true);
  const [state, setState] = useState<{ status: "idle" } | { status: "pairing"; link?: true } | { status: "failed"; error: string }>(
    pairingLink && "invalid" in pairingLink
      ? { status: "failed", error: "This pairing link is not valid. Run ~/.local/bin/alt pair on the computer running Altitude for a new code." }
      : { status: "idle" });
  const secure = trust.https || trust.local;
  const linkReady = pending.current && "code" in pending.current && !!pending.current.code;
  const linkBusy = (state.status === "pairing" && state.link) || (linkReady && trust.https && trusted.status === "trusted");
  const discardLink = () => {
    if (pending.current && "code" in pending.current) pending.current.code = "";
    pending.current = null;
  };
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      // StrictMode immediately sets up the same page again; actual disposal clears its pending capability.
      queueMicrotask(() => { if (!live.current) discardLink(); });
    };
  }, []);
  // Claim the capability before the request: StrictMode or a trust-state rerender cannot redeem it twice.
  useEffect(() => {
    if (!trust.https || trusted.status !== "trusted" || !pending.current || !("code" in pending.current) || !pending.current.code) return;
    const code = pending.current.code;
    discardLink();
    setState({ status: "pairing", link: true });
    void (async () => {
      try {
        await pairDevice(code);
        if (live.current) onPaired();
      } catch (error) {
        // A lost reply may already have set the cookie. Observation reconciles it without another pair POST.
        const observed = await readAccess().catch(() => null);
        if (!live.current) return;
        if (observed?.paired) { onPaired(); return; }
        const recovery = "Run ~/.local/bin/alt pair on the computer running Altitude for a new code.";
        setState({ status: "failed", error: error instanceof ApiError && error.status === 410
          ? `This pairing link has expired or was already used. ${recovery}`
          : error instanceof ApiError ? `${error.message} ${recovery}`
          : "Could not confirm pairing. Reopen the install link, or run ~/.local/bin/alt pair for a new code." });
      }
    })();
  }, [trust.https, trusted.status, onPaired]);
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    discardLink();
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
    discardLink();
    if (state.status === "failed") setState({ status: "idle" });
  };
  const name = trust.certificate?.name ?? "";
  const status = pill[trusted.status];
  return <main className="pair-screen">
    <div className="pair-brand"><BrandMark size={28} />Altitude</div>
    <section className="pair-card" aria-labelledby="pair-title">
      <h1 id="pair-title">Pair this device</h1>
      <p className="pair-lead">Altitude runs on the hosting computer. Trust its certificate, then pair this browser.</p>
      {removed ? <p role="status" className="pair-removed">This device is no longer paired. Pair it again to continue.</p> : null}
      <ol className="pair-steps">
        {!secure ? <p className="pair-lead">This Altitude serves plain HTTP. Pair on the computer running it.</p> : null}
        {trust.https ? <Step mark={trustMark[trusted.status]} title="Trust Altitude’s certificate"
          aside={<span className="chip pair-pill" data-tone={status.tone}>{trusted.status === "checking" ? <StatusMark label={status.label} /> : trusted.status === "trusted" ? <StatusMark busy={false} label={status.label} /> : status.label}</span>}>
          {trusted.status === "trusted" ? null : trusted.status === "manual" ? <>
            <p className="pair-lead">Altitude can’t check this automatically. Open this address in a new Private tab; if it loads without a warning, tap Continue.</p>
            <a className="link" href={guide} target="_blank" rel="noopener noreferrer">Certificate guide</a>
            <button type="button" className="btn btn-primary" onClick={confirm}>Continue</button>
          </> : <>
            <Instructions kind={kind} name={name} guide={guide} installLink={!!linkReady} here={!!trust.command} />
            {trusted.status === "untrusted" && untrusted[kind] ? <p role="status" className="text-meta">{untrusted[kind]}</p> : null}
            {trusted.status === "unknown" ? <p role="alert" className="text-meta text-danger">Couldn’t check.</p> : null}
            <button type="button" className="btn" disabled={trusted.status === "checking"} onClick={check}>Check again</button>
          </>}
        </Step> : null}
        {secure && trusted.status === "trusted" ? <Step mark={state.status === "pairing" || linkBusy ? "checking" : state.status === "failed" ? "problem" : "pending"} title="Pair this browser">
          {linkBusy ? <StatusMark label="Pairing…" /> :
          <form className="pair-form" onSubmit={(event) => void onSubmit(event)}>
            <p className="pair-lead">On the hosting computer, run:</p>
            <Command text="~/.local/bin/alt pair" />
            <p className="text-meta text-muted">A paired browser can make a code in Settings → Devices.</p>
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
          </form>}
        </Step> : null}
      </ol>
    </section>
  </main>;
}

/** Shows the app to a paired browser and the pairing screen to any other. Any API reply of 401 means this
 * browser lost its pairing (removed in Settings, or its cookie expired), so it returns here at once. */
export default function PairGate({ children, pairingLink = null }: { children: ReactNode; pairingLink?: PairingLink }) {
  const client = useQueryClient();
  const access = useQuery({ queryKey: ["access"], queryFn: readAccess, staleTime: Infinity, refetchOnWindowFocus: false, retry: 1 });
  const [removed, setRemoved] = useState(false);
  const link = useRef(pairingLink);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      queueMicrotask(() => {
        if (!mounted.current) {
          if (link.current && "code" in link.current) link.current.code = "";
          link.current = null;
        }
      });
    };
  }, []);
  useEffect(() => {
    if (access.data?.paired) {
      if (link.current && "code" in link.current) link.current.code = "";
      link.current = null;
    }
  }, [access.data?.paired]);
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
    return <PairScreen trust={access.data.trust} guide={access.data.trust.guide} removed={removed} pairingLink={link.current} onPaired={() => {
      link.current = null;
      // Replies cached while unpaired were refusals; the app starts fresh as the paired device, without kept drafts.
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "access" });
      forgetVisits();
      setRemoved(false);
      void access.refetch();
    }} />;
  }
  return children;
}
