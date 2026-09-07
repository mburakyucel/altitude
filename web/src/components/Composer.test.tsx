import { screen, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import Composer, { combineDraft, formatTimer } from "./Composer";
import type { ComposerProps } from "./Composer";
import { FakeMediaRecorder, installVoiceBrowser } from "./voiceTest";

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
  render(<Harness {...props} />);
  return { user, field: screen.getByLabelText("Message L3 about altitude") as HTMLTextAreaElement };
}

/** /api/transcribe answers `text` (or 503 for null); `gate` holds the answer until released. */
function stubTranscribe(text: string | null, gate?: Promise<void>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).includes("/api/transcribe")) return jsonResponse({ error: "not found" }, 404);
      await gate;
      return text == null ? jsonResponse({ error: "speech service unavailable" }, 503) : jsonResponse({ text });
    }),
  );
}

describe("Composer", () => {
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
    expect(onSubmit).toHaveBeenCalledWith("hello\nthere");
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
    expect(onSubmit).toHaveBeenCalledWith("later please");
    expect(field).toHaveValue("");
    expect(queue).toBeDisabled();
  });

  it("refused: the draft returns, the hint reads Not sent. Retry, and Retry sends the same text", async () => {
    let refuse = true;
    const onSubmit = vi.fn(async () => {
      if (refuse) throw new Error("409");
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
    expect(onSubmit).toHaveBeenLastCalledWith("ship it");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(field).toHaveValue("");
    expect(screen.getByText("L3 answers or creates one task.")).toBeInTheDocument();
  });

  it.each(["Escape", "Cancel"])("Listening: Cancel, Stop, Send; %s goes back with nothing added", async (action) => {
    const { getUserMedia, track } = installVoiceBrowser();
    stubTranscribe("never used");
    const { user, field } = mount({ initial: "Keep this" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));

    const stop = await screen.findByRole("button", { name: "Stop voice input" });
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(screen.getByRole("button", { name: "Cancel voice input" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send" }).textContent).toBe("");
    expect(screen.getByLabelText("Recording time")).toHaveTextContent("0:00");
    expect(field).toHaveAttribute("placeholder", "");
    expect(field).toHaveValue("Keep this");

    stop.focus();
    if (action === "Escape") await user.keyboard("{Escape}");
    else await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeInTheDocument());
    expect(field).toHaveValue("Keep this");
    expect(track.stop).toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).includes("/api/transcribe"))).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Transcribing then Landed: the transcript lands in the draft, cursor at the end, and nothing else appears", async () => {
    installVoiceBrowser();
    let release: () => void = () => {};
    stubTranscribe("and the tests", new Promise<void>((resolve) => (release = resolve)));
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice input" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText("Transcribing…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop voice input" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(field).toBeEnabled();

    release();
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
    let release: () => void = () => {};
    stubTranscribe("and the tests", new Promise<void>((resolve) => (release = resolve)));
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Fix the timer", onSubmit, busy: action === "Busy" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    const stop = await screen.findByRole("button", { name: "Stop voice input" });
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
    release();
    await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith("Fix the timer and the tests"));
    expect(field).toHaveValue("");
    expect(screen.queryByText("Transcribing…")).toBeNull();
  });

  it.each(["", "Keep this"])("Send at once: an empty transcript sends nothing and keeps draft '%s'", async (initial) => {
    installVoiceBrowser();
    stubTranscribe("  ");
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial, onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    field.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled());
    expect(field).toHaveValue(initial);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(!initial);
  });

  it.each([false, true])("Send at once uses the current submit callback and disabled=%s after transcription", async (disabled) => {
    installVoiceBrowser();
    let release: () => void = () => {};
    stubTranscribe("the transcript", new Promise<void>((resolve) => (release = resolve)));
    const original = vi.fn();
    const latest = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<Harness initial="Keep" onSubmit={original} />);
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Transcribing…");
    rerender(<Harness onSubmit={latest} disabled={disabled} />);
    release();
    await waitFor(() => expect(screen.queryByText("Transcribing…")).toBeNull());
    expect(original).not.toHaveBeenCalled();
    if (disabled) {
      expect(latest).not.toHaveBeenCalled();
      expect(screen.getByRole("textbox")).toHaveValue("Keep the transcript");
    } else {
      expect(latest).toHaveBeenCalledExactlyOnceWith("Keep the transcript");
      expect(screen.getByRole("textbox")).toHaveValue("");
    }
  });

  it.each(["Stop voice input", "Send"])("failure after %s: the hint reports it, the draft is unchanged, nothing sends", async (control) => {
    installVoiceBrowser();
    stubTranscribe(null);
    const onSubmit = vi.fn();
    const { user, field } = mount({ initial: "Draft stays", onSubmit });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.click(screen.getByRole("button", { name: control }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not transcribe. Typing works.");
    expect(field).toHaveValue("Draft stays");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  });

  it("Denied: the mic shows disabled and the hint says typing works", async () => {
    installVoiceBrowser();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    const { user, field } = mount({ initial: "still here" });
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    expect(await screen.findByText("Microphone blocked in the browser. Typing works.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeDisabled();
    expect(field).toHaveValue("still here");
    await user.type(field, " and typing");
    expect(field).toHaveValue("still here and typing");
  });

  it("Unavailable: no mic and Voice needs HTTPS on an insecure origin; no mic and no hint without recording", () => {
    installVoiceBrowser();
    vi.stubGlobal("isSecureContext", false);
    const first = render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.getByText("Voice needs HTTPS")).toBeInTheDocument();
    first.unmount();

    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("MediaRecorder", undefined);
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /voice input/ })).toBeNull();
    expect(screen.queryByText("Voice needs HTTPS")).toBeNull();
  });

  it("Ctrl+M starts and stops the microphone from the field", async () => {
    installVoiceBrowser();
    stubTranscribe("by keyboard");
    const { user, field } = mount();
    field.focus();
    await user.keyboard("{Control>}m{/Control}");
    await screen.findByRole("button", { name: "Stop voice input" });
    await user.keyboard("{Control>}m{/Control}");
    await waitFor(() => expect(field).toHaveValue("by keyboard"));
    expect(FakeMediaRecorder.instances[0]?.stopCalls).toBe(1);
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
