import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

/** Shared toast copy so failures read the same everywhere. */
export const TOAST_COPY = {
  saveFailed: "Couldn't save — your change was put back.",
} as const;

export interface ToastOptions {
  message: string;
  action?: { label: string; onClick: () => void };
  severity?: "failure" | "limit" | "info";
  /** ms the toast stays; defaults 4000, failures 8000. Every toast can be dismissed. */
  dwellMs?: number;
}

interface ActiveToast extends ToastOptions {
  id: number;
  dwellMs: number;
}

export interface ToastApi {
  show: (options: ToastOptions) => void;
  dismiss: () => void;
}

const ApiContext = createContext<ToastApi | null>(null);
const ActiveContext = createContext<ActiveToast | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ActiveToast | null>(null);
  const nextId = useRef(0);

  const api = useMemo<ToastApi>(
    () => ({
      show: (options) => {
        nextId.current += 1;
        const dwellMs = options.dwellMs ?? (options.severity === "failure" ? 8000 : 4000);
        setActive({ ...options, dwellMs, id: nextId.current });
      },
      dismiss: () => setActive(null),
    }),
    [],
  );

  return (
    <ApiContext.Provider value={api}>
      <ActiveContext.Provider value={active}>{children}</ActiveContext.Provider>
    </ApiContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useToast must be used inside <ToastProvider>");
  return api;
}

function SeverityMark({ severity }: { severity: ActiveToast["severity"] }) {
  if (severity === "failure") return <span aria-hidden className="mt-0.5 size-2 shrink-0 rounded-full bg-danger" />;
  if (severity === "limit")
    return (
      <svg aria-hidden viewBox="0 0 16 16" className="mt-0.5 size-4 shrink-0 text-muted">
        <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 4.5V8l2.5 1.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg aria-hidden viewBox="0 0 16 16" className="mt-0.5 size-4 shrink-0 text-muted">
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function Toast({ toast, onDismiss }: { toast: ActiveToast; onDismiss: () => void }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(toast.dwellMs);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    remaining.current = toast.dwellMs;
    startedAt.current = Date.now();
  }, [toast.id, toast.dwellMs]);

  useEffect(() => {
    if (paused) return;
    startedAt.current = Date.now();
    const timer = setTimeout(onDismiss, remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [paused, toast.id, onDismiss]);

  return (
    <div
      role="status"
      data-theme="dark"
      className="toast pointer-events-auto w-[min(420px,calc(100vw-24px))]"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") onDismiss();
      }}
    >
      <SeverityMark severity={toast.severity} />
      <p className="flex-1 text-meta text-ink">{toast.message}</p>
      {toast.action && (
        <button
          type="button"
          className="shrink-0 rounded-[6px] px-2 py-1 text-meta font-semibold text-accent-ink hover:bg-hairline"
          onClick={() => {
            onDismiss();
            toast.action?.onClick();
          }}
        >
          {toast.action.label}
        </button>
      )}
        <button
          type="button"
          aria-label="Dismiss"
          className="min-h-11 min-w-11 shrink-0 rounded-[6px] text-meta text-muted hover:bg-hairline"
          onClick={onDismiss}
        >
          ✕
        </button>
      <div
        className="toast-timer"
        style={{ "--dwell": `${toast.dwellMs}ms` } as CSSProperties}
        data-paused={paused}
      />
    </div>
  );
}

/** Where the active toast renders. Mount once, inside ToastProvider; position via className. */
export function ToastViewport({ className = "" }: { className?: string }) {
  const active = useContext(ActiveContext);
  const api = useToast();
  const onDismiss = useCallback(() => api.dismiss(), [api]);
  if (!active) return null;
  return (
    <div className={`pointer-events-none fixed z-40 ${className}`}>
      <Toast key={active.id} toast={active} onDismiss={onDismiss} />
    </div>
  );
}
