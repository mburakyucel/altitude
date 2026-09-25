#!/usr/bin/env python3
"""Build web/src/punctuation/assets/model.ort and scripts/punctuation/ops.config.

Source: 1-800-BAD-CODE/punctuation_fullstop_truecase_english (Apache-2.0) at a pinned Hugging Face
revision; the downloaded ONNX checkpoint and SentencePiece model are checked against SHA-256.

Steps:
1. Keep only the embedding rows of the pieces listed in web/src/punctuation/assets/vocab.tsv, in that
   order (row i of the trimmed table is line i of the file). The file is committed and is read here,
   not re-derived. It holds the 16,016 pieces kept from the 32k SentencePiece vocabulary: every
   special and single-character piece, plus the 16,000 pieces most frequent when the lowercased,
   punctuation-stripped English side of the IWSLT 2017 TED training talks and 400,000 lines of the
   WikiText-103 training split are encoded with the upstream SentencePiece model.
2. Quantize: MatMul weights to 4-bit MatMulNBits (symmetric, block 32), then the embedding Gather
   to int8 with onnxruntime dynamic quantization.
3. Convert to ORT format (optimization style Fixed, level all, no platform-specific layout) and
   write the operator/type config the minimal runtime build uses (scripts/punctuation/ops.config).

The quantized ONNX model (step 2) and ops.config are byte-for-byte reproducible. model.ort is not:
the converter writes its kernel type-string table in hash-map order, which varies per process, so
the flatbuffer offsets differ between runs while the tables it holds are identical.

Runs inside a throwaway venv with pinned packages. When started with any other Python it creates
${TMPDIR:-/tmp}/altitude-punctuation/venv-model and re-executes itself there. The exact command:

    python3.12 -m venv "${TMPDIR:-/tmp}/altitude-punctuation/venv-model"
    "${TMPDIR:-/tmp}/altitude-punctuation/venv-model/bin/pip" install --no-deps \
        onnx==1.23.0 onnxruntime==1.30.0 numpy==2.5.3 sentencepiece==0.2.2 protobuf==7.36.2 \
        flatbuffers==25.12.19 packaging==26.3 ml_dtypes==0.6.0 typing_extensions==4.16.0 \
        onnx-ir==1.0.0 sympy==1.14.0 mpmath==1.3.0

Downloads and intermediate files stay under ${TMPDIR:-/tmp}/altitude-punctuation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "web/src/punctuation/assets"
OPS_CONFIG = REPO / "scripts/punctuation/ops.config"
WORK = Path(os.environ.get("TMPDIR") or "/tmp") / "altitude-punctuation"
VENV = WORK / "venv-model"
PACKAGES = [
    "onnx==1.23.0", "onnxruntime==1.30.0", "numpy==2.5.3", "sentencepiece==0.2.2", "protobuf==7.36.2",
    "flatbuffers==25.12.19", "packaging==26.3", "ml_dtypes==0.6.0", "typing_extensions==4.16.0",
    "onnx-ir==1.0.0", "sympy==1.14.0", "mpmath==1.3.0",
]
REPO_ID = "1-800-BAD-CODE/punctuation_fullstop_truecase_english"
REVISION = "b26fd1c40e88678859048898218ea4edcc24c84a"
FILES = {
    "punct_cap_seg_en.onnx": "dd922d459da618cd324280889740608b76fb3e9e61d3f402291be1251f91421b",
    "spe_32k_lc_en.model": "9e86d0263de80b3b68327a21f5350c8cdf846e4c4400253c9baf05e3d44871c3",
}
EMBEDDING = "bert_model.embeddings.word_embeddings.weight"


def ensure_venv() -> None:
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not (VENV / "bin/python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run([str(VENV / "bin/pip"), "install", "--no-deps", *PACKAGES], check=True,
                       env={**os.environ, "PIP_CACHE_DIR": str(WORK / "pip-cache")})
    os.execv(str(VENV / "bin/python"), [str(VENV / "bin/python"), __file__, *sys.argv[1:]])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(name: str) -> Path:
    path = WORK / "upstream" / REVISION / name
    if not (path.exists() and sha256(path) == FILES[name]):
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/{name}"
        print("download", url, flush=True)
        with urllib.request.urlopen(url) as r, open(path.with_suffix(".part"), "wb") as f:
            shutil.copyfileobj(r, f)
        path.with_suffix(".part").rename(path)
    digest = sha256(path)
    if digest != FILES[name]:
        raise SystemExit(f"{name}: SHA-256 {digest} does not match the pinned {FILES[name]}")
    return path


def main() -> None:
    ensure_venv()
    import numpy as np
    import onnx
    from onnx import numpy_helper
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer
    import sentencepiece

    checkpoint, spm_model = download("punct_cap_seg_en.onnx"), download("spe_32k_lc_en.model")
    sp = sentencepiece.SentencePieceProcessor(model_file=str(spm_model))
    keep = []
    for n, line in enumerate((ASSETS / "vocab.tsv").read_text(encoding="utf-8").splitlines()):
        piece, score = line.split("\t")
        i = sp.piece_to_id(piece)
        if sp.id_to_piece(i) != piece or float(score) != sp.get_score(i):
            raise SystemExit(f"vocab.tsv line {n + 1}: {piece!r} is not an upstream piece with score {score}")
        keep.append(i)

    out = WORK / "model"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    model = onnx.load(str(checkpoint))
    table = next(t for t in model.graph.initializer if t.name == EMBEDDING)
    table.CopyFrom(numpy_helper.from_array(np.ascontiguousarray(numpy_helper.to_array(table)[keep]), EMBEDDING))

    quantizer = MatMulNBitsQuantizer(model, block_size=32, is_symmetric=True)
    quantizer.process()
    int4 = out / "int4.onnx"
    quantizer.model.save_model_to_file(str(int4), use_external_data_format=False)
    convert_dir = out / "convert"
    convert_dir.mkdir()
    quantize_dynamic(str(int4), str(convert_dir / "model.onnx"), weight_type=QuantType.QInt8,
                     op_types_to_quantize=["Gather"])
    subprocess.run([sys.executable, "-m", "onnxruntime.tools.convert_onnx_models_to_ort", str(convert_dir),
                    "--optimization_style", "Fixed", "--enable_type_reduction"], check=True)

    shutil.copyfile(convert_dir / "model.ort", ASSETS / "model.ort")
    config = (convert_dir / "required_operators_and_types.config").read_text().splitlines()
    ops = [line for line in config if not line.startswith("#")]
    OPS_CONFIG.write_text("# Generated by scripts/punctuation/build_model.py from assets/model.ort\n"
                          + "\n".join(ops) + "\n")
    for path in (convert_dir / "model.onnx", ASSETS / "model.ort", OPS_CONFIG):
        print(sha256(path), path.stat().st_size, path)


if __name__ == "__main__":
    main()
