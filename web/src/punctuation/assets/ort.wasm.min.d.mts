// The part of the onnxruntime-web 1.30.0 API (ort.wasm.min.mjs) that the punctuation worker uses.
export declare const env: {
  wasm: { numThreads?: number; wasmPaths?: { mjs: string; wasm: string } };
  logLevel?: "verbose" | "info" | "warning" | "error" | "fatal";
};

export declare class Tensor {
  constructor(type: "int64", data: BigInt64Array, dims: readonly number[]);
  readonly data: ArrayLike<number | bigint | boolean>;
  readonly dims: readonly number[];
}

export declare class InferenceSession {
  static create(model: Uint8Array): Promise<InferenceSession>;
  run(feeds: Record<string, Tensor>): Promise<Record<string, Tensor>>;
}
