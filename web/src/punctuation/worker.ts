// Module worker: loads the runtime, model and vocabulary, then punctuates one request at a time.
import * as ort from "./assets/ort.wasm.min.mjs";
import glueUrl from "./assets/ort-wasm-simd.mjs?url";
import wasmUrl from "./assets/ort-wasm-simd.wasm?url";
import modelUrl from "./assets/model.ort?url";
import vocabUrl from "./assets/vocab.tsv?url";
import { punctuateWords, type Infer } from "./punctuate";
import { parseVocab } from "./tokenizer";

export type Request = { id: number; words?: readonly string[] };
export type Reply = { id: number; words?: string[]; error?: string };

async function fetchOk(url: string): Promise<Response> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response;
}

async function load() {
  const absolute = (url: string) => new URL(url, self.location.href).href;
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.wasmPaths = { mjs: absolute(glueUrl), wasm: absolute(wasmUrl) };
  const [vocab, session] = await Promise.all([
    fetchOk(vocabUrl).then((r) => r.text()).then(parseVocab),
    fetchOk(modelUrl).then((r) => r.arrayBuffer()).then((b) => ort.InferenceSession.create(new Uint8Array(b))),
  ]);
  const infer: Infer = async (ids) => {
    const input = new ort.Tensor("int64", BigInt64Array.from(ids, BigInt), [1, ids.length]);
    const out = await session.run({ input_ids: input });
    return { post: Array.from(out.post_preds!.data, Number), cap: Array.from(out.cap_preds!.data, Number) };
  };
  return (words: readonly string[]) => punctuateWords(words, vocab, infer);
}

let ready: ReturnType<typeof load> | undefined;
let queue: Promise<unknown> = Promise.resolve();

self.onmessage = (event: MessageEvent<Request>) => {
  const { id, words } = event.data;
  ready ??= load();
  const task = queue.then(() => ready!).then((punctuate) => (words ? punctuate(words) : undefined));
  queue = task.catch(() => undefined);
  task.then(
    (result) => self.postMessage({ id, words: result } satisfies Reply),
    (error: unknown) => self.postMessage({ id, error: error instanceof Error ? error.message : String(error) } satisfies Reply),
  );
};
