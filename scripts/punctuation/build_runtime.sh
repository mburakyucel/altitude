#!/usr/bin/env bash
# Build the punctuation runtime: a minimal (ORT-format only), single-threaded, SIMD, Release
# onnxruntime-web 1.30.0 WebAssembly binary limited to the operators and types in ops.config.
# Writes web/src/punctuation/assets/ort-wasm-simd.{wasm,mjs} and vendors the matching
# onnxruntime-web 1.30.0 JS API entry (ort.wasm.min.mjs) from the npm registry tarball.
#
# Requires git, curl, cmake >= 3.28, make, python3 and a C/C++ host toolchain. The onnxruntime
# checkout, emsdk and build tree stay under ${TMPDIR:-/tmp}/altitude-punctuation.
set -euo pipefail

ORT_TAG=v1.30.0
ORT_COMMIT=f2c39fe2f838cf35ce7da92824f5a5e3ee6e88a7
EMSDK_VERSION=4.0.23
NPM_TGZ=https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.30.0.tgz
NPM_TGZ_SHA256=d2228df7e4616bc3348bf504ee888f3bec43789a273f0a63f3e68d203ce3bf71

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
ASSETS=$REPO/web/src/punctuation/assets
WORK=${TMPDIR:-/tmp}/altitude-punctuation
SRC=$WORK/onnxruntime
BUILD=$WORK/ort-build
mkdir -p "$WORK"

if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$ORT_TAG" https://github.com/microsoft/onnxruntime.git "$SRC"
fi
test "$(git -C "$SRC" rev-parse HEAD)" = "$ORT_COMMIT" || { echo "$SRC is not $ORT_TAG ($ORT_COMMIT)" >&2; exit 1; }
git -C "$SRC" submodule update --init --depth 1 cmake/external/emsdk

if [ ! -x "$WORK/venv-runtime/bin/python" ]; then
  python3 -m venv "$WORK/venv-runtime"
  "$WORK/venv-runtime/bin/pip" install --no-deps flatbuffers==25.12.19 packaging==26.3
fi
export PATH=$WORK/venv-runtime/bin:$PATH

# Source and build paths are mapped to fixed names so they do not end up in the binary.
PREFIX_MAP="-ffile-prefix-map=$SRC=onnxruntime -ffile-prefix-map=$BUILD=build"
rm -rf "$BUILD"
"$SRC/build.sh" --build_dir "$BUILD" --config Release --parallel --skip_tests --skip_submodule_sync \
  --build_wasm --enable_wasm_simd --emsdk_version "$EMSDK_VERSION" --target onnxruntime_webassembly \
  --minimal_build --include_ops_by_config "$HERE/ops.config" --enable_reduced_operator_type_support \
  --disable_rtti --disable_exceptions --disable_wasm_exception_catching --disable_ml_ops \
  --disable_generation_ops --disable_types string float4 float8 optional sparsetensor \
  --compile_no_warning_as_error \
  --cmake_extra_defines "CMAKE_C_FLAGS=$PREFIX_MAP" "CMAKE_CXX_FLAGS=$PREFIX_MAP"
install -m 644 "$BUILD/Release/ort-wasm-simd.wasm" "$BUILD/Release/ort-wasm-simd.mjs" "$ASSETS/"

curl -fsSL "$NPM_TGZ" -o "$WORK/onnxruntime-web-1.30.0.tgz"
echo "$NPM_TGZ_SHA256  $WORK/onnxruntime-web-1.30.0.tgz" | sha256sum -c -
tar -xzf "$WORK/onnxruntime-web-1.30.0.tgz" -C "$ASSETS" --strip-components=2 package/dist/ort.wasm.min.mjs

cd "$ASSETS" && sha256sum ort-wasm-simd.wasm ort-wasm-simd.mjs ort.wasm.min.mjs
