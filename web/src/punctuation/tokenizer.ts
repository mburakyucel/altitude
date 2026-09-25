// Unigram (SentencePiece) tokenizer over a "piece<TAB>score" vocabulary; line i is token id i.

export interface Vocab {
  readonly pieces: ReadonlyMap<string, { readonly id: number; readonly score: number }>;
  readonly maxPieceLength: number;
  readonly unk: number;
  readonly bos: number;
  readonly eos: number;
}

/** A token covering code points [from, to) of "▁" + word; from === 0 means it includes the "▁". */
export interface Token {
  readonly id: number;
  readonly from: number;
  readonly to: number;
}

const BOUNDARY = "▁";
const UNKNOWN_PENALTY = -100;

export function parseVocab(tsv: string): Vocab {
  const pieces = new Map<string, { id: number; score: number }>();
  let maxPieceLength = 0;
  tsv.split("\n").forEach((line, id) => {
    const tab = line.lastIndexOf("\t");
    if (tab < 0) return;
    const piece = line.slice(0, tab);
    pieces.set(piece, { id, score: Number(line.slice(tab + 1)) });
    maxPieceLength = Math.max(maxPieceLength, [...piece].length);
  });
  const special = (piece: string) => {
    const entry = pieces.get(piece);
    if (!entry) throw new Error(`Vocabulary has no ${piece}`);
    return entry.id;
  };
  return { pieces, maxPieceLength, unk: special("<unk>"), bos: special("<s>"), eos: special("</s>") };
}

/** Best-scoring segmentation of one lowercase word; a character outside the vocabulary becomes <unk>. */
export function encodeWord(vocab: Vocab, word: string): Token[] {
  const chars = [BOUNDARY, ...word];
  const n = chars.length;
  const best = new Float64Array(n + 1).fill(-Infinity);
  const from = new Int32Array(n + 1);
  const id = new Int32Array(n + 1);
  best[0] = 0;
  for (let end = 1; end <= n; end++) {
    for (let start = Math.max(0, end - vocab.maxPieceLength); start < end; start++) {
      const base = best[start]!;
      if (base === -Infinity) continue;
      const hit = vocab.pieces.get(chars.slice(start, end).join(""));
      if (!hit && end - start > 1) continue;
      const score = base + (hit ? hit.score : UNKNOWN_PENALTY);
      if (score > best[end]!) {
        best[end] = score;
        from[end] = start;
        id[end] = hit ? hit.id : vocab.unk;
      }
    }
  }
  const tokens: Token[] = [];
  for (let end = n; end > 0; end = from[end]!) tokens.push({ id: id[end]!, from: from[end]!, to: end });
  return tokens.reverse();
}
