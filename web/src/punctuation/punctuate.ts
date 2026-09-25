// Word-level punctuation and truecasing around the pcs_en model's token predictions.
import { encodeWord, type Token, type Vocab } from "./tokenizer";
import { planWindows } from "./windows";

/** Model predictions for one sequence of T tokens: a label per token and a case flag per piece character. */
export interface Predictions {
  /** Post-token label, length T: 0 none, 1 acronym, 2 ".", 3 ",", 4 "?". */
  readonly post: ArrayLike<number>;
  /** Upper-case flags, T x CAP_WIDTH, indexed by the character's position in its piece. */
  readonly cap: ArrayLike<number>;
}

export type Infer = (ids: readonly number[]) => Promise<Predictions>;

export const CAP_WIDTH = 16;
/** The model accepts at most 128 tokens including <s> and </s>. */
export const MAX_TOKENS = 128;
/** Longer words are left as they are: a quarter of the window keeps room for neighbours. */
const MAX_WORD_TOKENS = Math.floor((MAX_TOKENS - 2) / 4);
const LIMITS = { core: 48, context: 8, tokens: MAX_TOKENS - 2 };
// The acronym label (1) would dot every letter; the word keeps its casing and gets no mark.
const MARKS = ["", "", ".", ",", "?"];

/** The model's view of a word: lowercase, without marks except "." and "," between digits (3.5, 1,000). */
export function normalise(word: string): string {
  return word.toLowerCase().replace(/[?!]|(?<!\d)[.,]|[.,](?!\d)/g, "");
}

/**
 * True when the model may punctuate and case `word`: after one trailing ". , ? !" it is letters,
 * digits, apostrophes and hyphens, or a number. Anything else (U.S., google.com, and/or) is context
 * only and stays exactly as recognized.
 */
export function isPlain(word: string): boolean {
  return /^(?:[\p{L}\p{M}\p{N}'’-]+|\d+(?:[.,]\d+)*)$/u.test(word.replace(/[.,?!]$/, ""));
}

/** True when `output` is `normalised` with only letter case changed and at most one trailing ". , ?". */
export function isValidOutput(normalised: string, output: string): boolean {
  const base = /[.,?]$/.test(output) ? output.slice(0, -1) : output;
  const got = [...base];
  const want = [...normalised];
  return got.length === want.length && got.every((c, i) => c.toLowerCase() === want[i]);
}

function decodeWord(chars: readonly string[], tokens: readonly Token[], first: number, predictions: Predictions): string {
  const out = [...chars];
  tokens.forEach((token, k) => {
    for (let at = Math.max(token.from, 1); at < token.to; at++) {
      const offset = at - token.from;
      const upper = chars[at - 1]!.toUpperCase();
      if (offset < CAP_WIDTH && predictions.cap[(first + k) * CAP_WIDTH + offset] && [...upper].length === 1) {
        out[at - 1] = upper;
      }
    }
  });
  return out.join("") + (MARKS[predictions.post[first + tokens.length - 1]!] ?? "");
}

/**
 * Punctuate and case `words`. Each plain output word is the input word with letter case changed and
 * its trailing mark replaced by at most one of the model's; any other word is returned as given.
 */
export async function punctuateWords(words: readonly string[], vocab: Vocab, infer: Infer): Promise<string[]> {
  const output = [...words];
  const normalised = words.map(normalise);
  const tokens = normalised.map((word) => (word ? encodeWord(vocab, word) : []));
  const active = tokens.flatMap((t, i) => (t.length > 0 && t.length <= MAX_WORD_TOKENS ? [i] : []));
  for (const window of planWindows(active.map((i) => tokens[i]!.length), LIMITS)) {
    const ids = [vocab.bos];
    const firstToken = new Map<number, number>();
    for (let w = window.start; w < window.end; w++) {
      const index = active[w]!;
      firstToken.set(index, ids.length);
      ids.push(...tokens[index]!.map((t) => t.id));
    }
    ids.push(vocab.eos);
    const predictions = await infer(ids);
    for (let w = window.coreStart; w < window.coreEnd; w++) {
      const index = active[w]!;
      const word = decodeWord([...normalised[index]!], tokens[index]!, firstToken.get(index)!, predictions);
      if (isPlain(words[index]!) && isValidOutput(normalised[index]!, word)) output[index] = word;
    }
  }
  return output;
}
