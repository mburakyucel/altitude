import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HostCapture, UNREACHED, WAIT_MS, listenWithWorklet } from "./hostCapture";

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

  it("keeps recording while requests go unanswered and repeats each with the same samples, number and flag", async () => {
    const mic = microphone();
    let offline = true;
    reply = (call) => {
      if (offline) throw new TypeError("network");
      return call.path === "/api/voice/live" ? answer({ id: "rec" }) : answer({ text: `heard ${call.path}` });
    };
    const capture = new HostCapture(mic.stream, "s");
    const seen: string[] = [];
    HostCapture.watchers.add(() => seen.push(capture.connection));
    capture.onstop = vi.fn();
    capture.start();
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(10_000);
    for (let i = 0; i < 40; i++) mic.speak(8000);  // twenty seconds recorded while offline
    await vi.advanceTimersByTimeAsync(10_000);
    expect(capture.connection).toBe("lost");
    expect(capture.state).toBe("recording");
    expect(new Set(calls.map((call) => call.path))).toEqual(new Set(["/api/voice/live"]));
    offline = false;
    await vi.advanceTimersByTimeAsync(3000);
    const audio = calls.filter((call) => call.path.includes("/audio"));
    expect(audio.map((call) => [call.path, (call.body as Int16Array).length])).toEqual([
      ["/api/voice/live/rec/audio?seq=0&final=0", 160000], ["/api/voice/live/rec/audio?seq=1&final=0", 160000],
      ["/api/voice/live/rec/audio?seq=2&final=0", 8000]]);
    expect(audio.every((call) => call.headers["X-Voice-Selection"] === "s")).toBe(true);
    expect(capture.connection).toBe("ok");
    expect(seen).toEqual(["lost", "ok"]);

    // The final chunk's answer is lost: the same final chunk goes again and its answer lands.
    let lose = 2;
    reply = (call) => {
      if (lose-- > 0) throw new TypeError("network");
      return answer({ text: "the final words", final: true });
    };
    mic.speak(2000);
    capture.stop();
    await vi.advanceTimersByTimeAsync(5000);
    const finals = calls.filter((call) => call.path.includes("final=1"));
    expect(finals.map((call) => call.path)).toEqual(Array(3).fill("/api/voice/live/rec/audio?seq=3&final=1"));
    expect(new Set(finals.map((call) => call.body))).toHaveProperty("size", 1);
    expect(finals.map((call) => Array.from(call.body as Int16Array).length)).toEqual([2000, 2000, 2000]);
    expect(capture.text).toBe("the final words");
    expect(capture.failure).toBeNull();
    expect(capture.onstop).toHaveBeenCalledOnce();
    expect(HostCapture.retained.has(capture)).toBe(false);
  });

  it("replays the whole recording into a new host recording, holding the words shown until it catches up", async () => {
    const mic = microphone();
    let host = "first";
    let gone = false;
    reply = (call) => {
      if (call.path === "/api/voice/live") return answer({ id: host });
      if (gone && call.path.startsWith("/api/voice/live/first/")) return answer({ error: "Voice stopped: this recording has ended." }, 410);
      const count = calls.filter((c) => c.path.startsWith(`/api/voice/live/${host}/audio`)).length;
      return answer({ text: `${host} ${count}` });
    };
    const capture = new HostCapture(mic.stream, "s");
    const updates: string[] = [];
    capture.onupdate = (text) => updates.push(text);
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 3; i++) { mic.speak(8000); await vi.advanceTimersByTimeAsync(0); }
    expect(updates).toEqual(["first 1", "first 2", "first 3"]);
    gone = true;
    host = "second";  // the host restarted: the next open is a new recording
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.filter((call) => call.path === "/api/voice/live")).toHaveLength(2);
    const replayed = calls.filter((call) => call.path.startsWith("/api/voice/live/second/audio"));
    expect(replayed[0]!.path).toBe("/api/voice/live/second/audio?seq=0&final=0");
    expect((replayed[0]!.body as Int16Array).length).toBe(32000);
    expect(capture.connection).toBe("ok");
    expect(updates).toEqual(["first 1", "first 2", "first 3", "second 1"]);
  });

  it("holds the words while a replay is still short of them", async () => {
    const mic = microphone();
    let replaying = false;
    reply = (call) => {
      if (call.path === "/api/voice/live") return answer({ id: replaying ? "second" : "first" });
      if (call.path.startsWith("/api/voice/live/first/") && replaying) return answer({ error: "ended" }, 410);
      if (call.path.startsWith("/api/voice/live/second/audio?seq=1")) return new Promise<Response>(() => undefined);
      return answer({ text: replaying ? "short" : "a longer sentence already shown" });
    };
    const capture = new HostCapture(mic.stream, "s");
    const updates: string[] = [];
    capture.onupdate = (text) => updates.push(text);
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 30; i++) { mic.speak(8000); await vi.advanceTimersByTimeAsync(0); }  // fifteen seconds shown
    replaying = true;
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(0);
    // The first replayed chunk covers ten seconds of the fifteen: the longer words stay.
    expect(calls.filter((call) => call.path.startsWith("/api/voice/live/second/audio"))).toHaveLength(2);
    expect(updates.at(-1)).toBe("a longer sentence already shown");
    expect(capture.text).toBe("a longer sentence already shown");
    expect(capture.connection).toBe("catching-up");
  });

  it("asks a busy host again while replaying, and ends with the words shown if the connection stays lost two minutes after Stop", async () => {
    const mic = microphone();
    let opens = 0;
    let busy = 0;
    reply = (call) => {
      if (call.path === "/api/voice/live") {
        opens += 1;
        return opens > 1 && busy-- > 0 ? answer({ error: "Voice is busy on another device." }, 429) : answer({ id: `rec${opens}` });
      }
      if (call.path.startsWith("/api/voice/live/rec1/") && opens === 1 && busy === 2) return answer({ error: "ended" }, 410);
      return answer({ text: "words so far" });
    };
    const capture = new HostCapture(mic.stream, "s");
    capture.onstop = vi.fn();
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    busy = 2;  // the host restarted, and the lost recording still holds its slot for a moment
    mic.speak(8000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(calls.filter((call) => call.path === "/api/voice/live")).toHaveLength(4);
    expect(capture.text).toBe("words so far");
    expect(capture.state).toBe("recording");
    reply = () => { throw new TypeError("network"); };
    mic.speak(4000);
    capture.stop();
    await vi.advanceTimersByTimeAsync(WAIT_MS - 1000);
    expect(capture.state).toBe("recording");
    await vi.advanceTimersByTimeAsync(1000);
    expect(capture.state).toBe("inactive");
    expect([capture.failure, capture.unreached, capture.reason]).toEqual(["failed", true, UNREACHED]);
    expect(capture.text).toBe("words so far");
    expect(capture.onstop).toHaveBeenCalledOnce();
  });

  it("a busy host at the first start ends the capture with its reason", async () => {
    const mic = microphone();
    reply = () => answer({ error: "Voice is busy on another device." }, 429);
    const capture = new HostCapture(mic.stream, "s");
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    expect([capture.state, capture.reason]).toEqual(["inactive", "Voice is busy on another device."]);
  });

  for (const status of [401, 403, 409]) {
    it(`ends at once, keeping the words shown, when the host refuses with ${status}`, async () => {
      const mic = microphone();
      let refuse = false;
      reply = (call) => call.path === "/api/voice/live" ? answer({ id: "rec" })
        : refuse ? answer({ error: "Voice settings changed. Record again with the new setting." }, status) : answer({ text: "kept" });
      const capture = new HostCapture(mic.stream, "s");
      capture.start();
      await vi.advanceTimersByTimeAsync(0);
      mic.speak(8000);
      await vi.advanceTimersByTimeAsync(0);
      refuse = true;
      mic.speak(8000);
      await vi.advanceTimersByTimeAsync(0);
      expect([capture.state, capture.failure, capture.text, capture.outdated]).toEqual(["inactive", "failed", "kept", status === 409]);
      expect(calls.filter((call) => call.path.includes("/audio"))).toHaveLength(2);
    });
  }

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

  it("stops when the microphone gives no samples, and lands what was recorded when its track ends", async () => {
    const silent = microphone();
    const quiet = new HostCapture(silent.stream, "s");
    quiet.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(quiet.reason).toBe("Voice stopped: the microphone produced no audio.");
    expect(calls.at(-1)!.path).toBe("/api/voice/live/rec/cancel");

    const interrupted = microphone();
    const capture = new HostCapture(interrupted.stream, "s");
    const interrupt = vi.fn(() => capture.stop());
    capture.oninterrupted = interrupt;
    capture.start();
    await vi.advanceTimersByTimeAsync(0);
    interrupted.speak(1000);
    interrupted.track.dispatchEvent(new Event("ended"));
    expect(interrupt).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.at(-1)!.path).toBe("/api/voice/live/rec/audio?seq=0&final=1");
    expect([capture.state, capture.failure, capture.reason]).toEqual(["inactive", null, "Voice stopped: the microphone was interrupted."]);
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

  it("cancel during a repeated request ends at once and sends nothing more", async () => {
    const mic = microphone();
    vi.stubGlobal("fetch", vi.fn((path: string, init: RequestInit) => {
      calls.push({ path, body: init.body, headers: init.headers as Record<string, string> });
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }));
    const capture = new HostCapture(mic.stream, "s");
    capture.onstop = vi.fn();
    capture.start();
    mic.speak(8000);
    capture.stop();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(capture.connection).toBe("lost");
    const sent = calls.length;
    capture.cancel();
    await vi.advanceTimersByTimeAsync(WAIT_MS);
    expect(calls).toHaveLength(sent);
    expect([capture.state, capture.failure, capture.connection]).toEqual(["inactive", null, "ok"]);
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
