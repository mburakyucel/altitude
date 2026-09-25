import { describe, expect, it } from "vitest";
import { CAP_WIDTH, isPlain, isValidOutput, MAX_TOKENS, normalise, punctuateWords, type Infer } from "./punctuate";
import { parseVocab } from "./tokenizer";

// One piece per word ("▁" + word), plus single letters for anything else.
const words = ["so", "i", "think", "we", "should", "merge", "the", "pr", "today", "what", "do", "you", "3.5", "straße"];
const letters = [..."abcdefghijklmnopqrstuvwxyz0123456789.ß"];
const vocab = parseVocab(
  ["<unk>\t0", "<s>\t0", "</s>\t0", "▁\t-9", ...words.map((w) => `▁${w}\t-1`), ...letters.map((c) => `${c}\t-5`)].join("\n"),
);
const pieceOf = new Map([...vocab.pieces].map(([piece, { id }]) => [id, piece]));

/** Fake model: a mark or capitals chosen per piece by `rules`, recorded calls in `calls`. */
function fake(rules: Record<string, { mark?: number; caps?: number[] }>) {
  const calls: number[][] = [];
  const infer: Infer = async (ids) => {
    calls.push([...ids]);
    const post = ids.map((id) => rules[pieceOf.get(id)!]?.mark ?? 0);
    const cap = new Array<number>(ids.length * CAP_WIDTH).fill(0);
    ids.forEach((id, t) => rules[pieceOf.get(id)!]?.caps?.forEach((c) => (cap[t * CAP_WIDTH + c] = 1)));
    return { post, cap };
  };
  return { infer, calls };
}

describe("normalise", () => {
  it("lowercases and drops the recognizer's marks, keeping number separators", () => {
    expect(["Hello,", "U.S.", "Why?", "Wow!", "3.5", "1,000.", "iPhone"].map(normalise)).toEqual([
      "hello", "us", "why", "wow", "3.5", "1,000", "iphone",
    ]);
  });
});

describe("isPlain", () => {
  it("accepts words and numbers with one trailing mark, nothing else", () => {
    expect(["so", "Hello,", "don't", "e-mail?", "3.5", "1,000.", "straße", "naïve!"].every(isPlain)).toBe(true);
    expect(["U.S.", "google.com", "a@b.com", "and/or", "so?!", "?", ""].some(isPlain)).toBe(false);
  });
});

describe("isValidOutput", () => {
  it("allows case changes and one trailing mark only", () => {
    expect(isValidOutput("pr", "PR.")).toBe(true);
    expect(isValidOutput("so", "So,")).toBe(true);
    expect(isValidOutput("so", "so")).toBe(true);
    expect(isValidOutput("so", "so.,")).toBe(false);
    expect(isValidOutput("so", "so!")).toBe(false);
    expect(isValidOutput("us", "U.S.")).toBe(false);
    expect(isValidOutput("so", "sot")).toBe(false);
    expect(isValidOutput("straße", "STRASSE")).toBe(false);
  });
});

describe("punctuateWords", () => {
  it("applies marks and casing from the model, replacing the recognizer's", async () => {
    const { infer, calls } = fake({
      "▁so": { caps: [1], mark: 3 },
      "▁i": { caps: [1] },
      "▁pr": { caps: [1, 2], mark: 1 },
      "▁today": { mark: 2 },
      "▁you": { mark: 4 },
    });
    const input = ["So.", "I", "think", "we", "should", "merge", "the", "pr", "today", "what", "do", "you"];
    expect(await punctuateWords(input, vocab, infer)).toEqual([
      "So,", "I", "think", "we", "should", "merge", "the", "PR", "today.", "what", "do", "you?",
    ]);
    expect(calls).toHaveLength(1);
    expect(calls[0]![0]).toBe(vocab.bos);
    expect(calls[0]!.at(-1)).toBe(vocab.eos);
  });

  it("reads dotted and other non-plain words as context and returns them exactly as given", async () => {
    const { infer, calls } = fake({ "▁so": { caps: [1], mark: 2 }, u: { caps: [0] }, s: { mark: 2 } });
    const input = ["so", "U.S.", "google.com", "and/or", "you"];
    expect(await punctuateWords(input, vocab, infer)).toEqual(["So.", "U.S.", "google.com", "and/or", "you"]);
    expect(calls[0]!.length).toBeGreaterThan(4);
  });

  it("takes the case of multi-piece words per character and the mark from the last piece", async () => {
    const { infer } = fake({ "▁": { caps: [0] }, x: { caps: [0] }, y: { mark: 2 } });
    expect(await punctuateWords(["yxy"], vocab, infer)).toEqual(["yXy."]);
  });

  it("leaves a word unchanged when its prediction is not a case change", async () => {
    const { infer } = fake({ "▁straße": { caps: [1, 2, 3, 4, 5, 6] } });
    expect(await punctuateWords(["Straße!", "so"], vocab, infer)).toEqual(["STRAßE", "so"]);
  });

  it("returns words with nothing to tokenize as given", async () => {
    const { infer, calls } = fake({});
    expect(await punctuateWords(["?", "", "!!"], vocab, infer)).toEqual(["?", "", "!!"]);
    expect(await punctuateWords([], vocab, infer)).toEqual([]);
    expect(calls).toHaveLength(0);
  });

  it("covers long input with windows under the token limit, keeping each word's core prediction", async () => {
    const { infer, calls } = fake({ "▁we": { mark: 2 }, "▁so": { caps: [1] } });
    const input = Array.from({ length: 500 }, (_, i) => words[i % 12]!);
    const output = await punctuateWords(input, vocab, infer);
    expect(output).toEqual(input.map((w) => (w === "we" ? "we." : w === "so" ? "So" : w)));
    expect(calls.length).toBeGreaterThan(500 / 48);
    for (const ids of calls) expect(ids.length).toBeLessThanOrEqual(MAX_TOKENS);
  });
});
