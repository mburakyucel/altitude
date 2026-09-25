import { vi } from "vitest";
import { presetVoiceBackend } from "./voiceBackend";
import type { VoiceBackend } from "../data/api";
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

/** Browser voice primitives for route/component tests; emits one AAC/mp4 blob when stopped. */
export class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static isTypeSupported(type: string) {
    return type === "audio/mp4";
  }

  state: RecordingState = "inactive";
  mimeType: string;
  readonly stream: MediaStream;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  stopCalls = 0;

  constructor(stream: MediaStream, options?: MediaRecorderOptions) {
    this.stream = stream;
    this.mimeType = options?.mimeType ?? "audio/mp4";
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.stopCalls += 1;
    this.state = "inactive";
    queueMicrotask(() => {
      this.ondataavailable?.({ data: new Blob(["aac recording"], { type: this.mimeType }) });
      this.onstop?.();
    });
  }
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

export function installVoiceBrowser(options: { backend?: VoiceBackend; recognition?: boolean } = {}) {
  const backend = options.backend ?? "endpoint";
  const recognition = options.recognition ?? backend === "browser";
  FakeMediaRecorder.instances = [];
  FakeSpeechRecognition.instances = [];
  const track = { stop: vi.fn() };
  const mediaStream = { getTracks: () => [track] } as unknown as MediaStream;
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
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  if (recognition) vi.stubGlobal("SpeechRecognition", FakeSpeechRecognition);
  else vi.stubGlobal("SpeechRecognition", undefined);
  presetVoiceBackend(backend);
  return { getUserMedia, track };
}
