/** The install capability lives only in page memory. Remove its history entry before creating
 * the router or sending an access/trust request; ordinary route anchors remain untouched. */
export type PairingLink = { code: string } | { invalid: true } | null;

export function takePairingLink(): PairingLink {
  if (!window.location.hash.startsWith("#pair=")) return null;
  const fragment = window.location.hash.slice(6);
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
  try {
    const code = decodeURIComponent(fragment).toUpperCase();
    return /^[2-9A-HJ-NP-Z]{4}-?[2-9A-HJ-NP-Z]{4}$/.test(code) ? { code } : { invalid: true };
  } catch {
    return { invalid: true };
  }
}
