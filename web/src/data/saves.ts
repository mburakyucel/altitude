import { useMutation, useMutationState, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";

/*
 * A setting the operator changes shows its new value from the press (SPEC.md §1.1 Actions answer at once). The save
 * is one mutation keyed by the read it changes; that read's hook lays every save still on its way over the server's
 * data, so each view of it shows the change and the cache itself only ever holds what the server said. A save ends
 * only once `saved` has put the stored value in the cache, and reads of it still in flight are cancelled before
 * that, so nothing flashes back; a refused or lost save stops overlaying, the read is fetched again to show what is
 * stored, and the control says why with Retry.
 */

const saveKey = (key: QueryKey) => ["save", ...key];

export function useSave<Change, Result>(key: QueryKey, save: (change: Change) => Promise<Result>,
  saved: (client: QueryClient, result: Result, change: Change) => unknown) {
  const client = useQueryClient();
  return useMutation<Result, Error, Change>({
    mutationKey: saveKey(key),
    mutationFn: save,
    // A read begun before the save landed would put the old value back after it, so it is cancelled first.
    onSuccess: async (result, change) => {
      await client.cancelQueries({ queryKey: key, exact: true });
      await saved(client, result, change);
    },
    onError: () => client.invalidateQueries({ queryKey: key, exact: true }),
  });
}

/** The changes on their way for one read, oldest first: its hook applies them over what the server said. */
export function useSaving<Change>(key: QueryKey): Change[] {
  return useMutationState({
    filters: { mutationKey: saveKey(key), exact: true, status: "pending" },
    select: (mutation) => mutation.state.variables as Change,
  });
}
