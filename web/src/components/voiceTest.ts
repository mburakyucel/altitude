import { act } from "@testing-library/react";
import { vi } from "vitest";
import { presetVoiceBackend } from "./voiceBackend";
import type { HostVoice, VoiceBackend } from "../data/api";
import { HostCapture } from "./hostCapture";
import type { Punctuator } from "../punctuation";

/**
 * The punctuation model for tests (vitest.setup.ts mocks `../punctuation` with it): words pass through
 * unchanged unless a test installs `sentence` or its own `punctuate`, or replaces `load` to hold or
 * refuse the model.
 */
export const punctuationFixture = {
  load: (): Promise<Punctuator> => Promise.resolve({ punctuate: async (words) => punctuationFixture.punctuate(words) }),
  punctuate: (words: readonly string[]): string[] => [...words],
  reset() {
    punctuationFixture.load = () => Promise.resolve({ punctuate: async (words) => punctuationFixture.punctuate(words) });
    punctuationFixture.punctuate = (words) => [...words];
  },
};

/** A stand-in model: each request is one sentence, capitalized and ending in a period. */
export function sentence(words: readonly string[]): string[] {
  return words.map((word, index) => {
    const cased = index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word.toLowerCase();
    return index === words.length - 1 ? `${cased.replace(/[.,?]$/, "")}.` : cased.replace(/[.,?]$/, "");
  });
}

type FakeResult = { isFinal: boolean; 0: { transcript: string }; length: 1 };

/** The browser's speech recognizer for tests: the test hands it phrases, silence and errors. */
export class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = [];
  continuous = false;
  interimResults = false;
  lang = "";
  onresult: ((event: { results: FakeResult[] }) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  started = 0;
  stopped = 0;
  aborted = 0;
  running = false;
  /** false: Stop never gets its `end` event, as a recognizer waiting on its service. */
  answersStop = true;
  /** false: Cancel's abort gets no `end` event until the test sends one. */
  answersAbort = true;

  constructor() {
    FakeSpeechRecognition.instances.push(this);
  }

  start() {
    if (this.running) throw new Error("already started");
    this.running = true;
    this.started += 1;
  }

  stop() {
    this.stopped += 1;
    this.running = false;
    if (this.answersStop) queueMicrotask(() => this.onend?.());
  }

  abort() {
    this.aborted += 1;
    this.running = false;
    if (this.answersAbort) queueMicrotask(() => this.onend?.());
  }

  /** Deliver the session's results so far: settled phrases and the phrase still changing. */
  hear(finals: string[], interim = "") {
    const results = finals.map((transcript): FakeResult => ({ isFinal: true, 0: { transcript }, length: 1 }));
    if (interim) results.push({ isFinal: false, 0: { transcript: interim }, length: 1 });
    this.onresult?.({ results });
  }

  /** Chrome ends a continuous session after silence without an error. */
  silence() {
    this.running = false;
    this.onend?.();
  }

  fail(error: string) {
    this.onerror?.({ error });
    this.running = false;
    this.onend?.();
  }
}

/** Host voice's microphone in tests: `speak` delivers 16 kHz samples to the capture listening now. */
export const hostMicrophone = {
  deliver: null as ((chunk: Int16Array) => void) | null,
  speak(samples: number) { hostMicrophone.deliver?.(new Int16Array(samples)); },
};

export type HostVoiceCall = { path: string; seq: number; final: boolean; samples: number; selection: string };
type Held = { settle: () => void };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * This computer's speech service for tests: answers `/api/voice/live` with a recording id and each audio
 * chunk with `words` (the final chunk with `final`, or `words` when it is null). `connection` "drop" fails
 * every request as a lost network; "hold" keeps answers until `release()`. `refuse` answers the next start
 * ("live") or audio request with a status and message; `forget()` answers the next audio request 410, as a
 * host that no longer knows the recording. Other paths go to `fallback` (404 by default).
 */
export type HostVoiceOptions = { words?: string; final?: string | null; fallback?: (path: string, init?: RequestInit) => Response | Promise<Response> };

export function hostVoiceServer(options: HostVoiceOptions = {}) {
  const held: Held[] = [];
  let forgets = 0;
  const server = {
    words: options.words ?? "",
    final: options.final ?? null as string | null,
    connection: "ok" as "ok" | "drop" | "hold",
    refuse: null as { on: "live" | "audio"; status: number; error: string } | null,
    calls: [] as HostVoiceCall[],
    opened: 0,
    /** The audio requests, in order. */
    get audio() { return server.calls.filter((call) => call.path.includes("/audio")); },
    forget() { forgets += 1; },
    /** Answer every held request with what the server says now. */
    release() { held.splice(0).forEach((request) => request.settle()); },
    /** The connection returns: held requests are answered, and new ones are answered at once. */
    reconnect() { server.connection = "ok"; server.release(); },
    answer(path: string, init?: RequestInit): Response {
      if (path === "/api/voice/live") {
        if (server.refuse?.on === "live") return json({ error: server.refuse.error }, server.refuse.status);
        server.opened += 1;
        return json({ id: `rec-${server.opened}` });
      }
      if (path.endsWith("/cancel")) return json({ ok: true });
      if (server.refuse?.on === "audio") { const { status, error } = server.refuse; server.refuse = null; return json({ error }, status); }
      if (forgets) { forgets -= 1; return json({ error: "recording expired" }, 410); }
      const final = path.includes("final=1");
      return json(final ? { text: server.final ?? server.words, final: true } : { text: server.words });
    },
    fetch: vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = String(input);
      if (!path.startsWith("/api/voice/live")) return options.fallback ? options.fallback(path, init) : json({ error: "not found" }, 404);
      const query = new URL(path, "http://altitude.test").searchParams;
      const headers = (init?.headers ?? {}) as Record<string, string>;
      if (path.includes("/audio")) {
        server.calls.push({ path, seq: Number(query.get("seq")), final: query.get("final") === "1",
          samples: (init?.body as Int16Array).length, selection: headers["X-Voice-Selection"] ?? "" });
      } else server.calls.push({ path, seq: -1, final: false, samples: 0, selection: headers["X-Voice-Selection"] ?? "" });
      if (path.endsWith("/cancel")) return json({ ok: true });
      if (server.connection === "drop") throw new TypeError("Failed to fetch");
      if (server.connection === "hold") {
        return new Promise<Response>((resolve, reject) => {
          const signal = init?.signal;
          if (signal?.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
          const request: Held = { settle: () => resolve(server.answer(path, init)) };
          signal?.addEventListener("abort", () => {
            const at = held.indexOf(request);
            if (at >= 0) held.splice(at, 1);
            reject(new DOMException("Aborted", "AbortError"));
          });
          held.push(request);
        });
      }
      return server.answer(path, init);
    }),
    /** Serve this page's fetch. */
    install() { vi.stubGlobal("fetch", server.fetch); return server; },
  };
  return server;
}

/** Say `samples` of audio to the capture listening now (inside act, so its words render). */
export function speak(samples = 8000) {
  act(() => hostMicrophone.speak(samples));
}

export function installVoiceBrowser(options: { backend?: VoiceBackend; recognition?: boolean; host?: HostVoice } = {}) {
  const backend = options.backend ?? "host";
  const recognition = options.recognition ?? backend === "browser";
  FakeSpeechRecognition.instances = [];
  const track = Object.assign(new EventTarget(), { stop: vi.fn(), kind: "audio" });
  const mediaStream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  const getUserMedia = vi.fn(async () => mediaStream);
  const voiceNavigator = Object.create(window.navigator) as Navigator;
  Object.defineProperty(voiceNavigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  // jsdom's Navigator getters refuse a derived object; the recognizer reads the language.
  Object.defineProperty(voiceNavigator, "language", { configurable: true, value: "en-US" });
  vi.stubGlobal("navigator", voiceNavigator);
  vi.stubGlobal("isSecureContext", true);
  if (recognition) vi.stubGlobal("SpeechRecognition", FakeSpeechRecognition);
  else vi.stubGlobal("SpeechRecognition", undefined);
  vi.stubGlobal("AudioWorkletNode", class {});
  hostMicrophone.deliver = null;
  HostCapture.listen = async (_stream, samples) => {
    hostMicrophone.deliver = samples;
    return () => { if (hostMicrophone.deliver === samples) hostMicrophone.deliver = null; };
  };
  presetVoiceBackend(backend, options.host);
  return { getUserMedia, track };
}
