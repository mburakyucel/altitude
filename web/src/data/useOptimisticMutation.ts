import {
  useMutation,
  useQueryClient,
  type QueryKey,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useRef } from "react";
import { TOAST_COPY, useToast } from "./Toast";

export interface OptimisticMutationOptions<TInput, TOutput, TCache> {
  mutationFn: (input: TInput) => Promise<TOutput>;
  /** The query whose cache is patched optimistically and invalidated on settle. */
  queryKey: QueryKey;
  /** Return the patched cache, or undefined to leave the cache untouched (invalidate-only). */
  update: (cached: TCache | undefined, input: TInput) => TCache | undefined;
  failureMessage?: string;
}

/**
 * Mutation with optimistic cache patch, rollback + failure toast (with Retry) on error,
 * and invalidation on settle.
 */
export function useOptimisticMutation<TInput, TOutput, TCache>({
  mutationFn,
  queryKey,
  update,
  failureMessage = TOAST_COPY.saveFailed,
}: OptimisticMutationOptions<TInput, TOutput, TCache>): UseMutationResult<
  TOutput,
  Error,
  TInput,
  { previous: TCache | undefined }
> {
  const queryClient = useQueryClient();
  const toast = useToast();
  const retry = useRef<(input: TInput) => void>(() => {});

  const mutation = useMutation<TOutput, Error, TInput, { previous: TCache | undefined }>({
    mutationFn,
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<TCache>(queryKey);
      const next = update(previous, input);
      if (next !== undefined) queryClient.setQueryData(queryKey, next);
      return { previous };
    },
    onError: (_error, input, context) => {
      queryClient.setQueryData(queryKey, context?.previous);
      toast.show({
        message: failureMessage,
        severity: "failure",
        action: { label: "Retry", onClick: () => retry.current(input) },
      });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  });

  retry.current = (input) => mutation.mutate(input);
  return mutation;
}
