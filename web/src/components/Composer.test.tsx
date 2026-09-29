import { act, screen, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Composer, { combineDraft, formatTimer } from "./Composer";
import type { ComposerProps } from "./Composer";
import { ApiError } from "../data/api";
import type { HostVoiceOptions } from "./voiceTest";
import { FakeSpeechRecognition, hostMicrophone, hostVoiceServer, installVoiceBrowser, punctuationFixture, sentence, speak } from "./voiceTest";
import { HostCapture, UNREACHED, WAIT_MS } from "./hostCapture";
import { MemoryRouter } from "react-router";
import { presetVoiceBackend, updateVoiceSettings } from "./voiceBackend";

/*
 * The composer's states (SPEC.md §3.6), one test per row of the table. Issue #195 is the standing
 * failure: after a recording lands, the transcript is in the draft and nowhere else.
 */

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function Harness(props: Partial<ComposerProps> & { initial?: string }) {
  const [value, setValue] = useState(props.initial ?? "");
  return (
    <Composer
      conversation="project/altitude"
      value={value}
      onChange={setValue}
      onSubmit={props.onSubmit ?? (() => undefined)}
      placeholder="Message L3 about altitude"
      ariaLabel="Message L3 about altitude"
      {...props}
    />
  );
}

function mount(props: Partial<ComposerProps> & { initial?: string } = {}) {
  const user = userEvent.setup();
  const view = render(<Harness {...props} />);
  return { ...view, user, field: screen.getByLabelText("Message L3 about altitude") as HTMLTextAreaElement };
}

/** This computer's speech service: it answers `words` while listening and `final` at the end. */
function hostVoice(words = "", final: string | null = null, fallback?: HostVoiceOptions["fallback"]) {
  return hostVoiceServer({ words, final, fallback }).install();
}

/** Let every answer and render queued so far run (real timers). */
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

/** Start host voice and say half a second, so the composer is Listening with its first chunk answered. */
async function listen(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Start voice input" }));
  await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
  speak();
  await screen.findByText("Listening… Stop to add text, or Send.");
  await settle();
}

/** Advance fake timers inside act so every answer and render they release runs. */
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

/** Live words show as soon as they are heard, so a test reads each update at once (liveReveal.test.ts paces them). */
function reduceMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduce && query === "(prefers-reduced-motion: reduce)", media: query }));
}

describe("Composer", () => {
  beforeEach(() => reduceMotion(true));

  it("keeps text selectable but rejects typing and paste throughout microphone startup and listening", async () => {
    const { getUserMedia } = installVoiceBrowser();
    hostVoice();
    const stream = await getUserMedia();
    let open!: (stream: MediaStream) => void;
    getUserMedia.mockImplementationOnce(() => new Promise((resolve) => { open = resolve; }));
    const { user, field } = mount({ initial: "Keep this text" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    const next = [() => open(stream), () => hostMicrophone.speak(8000), () => undefined];
    for (const status of ["Opening microphone…", "Starting voice…", "Listening… Stop to add text, or Send."]) {
      expect(screen.getByRole("status")).toHaveTextContent(status);
      expect(screen.getByRole("status").closest(".composer-box")).not.toBeNull();
      expect(field).toHaveAttribute("readonly");
      await user.click(field);
      await user.keyboard("mutated{Backspace}");
      await user.paste("pasted text");
      expect(field).toHaveValue("Keep this text");
      await act(async () => next.shift()!());
    }
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    await user.type(field, " edited");
    expect(field).toHaveValue("Keep this text edited");
  });

  it.each(["resolve", "reject"])("ignores cancelled microphone acquisition %s after a new recording starts", async (result) => {
    const { getUserMedia } = installVoiceBrowser();
    hostVoice();
    const stopOld = vi.fn();
    let open!: (stream: MediaStream) => void;
    let reject!: (error: Error) => void;
    getUserMedia.mockImplementationOnce(() => new Promise((resolve, fail) => { open = resolve; reject = fail; }));
    const { user, field } = mount({ initial: "Draft" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await act(async () => result === "resolve" ? open({ getTracks: () => [{ stop: stopOld }] } as unknown as MediaStream) : reject(new DOMException("Late denial", "NotAllowedError")));
    expect(stopOld).toHaveBeenCalledTimes(result === "resolve" ? 1 : 0);
    await settle();
    expect(HostCapture.retained.size).toBe(1);
    expect(field).toHaveValue("Draft");
    expect(field).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
  });

  it("a cancelled voice Send cannot land into a newer recording when its request is answered later", async () => {
    installVoiceBrowser();
    const server = hostVoice("stale transcript");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Original", onSubmit });
    await listen(user);
    expect(field).toHaveValue("Original stale transcript");
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Transcribing…");
    const shown = field.value;
    await user.click(field);
    await user.keyboard("{Enter}changed");
    await user.paste("pasted");
    expect(field).toHaveValue(shown);
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Original");
    expect(field).not.toHaveAttribute("readonly");
    await user.type(field, " edited");
    server.words = "";
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    speak();
    // Every held answer arrives now, the cancelled recording's final one included.
    server.final = "stale final";
    await act(async () => server.reconnect());
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("Listening…");
    expect(field).toHaveValue("Original edited");
    expect(field).toHaveAttribute("readonly");
    expect(onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Original edited");
  });

  it("a voice Send the connection never returns to restores usable editing and the preexisting draft without sending", async () => {
    installVoiceBrowser();
    const server = hostVoice();
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep this", onSubmit });
    await listen(user);
    server.connection = "hold";
    vi.useFakeTimers();
    try {
      await act(async () => { screen.getByRole("button", { name: "Send" }).click(); });
      expect(field).toHaveAttribute("readonly");
      await advance(WAIT_MS);
      expect(screen.getByRole("alert")).toHaveTextContent(UNREACHED);
      expect(field).not.toHaveAttribute("readonly");
      expect(field).toHaveValue("Keep this");
      expect(onSubmit).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); }
    await user.type(field, " editing works");
    expect(field).toHaveValue("Keep this editing works");
  });

  it("delivers a late failure to the remounted composer while retaining its newer draft", async () => {
    let reject!: (error: Error) => void;
    const onSubmit = vi.fn(() => new Promise<void>((_, fail) => { reject = fail; }));
    const first = mount({ onSubmit });
    await first.user.type(first.field, "Submitted before leaving");
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    const second = mount({ onSubmit });
    await second.user.type(second.field, "Typed after returning");
    await act(async () => reject(new TypeError("Lost receipt")));
    expect(second.field).toHaveValue("Submitted before leaving\nTyped after returning");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery.");
    expect(onSubmit).toHaveBeenCalledOnce();
    second.unmount();
    const third = mount({ onSubmit });
    expect(third.field).toHaveValue("Submitted before leaving\nTyped after returning");
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("retires recovery at acceptance while the response remains open and leaves the next draft alone", async () => {
    let accept!: () => void;
    let finish!: () => void;
    const onSubmit = vi.fn((_text: string, accepted: () => void) => {
      accept = accepted;
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    const first = mount({ onSubmit });
    await first.user.type(first.field, "Accepted turn");
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => accept());
    expect(sessionStorage.getItem("altitude.submitted:project/altitude")).toBeNull();
    first.unmount();
    const second = mount({ onSubmit });
    expect(second.field).toHaveValue("");
    await second.user.type(second.field, "Next draft");
    await act(async () => finish());
    expect(second.field).toHaveValue("Next draft");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("restores reload evidence once under StrictMode and repeated task tab remounts", async () => {
    sessionStorage.setItem("altitude.submitted:task/sample/timer", JSON.stringify({
      pending: { "request-from-previous-document": "Reloaded submission" }, text: "", failure: null,
    }));
    const submit = vi.fn();
    function Tabs() {
      const [open, setOpen] = useState(true);
      const [value, setValue] = useState("Later task draft");
      return <><button onClick={() => setOpen(!open)}>Switch view</button>{open ?
        <Composer conversation="task/sample/timer" value={value} onChange={setValue} onSubmit={submit}
          ariaLabel="Task message" placeholder="Task message" /> : null}</>;
    }
    render(<StrictMode><Tabs /></StrictMode>);
    const user = userEvent.setup();
    const field = () => screen.getByRole("textbox", { name: "Task message" });
    expect(field()).toHaveValue("Reloaded submission\nLater task draft");
    for (let index = 0; index < 2; index++) {
      await user.click(screen.getByRole("button", { name: "Switch view" }));
      await user.click(screen.getByRole("button", { name: "Switch view" }));
      expect(field()).toHaveValue("Reloaded submission\nLater task draft");
    }
    expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(submit).not.toHaveBeenCalled();
  });

  it("keeps dictated edits in unconfirmed recovery across remount", async () => {
    installVoiceBrowser();
    hostVoice("", "and the tests");
    const first = mount({ onSubmit: () => Promise.reject(new TypeError("Lost receipt")) });
    await first.user.type(first.field, "Inspect the sample");
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    await listen(first.user);
    await first.user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(first.field).toHaveValue("Inspect the sample and the tests"));
    first.unmount();
    expect(mount().field).toHaveValue("Inspect the sample and the tests");
  });

  it("retains newer recovery edits across task tabs when browser updates fail", async () => {
    const submit = vi.fn(() => Promise.reject(new TypeError("Lost receipt")));
    function Tabs() {
      const [open, setOpen] = useState(true);
      const [value, setValue] = useState("");
      return <><button onClick={() => setOpen(!open)}>Switch view</button>{open ?
        <Composer conversation="task/sample/write-failure" value={value} onChange={setValue} onSubmit={submit}
          ariaLabel="Task message" placeholder="Task message" /> : null}</>;
    }
    render(<Tabs />);
    const user = userEvent.setup();
    const field = () => screen.getByRole("textbox", { name: "Task message" });
    await user.type(field(), "Submitted text");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
    try {
      await user.type(field(), " and newer edits");
      await user.click(screen.getByRole("button", { name: "Switch view" }));
      await user.click(screen.getByRole("button", { name: "Switch view" }));
      expect(field()).toHaveValue("Submitted text and newer edits");
      expect(screen.getByRole("alert")).toHaveTextContent("Keep this tab open");
      expect(submit).toHaveBeenCalledOnce();
    } finally { storage.mockRestore(); }
    await user.click(screen.getByRole("button", { name: "Switch view" }));
    await user.click(screen.getByRole("button", { name: "Switch view" }));
    expect(field()).toHaveValue("Submitted text and newer edits");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Keep this tab open");
  });

  it("keeps the draft unsent if its recovery copy cannot be saved", async () => {
    const onSubmit = vi.fn();
    const { user, field } = mount({ onSubmit });
    const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
    try {
      await user.type(field, "Keep this text");
      await user.click(screen.getByRole("button", { name: "Send" }));
      expect(field).toHaveValue("Keep this text");
      expect(screen.getByRole("alert")).toHaveTextContent("Your message was not sent.");
      expect(onSubmit).not.toHaveBeenCalled();
    } finally { storage.mockRestore(); }
  });

  it.each([true, false])("storage read failure after submission preserves accepted=%s semantics", async (accepted) => {
    let finish!: () => void;
    const onSubmit = vi.fn((_text: string, accept: () => void) => new Promise<void>((resolve, reject) => {
      finish = () => { if (accepted) { accept(); resolve(); } else reject(new TypeError("Lost receipt")); };
    }));
    const { user, field } = mount({ onSubmit });
    await user.type(field, "Submitted text");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "New draft");
    const storage = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
    try {
      await act(async () => finish());
      expect(field).toHaveValue(accepted ? "New draft" : "Submitted text\nNew draft");
      if (accepted) expect(screen.queryByRole("alert")).toBeNull();
      else expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery.");
    } finally { storage.mockRestore(); }
  });

  it("does not overwrite unread pending evidence or offer Retry for mixed unconfirmed recovery", async () => {
    const failures: Array<(error: Error) => void> = [];
    const { user, field } = mount({ onSubmit: () => new Promise<void>((_, reject) => failures.push(reject)) });
    for (const text of ["Unconfirmed text", "Refused text"]) {
      await user.type(field, text);
      await user.click(screen.getByRole("button", { name: "Send" }));
    }
    await act(async () => failures[0]!(new TypeError("Lost receipt")));
    const evidence = sessionStorage.getItem("altitude.submitted:project/altitude");
    const storage = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Read denied"); });
    try {
      await act(async () => failures[1]!(new ApiError(409, "Refused")));
      expect(field).toHaveValue("Refused text\nUnconfirmed text");
      expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery.");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    } finally { storage.mockRestore(); }
    expect(sessionStorage.getItem("altitude.submitted:project/altitude")).toBe(evidence);
  });

  // 2026-09-07 decision: the send control must not turn into visible Send/Queue words.
  it("Idle and Typing: the arrow enables with a draft; Enter sends and clears; Shift+Enter adds a line", async () => {
    const onSubmit = vi.fn();
    const { user, field } = mount({ onSubmit });
    const send = screen.getByRole("button", { name: "Send" });
    expect(send.textContent).toBe("");
    expect(send.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(send).toBeDisabled();

    await user.type(field, "hello");
    expect(send.textContent).toBe("");
    expect(send).toBeEnabled();
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(field).toHaveValue("hello\n");
    await user.type(field, "there");
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hello\nthere", expect.any(Function));
    expect(field).toHaveValue("");
    expect(send).toBeDisabled();
  });

  it("Busy: the arrow queues and the hint says the message runs next", async () => {
    const onSubmit = vi.fn();
    const { user, field } = mount({ onSubmit, busy: true });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    const queue = screen.getByRole("button", { name: "Queue" });
    expect(queue.textContent).toBe("");
    expect(queue.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(queue).toBeDisabled();
    expect(screen.getByText("L3 is mid-turn · runs next")).toBeInTheDocument();
    await user.type(field, "later please");
    expect(queue).toBeEnabled();
    await user.click(queue);
    expect(onSubmit).toHaveBeenCalledWith("later please", expect.any(Function));
    expect(field).toHaveValue("");
    expect(queue).toBeDisabled();
  });

  it("refused: the draft returns, the hint reads Not sent. Retry, and Retry sends the same text", async () => {
    let refuse = true;
    const onSubmit = vi.fn(async () => {
      if (refuse) throw new ApiError(409, "Refused");
    });
    const { user, field } = mount({ onSubmit, hint: "L3 answers or creates one task." });
    await user.type(field, "ship it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not sent. Retry");
    expect(alert).toHaveClass("text-danger");
    expect(field).toHaveValue("ship it");

    refuse = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit).toHaveBeenLastCalledWith("ship it", expect.any(Function));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(field).toHaveValue("");
    expect(screen.getByText("L3 answers or creates one task.")).toBeInTheDocument();
  });

  it.each([409, 500, null])("preserves unsent and newly typed drafts on failure %s", async (status) => {
    let reject!: (error: Error) => void;
    const onSubmit = vi.fn().mockImplementationOnce(() => new Promise<void>((_resolve, fail) => { reject = fail; })).mockResolvedValue(undefined);
    const { user, field } = mount({ onSubmit });
    await user.type(field, "Original draft");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "New draft");
    await act(async () => { reject(status ? new ApiError(status, "Request failed") : new TypeError("Network failed")); });
    expect(field).toHaveValue("Original draft\nNew draft");
    if (status === 409) {
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(onSubmit).toHaveBeenLastCalledWith("Original draft\nNew draft", expect.any(Function));
      expect(field).toHaveValue("");
    } else {
      expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(onSubmit).toHaveBeenCalledTimes(1);
    }
  });

  it("recovers concurrent refused sends without losing either unsent draft", async () => {
    const failures: Array<(error: Error) => void> = [];
    const onSubmit = vi.fn(() => new Promise<void>((_resolve, reject) => failures.push(reject)));
    const { user, field } = mount({ onSubmit });
    for (const text of ["First draft", "Second draft"]) {
      await user.type(field, text);
      await user.click(screen.getByRole("button", { name: "Send" }));
    }
    await act(async () => { failures.forEach((reject) => reject(new ApiError(409, "Refused"))); });
    expect(field).toHaveValue("Second draft\nFirst draft");
    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent("Not sent. Retry");
  });

  it.each([true, false])("keeps mixed failed sends unconfirmed when refusal finishes first: %s", async (refusalFirst) => {
    const failures: Array<(error: Error) => void> = [];
    const onSubmit = vi.fn(() => new Promise<void>((_resolve, reject) => failures.push(reject)));
    const { user, field } = mount({ onSubmit });
    for (const text of ["Refused draft", "Unconfirmed draft"]) {
      await user.type(field, text);
      await user.click(screen.getByRole("button", { name: "Send" }));
    }
    await user.type(field, "New draft");
    for (const index of refusalFirst ? [0, 1] : [1, 0]) {
      await act(async () => failures[index]!(index === 0 ? new ApiError(409, "Refused") : new TypeError("Network failed")));
    }
    expect(field).toHaveValue(refusalFirst ? "Unconfirmed draft\nRefused draft\nNew draft" : "Refused draft\nUnconfirmed draft\nNew draft");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it.each(["Escape", "Cancel"])("Listening: Cancel, Stop, Send; %s goes back with nothing added", async (action) => {
    const { getUserMedia, track } = installVoiceBrowser();
    const server = hostVoice("", "never used");
    const { user, field } = mount({ initial: "Keep this" });
    await listen(user);

    const stop = screen.getByRole("button", { name: "Stop voice input" });
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(screen.getByRole("button", { name: "Cancel voice input" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send" }).textContent).toBe("");
    expect(screen.getByLabelText("Recording time")).toHaveTextContent("0:00");
    expect(screen.getByRole("status")).toHaveTextContent("Listening… Stop to add text, or Send.");
    expect(field).toHaveAttribute("placeholder", "");
    expect(field).toHaveValue("Keep this");

    stop.focus();
    if (action === "Escape") await user.keyboard("{Escape}");
    else await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeInTheDocument());
    expect(field).toHaveValue("Keep this");
    expect(track.stop).toHaveBeenCalled();
    expect(server.audio.some((call) => call.final)).toBe(false);
    await waitFor(() => expect(server.calls.at(-1)?.path).toBe("/api/voice/live/rec-1/cancel"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Transcribing then Landed: the transcript lands in the draft, cursor at the end, and nothing else appears", async () => {
    installVoiceBrowser();
    const server = hostVoice("", "and the tests");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit });
    await listen(user);
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText("Transcribing…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop voice input" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(field).toHaveAttribute("readonly");

    await act(async () => server.reconnect());
    await waitFor(() => expect(field).toHaveValue("Fix the timer and the tests"));
    expect(screen.queryByText("Transcribing…")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
    expect(screen.queryByText("and the tests")).toBeNull();
    expect(screen.queryByRole("button", { name: /undo|insert|discard/i })).toBeNull();
    expect(document.activeElement).toBe(field);
    expect(field.selectionStart).toBe("Fix the timer and the tests".length);
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it.each(["Send", "Enter", "Busy"])("Send at once (%s): transcribes then submits the combined draft through the normal path", async (action) => {
    installVoiceBrowser();
    const server = hostVoice("", "and the tests");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit, busy: action === "Busy" });
    await listen(user);
    server.connection = "hold";
    const stop = screen.getByRole("button", { name: "Stop voice input" });
    const send = screen.getByRole("button", { name: action === "Busy" ? "Queue" : "Send" });
    expect(send.textContent).toBe("");
    expect(send.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    if (action === "Enter") {
      stop.focus();
      await user.keyboard("{Enter}");
    } else await user.click(send);
    expect(await screen.findByText("Transcribing…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: action === "Busy" ? "Queue" : "Send" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
    // Enter during transcription cannot submit the old draft or schedule a second send.
    field.focus();
    await user.keyboard("{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
    await act(async () => server.reconnect());
    await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith("Fix the timer and the tests", expect.any(Function)));
    expect(field).toHaveValue("");
    expect(screen.queryByText("Transcribing…")).toBeNull();
  });

  it.each(["", "Keep this"])("Send at once: an empty transcript sends nothing and keeps draft '%s'", async (initial) => {
    installVoiceBrowser();
    hostVoice("", "  ");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial, onSubmit });
    await listen(user);
    field.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled());
    expect(field).toHaveValue(initial);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(!initial);
  });

  it.each([false, true])("explicit voice Send retains its requested destination when later props change, disabled=%s", async (disabled) => {
    installVoiceBrowser();
    const server = hostVoice("", "the transcript");
    const original = vi.fn();
    const latest = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<Harness initial="Keep" onSubmit={original} />);
    await listen(user);
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Transcribing…");
    rerender(<Harness onSubmit={latest} disabled={disabled} />);
    await act(async () => server.reconnect());
    await waitFor(() => expect(screen.queryByText("Transcribing…")).toBeNull());
    expect(original).toHaveBeenCalledExactlyOnceWith("Keep the transcript", expect.any(Function));
    expect(latest).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("voice Send owns recorder completion before unmount, restores pending source UI, and preserves its next draft", async () => {
    installVoiceBrowser();
    const server = hostVoice("", "dictated");
    let accept!: () => void;
    let complete!: () => void;
    const original = vi.fn((_text: string, accepted: () => void) => { accept = accepted; return new Promise<void>((resolve) => { complete = resolve; }); });
    const other = vi.fn();
    const first = mount({ initial: "Original", onSubmit: original });
    await listen(first.user);
    server.connection = "hold";
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    const destination = mount({ conversation: "project/beta", initial: "Independent", onSubmit: other });
    await settle();
    expect(destination.field).toHaveValue("Independent");
    expect(destination.field).not.toHaveAttribute("readonly");
    destination.unmount();
    const source = mount({ onSubmit: other });
    expect(source.field).toHaveValue("Original");
    expect(source.field).toHaveAttribute("readonly");
    expect(screen.getByRole("status")).toHaveTextContent("Transcribing…");
    await act(async () => server.reconnect());
    await settle();
    expect(original).toHaveBeenCalledExactlyOnceWith("Original dictated", expect.any(Function));
    expect(other).not.toHaveBeenCalled();
    await source.user.type(source.field, "Next draft");
    await act(async () => { accept(); complete(); });
    expect(source.field).toHaveValue("Next draft");
    expect(sessionStorage.getItem("altitude.submitted:project/altitude")).toBeNull();
  });

  it.each(["failure", "cancel"])("voice %s restores its source draft after navigation without sending", async (outcome) => {
    installVoiceBrowser();
    const server = hostVoice();
    const onSubmit = vi.fn();
    const first = mount({ initial: "Source text", onSubmit });
    await listen(first.user);
    server.connection = "hold";
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    if (outcome === "failure") { await act(async () => server.reconnect()); await settle(); }
    const source = mount({ onSubmit });
    if (outcome === "cancel") {
      await source.user.click(screen.getByRole("button", { name: "Cancel voice input" }));
      await act(async () => server.reconnect());
    } else expect(screen.getByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(source.field).toHaveValue("Source text");
    expect(source.field).not.toHaveAttribute("readonly");
    expect(onSubmit).not.toHaveBeenCalled();
    await source.user.clear(source.field);
    source.unmount();
    expect(mount().field).toHaveValue("");
  });

  it("a voice-only transcription failure remains visible when returning after completion", async () => {
    installVoiceBrowser();
    const server = hostVoice();
    const first = mount();
    await listen(first.user);
    server.connection = "hold";
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    await act(async () => server.reconnect());
    await settle();
    const source = mount();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(source.field).toHaveValue("");
    expect(source.field).not.toHaveAttribute("readonly");
    await source.user.type(source.field, "New text");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sessionStorage.getItem("altitude.submitted:project/altitude")).toBeNull();
  });

  it.each([["a view change", false], ["visible Cancel", true]])("a late recorder stop after %s focuses the field only for a cancel made in view", async (_label, visibleCancel) => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field, rerender } = mount();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    const recording = FakeSpeechRecognition.instances[0]!;
    recording.answersAbort = false;
    if (visibleCancel) await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    else {
      rerender(<Harness active={false} />);
      await act(async () => undefined);
      rerender(<Harness active />);
    }
    act(() => field.blur());
    // Issue #495: the recognizer's end arrives after the view has returned.
    await act(async () => { recording.onend?.(); });
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    if (visibleCancel) await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toHaveFocus());
    expect(field).not.toHaveFocus();
  });

  it("Cancel before the last words releases that microphone and isolates a restarted recording", async () => {
    const { track } = installVoiceBrowser();
    const server = hostVoice("", "old recording");
    const { user, field } = mount({ initial: "Draft" });
    await listen(user);
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    // Send lets go of the microphone at once; only the last words are awaited.
    expect(track.stop).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Draft");
    server.final = "new recording";
    await act(async () => server.reconnect());
    await listen(user);
    expect(field).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Draft new recording"));
    // The cancelled recording's last request was abandoned unanswered; only the new one was answered.
    expect(server.audio.filter((call) => call.final).map((call) => call.path)).toEqual([
      "/api/voice/live/rec-1/audio?seq=1&final=1", "/api/voice/live/rec-2/audio?seq=1&final=1"]);
    expect(HostCapture.retained.size).toBe(0);
  });

  it.each(["refused", "unconfirmed"].flatMap((failure) => ["success", "failure", "cancel", "empty"].map((outcome) => ({ failure, outcome }))))("transfers existing $failure recovery once into a voice Send that ends in $outcome", async ({ failure, outcome }) => {
    installVoiceBrowser();
    sessionStorage.setItem("altitude.submitted:project/altitude", JSON.stringify({ pending: {}, text: "Recovered request", failure }));
    const server = hostVoice("", outcome === "empty" ? "" : "dictated");
    const onSubmit = vi.fn();
    const first = mount({ onSubmit });
    await listen(first.user);
    server.connection = "hold";
    if (outcome === "failure") server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    const source = mount({ onSubmit });
    expect(source.field).toHaveValue("Recovered request");
    if (outcome === "cancel") await source.user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await act(async () => server.reconnect());
    await settle();
    expect(source.field).toHaveValue(outcome === "success" ? "" : "Recovered request");
    expect(onSubmit).toHaveBeenCalledTimes(outcome === "success" ? 1 : 0);
    const warning = failure === "unconfirmed" ? "Could not confirm delivery. Check the conversation before sending again."
      : outcome === "failure" ? "Could not transcribe. Typing works." : "Not sent.";
    if (outcome === "success") expect(screen.queryByRole("alert")).toBeNull();
    else expect(screen.getByRole("alert")).toHaveTextContent(warning);
    source.unmount();
    expect(mount().field).toHaveValue(outcome === "success" ? "" : "Recovered request");
    if (outcome !== "success") expect(screen.getByRole("alert")).toHaveTextContent(warning);
  });

  it.each([false, true])("an earlier failed send remains unsent while voice Send finishes; failure after remount=%s", async (afterRemount) => {
    installVoiceBrowser();
    let refuse!: (error: Error) => void;
    const server = hostVoice("", "dictated");
    const onSubmit = vi.fn().mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { refuse = reject; }));
    const first = mount({ initial: "Earlier request", onSubmit });
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    await first.user.type(first.field, "Voice request");
    await listen(first.user);
    server.connection = "hold";
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    if (!afterRemount) await act(async () => refuse(new ApiError(409, "Refused")));
    const source = mount({ onSubmit });
    if (afterRemount) await act(async () => refuse(new ApiError(409, "Refused")));
    expect(source.field).toHaveValue("Voice request");
    expect(source.field).toHaveAttribute("readonly");
    await act(async () => server.reconnect());
    await settle();
    expect(onSubmit).toHaveBeenNthCalledWith(2, "Voice request dictated", expect.any(Function));
    expect(source.field).toHaveValue("Earlier request");
    expect(screen.getByRole("alert")).toHaveTextContent("Not sent.");
    source.unmount();
    const returned = mount({ onSubmit });
    expect(returned.field).toHaveValue("Earlier request");
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["Stop voice input", "Voice stopped: the speech process stopped. Typing works."],
    ["Send", "Could not transcribe. Typing works."],
  ])("failure after %s: the hint reports it, the draft is unchanged, nothing sends", async (control, hint) => {
    installVoiceBrowser();
    const server = hostVoice();
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Draft stays", onSubmit });
    await listen(user);
    server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    await user.click(screen.getByRole("button", { name: control }));
    expect(await screen.findByRole("alert")).toHaveTextContent(hint);
    expect(field).toHaveValue("Draft stays");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("Denied: the hint says typing works and the mic asks the browser again", async () => {
    installVoiceBrowser();
    hostVoice();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    const { user, field } = mount({ initial: "still here", busy: true });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText("Microphone blocked in the browser. Typing works.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Microphone blocked in the browser. Typing works.");
    expect(screen.queryByText("L3 is mid-turn · runs next")).toBeNull();
    expect(field).toHaveValue("still here");
    await user.type(field, " and typing");
    expect(field).toHaveValue("still here and typing");
    await listen(user);
    expect(screen.queryByText("Microphone blocked in the browser. Typing works.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
  });

  it("Unavailable: no mic and Voice needs HTTPS on an insecure origin; no mic and no hint without recording", () => {
    installVoiceBrowser();
    vi.stubGlobal("isSecureContext", false);
    const first = render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.getByText("Voice needs HTTPS")).toBeInTheDocument();
    first.unmount();

    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("AudioWorkletNode", undefined);
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.queryByText("Voice needs HTTPS")).toBeNull();
  });

  it("Ctrl+M starts and stops the microphone from the field", async () => {
    installVoiceBrowser();
    const server = hostVoice("", "by keyboard");
    const { user, field } = mount();
    field.focus();
    await user.keyboard("{Control>}m{/Control}");
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    speak();
    await screen.findByText("Listening… Stop to add text, or Send.");
    await user.keyboard("{Control>}m{/Control}");
    await waitFor(() => expect(field).toHaveValue("by keyboard"));
    expect(server.audio.filter((call) => call.final)).toHaveLength(1);
  });

  // ---- host voice: the microphone streams to this computer, whose words appear while listening ----
  /** The host's answers: each chunk is answered with how many samples it has heard. */
  function stubHost(options: { refuse?: string } = {}) {
    let heard = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/voice") return jsonResponse({ backend: "host", selection: "fixture-host", url: "", model: "", key_set: false, host: { state: "ready", download_bytes: 0 } });
      if (path === "/api/voice/live") return options.refuse ? jsonResponse({ error: options.refuse }, 429) : jsonResponse({ id: "rec", owner: "device" });
      if (path.endsWith("/cancel")) return jsonResponse({ ok: true });
      heard += (init?.body as Int16Array).length;
      return jsonResponse(path.includes("final=1") ? { text: `Heard ${heard} samples.`, final: true } : { text: `heard ${heard}` });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("host voice: Starting voice until samples arrive, words while listening, Stop lands the final words", async () => {
    installVoiceBrowser({ backend: "host" });
    const fetchMock = stubHost();
    const { user, field } = mount({ initial: "Note:" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Starting voice…");
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    act(() => hostMicrophone.speak(8000));
    expect(await screen.findByText("Listening… Stop to add text, or Send.")).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveValue("Note: heard 8000"));
    expect(field).toHaveAttribute("readonly");
    act(() => hostMicrophone.speak(2000));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Note: Heard 10000 samples."));
    expect(field).not.toHaveAttribute("readonly");
    expect(screen.queryByRole("status")).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/voice/live", expect.objectContaining({ headers: expect.objectContaining({ "X-Voice-Selection": "fixture-host" }) }));
    expect(fetchMock.mock.calls.map(([path]) => String(path))).not.toContain("/api/transcribe");
  });

  it("host voice: Send while listening sends the draft with the final words", async () => {
    installVoiceBrowser({ backend: "host" });
    stubHost();
    const onSubmit = vi.fn((_text: string, accepted: () => void) => accepted());
    const { user } = mount({ initial: "Please", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    act(() => hostMicrophone.speak(8000));
    await screen.findByText("Listening… Stop to add text, or Send.");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("Please Heard 8000 samples.", expect.any(Function)));
  });

  it("host voice: a stopped recording keeps the words shown and says why", async () => {
    installVoiceBrowser({ backend: "host" });
    stubHost();
    const { user, field } = mount();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    act(() => hostMicrophone.speak(8000));
    await waitFor(() => expect(field).toHaveValue("heard 8000"));
    stubHost({ refuse: "unused" });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: "Voice stopped: the speech process stopped." }, 503)));
    act(() => hostMicrophone.speak(8000));
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice stopped: the speech process stopped. Typing works.");
    expect(field).toHaveValue("heard 8000");
    expect(field).not.toHaveAttribute("readonly");
  });

  it("host voice: a refused start says why and opens nothing more", async () => {
    installVoiceBrowser({ backend: "host" });
    stubHost({ refuse: "Voice is busy on another device." });
    const { user, field } = mount({ initial: "Draft" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice is busy on another device. Typing works.");
    expect(field).toHaveValue("Draft");
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("host voice: Cancel discards the words on screen and the recording on the host", async () => {
    installVoiceBrowser({ backend: "host" });
    const fetchMock = stubHost();
    const { user, field } = mount({ initial: "Keep" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    act(() => hostMicrophone.speak(8000));
    await waitFor(() => expect(field).toHaveValue("Keep heard 8000"));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Keep");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/voice/live/rec/cancel", expect.anything()));
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  // ---- host voice through a lost connection: the recording goes on, the words catch up ----------------
  it("host voice: a lost connection keeps recording and the words shown, and they catch up once answers return", async () => {
    installVoiceBrowser();
    const server = hostVoice("first words", "first words and the rest");
    const { user, field } = mount({ initial: "Draft" });
    await listen(user);
    expect(field).toHaveValue("Draft first words");
    vi.useFakeTimers();
    try {
      server.connection = "drop";
      speak();
      await advance(0);
      expect(screen.getByRole("status")).toHaveTextContent("Connection lost — still recording. Your words will catch up.");
      for (let second = 0; second < 10; second++) { speak(); await advance(1000); }
      expect(screen.getByRole("status")).toHaveTextContent("Connection lost — still recording. Your words will catch up.");
      expect(screen.getByRole("button", { name: "Stop voice input" })).toBeInTheDocument();
      expect(field).toHaveValue("Draft first words");
      expect(field).toHaveAttribute("readonly");
      server.words = "first words and the rest";
      server.connection = "ok";
      await advance(3000);
      expect(screen.getByRole("status")).toHaveTextContent("Listening… Stop to add text, or Send.");
      expect(field).toHaveValue("Draft first words and the rest");
    } finally { vi.useRealTimers(); }
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(field).toHaveValue("Draft first words and the rest");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("host voice: when the host forgets the recording, the replay shows Catching up… and the words never shrink", async () => {
    installVoiceBrowser();
    const server = hostVoice("one two three");
    const { user, field } = mount();
    const shown: string[] = [];
    const record = () => { shown.push(field.value); };
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    // Twenty seconds recorded and answered: a replay needs two ten-second requests to cover them.
    speak(160_000);
    await screen.findByText("Listening… Stop to add text, or Send.");
    await settle();
    speak(160_000);
    await settle();
    expect(field).toHaveValue("one two three");
    record();
    server.forget();
    server.connection = "hold";
    speak();
    await act(async () => server.release());  // 410: the host opens a new recording, held
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("Catching up…");
    record();
    await act(async () => server.release());  // the new recording opens; its first ten seconds are held
    await settle();
    server.words = "one";
    await act(async () => server.release());  // the replay has not yet reached the words shown
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("Catching up…");
    expect(field).toHaveValue("one two three");
    record();
    server.words = "one two three four";
    await act(async () => server.reconnect());
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("Listening… Stop to add text, or Send.");
    expect(field).toHaveValue("one two three four");
    record();
    shown.forEach((value, index) => { if (index) expect(value.startsWith(shown[index - 1]!)).toBe(true); });
    expect(server.calls.filter((call) => call.path === "/api/voice/live")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
  });

  it("host voice: Stop while offline waits for the connection, and the X cancels with nothing added", async () => {
    installVoiceBrowser();
    const server = hostVoice("spoken");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Draft", onSubmit });
    await listen(user);
    expect(field).toHaveValue("Draft spoken");
    server.connection = "drop";
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    expect(await screen.findByText("Waiting for connection…")).toBeInTheDocument();
    expect(field).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Draft");
    expect(field).not.toHaveAttribute("readonly");
    expect(screen.queryByText("Waiting for connection…")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(HostCapture.retained.size).toBe(0);
    expect(server.calls.at(-1)?.path).toBe("/api/voice/live/rec-1/cancel");
    server.connection = "ok";
    await settle();
    expect(field).toHaveValue("Draft");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("host voice: Stop while offline past the wait lands the words shown and says the last words were not added", async () => {
    installVoiceBrowser();
    const server = hostVoice("spoken");
    const { user, field } = mount({ initial: "Draft" });
    await listen(user);
    vi.useFakeTimers();
    try {
      server.connection = "drop";
      await act(async () => { screen.getByRole("button", { name: "Stop voice input" }).click(); });
      await advance(WAIT_MS - 1);
      expect(screen.getByRole("status")).toHaveTextContent("Waiting for connection…");
      await advance(1);
      expect(field).toHaveValue("Draft spoken");
      expect(field).not.toHaveAttribute("readonly");
      expect(screen.getByRole("alert")).toHaveTextContent(UNREACHED);
      expect(HostCapture.retained.size).toBe(0);
    } finally { vi.useRealTimers(); }
  });

  it("host voice: a voice Send that outlives its composer sends its late words only to its own conversation", async () => {
    installVoiceBrowser();
    const server = hostVoice("", "the late words");
    const own = vi.fn();
    const other = vi.fn();
    const first = mount({ initial: "Original", onSubmit: own });
    await listen(first.user);
    vi.useFakeTimers();
    try {
      server.connection = "drop";
      await act(async () => { screen.getByRole("button", { name: "Send" }).click(); });
      await advance(0);
      expect(screen.getByRole("status")).toHaveTextContent("Waiting for connection…");
      first.unmount();
      render(<Harness conversation="project/beta" initial="Other draft" onSubmit={other} />);
      const field = screen.getByLabelText("Message L3 about altitude");
      await act(async () => { screen.getByRole("button", { name: "Start voice input" }).click(); });
      await advance(0);
      speak();
      await advance(0);
      expect(screen.getByRole("status")).toHaveTextContent("Connection lost — still recording. Your words will catch up.");
      server.words = "other words";
      server.connection = "ok";
      await advance(3000);
      expect(own).toHaveBeenCalledExactlyOnceWith("Original the late words", expect.any(Function));
      expect(other).not.toHaveBeenCalled();
      expect(field).toHaveValue("Other draft other words");
      expect(screen.getByRole("status")).toHaveTextContent("Listening… Stop to add text, or Send.");
      await act(async () => { screen.getByRole("button", { name: "Cancel voice input" }).click(); });
      expect(field).toHaveValue("Other draft");
    } finally { vi.useRealTimers(); }
    expect(other).not.toHaveBeenCalled();
    expect(HostCapture.retained.size).toBe(0);
  });

  it("host voice: a voice Send past the wait returns its text unsent to its own conversation with the reason", async () => {
    installVoiceBrowser();
    const server = hostVoice("spoken");
    const onSubmit = vi.fn();
    const first = mount({ initial: "Original", onSubmit });
    await listen(first.user);
    vi.useFakeTimers();
    try {
      server.connection = "drop";
      await act(async () => { screen.getByRole("button", { name: "Send" }).click(); });
      first.unmount();
      const other = render(<Harness conversation="project/beta" initial="Other draft" />);
      await advance(WAIT_MS);
      expect(screen.getByLabelText("Message L3 about altitude")).toHaveValue("Other draft");
      expect(screen.queryByRole("alert")).toBeNull();
      other.unmount();
    } finally { vi.useRealTimers(); }
    expect(JSON.parse(sessionStorage.getItem("altitude.submitted:project/altitude")!)).toMatchObject({ text: "Original spoken", failure: "unreached" });
    const source = mount({ onSubmit });
    expect(source.field).toHaveValue("Original spoken");
    expect(source.field).not.toHaveAttribute("readonly");
    expect(screen.getByRole("alert")).toHaveTextContent(UNREACHED);
    expect(onSubmit).not.toHaveBeenCalled();
    await source.user.type(source.field, "!");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("host voice: a third recording while two are still sending says so and opens no microphone", async () => {
    const { getUserMedia } = installVoiceBrowser();
    const server = hostVoice("words");
    const first = mount({ initial: "First" });
    await listen(first.user);
    server.connection = "drop";
    await first.user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    const second = mount({ conversation: "project/beta", initial: "Second" });
    await second.user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    speak();
    expect(await screen.findByText("Connection lost — still recording. Your words will catch up.")).toBeInTheDocument();
    await second.user.click(screen.getByRole("button", { name: "Send" }));
    second.unmount();
    expect(HostCapture.retained.size).toBe(2);
    const third = mount({ conversation: "project/gamma", initial: "Third" });
    await third.user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Voice is still sending an earlier recording. Typing works.");
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(third.field).toHaveValue("Third");
    await third.user.type(third.field, " typed");
    expect(third.field).toHaveValue("Third typed");
    server.connection = "ok";
    await waitFor(() => expect(HostCapture.retained.size).toBe(0), { timeout: 5000 });
    await third.user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText("Starting voice…")).toBeInTheDocument();
    await third.user.click(screen.getByRole("button", { name: "Cancel voice input" }));
  });

  it("host voice: an interrupted microphone lands the words and says why", async () => {
    const { track } = installVoiceBrowser();
    hostVoice("spoken", "spoken words");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Draft", onSubmit });
    await listen(user);
    act(() => { track.dispatchEvent(new Event("ended")); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice stopped: the microphone was interrupted. Typing works.");
    expect(field).toHaveValue("Draft spoken words");
    expect(field).not.toHaveAttribute("readonly");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("host voice: a 409 on a chunk ends at once with the words shown and reads the voice setting again", async () => {
    installVoiceBrowser({ recognition: true });
    const server = hostVoice("kept words", null, (path) => path === "/api/voice"
      ? jsonResponse({ backend: "browser", selection: "fixture-browser", host: { state: "ready", download_bytes: 0 } })
      : jsonResponse({ error: "not found" }, 404));
    const { user, field } = mount({ initial: "Draft" });
    await listen(user);
    expect(field).toHaveValue("Draft kept words");
    server.refuse = { on: "audio", status: 409, error: "Voice settings changed. Record again with the new setting." };
    speak();
    expect(await screen.findByRole("alert")).toHaveTextContent("Voice settings changed. Record again with the new setting. Typing works.");
    expect(field).toHaveValue("Draft kept words");
    expect(field).not.toHaveAttribute("readonly");
    expect(server.audio.some((call) => call.final)).toBe(false);
    await waitFor(() => expect(server.fetch).toHaveBeenCalledWith("/api/voice", expect.anything()));
    // The next capture uses the backend the server now reports.
    await user.click(await screen.findByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    expect(HostCapture.retained.size).toBe(0);
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
  });

  it.each([
    [{ state: "absent", download_bytes: 698_000_000 }, "Voice needs a one-time download on this computer. Set up voice"],
    [{ state: "outdated", download_bytes: 698_000_000 }, "Voice needs an update on this computer. Set up voice"],
    [{ state: "setting-up", download_bytes: 698_000_000, done_bytes: 1 }, "Voice is being set up on this computer. Typing works."],
  ] as const)("host voice not set up (%o): the mic explains instead of opening the microphone", async (host, hint) => {
    const { getUserMedia } = installVoiceBrowser({ backend: "host", host });
    const fetchMock = vi.fn(async () => jsonResponse({ backend: "host", selection: "fixture-host", url: "", model: "", key_set: false, host }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MemoryRouter><Harness /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText(hint.split(" Set up voice")[0]!, { exact: false })).toBeInTheDocument();
    if (hint.endsWith("Set up voice")) expect(await screen.findByRole("link", { name: "Set up voice" })).toHaveAttribute("href", "/settings/voice");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it.each([
    [{ state: "unavailable", reason: "voice runs on Linux x86_64 only for now" }, "Voice isn't available on this computer: voice runs on Linux x86_64 only for now. Typing works."],
    [{ state: "failed", download_bytes: 698_000_000, reason: "a download failed" }, "Voice setup did not finish. Retry in Settings. Typing works."],
  ] as const)("host voice that cannot run here (%o) hides the mic and says why", async (host, hint) => {
    installVoiceBrowser({ backend: "host", host });
    render(<MemoryRouter><Harness /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "Start voice input" })).toBeNull();
    expect(screen.getByText((_, node) => node?.classList.contains("composer-hint") === true && node.textContent === hint)).toBeInTheDocument();
  });

  // ---- browser recognition: the default backend shows words while listening and uploads nothing ----
  it("browser recognition: words appear while listening, the last phrase may change, Stop lands them and nothing else appears", async () => {
    installVoiceBrowser({ backend: "browser" });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    expect(recognizer).toMatchObject({ continuous: true, interimResults: true, started: 1 });
    expect(HostCapture.retained.size).toBe(0);
    expect(field).toHaveAttribute("readonly");

    act(() => recognizer!.hear([], "and the"));
    expect(field).toHaveValue("Fix the timer and the");
    act(() => recognizer!.hear([], "and the tets"));
    expect(field).toHaveValue("Fix the timer and the tets");
    act(() => recognizer!.hear(["and the tests"], "on"));
    expect(field).toHaveValue("Fix the timer and the tests on");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("Listening… Stop to add text, or Send.");

    act(() => recognizer!.hear(["and the tests", "on both sizes"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(recognizer!.stopped).toBe(1);
    expect(field).toHaveValue("Fix the timer and the tests on both sizes");
    expect(field.selectionStart).toBe("Fix the timer and the tests on both sizes".length);
    expect(document.activeElement).toBe(field);
    expect(screen.queryByText("Transcribing…")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    // Landed text is an ordinary draft: editing and sending work as with typed text.
    await user.type(field, "!");
    expect(field).toHaveValue("Fix the timer and the tests on both sizes!");
  });

  it("browser recognition: punctuated words land after Stop; without the model they land as heard and the hint says so", async () => {
    installVoiceBrowser({ backend: "browser" });
    punctuationFixture.punctuate = sentence;
    const { user, field } = mount({});
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[0]!.hear(["merge the pr today"]));
    await waitFor(() => expect(field).toHaveValue("Merge the pr today."));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(field).toHaveValue("Merge the pr today.");
    expect(screen.queryByText(/without punctuation/)).toBeNull();

    await user.clear(field);
    punctuationFixture.load = () => Promise.reject(new Error("no WebAssembly"));
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[1]!.hear(["merge the pr today"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(field).toHaveValue("merge the pr today");
    expect(screen.getByRole("status")).toHaveTextContent("Added without punctuation: this browser could not run it.");
    // The next capture clears it.
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    expect(screen.queryByText(/without punctuation/)).toBeNull();
  });

  it("browser recognition: Send while listening waits for the last phrase's punctuation and sends it", async () => {
    installVoiceBrowser({ backend: "browser" });
    let answer: (() => void) | undefined;
    punctuationFixture.load = () => Promise.resolve({
      punctuate: (words) => new Promise<string[]>((resolve) => { answer = () => resolve(sentence(words)); }),
    });
    const onSubmit = vi.fn();
    const { user } = mount({ initial: "Done with the timer.", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[0]!.hear(["merge the pr today"]));
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Transcribing…")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
    await waitFor(() => expect(answer).toBeDefined());
    act(() => answer!());
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("Done with the timer. Merge the pr today.", expect.any(Function)));
  });

  it("browser recognition: words from before the model has loaded land as heard, and the hint says it was loading", async () => {
    installVoiceBrowser({ backend: "browser" });
    punctuationFixture.load = () => new Promise(() => undefined);
    const { user, field } = mount({});
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[0]!.hear(["merge the pr today"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"), { timeout: 5000 });
    expect(field).toHaveValue("merge the pr today");
    expect(screen.getByRole("status")).toHaveTextContent("Added without punctuation: still loading. Next time it will be ready.");
  });

  it("browser recognition: the field follows the latest words once they overflow its height, and stops following after Stop", async () => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "Fix the timer" });
    const scrolls: number[] = [];
    Object.defineProperty(field, "scrollHeight", { value: 640, configurable: true });
    Object.defineProperty(field, "scrollTop", { get: () => 0, set: (top: number) => scrolls.push(top), configurable: true });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    scrolls.length = 0;
    act(() => recognizer!.hear(["and the tests on both sizes"], "and then"));
    expect(scrolls.at(-1)).toBe(640);
    recognizer!.answersStop = false;
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    scrolls.length = 0;
    // The last phrase that arrives while Stop waits for the recognizer stays in view too.
    act(() => recognizer!.hear(["and the tests on both sizes and then some"]));
    expect(scrolls.at(-1)).toBe(640);
    act(() => recognizer!.silence());
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    scrolls.length = 0;
    await user.type(field, "!");
    expect(scrolls).toEqual([]);
  });

  it("browser recognition: Send at once submits the draft and the recognized words through the normal path", async () => {
    installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    act(() => recognizer!.hear(["and the tests"]));
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith("Fix the timer and the tests", expect.any(Function)));
    expect(field).toHaveValue("");
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("browser recognition: Send with nothing recognized sends nothing and keeps the draft", async () => {
    installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep this", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled());
    expect(field).toHaveValue("Keep this");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("browser recognition: Cancel discards the words heard so far and restores the draft", async () => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "Keep this" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    act(() => recognizer!.hear(["forget this"], "and this"));
    expect(field).toHaveValue("Keep this forget this and this");
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(field).toHaveValue("Keep this");
    expect(screen.queryByText("forget this")).toBeNull();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("browser recognition: keeps listening when the recognizer ends on silence, retaining the words so far", async () => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    act(() => recognizer!.hear(["first part"], "sec"));
    act(() => recognizer!.silence());
    expect(recognizer!.started).toBe(2);
    expect(screen.getByRole("button", { name: "Stop voice input" })).toBeInTheDocument();
    expect(field).toHaveValue("first part");
    act(() => recognizer!.hear(["second part"]));
    expect(field).toHaveValue("first part second part");
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
    expect(field).toHaveValue("first part second part");
  });

  it("browser recognition: a recognizer error keeps the words already shown and says typing works; a refusal shows Denied and drops them", async () => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "still here" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[0]!.fail("network"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(field).toHaveValue("still here");
    expect(field).not.toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[1]!.hear(["two minutes of"], "dictation"));
    act(() => FakeSpeechRecognition.instances[1]!.fail("network"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(field).toHaveValue("still here two minutes of dictation");
    expect(field).not.toHaveAttribute("readonly");

    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[2]!.hear([], "half"));
    act(() => FakeSpeechRecognition.instances[2]!.fail("not-allowed"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone blocked in the browser. Typing works.");
    expect(field).toHaveValue("still here two minutes of dictation");
    // A refusal can be transient (Safari's speech service briefly unavailable): the next tap asks again.
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    act(() => FakeSpeechRecognition.instances[3]!.hear(["works again"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("still here two minutes of dictation works again"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(["browser", "host"] as const)("%s connects each waveform before capture starts and closes each graph once", async (backend) => {
    installVoiceBrowser({ backend });
    if (backend === "host") hostVoice("", "new words");
    const order: string[] = [];
    const closes: ReturnType<typeof vi.fn>[] = [];
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("AudioContext", class {
      close = vi.fn(async () => undefined);
      constructor() { closes.push(this.close); }
      createAnalyser() { return { fftSize: 512 }; }
      createMediaStreamSource() { return { connect: () => order.push("connected") }; }
    });
    const prototype = (backend === "browser" ? FakeSpeechRecognition.prototype : HostCapture.prototype) as { start(): void };
    const start = prototype.start;
    vi.spyOn(prototype, "start").mockImplementation(function (this: unknown) {
      order.push("start");
      start.call(this);
    });
    const view = mount({ initial: "Draft" });
    for (const action of ["Cancel", "Cancel", "Stop", "navigation"]) {
      await view.user.click(screen.getByRole("button", { name: "Start voice input" }));
      await screen.findByRole("button", { name: "Stop voice input" });
      expect(order).toEqual(Array.from({ length: closes.length }, () => ["connected", "start"]).flat());
      if (backend === "browser") act(() => FakeSpeechRecognition.instances.at(-1)!.hear(["new words"]));
      else { speak(); await screen.findByText("Listening… Stop to add text, or Send."); }
      if (action === "navigation") view.unmount();
      else await view.user.click(screen.getByRole("button", { name: `${action} voice input` }));
      await waitFor(() => closes.forEach((close) => expect(close).toHaveBeenCalledOnce()));
      if (action === "Cancel") expect(view.field).toHaveValue("Draft");
      if (action === "Stop") await waitFor(() => expect(view.field).toHaveValue("Draft new words"));
    }
  });

  it.each([
    ["browser", "constructor"], ["browser", "start"], ["host", "start"],
  ] as const)("releases a prepared waveform when %s %s throws, then retries", async (backend, failure) => {
    const { track } = installVoiceBrowser({ backend });
    if (backend === "host") hostVoice("", "recovered");
    const close = vi.fn(async () => undefined);
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("AudioContext", class {
      createAnalyser() { return { fftSize: 512 }; }
      createMediaStreamSource() { return { connect: vi.fn() }; }
      close = close;
    });
    if (backend === "browser") vi.stubGlobal("SpeechRecognition", class extends FakeSpeechRecognition {
      constructor() { super(); if (failure === "constructor") throw new Error("fixture construction failure"); }
      start() { throw new DOMException("fixture startup failure", "InvalidStateError"); }
    });
    else vi.spyOn(HostCapture.prototype, "start").mockImplementationOnce(() => { throw new DOMException("fixture startup failure", "InvalidStateError"); });
    const { user, field } = mount({ initial: "Draft" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(track.stop).toHaveBeenCalled();
    expect(field).toHaveValue("Draft");
    expect(field).not.toHaveAttribute("readonly");
    vi.stubGlobal("SpeechRecognition", FakeSpeechRecognition);
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    if (backend === "browser") act(() => FakeSpeechRecognition.instances.at(-1)!.hear(["recovered"]));
    else { await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull()); speak(); await screen.findByText("Listening… Stop to add text, or Send."); }
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Draft recovered"));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(2));
  });

  it.each(["Cancel", "Stop", "Send", "navigation", "recording cap"])("browser recognition: %s finishes native capture and waveform shutdown before another microphone opens", async (action) => {
    const { getUserMedia } = installVoiceBrowser({ backend: "browser" });
    let closed!: () => void;
    const close = vi.fn(() => new Promise<void>((resolve) => { closed = resolve; }));
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("AudioContext", class {
      createAnalyser() { return { fftSize: 512 }; }
      createMediaStreamSource() { return { connect: vi.fn() }; }
      close = close;
    });
    const onSubmit = vi.fn();
    const source = mount({ initial: "Typed draft", onSubmit });
    if (action === "recording cap") {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
      await act(async () => screen.getByRole("button", { name: "Start voice input" }).click());
    } else await source.user.click(screen.getByRole("button", { name: "Start voice input" }));
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.answersAbort = recognizer.answersStop = false;
    act(() => recognizer.hear(["spoken words"]));
    if (action === "recording cap") {
      try { await act(async () => { await vi.advanceTimersByTimeAsync(595_000); }); }
      finally { vi.useRealTimers(); }
      expect(recognizer.stopped).toBe(1);
      expect(screen.getByText("Transcribing…")).toBeInTheDocument();
    } else if (action === "navigation") source.unmount();
    else await source.user.click(screen.getByRole("button", { name: action === "Send" ? "Send" : `${action} voice input` }));
    // The graph must stay alive until native recognition releases the microphone.
    expect(close).not.toHaveBeenCalled();
    await act(async () => recognizer.onend?.());
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    const destination = action === "navigation" ? mount({ initial: "Other draft" }) : source;
    await destination.user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(screen.getByText("Opening microphone…")).toBeInTheDocument();
    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(FakeSpeechRecognition.instances).toHaveLength(1);
    // Cancelling this wait must not acquire a microphone when the old close finally resolves.
    await destination.user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await act(async () => closed());
    expect(getUserMedia).toHaveBeenCalledOnce();
    await destination.user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    act(() => FakeSpeechRecognition.instances[1]!.hear(["restart works"]));
    await destination.user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(destination.field).toHaveValue(action === "Stop" || action === "recording cap" ? "Typed draft spoken words restart works" : action === "Send" ? "restart works" : action === "navigation" ? "Other draft restart works" : "Typed draft restart works"));
    await act(async () => closed());
    expect(close).toHaveBeenCalledTimes(2);
    expect(onSubmit).toHaveBeenCalledTimes(action === "Send" ? 1 : 0);
  });

  it.each(["browser", "host"] as const)("%s microphone can retry after a waveform close never answers", async (backend) => {
    const { getUserMedia } = installVoiceBrowser({ backend });
    if (backend === "host") hostVoice("", "new words");
    let lateClose!: () => void;
    const close = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { lateClose = resolve; })).mockResolvedValue(undefined);
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("AudioContext", class {
      createAnalyser() { return { fftSize: 512 }; }
      createMediaStreamSource() { return { connect: vi.fn() }; }
      close = close;
    });
    const { user, field } = mount({ initial: "Draft" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    vi.useFakeTimers();
    try {
      await act(async () => screen.getByRole("button", { name: "Cancel voice input" }).click());
      await act(async () => screen.getByRole("button", { name: "Start voice input" }).click());
      expect(close).toHaveBeenCalledOnce();
      await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
      expect(getUserMedia).toHaveBeenCalledOnce();
      expect(screen.getByText("Opening microphone…")).toBeInTheDocument();
      await act(async () => screen.getByRole("button", { name: "Cancel voice input" }).click());
      expect(field).toHaveValue("Draft");
      expect(field).toBeEnabled();
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(getUserMedia).toHaveBeenCalledOnce();
      await act(async () => screen.getByRole("button", { name: "Start voice input" }).click());
      expect(getUserMedia).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
    // The old graph can settle after its timeout and after a new graph is already listening.
    // It must not close the new graph or change this capture's phase.
    await act(async () => lateClose());
    expect(close).toHaveBeenCalledOnce();
    if (backend === "host") { await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull()); speak(); }
    expect(screen.getByText("Listening… Stop to add text, or Send.")).toBeInTheDocument();
    if (backend === "browser") act(() => FakeSpeechRecognition.instances[1]!.hear(["new words"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Draft new words"));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(2));
  });

  it.each([false, true])("browser recognition survives waveform close rejection (setup fails: %s)", async (setupFails) => {
    installVoiceBrowser({ backend: "browser" });
    const close = vi.fn(async () => { throw new Error("Audio renderer unavailable"); });
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("AudioContext", class {
      createAnalyser() {
        if (setupFails) throw new Error("Cannot create analyser");
        return { fftSize: 512 };
      }
      createMediaStreamSource() { return { connect: vi.fn() }; }
      close = close;
    });
    const { user, field } = mount({ initial: "Draft" });
    for (let cycle = 0; cycle < 2; cycle++) {
      await user.click(screen.getByRole("button", { name: "Start voice input" }));
      await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(cycle + 1));
      act(() => FakeSpeechRecognition.instances[cycle]!.hear(["words"]));
      await user.click(screen.getByRole("button", { name: "Stop voice input" }));
      await waitFor(() => expect(field).toHaveValue(`Draft${" words".repeat(cycle + 1)}`));
      await waitFor(() => expect(close).toHaveBeenCalledTimes(cycle + 1));
    }
  });

  it("browser recognition: Cancel returns at once; a restart waits for the cancelled recognizer to end, over repeated cycles, keeping the draft", async () => {
    const { getUserMedia, track } = installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "Keep this" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    for (let cycle = 0; cycle < 3; cycle++) {
      await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(cycle + 1));
      const recognizer = FakeSpeechRecognition.instances[cycle]!;
      recognizer.answersAbort = false;
      act(() => recognizer.hear([`discard ${cycle}`], "and this"));
      await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
      expect(field).toHaveValue("Keep this");
      expect(field).not.toHaveAttribute("readonly");
      expect(recognizer.aborted).toBe(1);
      // The recognizer still holds the microphone: its stream stays open, and a restart waits.
      expect(track.stop).toHaveBeenCalledTimes(cycle);
      await user.click(screen.getByRole("button", { name: "Start voice input" }));
      expect(await screen.findByText("Opening microphone…")).toBeInTheDocument();
      expect(getUserMedia).toHaveBeenCalledTimes(cycle + 1);
      expect(FakeSpeechRecognition.instances).toHaveLength(cycle + 1);
      act(() => recognizer.onend?.());
      expect(track.stop).toHaveBeenCalledTimes(cycle + 1);
    }
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(4));
    act(() => FakeSpeechRecognition.instances[3]!.hear(["kept words"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Keep this kept words"));
    expect(field).not.toHaveAttribute("readonly");
  });

  it("browser recognition: a cancelled recognizer that never ends holds a restart at most three seconds; its late words, refusal and end cannot touch the next capture", async () => {
    const { track } = installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep this", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    const old = FakeSpeechRecognition.instances[0]!;
    old.answersAbort = false;
    vi.useFakeTimers();
    try {
      await act(async () => { screen.getByRole("button", { name: "Cancel voice input" }).click(); });
      await act(async () => { screen.getByRole("button", { name: "Start voice input" }).click(); });
      expect(screen.getByText("Opening microphone…")).toBeInTheDocument();
      expect(track.stop).not.toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(track.stop).toHaveBeenCalledOnce();
      expect(FakeSpeechRecognition.instances).toHaveLength(2);
    } finally { vi.useRealTimers(); }
    await screen.findByRole("button", { name: "Stop voice input" });
    const next = FakeSpeechRecognition.instances[1]!;
    act(() => { old.hear(["stale words"]); old.fail("not-allowed"); });
    act(() => next.hear(["new words"]));
    expect(field).toHaveValue("Keep this new words");
    expect(field).toHaveAttribute("readonly");
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("Keep this new words", expect.any(Function)));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("browser recognition: cancelling a restart that waits for a cancelled recognizer leaves its microphone to that recognizer's end", async () => {
    const { getUserMedia, track } = installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "Keep this" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    const old = FakeSpeechRecognition.instances[0]!;
    old.answersAbort = false;
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText("Opening microphone…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Keep this");
    expect(field).not.toHaveAttribute("readonly");
    expect(track.stop).not.toHaveBeenCalled();
    act(() => old.onend?.());
    expect(track.stop).toHaveBeenCalledOnce();
    await act(async () => undefined);
    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(FakeSpeechRecognition.instances).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it.each(["browser", "host"] as const)("%s backend: the X returns focus to the microphone, never the field, so no phone keyboard opens; Escape returns to the field", async (backend) => {
    installVoiceBrowser({ backend });
    hostVoice("", "never used");
    const { user, field } = mount({ initial: "Keep this" });
    const mic = () => screen.getByRole("button", { name: "Start voice input" });
    for (const cancelBy of ["X", "X", "Escape"]) {
      await user.click(mic());
      await screen.findByRole("button", { name: "Stop voice input" });
      if (backend === "browser") await waitFor(() => expect(FakeSpeechRecognition.instances.at(-1)?.running).toBe(true));
      if (cancelBy === "X") await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
      else await user.keyboard("{Escape}");
      await waitFor(() => expect(field).not.toHaveAttribute("readonly"));
      if (cancelBy === "X") {
        await waitFor(() => expect(mic()).toHaveFocus());
        expect(field).not.toHaveFocus();
      } else await waitFor(() => expect(field).toHaveFocus());
      expect(field).toHaveValue("Keep this");
    }
  });

  it("browser recognition: the X on a voice Send restores the draft and focuses the microphone, not the field", async () => {
    installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    FakeSpeechRecognition.instances[0]!.answersStop = false;
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(field).toHaveValue("Keep"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toHaveFocus());
    expect(field).not.toHaveFocus();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("browser recognition: Send right after Cancel sends the typed draft once, not the discarded words", async () => {
    installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Typed only", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    const recognizer = FakeSpeechRecognition.instances[0]!;
    recognizer.answersAbort = false;
    act(() => recognizer.hear(["discarded words"]));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledWith("Typed only", expect.any(Function));
    act(() => recognizer.onend?.());
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(field).toHaveValue("");
    expect(field).not.toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("browser recognition: cancelling a voice Send while the recognizer settles restores the draft, and a restart waits for that recognizer", async () => {
    const { getUserMedia } = installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(1));
    const old = FakeSpeechRecognition.instances[0]!;
    old.answersStop = false;
    old.answersAbort = false;
    act(() => old.hear(["unsent words"]));
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(field).toHaveValue("Keep"));
    expect(old.aborted).toBe(1);
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText("Opening microphone…")).toBeInTheDocument();
    expect(getUserMedia).toHaveBeenCalledOnce();
    act(() => old.onend?.());
    await waitFor(() => expect(FakeSpeechRecognition.instances).toHaveLength(2));
    act(() => FakeSpeechRecognition.instances[1]!.hear(["fresh"]));
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Keep fresh"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("browser recognition: Send with a recognizer error lands the words in the draft instead of sending", async () => {
    installVoiceBrowser({ backend: "browser" });
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Keep", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    act(() => recognizer!.hear(["these words"]));
    await user.click(screen.getByRole("button", { name: "Send" }));
    act(() => recognizer!.fail("network"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(field).toHaveValue("Keep these words");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("browser recognition: Cancel discards at once even while the recognizer is still settling, and late words stay out", async () => {
    installVoiceBrowser({ backend: "browser" });
    const { user, field } = mount({ initial: "Keep this" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    const [recognizer] = FakeSpeechRecognition.instances;
    recognizer!.answersStop = false;
    act(() => recognizer!.hear(["forget this"]));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Keep this");
    expect(field).not.toHaveAttribute("readonly");
    expect(recognizer!.aborted).toBe(1);
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
    act(() => recognizer!.hear(["forget this", "and this"]));
    expect(field).toHaveValue("Keep this");
    await user.click(screen.getByRole("button", { name: "Send" }));
  });

  it("browser recognition: no recognizer in this browser hides the mic and says so; the backend read hides the mic until it answers", async () => {
    installVoiceBrowser({ backend: "browser", recognition: false });
    const first = render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.getByText("This browser has no speech recognition. Typing works.")).toBeInTheDocument();
    first.unmount();

    installVoiceBrowser({ backend: "browser" });
    const second = render(<Harness />);
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeInTheDocument();
    expect(screen.queryByText("This browser has no speech recognition. Typing works.")).toBeNull();
    second.unmount();

    let answer: (backend: string) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).endsWith("/api/voice")) return jsonResponse({ error: "not found" }, 404);
      const backend = await new Promise<string>((resolve) => (answer = resolve));
      return jsonResponse({ backend, selection: `fixture-${backend}`, url: "", model: "", key_set: false, host: { state: "ready", download_bytes: 0 } });
    }));
    presetVoiceBackend(null);
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    answer("browser");
    expect(await screen.findByRole("button", { name: "Start voice input" })).toBeInTheDocument();
  });

  it("host voice sends with the capture's selection even when Settings changes while listening", async () => {
    installVoiceBrowser();
    const server = hostVoice("", "spoken");
    const { user, field } = mount({ initial: "Keep this" });
    await listen(user);
    act(() => updateVoiceSettings({ backend: "host", selection: "new-selection", host: { state: "ready", download_bytes: 0 } }));
    speak();
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Keep this spoken"));
    expect(server.calls.map((call) => call.selection)).toEqual(["fixture-host", "fixture-host", "fixture-host", "fixture-host"]);
  });

  it("combines a draft and a transcript with one space, and formats the timer", () => {
    expect(combineDraft("", "  hi ")).toBe("hi");
    expect(combineDraft("a", "b")).toBe("a b");
    expect(combineDraft("a ", "b")).toBe("a b");
    expect(combineDraft("a", "")).toBe("a");
    expect(formatTimer(0)).toBe("0:00");
    expect(formatTimer(65_000)).toBe("1:05");
    expect(formatTimer(594_999)).toBe("9:54");
  });
});

describe("Composer live words", () => {
  beforeEach(() => reduceMotion(false));

  const heard = "check the build, then the timer, and after that the tests on both phones";

  it("flow in after the draft while listening; Stop lands the whole final text at once, editable", async () => {
    installVoiceBrowser();
    const server = hostVoice(heard, `${heard}.`);
    const { user, field } = mount({ initial: "Please" });
    await listen(user);
    await waitFor(() => expect(field.value.length).toBeGreaterThan("Please ".length));
    expect(field.value).not.toBe(`Please ${heard}`);
    expect(`Please ${heard}`.startsWith(field.value)).toBe(true);
    expect(server.audio.length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue(`Please ${heard}.`));
    expect(field).not.toHaveAttribute("readonly");
  });

  it("Send while the words are still flowing sends the whole final text", async () => {
    installVoiceBrowser();
    hostVoice(heard, `${heard}.`);
    const onSubmit = vi.fn((_text: string, accepted: () => void) => accepted());
    const { user, field } = mount({ initial: "Please", onSubmit });
    await listen(user);
    await waitFor(() => expect(field.value.length).toBeGreaterThan("Please ".length));
    expect(field.value).not.toBe(`Please ${heard}`);
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(`Please ${heard}.`, expect.any(Function)));
    expect(field).toHaveValue("");
  });

  it("a recording that stops early while the words are still flowing lands every recognized word", async () => {
    installVoiceBrowser();
    const server = hostVoice(heard);
    const { user, field } = mount({ initial: "Please" });
    await listen(user);
    await waitFor(() => expect(field.value.length).toBeGreaterThan("Please ".length));
    expect(field.value).not.toBe(`Please ${heard}`);
    server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    act(() => hostMicrophone.speak(8000));
    expect(await screen.findByText("Voice stopped: the speech process stopped. Typing works.")).toBeInTheDocument();
    expect(field).toHaveValue(`Please ${heard}`);
    expect(field).not.toHaveAttribute("readonly");
  });

  it("Cancel while the words are still flowing restores the draft with nothing added", async () => {
    installVoiceBrowser();
    hostVoice(heard);
    const { user, field } = mount({ initial: "Please" });
    await listen(user);
    await waitFor(() => expect(field.value.length).toBeGreaterThan("Please ".length));
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(field).toHaveValue("Please");
    await act(() => new Promise((resolve) => setTimeout(resolve, 100)));
    expect(field).toHaveValue("Please");
  });
});
