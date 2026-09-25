# Punctuation model and runtime

`web/src/punctuation/assets/` holds the files the browser punctuator loads. These scripts rebuild
them. Downloads, virtual environments and build trees stay under `${TMPDIR:-/tmp}/altitude-punctuation`.

| Script | Writes |
|---|---|
| `python3 scripts/punctuation/build_model.py` | `assets/model.ort`, `scripts/punctuation/ops.config` |
| `scripts/punctuation/build_runtime.sh` | `assets/ort-wasm-simd.wasm`, `assets/ort-wasm-simd.mjs`, `assets/ort.wasm.min.mjs` |

Run `build_model.py` first: the runtime includes only the operators and types listed in `ops.config`.
`assets/vocab.tsv` is an input, not an output: the 16,016 pieces kept by frequency on public English
text (IWSLT 2017 TED training talks and WikiText-103), as `build_model.py` step 1 describes; that
selection is not scripted here. Each script's header describes its pinned inputs and steps.

## Sources and licences

| Asset | Source | Licence |
|---|---|---|
| `model.ort`, `vocab.tsv` | [1-800-BAD-CODE/punctuation_fullstop_truecase_english](https://huggingface.co/1-800-BAD-CODE/punctuation_fullstop_truecase_english) at revision `b26fd1c40e88678859048898218ea4edcc24c84a`, reduced to 16,016 vocabulary pieces and quantized (4-bit weights, int8 embeddings) | Apache-2.0 |
| `ort-wasm-simd.wasm`, `ort-wasm-simd.mjs` | [onnxruntime](https://github.com/microsoft/onnxruntime) `v1.30.0` (`f2c39fe2f838cf35ce7da92824f5a5e3ee6e88a7`), built with emsdk 4.0.23 as a minimal, single-threaded, SIMD Release WebAssembly build | MIT |
| `ort.wasm.min.mjs` | `dist/ort.wasm.min.mjs` from the npm package `onnxruntime-web@1.30.0` (tarball SHA-256 `d2228df7e4616bc3348bf504ee888f3bec43789a273f0a63f3e68d203ce3bf71`) | MIT |

## Committed artifacts

| File | Bytes | SHA-256 | Reproduced byte-for-byte |
|---|---:|---|---|
| `assets/model.ort` | 20,915,912 | `5de59d0648904ea54a31061c036c3b472d7b7b4ed610478130bdc72220a57bc5` | No: the ORT converter writes one table in per-process hash order. The quantized ONNX it converts (SHA-256 `61340f6fa231ca6b901707924b126d7d52c3114040d6d11068ac99c06838b67b`) is reproduced exactly, and repeated conversions hold the same nodes, initializer bytes and kernel type tables |
| `assets/vocab.tsv` | 470,499 | `4e71c5324ae1f00f68ae5a81b2789031aab3407c6972aef50747371b1641115d` | Input |
| `scripts/punctuation/ops.config` | 1,159 | `9231e9a809ff3f078d923a7bffa858da4c38d1040a06e3086def9233ba71dfcf` | Yes |
| `assets/ort-wasm-simd.wasm` | 1,869,417 | `91d5f39b50fb3b61e83bfe9eaa369607df6a9ffdb8e7da274656ac45f560e514` | Yes on the build machine (two clean builds matched; source and build paths are mapped out of the binary, other hosts untested) |
| `assets/ort-wasm-simd.mjs` | 11,965 | `4c056064db1e930f08301e80c16891fcf1faa7102caaa3f922e87ec5e224f807` | Yes on the build machine (two clean builds matched; source and build paths are mapped out of the binary, other hosts untested) |
| `assets/ort.wasm.min.mjs` | 50,126 | `219e6a1fc8a9938268d18efca3c91d310bd2f4a59bbd13744df5b2b7fc6cee3b` | Yes (copied from the pinned tarball) |
