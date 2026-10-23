import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { describe, expect, it, onTestFinished, vi } from "vitest";
import { ApiError } from "../data/api";
import Composer, { returnToComposer } from "./Composer";
import type { ComposerProps } from "./Composer";
import type { ImageSubmission } from "./ImageDraft";
import { ImageViewerHost, MessageImages } from "./MessageImages";
import { ShownFiles } from "./Shown";
import { hostMicrophone, hostVoiceServer, installVoiceBrowser, speak } from "./voiceTest";

const capability = { available: true, max_count: 4, max_bytes: 10 << 20, max_total_bytes: 20 << 20, max_pixels: 25_000_000, max_dimension: 8192 };
const image = () => new File(["fictional raster"], "screen.png", { type: "image/png" });
let capabilityRead: Promise<unknown> | undefined;
const json = (body: unknown, status = 200) => {
  const response = new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  if (body && typeof body === "object" && "available" in body) {
    const parsed = response.json();
    capabilityRead = parsed;
    response.json = () => parsed;
  }
  return response;
};

function browser(available = true) {
  capabilityRead = undefined;
  const revoke = vi.fn();
  let next = 0;
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL() { return `blob:image-${++next}`; }
    static override revokeObjectURL = revoke;
  });
  vi.stubGlobal("Image", class {
    naturalWidth = 640; naturalHeight = 360;
    onload: (() => void) | null = null;
    set src(_url: string) { queueMicrotask(() => this.onload?.()); }
  });
  vi.stubGlobal("fetch", vi.fn(async () => json({ ...capability, available, reason: available ? undefined : "Image decoder unavailable." })));
  return revoke;
}

/** This computer's speech service, answering `final` at the end; every other request reads image input. */
function hostVoice(final: string) {
  return hostVoiceServer({ final, fallback: async () => json(capability) }).install();
}

/** Start host voice and say half a second, so Send and Stop act on a listening capture. */
async function listen(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Start voice input" }));
  await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
  speak();
  await screen.findByText("Listening… Stop to add text, or Send.");
}

/** jsdom has no modal dialogs; opening only needs the dialog shown. */
function modal() {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  onTestFinished(() => { delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal; });
}

function Harness({ initial = "Explain this", ...props }: Partial<ComposerProps> & { initial?: string }) {
  const [text, setText] = useState(initial);
  return <Composer conversation={`project/${props.imageScope?.project ?? "alpha"}`} value={text} onChange={setText} onSubmit={() => undefined} ariaLabel="Message" placeholder="Message" imageScope={{ project: "alpha" }} {...props} />;
}

/**
 * A page's outbox (Conversation.tsx, Task.tsx): a sent message shows its copy, through view changes and remounts,
 * until the send is accepted. `keep` wraps a send so; only a reload or pairing again loses the outbox (`forget`).
 */
function outbox() {
  const shown = new Set<string>();
  return {
    shows: (copy: string) => shown.has(copy),
    forget: () => shown.clear(),
    keep: (submit: ComposerProps["onSubmit"]): ComposerProps["onSubmit"] => (text, accepted, images, copy) => {
      shown.add(copy!);
      return submit(text, () => { shown.delete(copy!); accepted(); }, images, copy);
    },
  };
}

async function select(files: File[] = [image()]) {
  // Request start does not mean the capability body has reached React yet.
  await act(async () => { await capabilityRead; });
  fireEvent.change(screen.getByLabelText("Choose images"), { target: { files } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Add images" })).toBeEnabled());
  await screen.findByLabelText("Selected images");
}

describe("image draft admission", () => {
  it("voice navigation sends image bytes through the original callback, and its failure leaves another draft alone", async () => {
    browser();
    installVoiceBrowser();
    const server = hostVoice("spoken");
    const original = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission) => {
      if (original.mock.calls.length === 1) throw new TypeError("Lost image receipt");
    });
    const destination = vi.fn();
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    const first = render(<Harness onSubmit={keep(original)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await listen(user);
    server.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    first.unmount();
    render(<Harness initial="Other unsent recovery" onSubmit={keep(destination)} shows={shows} />);
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    await act(async () => server.reconnect());
    await waitFor(() => expect(original).toHaveBeenCalledOnce());
    const sent = original.mock.calls[0]?.[2];
    expect(sent?.images).toEqual([{ name: "screen.png", data: btoa("fictional raster") }]);
    expect(sent?.previews[0]?.url).toBe(`data:image/png;base64,${btoa("fictional raster")}`);
    // The conversation shows the failure on its message; the field keeps what was typed since.
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Other unsent recovery"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(destination).not.toHaveBeenCalled();
  });

  it("keeps selection local, removes the strip and URLs, and leaves text intact", async () => {
    const revoke = browser();
    const user = userEvent.setup();
    render(<Harness />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    await select();
    expect(fetch).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Remove image screen.png" }));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
  });

  it("a refused message's words and images edited and sent again go under a fresh identity", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission, _copy?: string) => { if (onSubmit.mock.calls.length === 1) throw new ApiError(422, "Choose a smaller image."); });
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    render(<Harness conversation="project/edited-images" onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    // The message shows the refusal; the field and picker stay as Send left them until Edit.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    const [text, , sent] = onSubmit.mock.calls[0]!;
    await act(async () => returnToComposer("project/edited-images", text, sent!.previews));
    await screen.findByLabelText("Selected images");
    await user.type(screen.getByRole("textbox"), " more");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit.mock.calls[1]?.[0]).toBe("Explain this more");
    expect(onSubmit.mock.calls[1]?.[2]?.images).toEqual(sent!.images);
    expect(onSubmit.mock.calls[1]?.[2]?.request_id).not.toBe(sent!.request_id);
  });

  it("where the message shows its own failure, Send hands the images to it and Edit returns them to the picker", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, _accepted: () => void, _images?: ImageSubmission, _copy?: string) => { throw new ApiError(422, "refused"); });
    const { shows, keep } = outbox();
    const user = userEvent.setup();
    render(<Harness conversation="project/edit-images" onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const [, , submission, copy] = onSubmit.mock.calls[0]!;
    expect(copy).toEqual(expect.any(String));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.queryByText("Sending images…")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    await user.type(screen.getByRole("textbox"), "Typed since");
    await act(async () => returnToComposer("project/edit-images", "Explain this", submission!.previews));
    expect(screen.getByRole("textbox")).toHaveValue("Explain this\nTyped since");
    const strip = await screen.findByLabelText("Selected images");
    expect(strip.querySelectorAll("img")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
  });

  it("where the message shows its own state, the next message sends while images are still on their way", async () => {
    browser();
    let accept!: () => void;
    const onSubmit = vi.fn((_text: string, accepted: () => void, images?: ImageSubmission) =>
      images ? new Promise<void>((resolve) => { accept = () => { accepted(); resolve(); }; }) : Promise.resolve());
    const user = userEvent.setup();
    render(<Harness conversation="project/next-during-images" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    await user.type(screen.getByRole("textbox"), "And this next");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit.mock.calls[1]![0]).toBe("And this next");
    // Images chosen meanwhile stay when the first message is accepted.
    await select();
    await act(async () => accept());
    expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(1);
  });

  it("where the message shows its own state, Send hands its images over at once and the next message follows in order", async () => {
    browser();
    const onSubmit = vi.fn(async (_text: string, accepted: () => void, _images?: ImageSubmission) => { accepted(); });
    const user = userEvent.setup();
    render(<Harness conversation="project/send-order" initial="" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.type(screen.getByRole("textbox"), "With the image");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    await user.type(screen.getByRole("textbox"), "Words only");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit.mock.calls.map(([text, , images]) => [text, images?.images?.length])).toEqual([["With the image", 1], ["Words only", undefined]]);
  });

  it("names an image it cannot read when it is chosen, keeping the others", async () => {
    browser();
    const read = FileReader.prototype.readAsDataURL;
    const spy = vi.spyOn(FileReader.prototype, "readAsDataURL").mockImplementation(function (this: FileReader, blob: Blob) {
      if ((blob as File).name === "broken.png") { setTimeout(() => this.onerror?.(new ProgressEvent("error") as ProgressEvent<FileReader>)); return; }
      read.call(this, blob);
    });
    try {
      render(<Harness conversation="project/unreadable" />);
      await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
      await select([image(), new File(["fictional raster"], "broken.png", { type: "image/png" })]);
      expect(await screen.findByRole("alert")).toHaveTextContent("broken.png could not be read.");
      expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(1);
    } finally { spy.mockRestore(); }
  });

  it("Edit changes nothing when the message's images do not fit beside the picker's, and says why", async () => {
    browser();
    render(<Harness conversation="project/full-picker" initial="" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select([image(), image(), image(), image()]);
    const preview = { name: "earlier.png", url: "data:image/png;base64,iVBORw0KGgo=" };
    let returned = true;
    await act(async () => { returned = returnToComposer("project/full-picker", "Earlier words", [preview]); });
    expect(returned).toBe(false);
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByLabelText("Selected images").querySelectorAll("img")).toHaveLength(4);
    expect(screen.getByRole("alert")).toHaveTextContent("Up to 4 images per message. Remove an image to add another.");
  });

  it("rejects format, count, per-file and aggregate excess without replacing valid selections", async () => {
    browser();
    render(<Harness />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await select([new File(["svg"], "drawing.svg", { type: "image/svg+xml" })]);
    expect(screen.getByRole("alert")).toHaveTextContent("Choose PNG, JPEG or static WebP.");
    const large = new File(["x"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(large, "size", { value: (10 << 20) + 1 });
    await select([large]);
    expect(screen.getByRole("alert")).toHaveTextContent("exceeds 10 MiB");
    const ten = new File(["x"], "ten.png", { type: "image/png" });
    Object.defineProperty(ten, "size", { value: 10 << 20 });
    await select([ten, ten]);
    expect(screen.getByRole("alert")).toHaveTextContent("exceed 20 MiB total");
    fireEvent.click(screen.getByRole("button", { name: "Remove image ten.png" }));
    await select([image(), image(), image(), image()]);
    expect(screen.getAllByRole("button", { name: "Remove image screen.png" })).toHaveLength(4);
    expect(screen.getByRole("alert")).toHaveTextContent("Up to 4 images");
  });

  it("allows image-only sends; unavailable input leaves ordinary text usable", async () => {
    browser();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    const view = render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.clear(screen.getByRole("textbox"));
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0]).toBe("");
    view.unmount();
    browser(false);
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "Add images" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Image input unavailable. Image decoder unavailable.");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("keeps previews through voice cancel, and sends only a nonempty transcript with images", async () => {
    browser();
    installVoiceBrowser();
    hostVoice("the spoken explanation");
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await listen(user);
    expect(screen.queryByRole("button", { name: "Add images" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Cancel voice input" }));
    expect(screen.getByLabelText("Selected images")).toBeVisible();
    await listen(user);
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0]).toBe("Explain this the spoken explanation");
    expect(onSubmit.mock.calls[0]?.[2]?.images).toHaveLength(1);
  });

  it("releases local previews once sent, and a late refusal after navigation cannot restore another draft", async () => {
    const revoke = browser();
    let refuse: (error: Error) => void = () => undefined;
    const onSubmit = () => new Promise<void>((_resolve, reject) => { refuse = reject; });
    const user = userEvent.setup();
    const view = render(<Harness key="alpha" onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    // The message's bubble shows the images from Send; the picker lets go of its own previews.
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
    view.rerender(<Harness key="beta" initial="Beta draft" imageScope={{ project: "beta" }} />);
    await act(async () => refuse(new ApiError(422, "refused")));
    expect(screen.getByRole("textbox")).toHaveValue("Beta draft");
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("recovers an unconfirmed image caption after a reload, without persisting or resending the image", async () => {
    browser();
    const onSubmit = vi.fn(async () => { throw new TypeError("Lost receipt"); });
    const { shows, forget, keep } = outbox();
    const user = userEvent.setup();
    const view = render(<Harness onSubmit={keep(onSubmit)} shows={shows} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("");
    view.unmount();
    // A reload loses the message that showed the caption.
    forget();
    render(<Harness initial="" onSubmit={keep(onSubmit)} shows={shows} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the conversation before sending again.");
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(sessionStorage.getItem("altitude.submitted:project/alpha")).toContain("Explain this");
    expect(sessionStorage.getItem("altitude.submitted:project/alpha")).not.toContain("fictional raster");
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("keeps selected images editable while Stop holds sending", async () => {
    browser();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} sendDisabled />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await select();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(screen.getByRole("textbox"), " correction{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove image screen.png" }));
    expect(screen.queryByLabelText("Selected images")).toBeNull();
  });

  it.each(["", null])("voice Send with transcript %s retains images and sends nothing", async (transcript) => {
    browser(); installVoiceBrowser();
    const server = hostVoice(transcript ?? "");
    const onSubmit = vi.fn(); const user = userEvent.setup();
    render(<Harness onSubmit={onSubmit} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await select();
    await listen(user);
    await waitFor(() => expect(server.audio).toHaveLength(1));
    if (transcript === null) server.refuse = { on: "audio", status: 503, error: "Voice stopped: the speech process stopped." };
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(screen.getByLabelText("Selected images")).toBeVisible();
  });

  it.each([false, true])("late image decoding cannot repopulate another scope (remounted=%s)", async (remount) => {
    const revoke = browser();
    const view = render(<Harness key="alpha" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await select();
    let loaded = () => undefined;
    vi.stubGlobal("Image", class {
      naturalWidth = 640; naturalHeight = 360;
      onload: (() => void) | null = null;
      set src(_url: string) { loaded = () => { this.onload?.(); }; }
    });
    fireEvent.change(screen.getByLabelText("Choose images"), { target: { files: [new File(["late"], "late.png", { type: "image/png" })] } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    view.rerender(<Harness key={remount ? "beta" : "alpha"} imageScope={{ project: "beta" }} />);
    await act(async () => { loaded(); });
    expect(screen.queryByLabelText("Selected images")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("Explain this");
    expect(revoke).toHaveBeenCalledWith("blob:image-1");
    expect(revoke).toHaveBeenCalledWith("blob:image-2");
  });
});

describe("private image reads", () => {
  it("distinguishes denied/missing reads and Retry repeats only that image request", async () => {
    browser();
    let status = 403;
    vi.stubGlobal("fetch", vi.fn(async () => json({}, status)));
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={[{ id: "abc", name: "screen.png", mime_type: "image/png", size: 12, width: 2, height: 2, source_message_id: "message" }]} /></ImageViewerHost>, { wrapper: MemoryRouter });
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    status = 404;
    await user.click(screen.getByRole("button", { name: "Retry image screen.png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Image unavailable");
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/images/alpha/abc", "/api/images/alpha/abc"]);
  });

  it("starts a message this page sent from its picture, swaps in the saved copy on the same image, and still shows a refused read", async () => {
    browser();
    let status = 200;
    let answer!: () => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => {
      answer = () => resolve(status === 200 ? new Response("fictional raster", { headers: { "Content-Type": "image/png" } }) : json({}, status));
    })));
    const saved = { id: "abc", name: "screen.png", mime_type: "image/png", size: 12, width: 2, height: 2, source_message_id: "message" };
    // The server may shorten a name; the pictures match by position.
    const sent = [{ name: `${"s".repeat(140)}.png`, url: "data:image/png;base64,AAAA" }];
    const shown = (previews = sent) => <ImageViewerHost scope="/"><MessageImages project="alpha" images={[saved]} previews={previews} /></ImageViewerHost>;
    const { unmount } = render(shown(), { wrapper: MemoryRouter });
    const picture = screen.getByRole("img", { name: "screen.png" });
    expect(picture).toHaveAttribute("src", "data:image/png;base64,AAAA");
    expect(screen.getByRole("button", { name: "Open image screen.png" })).toContainElement(picture);
    expect(screen.queryByText("Loading image…")).toBeNull();
    await act(async () => answer());
    await waitFor(() => expect(picture).toHaveAttribute("src", "blob:image-1"));
    expect(screen.getByRole("img", { name: "screen.png" })).toBe(picture);
    unmount();

    status = 403;
    const refused = render(shown(), { wrapper: MemoryRouter });
    await act(async () => answer());
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(screen.queryByRole("img")).toBeNull();
    refused.unmount();

    // Pictures that are not this message's are never shown in its place.
    const other = render(shown([...sent, { name: "other.png", url: "data:image/png;base64,BBBB" }]), { wrapper: MemoryRouter });
    expect(screen.getByText("Loading image…")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    other.unmount();

    // The held picture's own read starts off screen too, so the picture never outlives an unread image.
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
    const calls = vi.mocked(fetch).mock.calls.length;
    render(shown(), { wrapper: MemoryRouter });
    expect(screen.getByRole("img", { name: "screen.png" })).toHaveAttribute("src", "data:image/png;base64,AAAA");
    expect(vi.mocked(fetch).mock.calls.length).toBe(calls + 1);
  });

  it("shapes one picture by its size between 9:16 and 16:9, and tiles between 3:4 and 4:3", () => {
    browser();
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const image = (id: string, width: number, height: number) => ({ id, name: `${id}.png`, mime_type: "image/png", size: 16, width, height, source_message_id: "message" });
    const aspect = (name: string) => Number(screen.getByText(name).closest("figure")!.style.getPropertyValue("--aspect"));
    const one = render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={[image("phone", 1170, 2532)]} /></ImageViewerHost>, { wrapper: MemoryRouter });
    expect(aspect("phone.png")).toBeCloseTo(9 / 16);
    one.unmount();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={[image("tall", 1170, 2532), image("wide", 1600, 900)]} /></ImageViewerHost>, { wrapper: MemoryRouter });
    expect(aspect("tall.png")).toBeCloseTo(3 / 4);
    expect(aspect("wide.png")).toBeCloseTo(4 / 3);
  });
});

/** Reduced motion lands every movement at once, so a test reads the viewer's resting states. */
function still() {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {} }));
}

/** Object URLs still held: what the page reads into memory and has not released. */
function objectUrls() {
  const live = new Set<string>();
  let next = 0;
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL() { const url = `blob:image-${++next}`; live.add(url); return url; }
    static override revokeObjectURL(url: string) { live.delete(url); }
  });
  return live;
}

const raster = () => vi.fn(async (_url: string) => new Response("fictional raster", { headers: { "Content-Type": "image/png" } }));
const pictures = (...names: string[]) => names.map((name) => ({ id: name, name: `${name}.png`, mime_type: "image/png", size: 16, width: 2000, height: 1000, source_message_id: "message" }));
const stage = () => document.querySelector<HTMLElement>(".image-viewer-stage")!;
const current = () => within(screen.getByRole("dialog")).getByRole("img");
const touch = (type: "pointerDown" | "pointerMove" | "pointerUp", x: number, y: number, pointerId = 1, pointerType = "touch") =>
  fireEvent[type](stage(), { pointerId, pointerType, clientX: x, clientY: y, button: 0 });
const pause = (ms: number) => act(() => new Promise((resolve) => setTimeout(resolve, ms)));

describe("image viewer", () => {
  it("keeps an open viewer and its zoom when the opener's row is replaced, then focuses the replacement", async () => {
    const revoke = browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    const images = [{ id: "abc", name: "queued.png", mime_type: "image/png", size: 16, width: 2, height: 2, source_message_id: "message" }];
    // Queued and stored rows are separate subtrees, as in the project conversation.
    const Page = ({ admitted }: { admitted: boolean }) => <ImageViewerHost scope="/projects/alpha">
      {admitted ? <div className="bubble"><MessageImages project="alpha" images={images} /></div> : <ul><li><MessageImages project="alpha" images={images} /></li></ul>}
    </ImageViewerHost>;
    const view = render(<Page admitted={false} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image queued.png" }));
    expect(screen.getByRole("button", { name: "Close image" })).toHaveFocus();
    await user.click(await screen.findByRole("button", { name: "Zoom image" }));
    const dialog = screen.getByRole("dialog", { name: "Image queued.png" });
    view.rerender(<Page admitted />);
    await screen.findByRole("button", { name: "Open image queued.png" });
    expect(screen.getByRole("dialog", { name: "Image queued.png" })).toBe(dialog);
    expect(screen.getByRole("button", { name: "Fit image" })).toHaveAttribute("aria-pressed", "true");
    // The thumbnail, the viewer's own copy and the new row each read once; the viewer is not disturbed.
    expect(fetch).toHaveBeenCalledTimes(3);
    await user.click(screen.getByRole("button", { name: "Close image" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open image queued.png" })).toHaveFocus();
    const before = revoke.mock.calls.length;
    view.rerender(<ImageViewerHost scope="/projects/beta"><p /></ImageViewerHost>);
    expect(revoke.mock.calls.length).toBeGreaterThan(before);
  });

  it("moves through a message's images within one history entry, and Back closes it", async () => {
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    let back = () => {};
    const History = () => { const navigate = useNavigate(); back = () => void navigate(-1); return <span data-testid="entry">{useLocation().key}</span>; };
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("first", "second", "third")} context="You · 14:05" /><History /></ImageViewerHost>, { wrapper: MemoryRouter });
    const page = screen.getByTestId("entry").textContent;
    await user.click(await screen.findByRole("button", { name: "Open image second.png" }));
    expect(screen.getByRole("dialog", { name: "Image second.png" })).toHaveTextContent("2 / 3");
    expect(screen.getByRole("dialog")).toHaveTextContent("You · 14:05");
    const entry = screen.getByTestId("entry").textContent;
    expect(entry).not.toBe(page);
    await user.click(screen.getByRole("button", { name: "Next image" }));
    expect(screen.getByRole("dialog", { name: "Image third.png" })).toHaveTextContent("3 / 3");
    expect(screen.queryByRole("button", { name: "Next image" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowLeft" });
    expect(screen.getByRole("dialog", { name: "Image first.png" })).toHaveTextContent("1 / 3");
    expect(screen.queryByRole("button", { name: "Previous image" })).toBeNull();
    expect(screen.getByTestId("entry").textContent).toBe(entry);
    await act(async () => back());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("entry").textContent).toBe(page);
  });

  it("fits a picture between the bars, zooms by double-tap, wheel, pinch and keys, and pans with its edges held", async () => {
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("wide")} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image wide.png" }));
    await waitFor(() => expect(current()).toHaveAttribute("src", "blob:image-2"));
    // 2000 × 1000 on a 1024 × 768 screen, 88 px kept at each side: fitted at 0.424, centred.
    const fitted = { width: "848px", height: "424px", transform: "translate(88px, 172px)" };
    expect(current()).toHaveStyle(fitted);
    // A double-tap zooms toward the point to the picture's own pixels; a second one returns to fit.
    for (const _ of [0, 1]) { touch("pointerDown", 512, 384); touch("pointerUp", 512, 384); }
    expect(current()).toHaveStyle({ width: "2000px", height: "1000px", transform: "translate(-488px, -116px)" });
    expect(screen.getByRole("button", { name: "Fit image" })).toHaveAttribute("aria-pressed", "true");
    // Zoomed, one finger pans and the edges hold: the picture's left edge never leaves the screen's.
    touch("pointerDown", 400, 400); touch("pointerMove", 420, 400); touch("pointerMove", 2000, 400);
    await pause(120);
    touch("pointerMove", 2000, 401); touch("pointerUp", 2000, 401);
    expect(current()).toHaveStyle({ transform: "translate(0px, -115px)" });
    expect(screen.getByRole("dialog", { name: "Image wide.png" })).toBeInTheDocument();
    for (const _ of [0, 1]) { touch("pointerDown", 512, 384); touch("pointerUp", 512, 384); }
    expect(current()).toHaveStyle(fitted);
    // A mouse wheel notch zooms toward the pointer; a trackpad's scroll does nothing at fit.
    fireEvent.wheel(stage(), { deltaY: 10, clientX: 512, clientY: 384 });
    expect(current()).toHaveStyle(fitted);
    fireEvent.wheel(stage(), { deltaY: -100, clientX: 512, clientY: 384 });
    expect(current()).toHaveStyle({ width: "1060px", height: "530px" });
    fireEvent.keyDown(document.body, { key: "0" });
    expect(current()).toHaveStyle(fitted);
    fireEvent.keyDown(document.body, { key: "+" });
    expect(current()).toHaveStyle({ width: "1272px" });
    fireEvent.keyDown(document.body, { key: "-" });
    expect(current()).toHaveStyle(fitted);
    // Two fingers pinch around their middle; released past the most zoom it settles back to the limit.
    touch("pointerDown", 412, 384, 1); touch("pointerDown", 612, 384, 2);
    touch("pointerMove", 812, 384, 2);
    expect(current()).toHaveStyle({ width: "1696px" });
    touch("pointerMove", 8000, 384, 2);
    touch("pointerUp", 8000, 384, 2); touch("pointerUp", 412, 384, 1);
    expect(current()).toHaveStyle({ width: "4000px", height: "2000px" });
  });

  it("follows a swipe to the next picture, springs back from a short one, and closes on a swipe down", async () => {
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("first", "second")} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image first.png" }));
    await waitFor(() => expect(current()).toHaveAttribute("src"));
    touch("pointerDown", 700, 400); touch("pointerMove", 680, 400); touch("pointerMove", 640, 400);
    // The neighbour slides in beside it, 16 px behind.
    const slides = document.querySelectorAll<HTMLElement>(".image-viewer-slide");
    expect([...slides].map((slide) => slide.style.transform)).toEqual(["translateX(-60px)", "translateX(980px)"]);
    await pause(120);
    touch("pointerMove", 641, 400); touch("pointerUp", 641, 400);
    expect(screen.getByRole("dialog", { name: "Image first.png" })).toBeInTheDocument();
    expect(document.querySelectorAll(".image-viewer-slide")).toHaveLength(1);
    // At the first picture a swipe the other way only resists.
    touch("pointerDown", 300, 400); touch("pointerMove", 330, 400); touch("pointerMove", 600, 400);
    expect(document.querySelector<HTMLElement>(".image-viewer-slide")!.style.transform).toBe("translateX(100px)");
    await pause(120);
    touch("pointerMove", 600, 401); touch("pointerUp", 600, 401);
    // Past a third of the width it carries on.
    touch("pointerDown", 800, 400); touch("pointerMove", 780, 400); touch("pointerMove", 300, 400); touch("pointerUp", 300, 400);
    expect(screen.getByRole("dialog", { name: "Image second.png" })).toHaveTextContent("2 / 2");
    touch("pointerDown", 500, 300); touch("pointerMove", 500, 320); touch("pointerMove", 500, 480);
    expect(screen.getByRole("dialog").style.getPropertyValue("--dim")).toBe(String(1 - 180 / 360));
    touch("pointerUp", 500, 480);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Open image first.png" })).toHaveFocus();
  });

  it("closes on a click in the dark outside the picture, never on the picture or after a drag", async () => {
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("wide")} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image wide.png" }));
    await waitFor(() => expect(current()).toHaveAttribute("src"));
    touch("pointerDown", 512, 384, 1, "mouse"); touch("pointerUp", 512, 384, 1, "mouse");
    touch("pointerDown", 512, 100, 1, "mouse"); touch("pointerMove", 520, 100, 1, "mouse"); touch("pointerUp", 520, 100, 1, "mouse");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    touch("pointerDown", 512, 100, 1, "touch"); touch("pointerUp", 512, 100, 1, "touch");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    touch("pointerDown", 512, 100, 1, "mouse"); touch("pointerUp", 512, 100, 1, "mouse");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reads the current picture, then its neighbours, holds at most three, releases them while hidden and all on close", async () => {
    browser();
    const live = objectUrls();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("a", "b", "c", "d")} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image b.png" }));
    await waitFor(() => expect(live.size).toBe(4 + 3));
    const read = () => vi.mocked(fetch).mock.calls.slice(4).map(([url]) => String(url).split("/").at(-1));
    expect(read()).toEqual(["b", "a", "c"]);
    await user.click(screen.getByRole("button", { name: "Next image" }));
    await waitFor(() => expect(read()).toEqual(["b", "a", "c", "d"]));
    await waitFor(() => expect(live.size).toBe(4 + 3));
    const visibility = (state: DocumentVisibilityState) => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    };
    visibility("hidden");
    expect(live.size).toBe(4 + 1);
    visibility("visible");
    await waitFor(() => expect(live.size).toBe(4 + 3));
    expect(read()).toEqual(["b", "a", "c", "d", "b", "d"]);
    await user.click(screen.getByRole("button", { name: "Close image" }));
    expect(live.size).toBe(4);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  it("offers Zoom, Copy, Download and Open on desktop, disabled while loading and gone when the picture fails", async () => {
    browser();
    modal(); still();
    let answer: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn((url: string) => url.endsWith("/later") ? new Promise<Response>((resolve) => { answer = resolve; })
      : Promise.resolve(new Response("fictional raster", { headers: { "Content-Type": "image/png" } }))));
    const write = vi.fn(async (_items: unknown[]) => {});
    vi.stubGlobal("ClipboardItem", class { constructor(readonly items: Record<string, unknown>) {} });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const saved: { href: string; download: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push({ href: this.href, download: this.download }); });
    const user = userEvent.setup();
    // After user-event's setup, which brings its own clipboard.
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { write } });
    const shown = { available: true, url: "/view", document: null, newer_url: null,
      images: ["/api/shown/one", "/api/shown/later"].map((url, index) => ({ name: `shot-${index + 1}.png`, path: `shot-${index + 1}.png`, url, width: 800, height: 600, embedded: false })) };
    render(<ImageViewerHost scope="/"><ShownFiles shown={shown as never} context="L2 · 14:05" /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(screen.getByRole("button", { name: "Open image shot-1.png" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("L2 · 14:05");
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy image" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Copy image" }));
    expect(write).toHaveBeenCalledTimes(1);
    expect(await within(dialog).findByRole("status")).toHaveTextContent("Image copied");
    await user.click(screen.getByRole("button", { name: "Download image" }));
    expect(saved).toEqual([{ href: expect.stringMatching(/^blob:/), download: "shot-1.png" }]);
    await user.click(screen.getByRole("button", { name: "Open in new tab" }));
    expect(opened).toHaveBeenCalledWith("/api/shown/one", "_blank", "noopener");
    await user.click(screen.getByRole("button", { name: "Next image" }));
    for (const name of ["Zoom image", "Copy image", "Download image", "Open in new tab"]) expect(screen.getByRole("button", { name })).toBeDisabled();
    await act(async () => answer!(new Response("{}", { status: 404 })));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Image unavailable");
    for (const name of ["Zoom image", "Copy image", "Download image", "Open in new tab"]) expect(screen.queryByRole("button", { name })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/later"))).toHaveLength(2));
    expect(within(dialog).getByText("Loading image…")).toBeInTheDocument();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("shares the held picture as a file on a phone, quietly when cancelled", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 390 });
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const share = vi.fn(async (_data: ShareData) => {});
    Object.assign(navigator, { canShare: () => true, share });
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><MessageImages project="alpha" images={pictures("phone")} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image phone.png" }));
    const button = await screen.findByRole("button", { name: "Share image" });
    expect(screen.queryByRole("button", { name: "Download image" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Zoom image" })).toBeNull();
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    const file = share.mock.calls[0]![0].files![0]!;
    expect([file.name, file.type]).toEqual(["phone.png", "image/png"]);
    share.mockRejectedValueOnce(new DOMException("Cancelled", "AbortError"));
    await user.click(button);
    expect(within(screen.getByRole("dialog")).getByRole("status")).toBeEmptyDOMElement();
    share.mockRejectedValueOnce(new Error("Refused"));
    await user.click(button);
    expect(within(screen.getByRole("dialog")).getByRole("status")).toHaveTextContent("Could not share the image");
    Reflect.deleteProperty(navigator, "canShare");
    Reflect.deleteProperty(navigator, "share");
  });

  it("opens a picked picture from the composer as not sent yet, with no actions", async () => {
    browser();
    modal(); still();
    const user = userEvent.setup();
    render(<ImageViewerHost scope="/"><Harness /></ImageViewerHost>, { wrapper: MemoryRouter });
    await act(async () => { await capabilityRead; });
    fireEvent.change(screen.getByLabelText("Choose images"), { target: { files: [image()] } });
    await user.click(await screen.findByRole("button", { name: "Open image screen.png" }));
    const dialog = screen.getByRole("dialog", { name: "Image screen.png" });
    expect(dialog).toHaveTextContent("Not sent yet");
    for (const name of ["Zoom image", "Copy image", "Download image", "Open in new tab", "Share image"]) expect(within(dialog).queryByRole("button", { name })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Close image" }));
    expect(screen.getByRole("button", { name: "Remove image screen.png" })).toBeInTheDocument();
  });

  it("closes the viewer when the page scope changes", async () => {
    browser();
    vi.stubGlobal("fetch", raster());
    modal(); still();
    const user = userEvent.setup();
    const images = pictures("screen");
    const view = render(<ImageViewerHost scope="/projects/alpha"><MessageImages project="alpha" images={images} /></ImageViewerHost>, { wrapper: MemoryRouter });
    await user.click(await screen.findByRole("button", { name: "Open image screen.png" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    view.rerender(<ImageViewerHost scope="/projects/alpha/tasks/other"><MessageImages project="alpha" images={images} /></ImageViewerHost>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    view.rerender(<ImageViewerHost scope="/projects/alpha"><MessageImages project="alpha" images={images} /></ImageViewerHost>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
