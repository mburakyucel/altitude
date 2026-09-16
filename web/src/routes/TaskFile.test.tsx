import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

const path = "/home/operator/.altitude/atlas/tasks/setup/commands.md";
const documentFile = { name: "commands.md", path, current_path: path, markdown: true, text: "# Setup\n\nRead **carefully**.\n\n- First step\n\n```sh\necho hello\n```" };
const route = (reference = path, project = "atlas") => `/projects/${project}/file?${new URLSearchParams({ path: reference })}`;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function mockFiles(read: (url: string) => Response | Promise<Response> = () => json(documentFile)) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/files/")) return read(url);
    if (url.startsWith("/api/overview")) return json({ projects: [], queue: [], wip: { per_project: {}, machine: 0 }, quota: {} });
    return json({ name: "atlas" });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TaskFile", () => {
  it("opens a file URI through the project API and renders markdown with a reversible Raw toggle", async () => {
    const fetch = mockFiles();
    const reference = `file://${path}`;
    const { user } = renderApp({ route: route(reference) });
    expect(await screen.findByRole("heading", { name: "commands.md" })).toBeVisible();
    expect(screen.getByLabelText("Referenced path")).toHaveTextContent(reference);
    expect(screen.getByRole("heading", { name: "Setup", level: 2 })).toBeVisible();
    expect(screen.getByText("carefully").tagName).toBe("STRONG");
    expect(screen.getByRole("listitem")).toHaveTextContent("First step");
    expect(screen.getByText("echo hello").tagName).toBe("PRE");
    expect(screen.getByText("Close this tab to return to the conversation.")).toBeVisible();
    const requests = fetch.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith("/api/files/"));
    expect(requests).toEqual([`/api/files/atlas?${new URLSearchParams({ path: reference })}`]);
    const toggle = screen.getByRole("button", { name: "Raw" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("heading", { name: "Setup" })).not.toBeInTheDocument();
    expect(document.querySelector(".task-file-raw")?.textContent).toBe(documentFile.text);
    await user.click(toggle);
    expect(screen.getByRole("heading", { name: "Setup" })).toBeVisible();
  });

  it("keeps text files literal and reports an empty file", async () => {
    mockFiles(() => json({ ...documentFile, name: "notes.txt", markdown: false, text: "# Literal\n<b>plain text</b>" }));
    const { router } = renderApp({ route: route() });
    await screen.findByRole("heading", { name: "notes.txt" });
    expect(document.querySelector(".task-file-raw")?.textContent).toBe("# Literal\n<b>plain text</b>");
    expect(screen.queryByRole("button", { name: "Raw" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Literal" })).not.toBeInTheDocument();
    mockFiles(() => json({ ...documentFile, text: "" }));
    await act(() => router.navigate(route(`${path}.empty`)));
    expect(await screen.findByText("This file is empty.")).toBeVisible();
  });

  it("shows the current location of an archived document without replacing the referenced path", async () => {
    const current = path.replace("/tasks/setup/", "/archive/setup/");
    mockFiles(() => json({ ...documentFile, current_path: current }));
    renderApp({ route: route() });
    expect(await screen.findByText(`Current location: ${current}`)).toBeVisible();
    expect(screen.getByLabelText("Referenced path")).toHaveTextContent(path);
  });

  it("copies the full reference and explains clipboard failure honestly", async () => {
    mockFiles();
    const { user } = renderApp({ route: route() });
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await user.click(await screen.findByRole("button", { name: "Copy path" }));
    expect(writeText).toHaveBeenCalledWith(path);
    expect(screen.getByText("Path copied.")).toBeVisible();
    writeText.mockRejectedValueOnce(new Error("permission denied"));
    await user.click(screen.getByRole("button", { name: "Copy path" }));
    expect(screen.getByText("Could not copy. Select the full path above to copy it manually.")).toBeVisible();
    expect(screen.queryByText("Path copied.")).not.toBeInTheDocument();
  });

  it.each([403, 404, 415])("keeps the target visible for unavailable files (%s) and retries to fresh content", async (status) => {
    let read = 0;
    mockFiles(() => ++read === 1 ? json({ error: "This document is unavailable." }, status) : json(documentFile));
    const { user } = renderApp({ route: route() });
    expect(await screen.findByRole("alert")).toHaveTextContent("File unavailable");
    expect(screen.getByLabelText("Referenced path")).toHaveTextContent(path);
    expect(screen.queryByRole("heading", { name: "Setup" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Setup" })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows network failure without claiming the file exists", async () => {
    mockFiles(() => Promise.reject(new TypeError("network failed")));
    renderApp({ route: route() });
    expect(await screen.findByRole("alert")).toHaveTextContent("Check your connection and try again.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("clears old contents, Raw and copy state while a different target loads, then shows its failure", async () => {
    let release: (response: Response) => void = () => {};
    const next = new Promise<Response>((resolve) => { release = resolve; });
    mockFiles((url) => url.includes("other.txt") ? next : json(documentFile));
    const { user, router } = renderApp({ route: route() });
    await user.click(await screen.findByRole("button", { name: "Raw" }));
    await user.click(screen.getByRole("button", { name: "Copy path" }));
    await act(() => router.navigate(route("/missing/other.txt")));
    expect(screen.getByText("Loading file…")).toBeVisible();
    expect(screen.getByLabelText("Referenced path")).toHaveTextContent("/missing/other.txt");
    expect(document.querySelector(".task-file-raw")).toBeNull();
    expect(screen.queryByRole("button", { name: "Raw" })).not.toBeInTheDocument();
    expect(screen.queryByText("Path copied.")).not.toBeInTheDocument();
    release(json({ error: "This document is unavailable." }, 404));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("Loading file…")).not.toBeInTheDocument();
    await act(() => router.navigate(route()));
    expect(await screen.findByRole("heading", { name: "Setup" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Raw" })).toHaveAttribute("aria-pressed", "false");
  });

  it("isolates project targets and escapes document HTML without loading embedded images", async () => {
    const text = "# Safe\n\n<script>alert('no')</script>\n\n![tracking](https://example.test/pixel.png)\n\n[Unsafe](javascript:alert)\n\n[Web](https://example.test/docs)";
    const fetch = mockFiles(() => json({ ...documentFile, text }));
    const { router } = renderApp({ route: route() });
    expect(await screen.findByRole("heading", { name: "Safe" })).toBeVisible();
    const content = screen.getByRole("region", { name: "File contents" });
    expect(content.querySelector("script, img, iframe")).toBeNull();
    expect(content.querySelector("a[href^='javascript:']")).toBeNull();
    expect(screen.getByRole("link", { name: "Web" })).toHaveAttribute("href", "https://example.test/docs");
    await act(() => router.navigate(route(path, "other")));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).startsWith("/api/files/other?"))).toBe(true));
    expect(await screen.findByRole("heading", { name: "Safe" })).toBeVisible();
  });
});
