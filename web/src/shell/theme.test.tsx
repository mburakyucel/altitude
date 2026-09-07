import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { ThemeToggle, applyTheme, readTheme } from "./theme";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  document.querySelector('meta[name="theme-color"]')?.remove();
});

describe("ThemeToggle", () => {
  it("is light by default and switches to dark: sets data-theme, persists, reads pressed", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    expect(readTheme()).toBe("light");
    const toggle = screen.getByRole("button", { name: "Dark theme" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("altitude.theme")).toBe("dark");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  it("switches back to light: clears data-theme and persists", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "Dark theme" }));
    await user.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("altitude.theme")).toBe("light");
  });

  it("reads a stored dark preference at boot and anything else as light", () => {
    localStorage.setItem("altitude.theme", "dark");
    expect(readTheme()).toBe("dark");
    localStorage.setItem("altitude.theme", "system");
    expect(readTheme()).toBe("light");
  });

  it("updates the browser theme color for dark and light", () => {
    const themeColor = document.createElement("meta");
    themeColor.name = "theme-color";
    document.head.append(themeColor);

    applyTheme("dark");
    expect(themeColor).toHaveAttribute("content", "#0f172a");

    applyTheme("light");
    expect(themeColor).toHaveAttribute("content", "#f8fafc");
  });

  it("creates the browser theme-color meta when it is missing", () => {
    expect(document.querySelector('meta[name="theme-color"]')).toBeNull();

    applyTheme("dark");

    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute(
      "content",
      "#0f172a",
    );
  });

  it("sits once in the rail's operator row", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    renderApp();

    expect(screen.getAllByRole("button", { name: "Dark theme" })).toHaveLength(1);
  });
});
