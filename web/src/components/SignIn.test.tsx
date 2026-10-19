import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { SignIn } from "./SignIn";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const waiting = { attempt: "attempt-1", state: "waiting", link: "https://example.com/sign-in", started: 100, error: null, saved: null };
const idle = { attempt: null, state: "idle", link: null, started: null, error: null, saved: null };

it("names Cancelling… on Cancel alone while the cancel is held, and offers Sign in once it answers", async () => {
  let release!: (response: Response) => void;
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "POST" ? new Promise<Response>((resolve) => { release = resolve; }) : json(waiting));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SignIn engine="alpha" label="Alpha" signedIn={false} /></QueryClientProvider>);
  await user.type(await screen.findByLabelText("Paste the code Alpha shows"), "CODE-1");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(JSON.parse(String(fetchMock.mock.calls.find(([, init]) => init?.method === "POST")?.[1]?.body))).toEqual({ action: "cancel", attempt: "attempt-1" });
  expect(screen.getByRole("button", { name: "Cancelling…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Finish sign-in" })).toBeDisabled();
  expect(screen.queryByText("Signing in…")).toBeNull();
  await act(async () => release(json(idle)));
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Cancelling…" })).toBeNull();
  expect(screen.queryByRole("form", { name: "Alpha sign-in" })).toBeNull();
});
