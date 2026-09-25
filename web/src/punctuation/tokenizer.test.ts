import { describe, expect, it } from "vitest";
import { encodeWord, parseVocab } from "./tokenizer";

const vocab = parseVocab(
  ["<unk>\t0", "<s>\t0", "</s>\t0", "▁\t-6", "▁he\t-3", "llo\t-3", "▁hello\t-5", "l\t-4", "o\t-4", "h\t-4", "e\t-4"].join("\n"),
);
const pieces = (word: string) => encodeWord(vocab, word).map((t) => [t.id, t.from, t.to]);

describe("parseVocab", () => {
  it("numbers pieces by line and finds the special tokens", () => {
    expect([vocab.unk, vocab.bos, vocab.eos]).toEqual([0, 1, 2]);
    expect(vocab.pieces.get("llo")).toEqual({ id: 5, score: -3 });
    expect(vocab.maxPieceLength).toBe(6);
  });
});

describe("encodeWord", () => {
  it("chooses the highest-scoring segmentation", () => {
    expect(pieces("hello")).toEqual([[6, 0, 6]]); // -5 beats "▁he" + "llo" at -6
    expect(pieces("hell")).toEqual([[4, 0, 3], [7, 3, 4], [7, 4, 5]]);
  });

  it("maps a character outside the vocabulary to <unk> and keeps positions in code points", () => {
    expect(pieces("h😀")).toEqual([[3, 0, 1], [9, 1, 2], [0, 2, 3]]);
  });
});
