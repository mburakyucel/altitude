import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearVoiceDiagnostics, startVoiceDiagnostics, stopVoiceDiagnostics, traceVoice, traceVoiceTracks, voiceDiagnosticReport, voiceDiagnosticsActive, voiceError } from "./voiceTrace";
import { RecognitionCapture } from "./recognition";
import { FakeSpeechRecognition } from "./voiceTest";

beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: false })));
afterEach(() => { clearVoiceDiagnostics(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const events = () => JSON.parse(voiceDiagnosticReport()).events;

it("observes nothing until explicitly started; stopping freezes and clearing deletes the report", () => {
  const stream = { getAudioTracks: vi.fn(() => []) } as unknown as MediaStream;
  traceVoice("capture.open");
  traceVoiceTracks("microphone.opened", stream);
  expect(stream.getAudioTracks).not.toHaveBeenCalled();
  expect(events()).toEqual([]);
  startVoiceDiagnostics();
  traceVoice("capture.open");
  stopVoiceDiagnostics();
  const saved = voiceDiagnosticReport();
  traceVoice("capture.cancel");
  expect(voiceDiagnosticReport()).toBe(saved);
  clearVoiceDiagnostics();
  expect(events()).toEqual([]);
});

it("bounds collection by event count and time and starts a clean report", () => {
  vi.useFakeTimers();
  startVoiceDiagnostics();
  for (let n = 0; n < 300; n++) traceVoice("waveform.sample", undefined, { time: n });
  expect(events()).toHaveLength(256);
  expect(JSON.parse(voiceDiagnosticReport()).droppedEvents).toBe(45);
  vi.advanceTimersByTime(10 * 60 * 1000);
  expect(voiceDiagnosticsActive()).toBe(false);
  const saved = voiceDiagnosticReport();
  traceVoice("capture.cancel");
  expect(voiceDiagnosticReport()).toBe(saved);
  startVoiceDiagnostics();
  expect(events()).toHaveLength(1);
  expect(JSON.parse(voiceDiagnosticReport()).droppedEvents).toBe(0);
});

it("records recognizer cancellation and stale callbacks without speech, draft, track identifiers or error messages", () => {
  vi.useFakeTimers();
  vi.stubGlobal("SpeechRecognition", FakeSpeechRecognition);
  FakeSpeechRecognition.instances = [];
  const track = { label: "private microphone", id: "private-id", readyState: "live", enabled: true, muted: true, stop: vi.fn() };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  startVoiceDiagnostics();
  const capture = new RecognitionCapture(stream, { lang: "fr-FR", before: "private draft" });
  capture.start();
  const recognizer = FakeSpeechRecognition.instances[0]!;
  recognizer.answersAbort = false;
  recognizer.hear(["private speech"]);
  capture.cancel();
  recognizer.hear(["late private speech"]);
  recognizer.onerror?.({ error: "private error content" });
  vi.advanceTimersByTime(3000);
  const report = voiceDiagnosticReport();
  expect(report).not.toContain("private");
  expect(events()).toEqual(expect.arrayContaining([
    expect.objectContaining({ event: "recognizer.cancel" }),
    expect.objectContaining({ event: "recognizer.error", error: "other" }),
    expect.objectContaining({ event: "recognizer.end-timeout" }),
    expect.objectContaining({ event: "microphone.released", muted: true }),
  ]));
  expect(capture.text).not.toContain("late");
  expect(voiceError(new Error("secret"))).toBe("other");
});

it("identifies only the loaded asset basename, excluding server and conversation addresses", () => {
  const script = document.createElement("script");
  script.src = "https://private.example/assets/index-fixture123.js?secret=private";
  document.head.append(script);
  try {
    expect(JSON.parse(voiceDiagnosticReport()).build).toBe("index-fixture123.js");
    expect(voiceDiagnosticReport()).not.toContain("private");
  } finally { script.remove(); }
});
