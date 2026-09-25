import { describe, expect, it } from "vitest";
import { inputPiece } from "./TerminalScreen";

describe("terminal input pieces", () => {
  it("sends a long paste in 16 KiB pieces without splitting a character", () => {
    expect(inputPiece("ls\r")).toBe("ls\r");
    expect(inputPiece("a".repeat(20_000))).toHaveLength(16_384);
    const across = `${"a".repeat(16_383)}😀b`;
    const first = inputPiece(across);
    expect(first).toBe("a".repeat(16_383));
    expect(inputPiece(across.slice(first.length))).toBe("😀b");
  });
});
