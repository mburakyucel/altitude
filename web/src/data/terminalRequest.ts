/**
 * Whether altd would route this request path to the terminal. altd drops empty path segments, so
 * `/api//terminal` counts; the dev proxy refuses these so altd never sees the proxy as a terminal client.
 */
export function terminalRequest(url: string | undefined): boolean {
  let path: string;
  try {
    path = decodeURIComponent((url ?? "/").split(/[?#]/)[0]!);
  } catch {
    return true; // a path this cannot read is not proxied
  }
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return parts[0] === "api" && (parts[1] ?? "").startsWith("terminal");
}
