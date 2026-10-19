import { useMutation, useMutationState, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useMemo } from "react";

/*
 * A setting the operator changes shows its new value from the press (SPEC.md §1.1 Actions answer at once). The save
 * is one mutation keyed by the read it changes; that read's hook lays every save still on its way over the server's
 * data, so each view of it shows the change and the cache itself only ever holds what the server said. A save ends
 * only once `saved` has put the stored value in the cache, and reads of it still in flight are cancelled before
 * that, so nothing flashes back; a refused or lost save stops overlaying, the read is fetched again to show what is
 * stored, and the control says why with Retry. A change shown in several reads names them all (`reads`).
 */

const saveKey = (key: QueryKey) => ["save", ...key];

export function useSave<Change, Result>(key: QueryKey, save: (change: Change) => Promise<Result>,
  saved: (client: QueryClient, result: Result, change: Change) => unknown, reads: (change: Change) => QueryKey[] = () => [key]) {
  const client = useQueryClient();
  return useMutation<Result, Error, Change>({
    mutationKey: saveKey(key),
    mutationFn: save,
    // A read begun before the save landed would put the old value back after it, so it is cancelled first.
    onSuccess: async (result, change) => {
      await Promise.all(reads(change).map((read) => client.cancelQueries({ queryKey: read, exact: true })));
      await saved(client, result, change);
    },
    onError: (_error, change) => Promise.all(reads(change).map((read) => client.invalidateQueries({ queryKey: read, exact: true }))),
  });
}

/** The changes on their way for one read, oldest first: its hook applies them over what the server said. */
export function useSaving<Change>(key: QueryKey): Change[] {
  return useMutationState({
    filters: { mutationKey: saveKey(key), exact: true, status: "pending" },
    select: (mutation) => mutation.state.variables as Change,
  });
}

/**
 * The changes whose latest save was refused, by `id`, oldest first: every view of them shows why, including a view
 * that mounts after the refusal, and saving the change again clears it.
 */
export function useRefused<Change>(key: QueryKey, id: (change: Change) => string): Map<string, { change: Change; error: Error }> {
  const saves = useMutationState({
    filters: { mutationKey: saveKey(key), exact: true },
    select: (mutation) => ({ change: mutation.state.variables as Change, error: mutation.state.status === "error" ? mutation.state.error : null }),
  });
  return useMemo(() => {
    const latest = new Map<string, { change: Change; error: Error | null }>();
    for (const save of saves) latest.set(id(save.change), save);
    return new Map([...latest].flatMap(([key, save]) => save.error ? [[key, { change: save.change, error: save.error }] as const] : []));
  }, [saves, id]);
}
