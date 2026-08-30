import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { ThemeToggle, applyTheme } from "./theme";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  document.querySelector('meta[name="theme-color"]')?.remove();
});

describe("ThemeToggle", () => {
  it("switches to dark: sets data-theme, persists, marks the button pressed", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("altitude.theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switches back to light: clears data-theme and persists", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "Dark" }));
    await user.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("altitude.theme")).toBe("light");
  });

  it("system follows the OS preference (light in jsdom)", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "Dark" }));
    await user.click(screen.getByRole("button", { name: "System" }));
    expect(localStorage.getItem("altitude.theme")).toBe("system");
    // jsdom's matchMedia never matches (prefers-color-scheme: dark) → light
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("applyTheme respects a stored dark preference at boot", () => {
    localStorage.setItem("altitude.theme", "dark");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
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

  it("renders theme controls in both shell locations", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    renderApp();

    const themeControls = screen.getAllByRole("group", { name: "Theme" });
    expect(themeControls).toHaveLength(2);
    themeControls.forEach((control) => expect(control).toBeInTheDocument());
  });
});
