/**
 * A shown document's image source as a worktree path, with the rule publication used to copy it
 * (altitude/tasks.py `shown_reference`): the query and fragment dropped, percent-decoded, then resolved from the
 * document's folder, or from the worktree root after one `/`. `null` for a source with a scheme or a protocol-relative
 * one; `undefined` for a source that is empty or leaves the worktree.
 */
export function shownReference(source: string, document: string): string | null | undefined {
  // Like Python's `unquote`, a sequence that does not decode stays as written.
  const target = source.split("#", 1)[0]!.split("?", 1)[0]!.replace(/(?:%[0-9A-Fa-f]{2})+/g, (encoded) => {
    try { return decodeURIComponent(encoded); } catch { return encoded; }
  });
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(target) || target.startsWith("//") || target.startsWith("\\\\")) return null;
  if (!target || target.includes("\\") || /[\x00-\x1f]/.test(target)) return undefined;
  const parts = target.startsWith("/") ? [] : document.split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "..") {
      if (!parts.length) return undefined;
      parts.pop();
    } else if (part !== "" && part !== ".") parts.push(part);
  }
  return parts.length ? parts.join("/") : undefined;
}
