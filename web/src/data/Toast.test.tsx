import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ToastProvider, ToastViewport, useToast, type ToastOptions } from "./Toast";

function Notice({ severity }: { severity: ToastOptions["severity"] }) {
  const toast = useToast();
  return <><button onClick={() => toast.show({ message: "Saved notice", severity })}>Show notice</button><ToastViewport /></>;
}

describe("toast dismissal", () => {
  it.each(["info", "limit", "failure"] as const)("closes %s notices immediately and allows a later notice", async (severity) => {
    const user = userEvent.setup();
    render(<ToastProvider><Notice severity={severity} /></ToastProvider>);
    await user.click(screen.getByRole("button", { name: "Show notice" }));
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show notice" }));
    screen.getByRole("button", { name: "Dismiss" }).focus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
