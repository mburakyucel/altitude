import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProseTerminal, runCommand } from "./CodeBlock";
import { Prose } from "./Prose";
import { requestCommand, subscribeCommands, takeCommand } from "../data/terminalCommand";

const block = (body: string, info = "run") => `Run this:\n\n\`\`\`${info}\n${body}\n\`\`\`\n\nThen tell me.`;

describe("run commands", () => {
  it("keeps one line byte for byte and refuses more lines, control and invisible characters anywhere", () => {
    expect(runCommand("  printf x\\ ")).toEqual({ command: "  printf x\\ " });
    expect(runCommand("echo 'ünïcode ✓' && ls -la")).toEqual({ command: "echo 'ünïcode ✓' && ls -la" });
    const lines = "Not offered for the terminal: more than one line.";
    for (const text of ["cd /tmp\nls", "cd /tmp\rls", "a\u2028b", "a\u2029b", "ls\n", "\nls", "ls\u2028"]) expect(runCommand(text)).toEqual({ refused: lines });
    const hidden = "Not offered for the terminal: it contains a control or invisible character.";
    for (const text of ["a\tb", "echo \x1b[31m", "rm​ -rf", "echo ‮gnp.exe", "a\x7fb", "a\u0085b", "a\u2066b", "\tls", "ls\t", "\ufeffls", "ls\u200b"]) {
      expect(runCommand(text)).toEqual({ refused: hidden });
    }
    expect(runCommand(" \n ")).toEqual({ refused: "Not offered for the terminal: the command is empty." });
  });
});

describe("code blocks in prose", () => {
  it("opens a run block's exact shown command in the conversation's terminal", async () => {
    const open = vi.fn();
    render(<ProseTerminal value={{ open }}><Prose text={block("echo ran-$((20+22)) \\ ")} /></ProseTerminal>);
    const group = screen.getByRole("group", { name: "Command" });
    const shown = group.querySelector("pre")!.textContent;
    expect(shown).toBe("echo ran-$((20+22)) \\ ");
    await userEvent.click(screen.getByRole("button", { name: "Open in terminal" }));
    expect(open).toHaveBeenCalledWith(shown);
  });

  it("offers Copy only where the conversation has no terminal, and says why", () => {
    const { rerender } = render(<ProseTerminal value={{ unavailable: "This task has no terminal now." }}><Prose text={block("ls")} /></ProseTerminal>);
    expect(screen.getByText("This task has no terminal now.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Open in terminal" })).toBeNull();
    rerender(<Prose text={block("ls")} />);
    expect(screen.getByRole("group", { name: "Command" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Copy" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Open in terminal" })).toBeNull();
  });

  it("shows a refused run block verbatim with its reason and no action", () => {
    render(<ProseTerminal value={{ open: vi.fn() }}><Prose text={block("cd /tmp\nls")} /></ProseTerminal>);
    expect(screen.getByRole("group", { name: "Command" }).querySelector("pre")!.textContent).toBe("cd /tmp\nls");
    expect(screen.getByText("Not offered for the terminal: more than one line.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Open in terminal" })).toBeNull();
  });

  it("never makes another fence an action", () => {
    for (const info of ["sh", "", " bash", "runnable", "run sh"]) {
      const { unmount } = render(<ProseTerminal value={{ open: vi.fn() }}><Prose text={block("uname -a", info)} /></ProseTerminal>);
      expect(screen.queryByRole("group", { name: "Command" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Open in terminal" })).toBeNull();
      expect(screen.getByRole("button", { name: "Copy" })).toBeVisible();
      unmount();
    }
  });

  it("copies verbatim and says when the browser refuses", async () => {
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Prose text={block("uname -a", "sh")} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("uname -a");
    expect(await screen.findByText("Copied")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Copied" }));
    expect(await screen.findByText("Couldn't copy")).toBeVisible();
    await waitFor(() => expect(screen.getByText("Copy")).toBeVisible(), { timeout: 3_000 });
  });
});

describe("terminal command requests", () => {
  it("are taken once by their own terminal, and a newer one replaces an untaken one", () => {
    const heard = vi.fn();
    const stop = subscribeCommands(heard);
    requestCommand("atlas", "task-a", "echo one");
    requestCommand("atlas", "task-a", "echo two");
    expect(heard).toHaveBeenCalledTimes(2);
    expect(takeCommand("atlas")).toBeNull();
    expect(takeCommand("atlas", "task-b")).toBeNull();
    expect(takeCommand("atlas", "task-a")).toBe("echo two");
    expect(takeCommand("atlas", "task-a")).toBeNull();
    requestCommand("atlas", undefined, "echo project");
    expect(takeCommand("atlas", "task-a")).toBeNull();
    expect(takeCommand("atlas")).toBe("echo project");
    stop();
    requestCommand("atlas", undefined, "echo later");
    expect(heard).toHaveBeenCalledTimes(3);
    takeCommand("atlas");
  });
});
