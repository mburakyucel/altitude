import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { ApiError, transcribeVoice } from "../data/api";

/*
 * The one composer (SPEC.md §3.6): project chat and task conversation. The page owns
 * the draft and the send; the composer owns the states in the §3.6 table: Idle, Typing, Sending (the
 * page's bubble at 60%), Busy (the arrow queues), Listening, Transcribing, Landed (the transcript is
 * appended to the draft and nothing else appears, issue #195), Denied, Unavailable, and a refused
 * send ("Not sent. Retry."). Voice is capped at ten minutes; audio never becomes state anywhere.
 */

/** The server decodes at most ten minutes; five seconds under it absorbs timer delay and container padding. */
export const MAX_RECORDING_MS = 595_000;
const LAST_MINUTE_MS = 60_000;
const MAX_UPLOAD_BYTES = 16 << 20;
/** A transcription that takes longer than this is a failure the hint reports; typing still works. */
const TRANSCRIBE_TIMEOUT_MS = 60_000;
const WAVE_BARS = 28;

type Phase = "idle" | "starting" | "listening" | "transcribing";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  /** Resolve accepted sends. Explicit HTTP refusal restores the draft with Retry; an uncertain
   * transport/server failure restores it with a reminder to check the conversation first. */
  onSubmit: (text: string) => void | Promise<void>;
  placeholder: string;
  ariaLabel: string;
  /** L3 is mid-turn: the arrow queues; desktop also explains that the message runs next. */
  busy?: boolean;
  /** The desktop hint under the field when no state claims it (12px muted). */
  hint?: ReactNode;
  /** The desktop pill: the engine pin on L3 chat; phone uses project details. */
  pill?: ReactNode;
  disabled?: boolean;
  autoFocus?: boolean;
}

/** Append dictated text as normal prose without altering any existing draft characters. */
export function combineDraft(draft: string, transcript: string): string {
  const spoken = transcript.trim();
  if (!spoken) return draft;
  if (!draft) return spoken;
  return `${draft}${/\s$/.test(draft) ? "" : " "}${spoken}`;
}

/** Why voice is unavailable: "insecure" (the hint says so), "unsupported" (the mic simply hides), or null. */
export function voiceUnavailable(): "insecure" | "unsupported" | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return "unsupported";
  if (window.isSecureContext === false) return "insecure";
  if (typeof MediaRecorder === "undefined" || typeof navigator.mediaDevices?.getUserMedia !== "function") {
    return "unsupported";
  }
  return null;
}

function recordingMimeType(): string {
  const choices = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  return (
    choices.find((type) => {
      try {
        return MediaRecorder.isTypeSupported(type);
      } catch {
        return false;
      }
    }) ?? ""
  );
}

function audioSession(type: "play-and-record" | "playback") {
  try {
    const session = (navigator as Navigator & { audioSession?: { type: string } }).audioSession;
    if (session) session.type = type;
  } catch {
    // Optional iOS hint only; capture still works without it.
  }
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
 * The live waveform while listening: the recent loudness as bars, drawn from an AnalyserNode. It
 * freezes (the loop stops, the last frame stays) while transcribing. Where the page has no audio
 * graph (a test runtime), the canvas simply stays blank.
 */
function useWaveform(stream: MediaStream | null, running: boolean) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const levels = useRef<number[]>(new Array<number>(WAVE_BARS).fill(0));

  useEffect(() => {
    if (!stream || !running || typeof AudioContext === "undefined" || !canvas.current) return;
    let frame = 0;
    let context: AudioContext | null = null;
    let analyser: AnalyserNode | null = null;
    try {
      context = new AudioContext();
      analyser = context.createAnalyser();
      analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser);
    } catch {
      return;
    }
    const data = new Uint8Array(analyser.fftSize);
    const node = canvas.current;
    const draw = () => {
      if (!analyser || !node) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const sample of data) {
        const v = (sample - 128) / 128;
        sum += v * v;
      }
      const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
      levels.current = [...levels.current.slice(1), level];
      const ctx = node.getContext("2d");
      if (ctx) {
        const width = node.width;
        const height = node.height;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = getComputedStyle(node).color;
        const gap = width / WAVE_BARS;
        levels.current.forEach((value, index) => {
          const bar = Math.max(3, value * height);
          ctx.fillRect(index * gap + gap * 0.25, (height - bar) / 2, gap * 0.5, bar);
        });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      void context?.close().catch(() => undefined);
    };
  }, [stream, running]);

  return canvas;
}

export default function Composer({
  value,
  onChange,
  onSubmit,
  placeholder,
  ariaLabel,
  busy = false,
  hint,
  pill,
  disabled = false,
  autoFocus = false,
}: ComposerProps) {
  const hintId = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const cancelled = useRef(false);
  const stopRequested = useRef(false);
  const sendAfterTranscribing = useRef(false);
  const capTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const startedAt = useRef(0);
  const draft = useRef(value);
  draft.current = value;

  const [phase, setPhase] = useState<Phase>("idle");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [denied, setDenied] = useState(false);
  const [voiceFailure, setVoiceFailure] = useState("");
  const [sendFailure, setSendFailure] = useState<"refused" | "unconfirmed" | null>(null);
  const unavailable = voiceUnavailable();
  const canvas = useWaveform(stream, phase === "listening");

  useLayoutEffect(() => {
    const node = field.current;
    if (!node) return;
    const sizeField = () => {
      node.style.height = "auto";
      node.style.height = `${node.scrollHeight}px`;
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
  }, [value, phase]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (capTimer.current) clearTimeout(capTimer.current);
      abort.current?.abort();
      const active = recorder.current;
      recorder.current = null;
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
    queueMicrotask(() => {
      const node = field.current;
      if (!node) return;
      node.focus({ preventScroll: true });
      if (position != null) node.setSelectionRange(position, position);
    });
  }, []);

  const releaseStream = useCallback((released: MediaStream | null) => {
    released?.getTracks().forEach((track) => track.stop());
    setStream(null);
    audioSession("playback");
  }, []);

  // ---- send: the draft becomes the page's bubble at once; a refusal brings it back ----------------
  const submit = useCallback(
    async (text: string) => {
      const ready = text.trim();
      if (!ready || disabled) return;
      setSendFailure(null);
      setVoiceFailure("");
      draft.current = "";
      onChange("");
      try {
        await onSubmit(ready);
        focusField();
      } catch (error) {
        if (!mounted.current) return;
        const recovered = [text, draft.current].filter(Boolean).join("\n");
        draft.current = recovered;
        onChange(recovered);
        setSendFailure((current) => current === "unconfirmed" || !(error instanceof ApiError && error.status < 500)
          ? "unconfirmed" : "refused");
        focusField(recovered.length);
      }
    },
    [disabled, focusField, onChange, onSubmit],
  );
  // A recording can outlive the render that supplied its submit callback or disabled state.
  const currentSubmit = useRef(submit);
  currentSubmit.current = submit;

  // ---- voice: listening, transcribing, landed; every failure is one hint and an unchanged draft ----
  const finish = useCallback(
    async (finished: MediaRecorder, used: MediaStream | null) => {
      if (recorder.current !== finished) return;
      recorder.current = null;
      stopRequested.current = false;
      if (capTimer.current) clearTimeout(capTimer.current);
      capTimer.current = null;
      releaseStream(used);
      const parts = chunks.current;
      chunks.current = [];
      if (!mounted.current) return;
      if (cancelled.current) {
        setPhase("idle");
        focusField();
        return;
      }
      const type = finished.mimeType || recordingMimeType() || "application/octet-stream";
      const audio = parts.length ? new Blob(parts, { type }) : null;
      if (!audio?.size || audio.size > MAX_UPLOAD_BYTES) {
        setPhase("idle");
        setVoiceFailure("Could not transcribe. Typing works.");
        focusField();
        return;
      }
      const request = new AbortController();
      abort.current = request;
      const timeout = setTimeout(() => request.abort(), TRANSCRIBE_TIMEOUT_MS);
      setPhase("transcribing");
      try {
        const text = await transcribeVoice(audio, request.signal);
        if (!mounted.current || cancelled.current) return;
        // Landed: appended to the draft, cursor at the end, nothing else on screen (issue #195).
        const next = combineDraft(draft.current, text);
        onChange(next);
        setPhase("idle");
        if (sendAfterTranscribing.current && text.trim()) void currentSubmit.current(next);
        else focusField(next.length);
      } catch {
        if (!mounted.current || cancelled.current) return;
        setPhase("idle");
        setVoiceFailure("Could not transcribe. Typing works.");
        focusField();
      } finally {
        clearTimeout(timeout);
        if (abort.current === request) abort.current = null;
      }
    },
    [focusField, onChange, releaseStream],
  );

  const stop = useCallback((send = false) => {
    const active = recorder.current;
    if (!active || stopRequested.current) return;
    stopRequested.current = true;
    sendAfterTranscribing.current = send;
    setPhase("transcribing");
    try {
      if (active.state !== "inactive") active.stop();
      else void finish(active, stream);
    } catch {
      void finish(active, stream);
    }
  }, [finish, stream]);

  const start = useCallback(async () => {
    if (unavailable || denied || disabled || phase !== "idle") return;
    setVoiceFailure("");
    setSendFailure((current) => current === "unconfirmed" ? current : null);
    setElapsed(0);
    cancelled.current = false;
    stopRequested.current = false;
    sendAfterTranscribing.current = false;
    chunks.current = [];
    setPhase("starting");
    audioSession("play-and-record");
    let opened: MediaStream | null = null;
    try {
      opened = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || cancelled.current) {
        releaseStream(opened);
        return;
      }
      const mimeType = recordingMimeType();
      const active = new MediaRecorder(opened, mimeType ? { mimeType } : undefined);
      const used = opened;
      recorder.current = active;
      active.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      active.onstop = () => void finish(active, used);
      active.onerror = () => {
        cancelled.current = false;
        chunks.current = [];
        try {
          if (active.state !== "inactive") active.stop();
          else void finish(active, used);
        } catch {
          void finish(active, used);
        }
      };
      active.start();
      startedAt.current = Date.now();
      setStream(opened);
      setPhase("listening");
      capTimer.current = setTimeout(() => {
        try {
          if (active.state !== "inactive") active.stop();
        } catch {
          void finish(active, used);
        }
      }, MAX_RECORDING_MS);
    } catch (cause) {
      releaseStream(opened);
      if (!mounted.current) return;
      const name = cause instanceof DOMException ? cause.name : "";
      setPhase("idle");
      if (name === "NotAllowedError" || name === "SecurityError") setDenied(true);
      else setVoiceFailure("Could not open the microphone. Typing works.");
      focusField();
    }
  }, [denied, disabled, finish, focusField, phase, releaseStream, unavailable]);

  /** Esc while listening: back to the previous state, nothing added (SPEC.md §3.6). */
  const cancel = useCallback(() => {
    cancelled.current = true;
    if (capTimer.current) clearTimeout(capTimer.current);
    capTimer.current = null;
    abort.current?.abort();
    abort.current = null;
    const active = recorder.current;
    if (active) {
      try {
        if (active.state !== "inactive") {
          active.stop();
          return;
        }
      } catch {
        // Release below when a broken recorder cannot emit its stop event.
      }
      recorder.current = null;
    }
    chunks.current = [];
    releaseStream(stream);
    setPhase("idle");
    focusField();
  }, [focusField, releaseStream, stream]);

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
  const canSend = !disabled && (phase === "listening" || (phase === "idle" && value.trim().length > 0));
  const micShown = !unavailable;
  const micDisabled = denied || disabled || transcribing;

  let hintText: ReactNode = hint ?? null;
  let routineHint = false;
  let hintTone: "muted" | "danger" = "muted";
  let hintRole: "alert" | "status" | undefined;
  if (sendFailure === "refused") {
    hintTone = "danger";
    hintRole = "alert";
    hintText = (
      <>
        Not sent.{" "}
        <button type="button" className="link" onClick={() => void submit(draft.current)}>
          Retry
        </button>
      </>
    );
  } else if (sendFailure === "unconfirmed") {
    hintTone = "danger";
    hintRole = "alert";
    hintText = "Could not confirm delivery. Check the conversation before sending again.";
  } else if (transcribing) {
    hintRole = "status";
    hintText = "Transcribing…";
  } else if (listening) {
    hintRole = "status";
    hintText = phase === "starting" ? "Opening microphone…" : "Listening… Stop to add text, or Send.";
  } else if (voiceFailure) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = voiceFailure;
  } else if (denied) {
    hintTone = "danger";
    hintRole = "alert";
    hintText = "Microphone blocked in the browser. Typing works.";
  } else if (unavailable === "insecure") {
    hintText = "Voice needs HTTPS";
  } else {
    routineHint = true;
    if (busy) {
      hintRole = "status";
      hintText = "L3 is mid-turn · runs next";
    }
  }

  return (
    <div className="composer" data-phase={phase} data-busy={busy || undefined} onKeyDown={onComposerKeyDown}>
      <div className="composer-box">
        <textarea
          ref={field}
          className="composer-field"
          aria-label={ariaLabel}
          aria-describedby={hintText ? hintId : undefined}
          placeholder={listening ? "" : placeholder}
          value={value}
          rows={1}
          disabled={disabled}
          autoFocus={autoFocus}
          onChange={(event) => {
            onChange(event.target.value);
            if (sendFailure === "refused") setSendFailure(null);
          }}
          onKeyDown={onFieldKeyDown}
        />
        <div className="composer-row">
          {pill ? <div className="composer-pill">{pill}</div> : null}
          {listening || transcribing ? (
            <div className="composer-voice" data-frozen={transcribing || undefined}>
              {listening ? (
                <button type="button" className="composer-icon composer-cancel" aria-label="Cancel voice input" onClick={cancel}>
                  <CloseIcon />
                </button>
              ) : null}
              <canvas ref={canvas} className="composer-wave" width={140} height={24} aria-hidden />
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
            onClick={() => phase === "listening" ? stop(true) : void submit(value)}
          >
            <SendIcon />
          </button>
        </div>
      </div>
      {hintText ? (
        <p id={hintId} className={`composer-hint ${hintTone === "danger" ? "text-danger" : "text-muted"}`} data-routine={routineHint || undefined} role={hintRole}>
          {hintText}
        </p>
      ) : null}
    </div>
  );
}
