// Sliding windows over words: each window predicts its core words, with context words on either side.

export interface Window {
  readonly start: number;
  readonly coreStart: number;
  readonly coreEnd: number;
  readonly end: number;
}

export interface WindowLimits {
  readonly core: number;
  readonly context: number;
  /** Token budget per window; every word must fit in a quarter of it. */
  readonly tokens: number;
}

/** Windows whose cores cover every word exactly once, each within `limits.tokens` tokens. */
export function planWindows(tokenCounts: readonly number[], limits: WindowLimits): Window[] {
  const count = (i: number) => tokenCounts[i]!;
  const coreBudget = Math.floor((limits.tokens * 3) / 4);
  const windows: Window[] = [];
  for (let coreStart = 0; coreStart < tokenCounts.length; ) {
    let coreEnd = coreStart + 1;
    let used = count(coreStart);
    while (coreEnd < tokenCounts.length && coreEnd - coreStart < limits.core && used + count(coreEnd) <= coreBudget) {
      used += count(coreEnd++);
    }
    let start = coreStart;
    let end = coreEnd;
    for (let grew = true; grew; ) {
      grew = false;
      if (coreStart - start < limits.context && start > 0 && used + count(start - 1) <= limits.tokens) {
        used += count(--start);
        grew = true;
      }
      if (end - coreEnd < limits.context && end < tokenCounts.length && used + count(end) <= limits.tokens) {
        used += count(end++);
        grew = true;
      }
    }
    windows.push({ start, coreStart, coreEnd, end });
    coreStart = coreEnd;
  }
  return windows;
}
