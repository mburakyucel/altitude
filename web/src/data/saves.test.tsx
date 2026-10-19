import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { useSave, useSaving } from "./saves";

type Flag = { on: boolean };

/** One view of the read, with the saves on their way laid over it, as the app's read hooks do. */
function useFlag(read: () => Promise<Flag>) {
  const saving = useSaving<Flag>(["flag"]);
  const select = useCallback((data: Flag) => saving.reduce((flag, change) => ({ ...flag, ...change }), data), [saving]);
  return useQuery({ queryKey: ["flag"], queryFn: read, select }).data;
}

/** Two views of one read, and a control that saves it. */
function Views({ send, read }: { send: (on: boolean) => Promise<Flag>; read: () => Promise<Flag> }) {
  const first = useFlag(read);
  const second = useFlag(read);
  const save = useSave<Flag, Flag>(["flag"], ({ on }) => send(on), (client, flag) => { client.setQueryData(["flag"], flag); });
  return <>
    <button onClick={() => save.mutate({ on: !first?.on })}>{`${first?.on} ${second?.on} ${save.status}`}</button>
    {save.error ? <p role="alert">{save.error.message}</p> : null}
  </>;
}

function setup() {
  const answers: { resolve: (flag: Flag) => void; reject: (error: Error) => void }[] = [];
  let stored = false;
  const send = (on: boolean) => new Promise<Flag>((resolve, reject) => answers.push({ resolve: (flag) => { stored = on; resolve(flag); }, reject }));
  render(<QueryClientProvider client={new QueryClient()}><Views send={send} read={async () => ({ on: stored })} /></QueryClientProvider>);
  return answers;
}

describe("a setting's save", () => {
  it("shows the change in every view at the press and settles there without flashing back", async () => {
    const answers = setup();
    await screen.findByText("false false idle");
    await act(async () => screen.getByRole("button").click());
    await screen.findByText("true true pending");
    await act(async () => answers[0]!.resolve({ on: true }));
    await screen.findByText("true true success");
  });

  it("puts a refused change back and reads what is stored", async () => {
    const answers = setup();
    await screen.findByText("false false idle");
    await act(async () => screen.getByRole("button").click());
    await screen.findByText("true true pending");
    await act(async () => answers[0]!.reject(new Error("Change refused.")));
    await screen.findByText("false false error");
    expect(screen.getByRole("alert")).toHaveTextContent("Change refused.");
  });
});
