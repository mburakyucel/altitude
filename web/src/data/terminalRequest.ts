/**
 * Whether altd could route this request path to the terminal; the dev proxy refuses these so altd never
 * sees the proxy as a terminal client. altd splits the raw path and drops empty segments, so
 * `/api//terminal` counts; the decoded, dot-resolved reading is refused too, and so is any path with
 * `;` parameters, which altd's parser removes before routing.
 */
export function terminalRequest(url: string | undefined): boolean {
  const raw = (url ?? "/").split(/[?#]/)[0]!;
  if (raw.includes(";")) return true;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return true; // a path this cannot read is not proxied
  }
  const resolved: string[] = [];
  for (const part of decoded.split("/")) {
    if (part === "..") resolved.pop();
    else if (part && part !== ".") resolved.push(part);
  }
  return [raw.split("/").filter(Boolean), resolved].some((parts) => parts[0] === "api" && (parts[1] ?? "").startsWith("terminal"));
}
