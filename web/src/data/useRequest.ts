import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useRef } from "react";
import { useToast } from "./Toast";

/**
 * A request Altitude carries out (SPEC.md §1.1): its control shows the work in progress, a failure raises a toast
 * with Retry, and the read that shows the result is refreshed when it settles.
 */
export function useRequest<TOutput>({ mutationFn, queryKey, failureMessage }: {
  mutationFn: () => Promise<TOutput>; queryKey: QueryKey; failureMessage: string;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const retry = useRef(() => {});
  const mutation = useMutation<TOutput, Error, void>({
    mutationFn,
    onError: () => toast.show({ message: failureMessage, severity: "failure", action: { label: "Retry", onClick: () => retry.current() } }),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  });
  retry.current = () => mutation.mutate();
  return mutation;
}
