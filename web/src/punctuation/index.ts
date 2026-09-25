// Browser punctuation and truecasing for dictated words, run off the main thread.
import type { Reply, Request } from "./worker";

export interface Punctuator {
  punctuate(words: readonly string[]): Promise<string[]>;
}

/** A request that takes longer means a stuck worker: it is dropped and the next load starts afresh. */
const REQUEST_MS = 10000;

let loading: Promise<Punctuator> | undefined;

/** Loads the model once per page; after a failure the next call starts a fresh load. */
export function loadPunctuator(): Promise<Punctuator> {
  loading ??= Promise.resolve().then(start).catch((error: unknown) => {
    loading = undefined;
    throw error;
  });
  return loading;
}

function start(): Promise<Punctuator> {
  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const pending = new Map<number, { resolve: (words?: string[]) => void; reject: (error: Error) => void }>();
  let failure: Error | undefined;
  let next = 0;
  worker.onmessage = (event: MessageEvent<Reply>) => {
    const { id, words, error } = event.data;
    const call = pending.get(id);
    pending.delete(id);
    if (error === undefined) call?.resolve(words);
    else call?.reject(new Error(error));
  };
  const fail = (error: Error) => {
    failure = error;
    worker.terminate();
    loading = undefined;
    for (const call of pending.values()) call.reject(failure);
    pending.clear();
  };
  worker.onerror = (event) => {
    event.preventDefault();
    fail(new Error(event.message || "Punctuation worker failed"));
  };
  // Loading has no limit (a slow connection is still downloading); punctuating has REQUEST_MS.
  const call = (words?: readonly string[]) =>
    new Promise<string[] | undefined>((resolve, reject) => {
      if (failure) return reject(failure);
      const id = next++;
      const timer = words && setTimeout(() => fail(new Error("Punctuation did not answer in time")), REQUEST_MS);
      pending.set(id, {
        resolve: (result) => { clearTimeout(timer); resolve(result); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      worker.postMessage({ id, words } satisfies Request);
    });
  return call().then(
    () => ({
      punctuate: async (words) => (words.length === 0 ? [] : ((await call([...words])) ?? [...words])),
    }),
    (error: unknown) => {
      worker.terminate();
      throw error;
    },
  );
}
