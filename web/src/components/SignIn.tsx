import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { changeSignIn, useSignIn } from "../data/api";
import type { SignIn as View } from "../data/api";

const INTERRUPTED = "The sign-in was interrupted.";
const open = (state?: View["state"]) => state === "opening" || state === "waiting" || state === "finishing";

function Busy({ text }: { text: string }) {
  return <span className="sign-in-busy"><span className="status-mark" aria-hidden><span className="spinner" /></span>{text}</span>;
}

/**
 * Altitude's own sign-in on an engine, in its prerequisite row (SPEC.md §3.12): the service runs the engine's setup,
 * this block opens its link and passes the code the engine's page shows. The sign-in lives in the service, so a
 * reload or another paired browser finds it open; one that disappears while followed here was interrupted, unless a
 * sign-in was saved after it began.
 */
export function SignIn({ engine, label, signedIn }: { engine: string; label: string; signedIn: boolean }) {
  const client = useQueryClient();
  const view = useSignIn(engine);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"start" | "finish" | "cancel" | null>(null);
  // The open sign-in this browser follows, and an outcome the service no longer shows: done, or a sentence.
  const [followed, setFollowed] = useState<{ attempt: string; started: number } | null>(null);
  const [outcome, setOutcome] = useState<{ done: true } | { error: string; earlier: boolean } | null>(null);
  const [seen, setSeen] = useState<View | undefined>(undefined);
  const field = useRef<HTMLInputElement>(null);
  const data = view.data;
  if (data !== seen && busy === null) {
    setSeen(data);
    if (data && followed && data.attempt !== followed.attempt) {  // it ended elsewhere: saved since, or interrupted
      const saved = data.saved !== null && data.saved >= followed.started;
      setFollowed(null);
      setCode("");
      setOutcome(saved ? { done: true } : { error: INTERRUPTED, earlier: data.saved !== null });
      if (saved) void client.invalidateQueries({ queryKey: ["prerequisites"] });
    } else if (data && followed && !open(data.state)) {
      setFollowed(null);  // its failure shows from the service
    } else if (data && !followed && open(data.state) && !(outcome && "error" in outcome)) {
      setFollowed({ attempt: data.attempt!, started: data.started! });  // opened here before, or in another browser
      setOutcome(null);
      setCode("");
    }
  }
  const waiting = data?.state === "waiting" && busy === null;
  const refetch = view.refetch;
  useEffect(() => {  // back from the engine's page: read the sign-in again, and be ready for the code
    const back = () => {
      if (document.visibilityState !== "visible") return;
      void refetch();
      if (waiting && !field.current?.value) field.current?.focus();
    };
    document.addEventListener("visibilitychange", back);
    window.addEventListener("focus", back);
    return () => { document.removeEventListener("visibilitychange", back); window.removeEventListener("focus", back); };
  }, [refetch, waiting]);

  const act = async (action: "start" | "finish" | "cancel") => {
    setBusy(action);
    setOutcome(null);
    try {
      const attempt = followed?.attempt ?? "";  // the code goes to the sign-in whose link this page showed
      const next = await changeSignIn(engine, action === "start" ? { action } : action === "finish"
        ? { action, attempt, code } : { action, attempt });
      setCode("");
      setFollowed(action === "start" && open(next.state) ? { attempt: next.attempt!, started: next.started! } : null);
      if (next.state === "done") {
        setOutcome({ done: true });
        void client.invalidateQueries({ queryKey: ["prerequisites"] });
      } else if (action === "finish" && next.attempt !== attempt) {
        setOutcome({ error: INTERRUPTED, earlier: next.saved !== null });  // replaced or cancelled meanwhile
      }
      client.setQueryData(["sign-in", engine], next);
      setSeen(next);
    } catch (error) {
      setFollowed(null);
      setOutcome({ error: (error as Error).message, earlier: signedIn });
      void refetch();
    } finally {
      setBusy(null);
    }
  };

  if (view.isPending) return null;
  if (view.isError) return <p role="alert" className="text-meta text-danger">Could not check the sign-in. <button type="button" className="link" onClick={() => void refetch()}>Retry</button></p>;
  const shown = view.data;
  const state = busy === "start" ? "opening" : shown.state;
  // An outcome seen only here outranks what the service shows now, until Try again.
  const failed = outcome && "error" in outcome ? outcome
    : state === "failed" ? { error: shown.error ?? INTERRUPTED, earlier: shown.saved !== null && shown.saved < (shown.started ?? 0) } : null;
  if (failed) {
    return <div className="sign-in">
      <p role="alert" className="text-meta text-danger">{failed.error}{failed.earlier ? " Your earlier sign-in still works." : ""}</p>
      <div><button type="button" className="btn" onClick={() => void act("start")}>Try again</button></div>
    </div>;
  }
  if (state === "opening") {
    return <div className="sign-in"><div><button type="button" className="btn btn-primary" disabled><Busy text={`Opening ${label} sign-in…`} /></button></div></div>;
  }
  if (state === "waiting" || state === "finishing") {
    const finishing = state === "finishing" || busy !== null;
    return <form className="sign-in" aria-label={`${label} sign-in`} onSubmit={(event) => { event.preventDefault(); if (code.trim()) void act("finish"); }}>
      <ol>
        <li><span className="text-meta">Approve Altitude on {label}'s page.</span>
          <div className="sign-in-link">{shown.link && !finishing
            ? <a className="btn" href={shown.link} target="_blank" rel="noopener noreferrer">Open {label} sign-in <span aria-hidden>↗</span></a>
            : <span className="btn" aria-disabled="true">Open {label} sign-in <span aria-hidden>↗</span></span>}</div></li>
        <li><label className="onboarding-field">Paste the code {label} shows
          <input ref={field} value={code} placeholder={`Code from ${label}`} disabled={finishing} autoComplete="off" autoCapitalize="off"
            autoCorrect="off" spellCheck={false} enterKeyHint="done" onChange={(event) => setCode(event.target.value)} /></label></li>
      </ol>
      <p className="text-meta text-muted">The code works once, for this sign-in only.</p>
      <div className="sign-in-actions">
        <button type="button" className="btn btn-ghost" disabled={finishing} onClick={() => void act("cancel")}>{busy === "cancel" ? <Busy text="Cancelling…" /> : "Cancel"}</button>
        <button type="submit" className="btn btn-primary" disabled={finishing || !code.trim()}>{finishing && busy !== "cancel" ? <Busy text="Signing in…" /> : "Finish sign-in"}</button>
      </div>
    </form>;
  }
  if (outcome && "done" in outcome) return <p role="status" className="sign-in-done text-meta">Signed in. Altitude's {label} runs use this sign-in for a year.</p>;
  return <div className="sign-in"><div>
    <button type="button" className={signedIn ? "btn" : "btn btn-primary"} onClick={() => void act("start")}>{signedIn ? "Sign in again" : "Sign in"}</button>
  </div></div>;
}
