import { useState, useSyncExternalStore } from "react";
import { clearVoiceDiagnostics, startVoiceDiagnostics, stopVoiceDiagnostics, subscribeVoiceDiagnostics, voiceDiagnosticReport, voiceDiagnosticsActive } from "./voiceDiagnostics";

export default function VoiceDiagnostics() {
  const active = useSyncExternalStore(subscribeVoiceDiagnostics, voiceDiagnosticsActive);
  const [report, setReport] = useState("");
  const [copied, setCopied] = useState("");
  return <details className="settings-card">
    <summary>Voice troubleshooting</summary>
    <p className="text-meta text-muted">Collect microphone states, errors, timing and browser version on this device for up to ten minutes. No audio, dictated words or drafts. Nothing is sent automatically. Reloading clears the report.</p>
    <p className="text-meta">Start, return to your conversation and reproduce the problem. Then come back here to view and copy the report.</p>
    <div className="onboarding-nav">
      {active ? <button type="button" className="btn" onClick={stopVoiceDiagnostics}>Stop diagnostics</button>
        : <button type="button" className="btn" onClick={() => { startVoiceDiagnostics(); setReport(""); setCopied(""); }}>Start diagnostics</button>}
      <button type="button" className="btn" onClick={() => { stopVoiceDiagnostics(); setReport(voiceDiagnosticReport()); setCopied(""); }}>View report</button>
      <button type="button" className="btn" onClick={() => { clearVoiceDiagnostics(); setReport(""); setCopied("Cleared."); }}>Clear report</button>
    </div>
    {active ? <p role="status" className="text-meta">Collecting on this device. Return here after reproducing.</p> : null}
    {report ? <>
      <label>Voice diagnostic report<textarea readOnly rows={8} value={report} style={{ width: "100%" }} /></label>
      <button type="button" className="btn" onClick={() => {
        void navigator.clipboard?.writeText(report).then(() => setCopied("Copied. Paste it into the task conversation."), () => setCopied("Could not copy. Select and copy the report above."));
        if (!navigator.clipboard) setCopied("Could not copy. Select and copy the report above.");
      }}>Copy report</button>
    </> : null}
    {copied ? <p role="status" className="text-meta">{copied}</p> : null}
  </details>;
}
