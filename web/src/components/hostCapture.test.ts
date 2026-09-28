import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HostCapture, listenWithWorklet } from "./hostCapture";

/* Host voice's streaming rules; the composer tests cover what the user sees. */
type Call = { path: string; body: unknown; headers: Record<string, string> };

function answer(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A microphone whose `speak` delivers 16 kHz samples, and whose track can end on its own. */
function microphone() {
  const track = Object.assign(new EventTarget(), { stop: vi.fn(), kind: "audio" });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  let deliver: ((chunk: Int16Array) => void) | null = null;
  const unlisten = vi.fn();
  HostCapture.listen = async (_stream, samples) => { deliver = samples; return unlisten; };
  return { stream, track, unlisten, speak: (samples: number) => deliver?.(new Int16Array(samples)) };
}

describe("HostCapture", () => {
  let calls: Call[];
  let reply: (call: Call) => Response | Promise<Response>;

  beforeEach(() => {
    vi.useFakeTimers();
    calls = [];
    reply = (call) => call.path === "/api/voice/live" ? answer({ id: "rec" }) : answer({ text: `${calls.filter((c) => c.path.includes("/audio")).length} chunks` });
    vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit) => {
      const call = { path, body: init.body, headers: init.headers as Record<string, string> };
      calls.push(call);
      return reply(call);
    }));
  });
  afterEach(() => {
    HostCapture.listen = listenWithWorklet;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("listens once samples arrive, sends half-second chunks one at a time and ends with the final words", async () => {
    const mic = microphone();
    const capture = new HostCapture(mic.stream, "selection-1");
    const updates: string[] = [];
    const listening = vi.fn();
    capture.onupdate = (text) => updates.push(text);
    capture.onlistening = listening;
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls[0]).toMatchObject({ path: "/api/voice/live", headers: { "X-Voice-Selection": "selection-1" } });
    expect(listening).not.toHaveBeenCalled();
    mic.speak(4000);
    expect(listening).toHaveBeenCalledOnce();
    mic.speak(4000);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.slice(1).map((call) => call.path)).toEqual(["/api/voice/live/rec/audio?seq=0&final=0"]);
    expect((calls[1]!.body as Int16Array).length).toBe(8000);
    expect(updates).toEqual(["1 chunks"]);
    mic.speak(3000);
    capture.onstop = vi.fn();
    capture.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.at(-1)!.path).toBe("/api/voice/live/rec/audio?seq=1&final=1");
    expect((calls.at(-1)!.body as Int16Array).length).toBe(3000);
    expect(capture.state).toBe("inactive");
    expect(capture.onstop).toHaveBeenCalledOnce();
    expect(capture.failure).toBeNull();
    expect(mic.track.stop).toHaveBeenCalled();
    expect(mic.unlisten).toHaveBeenCalled();
  });

  it("gathers samples while a request is in flight and sends them together, at most ten seconds each", async () => {
    const mic = microphone();
    let release: () => void = () => undefined;
    reply = (call) => call.path === "/api/voice/live" ? answer({ id: "rec" })
      : call.path.includes("seq=0") ? new Promise((resolve) => { release = () => resolve(answer({ text: "slow" })); }) : answer({ text: "caught up" });
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 25; i++) mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.filter((call) => call.path.includes("/audio"))).toHaveLength(1);
    release();
    await vi.advanceTimersByTimeAsync(0);
    const sizes = calls.filter((call) => call.path.includes("/audio")).map((call) => (call.body as Int16Array).length);
    expect(sizes).toEqual([8000, 160000, 40000]);
    expect(capture.text).toBe("caught up");
  });

  it("repeats a lost request with the same number, and stops once the connection stays lost", async () => {
    const mic = microphone();
    let failures = 1;
    reply = (call) => {
      if (call.path === "/api/voice/live") return answer({ id: "rec" });
      if (failures-- > 0) throw new TypeError("network");
      return answer({ text: "back" });
    };
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.slice(1).map((call) => call.path)).toEqual(["/api/voice/live/rec/audio?seq=0&final=0", "/api/voice/live/rec/audio?seq=0&final=0"]);
    expect(capture.text).toBe("back");
    failures = 10;
    capture.onstop = vi.fn();
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.failure).toBe("failed");
    expect(capture.reason).toBe("Voice stopped: the connection to Altitude was lost.");
    expect(capture.text).toBe("back");
    expect(capture.onstop).toHaveBeenCalledOnce();
  });

  it("stops with the host's reason when it refuses", async () => {
    const mic = microphone();
    reply = (call) => call.path === "/api/voice/live" ? answer({ error: "Voice is busy on another device." }, 409) : answer({ ok: true });
    const capture = new HostCapture(mic.stream, "s");
    capture.onstop = vi.fn();
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.reason).toBe("Voice is busy on another device.");
    expect(capture.onstop).toHaveBeenCalledOnce();
    expect(mic.track.stop).toHaveBeenCalled();
  });

  it("stops when the microphone gives no samples or its track ends", async () => {
    const silent = microphone();
    const quiet = new HostCapture(silent.stream, "s");
    quiet.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(quiet.reason).toBe("Voice stopped: the microphone produced no audio.");
    expect(calls.at(-1)!.path).toBe("/api/voice/live/rec/cancel");

    const interrupted = microphone();
    const capture = new HostCapture(interrupted.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    interrupted.speak(1000);
    interrupted.track.dispatchEvent(new Event("ended"));
    expect(capture.reason).toBe("Voice stopped: the microphone was interrupted.");
    expect(capture.state).toBe("inactive");
  });

  it("stops when the browser cannot process the microphone", async () => {
    const mic = microphone();
    HostCapture.listen = () => Promise.reject(new DOMException("no worklet", "AbortError"));
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.reason).toBe("Voice stopped: this browser could not process the microphone.");
  });

  it("cancel discards the recording on the host at once", async () => {
    const mic = microphone();
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    mic.speak(8000);
    capture.cancel();
    await vi.advanceTimersByTimeAsync(0);
    expect(capture.state).toBe("inactive");
    expect(calls.at(-1)!.path).toBe("/api/voice/live/rec/cancel");
    expect(mic.track.stop).toHaveBeenCalled();
  });

  it("stops when the host never answers the start, even while the microphone delivers", async () => {
    const mic = microphone();
    vi.stubGlobal("fetch", vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const capture = new HostCapture(mic.stream, "s");
    capture.onstop = vi.fn();
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    mic.speak(8000);
    capture.stop();
    await vi.advanceTimersByTimeAsync(12000);
    expect(capture.state).toBe("inactive");
    expect(capture.failure).toBe("failed");
    expect(capture.reason).toBe("Voice stopped: the connection to Altitude was lost.");
    expect(capture.onstop).toHaveBeenCalledOnce();
    expect(mic.track.stop).toHaveBeenCalled();
  });

  it("a recording opened after cancel is discarded on the host too", async () => {
    const mic = microphone();
    let open: () => void = () => undefined;
    reply = (call) => call.path === "/api/voice/live" ? new Promise((resolve) => { open = () => resolve(answer({ id: "late" })); }) : answer({ ok: true });
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    capture.cancel();
    open();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.at(-1)!.path).toBe("/api/voice/live/late/cancel");
  });
});
