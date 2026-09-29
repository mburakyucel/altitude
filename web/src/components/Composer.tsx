import { useCallback, useEffect, useId, useLayoutEffect, useReducer, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject } from "react";
import { ApiError, imageSendRefused } from "../data/api";
import type { VoiceSettings } from "../data/api";
import { Link } from "react-router";
import { IMAGE_HELP, useImageDraft } from "./ImageDraft";
import type { ImageScope, ImageSubmission } from "./ImageDraft";
import { HostCapture, MAX_RETAINED, UNREACHED } from "./hostCapture";
import { useLiveReveal } from "./liveReveal";
import { RecognitionCapture, recognitionAvailable } from "./recognition";
import { refreshVoiceBackend, useVoiceBackend } from "./voiceBackend";
import { traceVoice, traceVoiceTracks, voiceError } from "./voiceTrace";

/*
 * The one composer (SPEC.md §3.6): project chat and task conversation. The page owns
 * the draft and the send; the composer owns the states in the §3.6 table: Idle, Typing, Sending (the
 * page's bubble at 60%), Busy (the arrow queues), Listening, Transcribing, Landed (the transcript is
 * appended to the draft and nothing else appears, issue #195), Denied, Unavailable, and a refused
 * send ("Not sent. Retry."). Voice is capped at ten minutes; audio never becomes state anywhere.
 * The installation's voice backend decides the capture: host voice streams to this computer's speech
 * model and browser recognition never uploads; both show their words flowing in while listening and run the same
 * recorder-shaped state machine. Host voice keeps recording through a lost connection and its words catch
 * up; after Stop or Send it waits for the connection with Cancel.
 */

/** Recordings stop five seconds under ten minutes, absorbing timer delay and container padding. */
export const MAX_RECORDING_MS = 595_000;
const LAST_MINUTE_MS = 60_000;
const WAVE_BARS = 28;
const WAVEFORM_CLOSE_MS = 3000;

/** Give asynchronous audio graph shutdown a chance to finish before another microphone opens. */
let waveformClosing: Promise<void> | null = null;

function closeWaveform(context: AudioContext) {
  traceVoice("waveform.close-wait", context, { state: context.state });
  // Capture cleanup publishes idle() synchronously; read it after that cleanup finishes.
  const closing = Promise.resolve().then(() => RecognitionCapture.idle())
    .then(() => new Promise<void>((resolve) => {
      // A browser that never acknowledges close must not disable voice until a page reload.
      const timer = setTimeout(() => { traceVoice("waveform.close-timeout", context); resolve(); }, WAVEFORM_CLOSE_MS);
      void Promise.resolve().then(() => { traceVoice("waveform.close", context); return context.close(); })
        .catch((error) => traceVoice("waveform.close-error", context, { error: voiceError(error) })).finally(() => {
        traceVoice("waveform.close-settled", context, { state: context.state });
        clearTimeout(timer);
        resolve();
      });
    }));
  const pending = Promise.all([waveformClosing, closing]).then(() => {
    if (waveformClosing === pending) waveformClosing = null;
  });
  waveformClosing = pending;
}

type Phase = "idle" | "starting" | "listening" | "transcribing";
type Capture = RecognitionCapture | HostCapture;
/** "transcription": the recording failed; "unreached": host voice lost the connection past its wait. */
type SendFailure = "refused" | "unconfirmed" | "transcription" | "unreached" | null;
type VoiceSend = { id: string; text: string; failure: SendFailure; controller: AbortController; send: ComposerProps["onSubmit"]; images?: Promise<ImageSubmission>; capture: Capture; cancel: () => void };
const voiceSends = new Map<string, VoiceSend>();

// #320: only submitted text lives beyond a composer. These records never initiate a send.
type SubmittedText = { pending: Record<string, string>; text: string; failure: SendFailure };
const liveSubmissions = new Set<string>();
// A failed update retains the current recovery within this document. The stored bytes identify
// the copy it supersedes; clearing/replacing browser storage also discards that fallback.
const unwrittenRecovery = new Map<string, { stored: string | null; value: SubmittedText }>();
const recoveryViews = new Map<string, { draft: () => string; failure: () => SendFailure; restore: (saved: SubmittedText, unavailable?: boolean) => void;
  voice: (send: VoiceSend | null) => void; submit: (text: string, retry?: ImageSubmission, voice?: VoiceSend) => Promise<void> }>();
const recoveryKey = (conversation: string) => `altitude.submitted:${conversation}`;
function readSubmitted(conversation: string): SubmittedText {
  const saved = sessionStorage.getItem(recoveryKey(conversation));
  const unwritten = unwrittenRecovery.get(conversation);
  if (unwritten?.stored === saved) return structuredClone(unwritten.value);
  unwrittenRecovery.delete(conversation);
  return saved ? JSON.parse(saved) as SubmittedText : { pending: {}, text: "", failure: null };
}
function saveSubmitted(conversation: string, saved: SubmittedText, beforeSend = false) {
  const key = recoveryKey(conversation);
  const stored = sessionStorage.getItem(key);
  try {
    if (saved.text || saved.failure === "transcription" || saved.failure === "unreached" || Object.keys(saved.pending).length) sessionStorage.setItem(key, JSON.stringify(saved));
    else sessionStorage.removeItem(key);
    unwrittenRecovery.delete(conversation);
  } catch (error) {
    if (!beforeSend) unwrittenRecovery.set(conversation, { stored, value: structuredClone(saved) });
    throw error;
  }
}
function beginSubmitted(conversation: string, text: string, id: string = crypto.randomUUID(), preserve = false) {
  const saved = readSubmitted(conversation);
  saved.pending[id] = text;
  if (!preserve) { saved.text = ""; saved.failure = null; }
  saveSubmitted(conversation, saved, true);
  liveSubmissions.add(id);
  return id;
}
function settleSubmitted(conversation: string, id: string, text: string, failure: SendFailure, restore = false) {
  if (!liveSubmissions.delete(id)) return;
  let saved: SubmittedText;
  let readable = true;
  try { saved = readSubmitted(conversation); }
  catch { readable = false; saved = { pending: {}, text: "", failure: null }; }
  delete saved.pending[id];
  const view = recoveryViews.get(conversation);
  if (failure || restore) {
    saved.text = [text, view?.draft() ?? saved.text].filter(Boolean).join("\n");
    saved.failure = [failure, saved.failure, view?.failure()].includes("unconfirmed") ? "unconfirmed" : failure ?? view?.failure() ?? saved.failure;
  }
  // If browser storage stops accepting writes after admission, its earlier pending copy remains
  // recoverable as unconfirmed on reload. A storage error cannot undo a server receipt.
  try { if (readable) saveSubmitted(conversation, saved); } catch { /* retain the pending recovery copy */ }
  if (failure || restore) view?.restore(saved, !readable || unwrittenRecovery.has(conversation));
}

export interface ComposerProps {
  /** Stable project/task identity, independent of route tab and display title. */
  conversation: string;
  value: string;
  onChange: (value: string) => void;
  /** Resolve accepted sends. Explicit HTTP refusal restores the draft with Retry; an uncertain
   * transport/server failure restores it with a reminder to check the conversation first. */
  onSubmit: (text: string, onAccepted: () => void, images?: ImageSubmission) => void | Promise<void>;
  /** Freeze any reply context when Send is requested, before asynchronous voice preparation. */
  prepareSubmit?: () => ComposerProps["onSubmit"];
  imageScope?: ImageScope;
  placeholder: string;
  ariaLabel: string;
  /** L3 is mid-turn: the arrow queues; desktop also explains that the message runs next. */
  busy?: boolean;
  /** The desktop hint under the field when no state claims it (12px muted). */
  hint?: ReactNode;
  /** The desktop pill: the engine pin on L3 chat; phone uses project details. */
  pill?: ReactNode;
  disabled?: boolean;
  /** A worker stop can hold sending while the operator continues editing or dictating. */
  sendDisabled?: boolean;
  selection?: RefObject<{ start: number; end: number } | null>;
  onEscapeOwnership?: (owned: boolean) => void;
  autoFocus?: boolean;
  active?: boolean;
}

/** Append dictated text as normal prose without altering any existing draft characters. */
export function combineDraft(draft: string, transcript: string): string {
  const spoken = transcript.trim();
  if (!spoken) return draft;
  if (!draft) return spoken;
  return `${draft}${/\s$/.test(draft) ? "" : " "}${spoken}`;
}

/**
 * Why voice is unavailable: "insecure" (the hint says so), "unrecognized" (the browser backend has
 * no speech recognition; the hint says so), "host" (host voice cannot run on this computer, or its setup
 * failed; the hint says why), "unsupported" (the mic simply hides), "pending" while the backend is still
 * being read (the mic hides), or null.
 */
export function voiceUnavailable(voice: VoiceSettings | null): "insecure" | "unrecognized" | "host" | "unsupported" | "pending" | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return "unsupported";
  if (window.isSecureContext === false) return "insecure";
  if (voice === null) return "pending";
  if (typeof navigator.mediaDevices?.getUserMedia !== "function") return "unsupported";
  if (voice.backend === "browser") return recognitionAvailable() ? null : "unrecognized";
  if (voice.host.state === "unavailable" || voice.host.state === "failed") return "host";
  return typeof AudioWorkletNode === "undefined" ? "unsupported" : null;
}

/** What the hint says when host voice cannot run here; the Settings link offers setup or Retry. */
function hostVoiceHint(voice: VoiceSettings): ReactNode {
  const host = voice.host;
  if (host.state === "unavailable") return `Voice isn't available on this computer: ${host.reason}. Typing works.`;
  if (host.state === "failed") return <>Voice setup did not finish. <Link className="link" to="/settings/voice">Retry in Settings</Link>. Typing works.</>;
  if (host.state === "setting-up") return "Voice is being set up on this computer. Typing works.";
  return <>{host.state === "outdated" ? "Voice needs an update on this computer." : "Voice needs a one-time download on this computer."}{" "}
    <Link className="link" to="/settings/voice">Set up voice</Link></>;
}

/**
 * Discard a live capture without waiting for its last words. A recognizer lets go of the microphone
 * later; the capture releases the stream then, and the next capture waits for that
 * (`RecognitionCapture.idle`) instead of starting on top of it. Host voice lets go at once.
 */
function discardRecognition(capture: Capture) {
  capture.onupdate = null;
  capture.onstop = null;
  capture.cancel();
}

export function formatTimer(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 5l10 10M15 5 5 15" />
    </svg>
  );
}

/**
 * The live waveform while listening: the recent loudness as bars, drawn from an AnalyserNode into a
 * backing store sized from the canvas's CSS box at the device pixel ratio, so it is crisp at every
 * width. It freezes (the loop stops, the last frame stays) while transcribing. Where the page has no
 * audio graph (a test runtime), the canvas simply stays blank.
 */
type Waveform = { context: AudioContext; analyser: AnalyserNode | null; stream: MediaStream };

/** Connect the graph before capture starts, rather than in a later React effect. */
function openWaveform(stream: MediaStream): Waveform | null {
  if (typeof AudioContext === "undefined") return null;
  let context: AudioContext | null = null;
  try {
    context = new AudioContext();
    traceVoice("waveform.created", context, { state: context.state });
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    traceVoice("waveform.connected", context, { state: context.state });
    return { context, analyser, stream };
  } catch (error) {
    traceVoice("waveform.setup-error", context ?? undefined, { error: voiceError(error) });
    // Even a partially built graph belongs to this capture and closes with it, not during startup.
    return context ? { context, analyser: null, stream } : null;
  }
}

function useWaveform(graph: Waveform | null, running: boolean) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const levels = useRef<number[]>(new Array<number>(WAVE_BARS).fill(0));

  useEffect(() => {
    if (!graph?.analyser || !running || !canvas.current) return;
    const { context, analyser, stream } = graph;
    let frame = 0;
    const data = new Uint8Array(analyser.fftSize);
    let lastSample = -Infinity;
    const node = canvas.current;
    const draw = () => {
      if (!analyser || !node) return;
      const box = node.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(box.width * scale));
      const height = Math.max(1, Math.round(box.height * scale));
      if (node.width !== width || node.height !== height) {
        node.width = width;
        node.height = height;
      }
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const sample of data) {
        const v = (sample - 128) / 128;
        sum += v * v;
      }
      const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
      if (performance.now() - lastSample >= 1000 && context) {
        lastSample = performance.now();
        traceVoice("waveform.sample", context, { state: context.state, time: context.currentTime,
          signal: context.state === "running" ? level > 0.01 : undefined });
        traceVoiceTracks("microphone.sample", stream);
      }
      levels.current = [...levels.current.slice(1), level];
      const ctx = node.getContext("2d");
      if (ctx) {
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = getComputedStyle(node).color;
        const gap = width / WAVE_BARS;
        levels.current.forEach((value, index) => {
          const bar = Math.max(3 * scale, value * height);
          ctx.fillRect(index * gap + gap * 0.25, (height - bar) / 2, gap * 0.5, bar);
        });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [graph, running]);

  return canvas;
}

export default function Composer({
  conversation,
  value,
  onChange,
  onSubmit,
  prepareSubmit,
  placeholder,
  ariaLabel,
  busy = false,
  hint,
  pill,
  disabled = false,
  sendDisabled = false,
  selection,
  onEscapeOwnership,
  autoFocus = false,
  active = true,
  imageScope,
}: ComposerProps) {
  const hintId = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const visible = useRef(active);
  visible.current = active;
  const picker = useRef<HTMLInputElement>(null);
  const images = useImageDraft(imageScope);
  const [admission, setAdmission] = useState<"" | "sending" | "uncertain">("");
  const admitting = useRef(false);
  const retryImage = useRef<{ text: string; submission: ImageSubmission; recoveryId: string; send: ComposerProps["onSubmit"]; preserveDraft: boolean } | null>(null);
  const deferredRecovery = useRef<SubmittedText | null>(null);
  const [refusalReason, setRefusalReason] = useState("");
  const recorder = useRef<Capture | null>(null);
  const cancelled = useRef(false);
  // Only a cancel made in view takes focus back, even when a recorder reports its end later (issue
  // #495): the field after Escape, the microphone after the X, so a phone keyboard does not open.
  const focusAfterCancel = useRef<"field" | "mic" | null>(null);
  const mic = useRef<HTMLButtonElement>(null);
  const stopRequested = useRef(false);
  const sendAfterTranscribing = useRef<VoiceSend | null>(null);
  const capTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const startedAt = useRef(0);
  const draft = useRef(value);
  draft.current = value;

  const [capturePhase, setPhase] = useState<Phase>("idle");
  const [voiceSend, setVoiceSend] = useState(() => voiceSends.get(conversation) ?? null);
  const phase = voiceSend ? "transcribing" : capturePhase;
  /** The words recognized so far, flowing in after the draft while listening. */
  const [live, setLive] = useState<string | null>(null);
  const flowing = useLiveReveal(live);
  const displayedDraft = voiceSend?.text ?? (flowing === null ? value : combineDraft(value, flowing));
  const waveform = useRef<Waveform | null>(null);
  const releaseWaveform = useCallback(() => {
    const graph = waveform.current;
    waveform.current = null;
    if (graph) closeWaveform(graph.context);
  }, []);
  const [elapsed, setElapsed] = useState(0);
  const [denied, setDenied] = useState(false);
  const [voiceFailure, setVoiceFailure] = useState("");
  /** Host voice was asked for before this computer finished its setup: the hint offers Settings. */
  const [setupNeeded, setSetupNeeded] = useState(false);
  /** Host voice: the microphone is open and the first samples are awaited. */
  const [startingVoice, setStartingVoice] = useState(false);
  /** Browser dictation landed without punctuation: the bundled model could not run in this browser. */
  const [unpunctuated, setUnpunctuated] = useState<"loading" | "failed" | null>(null);
  const [sendFailure, setSendFailure] = useState<SendFailure>(null);
  const [recoveryUnavailable, setRecoveryUnavailable] = useState(false);
  const failure = useRef(sendFailure);
  failure.current = sendFailure;
  const voice = useVoiceBackend();
  const backend = voice?.backend ?? null;
  const captureSelection = useRef("");
  const unavailable = voiceUnavailable(voice);
  const [, watched] = useReducer((count: number) => count + 1, 0);
  useEffect(() => {
    HostCapture.watchers.add(watched);
    return () => { HostCapture.watchers.delete(watched); };
  }, []);
  const canvas = useWaveform(waveform.current, phase === "listening");

  useLayoutEffect(() => {
    const node = field.current;
    if (!node) return;
    const sizeField = () => {
      node.style.height = "auto";
      node.style.height = `${node.scrollHeight}px`;
      // Recognized words land at the end of a read-only field: once it reaches its cap, follow them,
      // including the last phrase that arrives while Stop waits for the recognizer to settle.
      if (live !== null) node.scrollTop = node.scrollHeight;
    };
    sizeField();
    let width = node.clientWidth;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (width === node.clientWidth) return;
      width = node.clientWidth;
      sizeField();
    });
    observer?.observe(node);
    window.addEventListener("resize", sizeField);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", sizeField);
    };
  }, [displayedDraft, phase]);

  useEffect(() => {
    onEscapeOwnership?.(active && phase !== "idle");
    return () => onEscapeOwnership?.(false);
  }, [active, phase, onEscapeOwnership]);
  useEffect(() => {
    if (selection?.current) field.current?.setSelectionRange(selection.current.start, selection.current.end);
  }, [selection]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      releaseWaveform();
      if (capTimer.current) clearTimeout(capTimer.current);
      const sending = sendAfterTranscribing.current;
      const retained = sending && voiceSends.get(conversation) === sending;
      if (!retained) abort.current?.abort();
      const active = recorder.current;
      if (!retained) recorder.current = null;
      if (!retained && active && active.state !== "inactive") {
        discardRecognition(active);
        return;
      }
      try {
        if (active && active.state !== "inactive") active.stop();
      } catch {
        // Nothing to release: the recorder is already gone.
      }
      active?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    if (phase !== "listening") return;
    const timer = setInterval(() => setElapsed(Date.now() - startedAt.current), 250);
    return () => clearInterval(timer);
  }, [phase]);

  const focusField = useCallback((position?: number) => {
    const target = focusAfterCancel.current;
    focusAfterCancel.current = null;
    queueMicrotask(() => {
      if (target === "mic") {
        if (visible.current) mic.current?.focus({ preventScroll: true });
        return;
      }
      const node = field.current;
      if (!node || !visible.current) return;
      node.focus({ preventScroll: true });
      if (position != null) node.setSelectionRange(position, position);
    });
  }, []);

  useEffect(() => {
    const view = {
      voice: (send: VoiceSend | null) => { setVoiceSend(send); if (!send) setPhase("idle"); },
      submit: (text: string, retry?: ImageSubmission, voice?: VoiceSend) => currentSubmit.current(text, retry, voice),
      draft: () => deferredRecovery.current?.text ?? draft.current,
      failure: () => failure.current,
      restore: (saved: SubmittedText, unavailable = false) => {
        if (admitting.current) { deferredRecovery.current = saved; return; }
        draft.current = saved.text;
        onChange(saved.text);
        failure.current = saved.failure;
        setSendFailure(saved.failure);
        setRecoveryUnavailable(unavailable);
        focusField(saved.text.length);
      },
    };
    recoveryViews.set(conversation, view);
    try {
      const saved = readSubmitted(conversation);
      // A reload has no live request to await. Keep its text without claiming acceptance or resending.
      for (const [id, text] of Object.entries(saved.pending)) {
        if (liveSubmissions.has(id)) continue;
        saved.text = [text, saved.text].filter(Boolean).join("\n");
        saved.failure = "unconfirmed";
        delete saved.pending[id];
      }
      if (saved.text || saved.failure === "transcription" || saved.failure === "unreached") {
        saved.text = [saved.text, draft.current].filter(Boolean).join("\n");
        view.restore(saved);
        try { saveSubmitted(conversation, saved); setRecoveryUnavailable(false); }
        catch { setRecoveryUnavailable(true); }
      }
    } catch { setVoiceFailure("Message recovery is unavailable in this browser."); }
    return () => {
      if (recoveryViews.get(conversation) === view) recoveryViews.delete(conversation);
      // Task tabs retain their parent draft. Transfer recovered text to storage on leaving so a
      // remount restores it once, while ordinary unsent task drafts keep their existing owner.
      try {
        if (readSubmitted(conversation).text) { draft.current = ""; onChange(""); }
      } catch { /* keep the parent draft */ }
    };
  }, [conversation, focusField, onChange]);

  const editDraft = useCallback((text: string) => {
    draft.current = text;
    onChange(text);
    try {
      const saved = readSubmitted(conversation);
      if (saved.failure || saved.text) {
        saved.text = saved.failure === "unconfirmed" ? text : "";
        if (saved.failure !== "unconfirmed") saved.failure = null;
        saveSubmitted(conversation, saved);
      }
      setRecoveryUnavailable(false);
    } catch { setRecoveryUnavailable(true); }
    if (failure.current !== "unconfirmed") { failure.current = null; setSendFailure(null); }
  }, [conversation, onChange]);

  const releaseStream = useCallback((released: MediaStream | null) => {
    released?.getTracks().forEach((track) => track.stop());
    releaseWaveform();
  }, [releaseWaveform]);

  // ---- send: the draft becomes the page's bubble at once; a refusal brings it back ----------------
  const submit = useCallback(
    async (text: string, retry?: ImageSubmission, voice?: VoiceSend) => {
      const ready = text.trim();
      const preserveDraft = Boolean(voice || (retry && retryImage.current?.preserveDraft));
      const send = voice?.send ?? (retry ? retryImage.current?.send : undefined) ?? onSubmit;
      const withImages = Boolean(retry || (voice ? voice.images : images.selected.length));
      if (!voice && ((!ready && !withImages) || disabled || sendDisabled || images.checking || (admitting.current && !retry))) return;
      if (withImages && !retry && !voice && !images.capability?.available) {
        images.setError(`Image input unavailable. ${images.capability?.reason ?? "Checking image input…"}`);
        return;
      }
      const id = voice?.id ?? (retry && retryImage.current ? retryImage.current.recoveryId : crypto.randomUUID());
      try {
        beginSubmitted(conversation, text, id, preserveDraft);
      } catch {
        if (voice) settleSubmitted(conversation, id, text, "refused");
        setVoiceFailure("Could not save message recovery. Your message was not sent.");
        return;
      }
      if (!preserveDraft) { failure.current = null; setSendFailure(null); }
      setRecoveryUnavailable(false); setRefusalReason("");
      setVoiceFailure(""); setSetupNeeded(false); setUnpunctuated(null); images.setError("");
      if (withImages) { admitting.current = true; setAdmission("sending"); }
      if (!preserveDraft) { draft.current = ""; onChange(""); }
      let accepted = false;
      const accept = () => {
        accepted = true;
        settleSubmitted(conversation, id, text, null);
      };
      let submission = retry;
      try {
        if (withImages) {
          submission ??= await (voice?.images ?? images.submission());
          if (!mounted.current && !voice) {
            settleSubmitted(conversation, id, text, "refused");
            return;
          }
          retryImage.current = { text, submission, recoveryId: id, send, preserveDraft };
          await send(ready, accept, submission);
        } else await send(ready, accept);
        accept();
      } catch (error) {
        if (!accepted) {
          if (withImages && submission && !imageSendRefused(error) && mounted.current) {
            // Keep the immutable image retry here; a later remount recovers only its caption.
            liveSubmissions.delete(id);
            setAdmission("uncertain");
            return;
          }
          settleSubmitted(conversation, id, text,
            !submission && withImages || error instanceof ApiError && error.status < 500 ? "refused" : "unconfirmed");
          if (mounted.current && withImages && error instanceof Error) setRefusalReason(error.message);
        }
      }
      if (!mounted.current) return;
      if (withImages) {
        if (accepted) images.clear();
        admitting.current = false; setAdmission(""); retryImage.current = null;
        if (deferredRecovery.current) {
          const saved = deferredRecovery.current; deferredRecovery.current = null;
          let unavailable = false;
          try {
            const current = readSubmitted(conversation);
            current.text = saved.text; current.failure = saved.failure;
            saveSubmitted(conversation, current);
          } catch { unavailable = true; }
          recoveryViews.get(conversation)?.restore(saved, unavailable);
        }
      }
      focusField();
    },
    [conversation, disabled, sendDisabled, focusField, images, onChange, onSubmit],
  );
  // A returning source view owns admission UI; an explicit voice Send retains its captured transport.
  const currentSubmit = useRef(submit);
  currentSubmit.current = submit;

  const endVoiceSend = useCallback((sending: VoiceSend, problem?: "transcription" | "unreached") => {
    if (voiceSends.get(conversation) !== sending) return;
    voiceSends.delete(conversation);
    recoveryViews.get(conversation)?.voice(null);
    settleSubmitted(conversation, sending.id, sending.text, sending.failure === "unconfirmed" ? "unconfirmed" : problem ?? sending.failure, true);
  }, [conversation]);

  // ---- voice: listening, transcribing, landed; every failure is one hint and an unchanged draft ----
  const finish = useCallback(
    (finished: Capture, used: MediaStream | null) => {
      if (recorder.current !== finished) return;
      recorder.current = null;
      stopRequested.current = false;
      if (capTimer.current) clearTimeout(capTimer.current);
      capTimer.current = null;
      if (mounted.current) releaseStream(used);
      else used?.getTracks().forEach((track) => track.stop());
      const sending = sendAfterTranscribing.current;
      if (mounted.current) setLive(null);
      if (!mounted.current && !sending) return;
      if (cancelled.current || sending?.controller.signal.aborted) {
        setPhase("idle");
        if (!cancelled.current || focusAfterCancel.current) focusField();
        return;
      }
      // The words are already here. Words shown before an error still land in the draft; only a refusal
      // discards them.
      const text = finished.failure === "denied" ? "" : finished.text;
      const unreached = finished instanceof HostCapture && finished.unreached;
      // The host refused the page's setting or setup: read them again.
      if (finished instanceof HostCapture && finished.outdated) refreshVoiceBackend();
      if (sending) {
        if (finished.failure) {
          sending.text = combineDraft(sending.text, text);
          endVoiceSend(sending, unreached ? "unreached" : "transcription");
          return;
        }
        if (!text.trim()) { endVoiceSend(sending); return; }
        voiceSends.delete(conversation);
        const view = recoveryViews.get(conversation);
        view?.voice(null);
        void (view?.submit ?? currentSubmit.current)(combineDraft(sending.text, text), undefined, sending);
        return;
      }
      setPhase("idle");
      if (finished.failure === "denied") setDenied(true);
      else if (unreached) setVoiceFailure(UNREACHED);
      else if (finished instanceof HostCapture && finished.reason) setVoiceFailure(`${finished.reason} Typing works.`);
      else if (finished.failure) setVoiceFailure("Could not transcribe. Typing works.");
      if (text.trim()) setUnpunctuated(finished.unpunctuated);
      if (text.trim()) {
        const next = combineDraft(draft.current, text);
        editDraft(next);
        focusField(next.length);
        return;
      }
      focusField();
    },
    [conversation, editDraft, endVoiceSend, focusField, releaseStream],
  );

  const stop = useCallback((send = false) => {
    const active = recorder.current;
    if (!active || stopRequested.current) return;
    if (send) {
      if (disabled || sendDisabled || images.checking || (images.selected.length && !images.capability?.available)) return;
      let id: string;
      const priorFailure = failure.current;
      try { id = beginSubmitted(conversation, draft.current); }
      catch { setVoiceFailure("Could not save message recovery. Your message was not sent."); return; }
      failure.current = null;
      setSendFailure(null);
      setRecoveryUnavailable(false);
      const sending: VoiceSend = { id, text: draft.current, failure: priorFailure, controller: new AbortController(), send: prepareSubmit?.() ?? onSubmit,
        images: images.selected.length ? images.submission() : undefined, capture: active,
        cancel: () => {
          sending.controller.abort();
          if (recorder.current === active) recorder.current = null;
          if (active.state !== "inactive") discardRecognition(active);
          else active.stream.getTracks().forEach((track) => track.stop());
          releaseWaveform();
          setLive(null);
          if (capTimer.current) clearTimeout(capTimer.current);
          endVoiceSend(sending);
        },
      };
      // Image reads start now, while the selected Files and reply context still belong to this Send.
      void sending.images?.catch(() => undefined);
      sendAfterTranscribing.current = sending;
      voiceSends.set(conversation, sending);
      recoveryViews.get(conversation)?.voice(sending);
      draft.current = "";
      onChange("");
    }
    stopRequested.current = true;
    setPhase("transcribing");
    try {
      if (active.state !== "inactive") active.stop();
      else finish(active, active.stream);
    } catch {
      finish(active, active.stream);
    } finally {
      releaseWaveform();
    }
  }, [conversation, disabled, endVoiceSend, finish, images, onChange, onSubmit, prepareSubmit, releaseWaveform, sendDisabled]);

  const start = useCallback(async () => {
    if (unavailable || disabled || admitting.current || phase !== "idle") return;
    if (voice?.backend === "host" && voice.host.state !== "ready") {
      // Setup may have finished since this page read it: read again, and the next tap starts.
      setSetupNeeded(true);
      refreshVoiceBackend();
      return;
    }
    if (voice?.backend === "host" && HostCapture.retained.size >= MAX_RETAINED) {
      setVoiceFailure("Voice is still sending an earlier recording. Typing works.");
      return;
    }
    captureSelection.current = voice?.selection ?? "";
    focusAfterCancel.current = null;
    setVoiceFailure("");
    setSetupNeeded(false);
    setStartingVoice(false);
    setUnpunctuated(null);
    setDenied(false);
    setElapsed(0);
    cancelled.current = false;
    stopRequested.current = false;
    sendAfterTranscribing.current = null;
    setPhase("starting");
    const opening = new AbortController();
    traceVoice("capture.open", opening, { backend: backend ?? undefined });
    abort.current = opening;
    const ending = RecognitionCapture.idle();
    if (ending || waveformClosing) {
      traceVoice("capture.wait-for-release", opening);
      await Promise.all([ending, waveformClosing]);
      traceVoice("capture.release-ready", opening);
      if (!mounted.current || opening.signal.aborted) return;
    }
    let opened: MediaStream | null = null;
    try {
      // Every backend opens the microphone: the waveform draws from it, and the browser backend's
      // recognizer needs the same permission.
      traceVoice("microphone.request", opening);
      opened = await navigator.mediaDevices.getUserMedia({ audio: true });
      traceVoiceTracks("microphone.opened", opened);
      if (!mounted.current || opening.signal.aborted) {
        opened.getTracks().forEach((track) => track.stop());
        return;
      }
      const used = opened;
      // Warm native recognition may start before React's next effect. Connect its companion
      // waveform first, after the old capture's release gate, with no await before start().
      waveform.current = openWaveform(opened);
      if (backend === "host") {
        const host = new HostCapture(opened, captureSelection.current);
        host.onupdate = (text) => { if (recorder.current === host && !cancelled.current) setLive(text); };
        host.onstop = () => finish(host, used);
        host.oninterrupted = () => stop();
        host.onlistening = () => {
          if (recorder.current !== host) return;
          traceVoice("capture.listening", host);
          startedAt.current = Date.now();
          setStartingVoice(false);
          setPhase("listening");
          capTimer.current = setTimeout(() => stop(), MAX_RECORDING_MS);
        };
        recorder.current = host;
        setStartingVoice(true);
        host.start();
        return;
      }
      const active = new RecognitionCapture(opened, { before: draft.current });
      active.onupdate = (text) => { if (recorder.current === active && !cancelled.current) setLive(text); };
      active.onstop = () => finish(active, used);
      recorder.current = active;
      active.start();
      traceVoice("capture.listening", active);
      startedAt.current = Date.now();
      setPhase("listening");
      capTimer.current = setTimeout(() => stop(), MAX_RECORDING_MS);
    } catch (cause) {
      traceVoice("capture.open-error", opening, { error: voiceError(cause) });
      const failed = recorder.current;
      if (failed?.stream === opened) {
        recorder.current = null;
        discardRecognition(failed);
      }
      opened?.getTracks().forEach((track) => track.stop());
      if (!mounted.current || opening.signal.aborted) return;
      releaseStream(null);
      const name = cause instanceof DOMException ? cause.name : "";
      setPhase("idle");
      if (name === "NotAllowedError" || name === "SecurityError") setDenied(true);
      else setVoiceFailure("Could not open the microphone. Typing works.");
      focusField();
    }
  }, [backend, voice, disabled, finish, focusField, phase, releaseStream, stop, unavailable]);

  /** Esc while listening: back to the previous state, nothing added (SPEC.md §3.6). */
  const cancel = useCallback((refocus: "field" | "mic" = "field") => {
    traceVoice("capture.cancel");
    focusAfterCancel.current = visible.current ? refocus : null;
    const sending = voiceSends.get(conversation);
    if (sending) { sending.cancel(); return; }
    cancelled.current = true;
    setLive(null);
    if (capTimer.current) clearTimeout(capTimer.current);
    capTimer.current = null;
    abort.current?.abort();
    abort.current = null;
    const active = recorder.current;
    if (active && active.state !== "inactive") {
      recorder.current = null;
      discardRecognition(active);
      releaseWaveform();
      setPhase("idle");
      focusField();
      return;
    }
    recorder.current = null;
    // Only this composer's own capture: a discarded recognizer keeps its stream until it ends.
    releaseStream(active?.stream ?? null);
    setPhase("idle");
    focusField();
  }, [conversation, focusField, releaseStream, releaseWaveform]);

  useEffect(() => {
    if (!active && capturePhase !== "idle" && !voiceSend) cancel();
  }, [active, capturePhase, voiceSend, cancel]);

  useEffect(() => {
    if (phase === "idle" || (!active && !voiceSend)) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented ||
          document.querySelector('[role="dialog"], [role="menu"], [aria-haspopup][aria-expanded="true"], .overlay')) return;
      event.preventDefault();
      // A submitted voice message keeps sending offscreen; Escape must not abort its browser request.
      if (visible.current) cancel();
    };
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [active, phase, voiceSend, cancel]);

  const toggleMic = useCallback(() => {
    if (phase === "listening") stop();
    else if (phase === "idle") void start();
  }, [phase, start, stop]);

  /** Recording shortcuts work from anywhere in the composer, the field or a control. */
  const onComposerKeyDown = (event: ReactKeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && (event.key === "m" || event.key === "M")) {
      event.preventDefault();
      toggleMic();
    } else if (event.key === "Escape" && phase !== "idle") {
      event.preventDefault();
      cancel();
    } else if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && phase === "listening") {
      event.preventDefault();
      stop(true);
    }
  };

  /** Enter in the field sends; Shift+Enter is the newline the field keeps. */
  const onFieldKeyDown = (event: ReactKeyboardEvent) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (phase === "idle") void submit(value);
  };

  // ---- what is on screen ----------------------------------------------------------------------------
  const listening = phase === "listening" || phase === "starting";
  const transcribing = phase === "transcribing";
  const remaining = MAX_RECORDING_MS - elapsed;
  const canSend = !disabled && !sendDisabled && !admission && !images.checking && (!images.selected.length || images.capability?.available) && (phase === "listening" || (phase === "idle" && (value.trim().length > 0 || images.selected.length > 0)));
  const micShown = !unavailable;
  const micDisabled = disabled || transcribing || Boolean(admission);

  let hintText: ReactNode = hint ?? null;
  let routineHint = false;
  let hintTone: "muted" | "danger" = "muted";
  let hintRole: "alert" | "status" | undefined;
  // A voice Send keeps its capture after this composer remounts; otherwise the capture is this composer's own.
  const shown = voiceSend?.capture ?? recorder.current;
  const connection = shown instanceof HostCapture ? shown.connection : "ok";
  if (transcribing) {
    hintRole = "status";
    hintText = connection === "lost" ? "Waiting for connection…" : connection === "catching-up" ? "Catching up…" : "Transcribing…";
  } else if (listening) {
    hintRole = "status";
    hintText = phase === "starting" ? startingVoice ? "Starting voice…" : "Opening microphone…"
      : connection === "lost" ? "Connection lost — still recording. Your words will catch up."
      : connection === "catching-up" ? "Catching up…" : "Listening… Stop to add text, or Send.";
  } else if (admission === "uncertain") {
    hintTone = "danger";
    hintRole = "alert";
    hintText = <>Could not confirm send. <button type="button" className="link" onClick={() => { const retry = retryImage.current; if (retry) void submit(retry.text, retry.submission); }}>Retry</button></>;
  } else if (admission === "sending") {
    hintRole = "status";
    hintText = "Sending images…";
  } else if (sendFailure === "transcription" || sendFailure === "unreached") {
    hintTone = "danger";
    hintRole = "alert";
    hintText = sendFailure === "unreached" ? UNREACHED : "Could not transcribe. Typing works.";
  } else if (sendFailure === "refused" && !images.error) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = (
      <>
        Not sent. {refusalReason}{" "}
        <button type="button" className="link" onClick={() => void submit(draft.current)}>
          Retry
        </button>
      </>
    );
  } else if (sendFailure === "unconfirmed") {
    hintTone = "danger";
    hintRole = "alert";
    hintText = "Could not confirm delivery. Check the conversation before sending again.";
  } else if (images.error || (images.selected.length && !images.capability?.available)) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = images.error || `Image input unavailable. ${images.capability?.reason ?? "Checking image input…"}`;
  } else if (voiceFailure) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = voiceFailure;
  } else if (denied) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = "Microphone blocked in the browser. Typing works.";
  } else if (unpunctuated) {
    hintRole = "status";
    hintText = unpunctuated === "loading"
      ? "Added without punctuation: still loading. Next time it will be ready."
      : "Added without punctuation: this browser could not run it.";
  } else if (unavailable === "insecure") {
    hintText = "Voice needs HTTPS";
  } else if (unavailable === "unrecognized") {
    hintText = "This browser has no speech recognition. Typing works.";
  } else if (voice?.backend === "host" && (unavailable === "host" || setupNeeded)) {
    hintRole = setupNeeded ? "status" : undefined;
    hintText = hostVoiceHint(voice);
  } else {
    routineHint = true;
    if (busy) {
      hintRole = "status";
      hintText = "L3 is mid-turn · runs next";
    }
  }
  if (recoveryUnavailable) {
    hintRole = "alert";
    hintTone = "danger";
    hintText = <>{hintText} Recovery could not be updated. Keep this tab open to retain your latest text.</>;
  }

  const feedback = hintText ? (
    <p id={hintId} className={`composer-hint ${hintTone === "danger" ? "text-danger" : "text-muted"}`} data-routine={routineHint || undefined} role={hintRole}>
      {phase !== "idle" ? <span className="spinner" aria-hidden="true" /> : null}{hintText}
    </p>
  ) : null;

  return (
    <div className="composer" data-phase={phase} data-busy={busy || undefined} onKeyDown={onComposerKeyDown}
      onPaste={(event) => {
        const files = Array.from(event.clipboardData.items).filter((item) => item.kind === "file" && item.type.startsWith("image/")).map((item) => item.getAsFile()).filter((file): file is File => file !== null);
        if (!imageScope || !files.length) return;
        event.preventDefault();
        if (!disabled && !admission && phase === "idle") void images.add(files);
      }}>
      <div className="composer-box">
        {phase !== "idle" ? feedback : null}
        {images.selected.length && !admission ? <div className="image-draft" aria-label="Selected images">{images.selected.map((image) =>
          <div className="image-draft-item" key={image.key}>
            <img src={image.url} alt={image.name} />
            <button className="image-remove" type="button" aria-label={`Remove image ${image.name}`} disabled={disabled || phase !== "idle"} onClick={() => images.remove(image.key)}><CloseIcon /></button>
          </div>)}</div> : null}
        <textarea
          ref={field}
          className="composer-field"
          aria-label={ariaLabel}
          aria-describedby={hintText ? hintId : undefined}
          placeholder={listening ? "" : placeholder}
          value={displayedDraft}
          rows={1}
          disabled={disabled || Boolean(admission)}
          readOnly={phase !== "idle"}
          autoFocus={autoFocus}
          onChange={(event) => editDraft(event.target.value)}
          onKeyDown={onFieldKeyDown}
          onSelect={(event) => { if (selection) selection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }}
          onBlur={(event) => { if (selection) selection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }}
        />
        <div className="composer-row">
          {pill ? <fieldset role="presentation" className="composer-pill" disabled={disabled || Boolean(admission)}>{pill}</fieldset> : null}
          {imageScope && !listening && !transcribing ? <>
            <input ref={picker} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden aria-label="Choose images" onChange={(event) => {
              if (!disabled && !admission) void images.add(Array.from(event.target.files ?? []));
              event.target.value = "";
            }} />
            <button type="button" className="composer-icon composer-add-image" aria-label="Add images" title={IMAGE_HELP} disabled={disabled || Boolean(admission) || images.checking} onClick={() => {
              if (!images.capability?.available) images.setError(`Image input unavailable. ${images.capability?.reason ?? "Checking image input…"}`);
              else picker.current?.click();
            }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4" /><circle cx="15" cy="8" r="1.5" /></svg></button>
          </> : null}
          {listening || transcribing ? (
            <div className="composer-voice" data-frozen={transcribing || undefined}>
              <button type="button" className="composer-icon composer-cancel" aria-label="Cancel voice input" onClick={() => cancel("mic")}>
                <CloseIcon />
              </button>
              <canvas ref={canvas} className="composer-wave" aria-hidden />
              <span
                className="composer-timer"
                data-danger={listening && remaining <= LAST_MINUTE_MS ? "" : undefined}
                aria-label={listening ? "Recording time" : undefined}
              >
                {formatTimer(elapsed)}
              </span>
            </div>
          ) : (
            <span className="composer-spacer" />
          )}
          {micShown ? (
            <button
              type="button"
              ref={mic}
              className={`composer-icon composer-mic${listening ? " composer-stop" : ""}`}
              aria-label={listening ? "Stop voice input" : "Start voice input"}
              aria-keyshortcuts="Control+M Meta+M"
              disabled={micDisabled && !listening}
              onClick={toggleMic}
            >
              {listening ? <StopIcon /> : <MicIcon />}
            </button>
          ) : null}
          <button
            type="button"
            className="composer-icon composer-send"
            aria-label={busy ? "Queue" : "Send"}
            disabled={!canSend}
            // Pressing Send leaves focus in the field, so a phone keyboard stays open instead of closing
            // and reopening with the bottom navigation around every message.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => phase === "listening" ? stop(true) : void submit(value)}
          >
            <SendIcon />
          </button>
        </div>
      </div>
      {phase === "idle" ? feedback : null}
    </div>
  );
}
