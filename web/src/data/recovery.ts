/**
 * Submitted-text recovery (#320): what the composer (Composer.tsx) keeps of each conversation's sent words beyond
 * itself. One record per conversation lives in this browser tab's sessionStorage; this document also keeps its
 * latest copy of each record and the recovered words each field shows. These records never initiate a send.
 */

/** "transcription": the recording failed; "unreached": host voice lost the connection past its wait. */
export type SendFailure = "refused" | "unconfirmed" | "transcription" | "unreached" | null;
/** `refused` names the pending copies the server refused; every other pending copy recovers as unconfirmed. */
export type SubmittedText = { pending: Record<string, string>; text: string; failure: SendFailure; refused?: string[] };

export const recoveryKey = (conversation: string) => `altitude.submitted:${conversation}`;

/**
 * The latest record this document read or wrote, so a failed storage read or write later in the document still
 * sees every edit. The stored bytes identify the copy it matches; storage cleared or replaced outside this document
 * wins again. `unwritten` marks a value storage did not accept.
 */
export const documentRecovery = new Map<string, { stored: string | null | undefined; value: SubmittedText; unwritten: boolean }>();

/** The recovered words each conversation's field last showed in this document, for the record it read. */
export const heldFields = new Map<string, string>();

/** The pending copies a send in this document still owns; a late callback for any other copy changes nothing. */
export const liveSubmissions = new Set<string>();

/**
 * Removing a project drops its conversations' recovery: the stored records, its sends' claims on their copies, and
 * this document's copies, each left empty for the bytes it matched so a record storage fails to delete reads as
 * empty here.
 */
export function forgetRecovery(project: string) {
  const ours = (conversation: string) => conversation === `project/${project}` || conversation.startsWith(`task/${project}/`);
  const forget = (conversation: string, stored: string | null | undefined) =>
    documentRecovery.set(conversation, { stored, value: { pending: {}, text: "", failure: null }, unwritten: false });
  for (const [conversation, copy] of documentRecovery) {
    if (!ours(conversation)) continue;
    for (const id of Object.keys(copy.value.pending)) liveSubmissions.delete(id);
    forget(conversation, copy.stored);
  }
  for (const conversation of [...heldFields.keys()]) if (ours(conversation)) heldFields.delete(conversation);
  let keys: string[];
  try { keys = Object.keys(sessionStorage); } catch { return; /* browser storage is unavailable: it holds nothing to read */ }
  for (const key of keys) {
    const conversation = key.slice(recoveryKey("").length);
    if (!key.startsWith(recoveryKey("")) || !ours(conversation)) continue;
    try {
      forget(conversation, sessionStorage.getItem(key));
      sessionStorage.removeItem(key);
    } catch { /* the record stays in storage; this document reads it as empty */ }
  }
}
