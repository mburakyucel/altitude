import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { transcribeVoice } from "../data/api";

// Leave five seconds below the server's decoded-audio cap for timer delay and AAC/container padding.
const MAX_RECORDING_MS = 115_000;
const MAX_UPLOAD_BYTES = 12 << 20;

type VoicePhase = "idle" | "starting" | "recording" | "stopping" | "transcribing" | "review" | "error";

interface VoiceComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (text: string) => void | Promise<void>;
  ariaLabel: string;
  placeholder: string;
  submitLabel?: string;
  submitting?: boolean;
  disabled?: boolean;
  rows?: number;
  autoFocus?: boolean;
  className?: string;
  actions?: ReactNode;
}

/** Append dictated text as normal prose without altering any existing draft characters. */
export function combineDraft(draft: string, transcript: string): string {
  const spoken = transcript.trim();
  if (!spoken) return draft;
  if (!draft) return spoken;
  return `${draft}${/\s$/.test(draft) ? "" : " "}${spoken}`;
}

function unavailableReason(): string | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return "Voice input is not supported in this browser. You can keep typing.";
  }
  if (window.isSecureContext === false) {
    return "Voice input needs HTTPS. Open Altitude's secure address, or keep typing.";
  }
  if (
    typeof MediaRecorder === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    return "Voice input is not supported in this browser. You can keep typing.";
  }
  return null;
}

function recordingMimeType(): string {
  const choices = [
    "audio/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  return choices.find((type) => {
    try {
      return MediaRecorder.isTypeSupported(type);
    } catch {
      return false;
    }
  }) ?? "";
}

function audioSession(type: "play-and-record" | "playback") {
  try {
    const session = (navigator as Navigator & { audioSession?: { type: string } }).audioSession;
    if (session) session.type = type;
  } catch {
    // Optional iOS hint only; capture still works without it.
  }
}

function MicIcon({ stop = false }: { stop?: boolean }) {
  return stop ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7" />
    </svg>
  );
}

export default function VoiceComposer({
  value,
  onChange,
  onSubmit,
  ariaLabel,
  placeholder,
  submitLabel = "Send",
  submitting = false,
  disabled = false,
  rows = 3,
  autoFocus = false,
  className = "",
  actions,
}: VoiceComposerProps) {
  const statusId = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const editAction = useRef<HTMLButtonElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const cancelled = useRef(false);
  const recorderFailure = useRef("");
  const stopRequested = useRef(false);
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const startedAt = useRef(0);

  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [transcript, setTranscript] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [completing, setCompleting] = useState(false);
  const unavailable = unavailableReason();

  const clearStopTimer = useCallback(() => {
    if (stopTimer.current) clearTimeout(stopTimer.current);
    stopTimer.current = null;
  }, []);

  const releaseStream = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    audioSession("playback");
  }, []);

  const focusField = useCallback((position?: number) => {
    queueMicrotask(() => {
      field.current?.focus();
      if (position != null) field.current?.setSelectionRange(position, position);
    });
  }, []);

  const finishRecording = useCallback(
    async (finished: MediaRecorder) => {
      if (recorder.current !== finished) return;
      recorder.current = null;
      stopRequested.current = false;
      clearStopTimer();
      releaseStream();
      const parts = chunks.current;
      chunks.current = [];

      if (cancelled.current) {
        if (mounted.current) {
          setPhase("idle");
          setNotice("Voice input canceled. Your draft is unchanged.");
          focusField();
        }
        return;
      }
      if (recorderFailure.current) {
        if (mounted.current) {
          setPhase("error");
          setError(recorderFailure.current);
          focusField();
        }
        return;
      }

      const type = finished.mimeType || recordingMimeType() || "application/octet-stream";
      const audio = parts.length ? new Blob(parts, { type }) : null;
      if (!audio?.size) {
        if (mounted.current) {
          setPhase("error");
          setError("The recorder did not capture audio. Your draft is unchanged; try again.");
          focusField();
        }
        return;
      }
      if (audio.size > MAX_UPLOAD_BYTES) {
        if (mounted.current) {
          setPhase("error");
          setError("That recording is too large. Your draft is unchanged; try a shorter clip.");
          focusField();
        }
        return;
      }

      const request = new AbortController();
      abort.current = request;
      setPhase("transcribing");
      setNotice("Transcribing your recording…");
      try {
        const text = await transcribeVoice(audio, request.signal);
        if (!mounted.current || request.signal.aborted) return;
        setTranscript(text);
        setPhase("review");
        setNotice("Transcript ready. Review it before inserting or sending.");
      } catch (cause) {
        if (!mounted.current || request.signal.aborted) return;
        setPhase("error");
        setError(cause instanceof Error ? cause.message : "Transcription failed. Your draft is unchanged.");
        focusField();
      } finally {
        if (abort.current === request) abort.current = null;
      }
    },
    [clearStopTimer, focusField, releaseStream],
  );

  const stopRecording = useCallback(() => {
    const active = recorder.current;
    if (!active || stopRequested.current) return;
    stopRequested.current = true;
    clearStopTimer();
    setPhase("stopping");
    setNotice("Recording stopped. Preparing the transcript…");
    try {
      if (active.state !== "inactive") {
        active.stop();
        return;
      }
      // Browser stop/data events are queued. Give them a turn before recovering an already-inactive recorder.
      setTimeout(() => void finishRecording(active), 0);
    } catch {
      recorderFailure.current = "Recording stopped unexpectedly. Your draft is unchanged; try again.";
      void finishRecording(active);
    }
  }, [clearStopTimer, finishRecording]);

  const startRecording = useCallback(async () => {
    if (unavailable) {
      setPhase("error");
      setError(unavailable);
      focusField();
      return;
    }
    setError("");
    setTranscript("");
    setElapsed(0);
    setPhase("starting");
    setNotice("Waiting for microphone access…");
    cancelled.current = false;
    recorderFailure.current = "";
    stopRequested.current = false;
    chunks.current = [];
    audioSession("play-and-record");
    try {
      const opened = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || cancelled.current) {
        opened.getTracks().forEach((track) => track.stop());
        audioSession("playback");
        return;
      }
      stream.current = opened;
      const mimeType = recordingMimeType();
      const active = new MediaRecorder(opened, mimeType ? { mimeType } : undefined);
      recorder.current = active;
      active.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      active.onstop = () => void finishRecording(active);
      active.onerror = () => {
        recorderFailure.current = "Recording stopped unexpectedly. Your draft is unchanged; try again.";
        stopRecording();
      };
      active.start();
      startedAt.current = Date.now();
      setPhase("recording");
      setNotice("Recording… press Stop when you finish.");
      stopTimer.current = setTimeout(() => {
        setNotice("Recording limit reached. Preparing the transcript…");
        stopRecording();
      }, MAX_RECORDING_MS);
    } catch (cause) {
      releaseStream();
      if (!mounted.current || cancelled.current) return;
      const name = cause instanceof DOMException ? cause.name : "";
      const denied = name === "NotAllowedError" || name === "SecurityError";
      setPhase("error");
      setError(
        denied
          ? "Microphone access is blocked. Allow it for Altitude in Safari settings, then try again."
          : "The microphone could not be opened. Your draft is unchanged; try again or keep typing.",
      );
      focusField();
    }
  }, [finishRecording, focusField, releaseStream, stopRecording, unavailable]);

  const cancelVoice = useCallback(() => {
    cancelled.current = true;
    clearStopTimer();
    abort.current?.abort();
    abort.current = null;
    const active = recorder.current;
    if (active) {
      setPhase("stopping");
      setNotice("Canceling voice input…");
      if (stopRequested.current) return;
      stopRequested.current = true;
      try {
        if (active.state !== "inactive") active.stop();
        else setTimeout(() => void finishRecording(active), 0);
        return;
      } catch {
        // Release below when a broken recorder cannot emit its stop event.
      }
    }
    recorder.current = null;
    stopRequested.current = false;
    chunks.current = [];
    releaseStream();
    setPhase("idle");
    setNotice("Voice input canceled. Your draft is unchanged.");
    focusField();
  }, [clearStopTimer, finishRecording, focusField, releaseStream]);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = setInterval(() => setElapsed(Date.now() - startedAt.current), 1_000);
    return () => clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    if (phase === "review") editAction.current?.focus();
  }, [phase]);

  useEffect(() => {
    // React StrictMode mounts this effect twice in development; re-arm before its second pass.
    mounted.current = true;
    return () => {
      mounted.current = false;
      cancelled.current = true;
      clearStopTimer();
      abort.current?.abort();
      const active = recorder.current;
      if (active) active.onstop = null;
      if (active && active.state !== "inactive") {
        try {
          active.stop();
        } catch {
          // Recorder is already gone.
        }
      }
      releaseStream();
    };
  }, [clearStopTimer, releaseStream]);

  const insertTranscript = () => {
    const combined = combineDraft(value, transcript);
    onChange(combined);
    setError("");
    setTranscript("");
    setPhase("idle");
    setNotice("Transcript inserted. You can edit it before sending.");
    focusField(combined.length);
  };

  const submit = async (text: string, fromVoice: boolean) => {
    const ready = text.trim();
    if (!ready || completing || submitting || disabled) return;
    setCompleting(true);
    setError("");
    try {
      await onSubmit(ready);
      if (fromVoice && mounted.current) {
        setError("");
        setTranscript("");
        setPhase("idle");
        setNotice("");
        focusField();
      }
    } catch {
      if (mounted.current) {
        setError(
          fromVoice
            ? "The message was not sent. Your transcript is still here."
            : "The message was not sent. Your draft is unchanged.",
        );
      }
    } finally {
      if (mounted.current) setCompleting(false);
    }
  };

  const voiceBusy = phase === "starting" || phase === "recording" || phase === "stopping" || phase === "transcribing";
  const status = error || notice || unavailable || "";
  const micLabel = unavailable
    ? "Voice input unavailable"
    : phase === "review"
      ? "Voice transcript awaiting review"
      : phase === "recording"
        ? "Stop voice recording"
        : phase === "starting"
          ? "Waiting for microphone access"
          : phase === "stopping"
            ? "Preparing voice recording"
            : phase === "transcribing"
              ? "Transcribing voice recording"
              : "Start voice recording";

  return (
    <form
      className={`voice-composer ${className}`.trim()}
      onSubmit={(event) => {
        event.preventDefault();
        if (phase !== "review" && !voiceBusy) void submit(value, false);
      }}
    >
      <div className="voice-composer-field">
        <textarea
          ref={field}
          className="voice-composer-textarea field"
          aria-label={ariaLabel}
          aria-describedby={status ? statusId : undefined}
          placeholder={placeholder}
          rows={rows}
          value={value}
          autoFocus={autoFocus}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.metaKey || event.ctrlKey) &&
              phase !== "review" &&
              !voiceBusy
            ) {
              event.preventDefault();
              void submit(value, false);
            }
          }}
        />
        <button
          type="button"
          className="voice-mic"
          data-recording={phase === "recording"}
          aria-label={micLabel}
          aria-describedby={status ? statusId : undefined}
          aria-disabled={Boolean(unavailable) || phase === "starting" || phase === "stopping" || phase === "transcribing" || phase === "review" || disabled}
          disabled={submitting || completing || disabled || phase === "starting" || phase === "stopping" || phase === "transcribing" || phase === "review"}
          title={unavailable ?? micLabel}
          onClick={() => {
            if (unavailable) {
              setPhase("error");
              setError(unavailable);
            } else if (phase === "recording") {
              stopRecording();
            } else {
              void startRecording();
            }
          }}
        >
          <MicIcon stop={phase === "recording"} />
        </button>
      </div>

      {status ? (
        <div className="voice-status-row">
          <p
            id={statusId}
            className={`${error ? "voice-status text-danger" : "voice-status text-muted"}${phase === "review" && !error ? " voice-status-review" : ""}`}
            role={error ? "alert" : "status"}
            aria-live={error ? "assertive" : "polite"}
          >
            {phase === "recording" ? <span className="voice-recording-dot" aria-hidden="true" /> : null}
            {status}
          </p>
          {phase === "recording" ? (
            <span className="voice-timer text-meta text-muted" aria-hidden="true">
              {Math.floor(elapsed / 60_000)}:{String(Math.floor(elapsed / 1_000) % 60).padStart(2, "0")}
            </span>
          ) : null}
          {voiceBusy ? (
            <button type="button" className="btn btn-ghost voice-cancel" onClick={cancelVoice}>
              Cancel voice input
            </button>
          ) : null}
        </div>
      ) : null}

      {phase === "review" ? (
        <section className="voice-review" aria-label="Voice transcript review">
          <p className="label">Transcript</p>
          <p className="voice-transcript" tabIndex={0} aria-label="Voice transcript">
            {transcript}
          </p>
          <div className="voice-review-actions">
            <button ref={editAction} type="button" className="btn" onClick={insertTranscript}>
              Edit / insert
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={completing || submitting || disabled}
              onClick={() => void submit(combineDraft(value, transcript), true)}
            >
              {submitLabel}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setError("");
                setTranscript("");
                setPhase("idle");
                setNotice("Transcript discarded. Your draft is unchanged.");
                focusField();
              }}
            >
              Discard transcript
            </button>
          </div>
        </section>
      ) : null}

      {phase !== "review" ? (
        <div className="voice-composer-actions">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || completing || disabled || voiceBusy || value.trim().length === 0}
          >
            {submitting || completing ? "Sending…" : submitLabel}
          </button>
          {actions}
        </div>
      ) : null}
    </form>
  );
}
