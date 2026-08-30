import { useState } from "react";
import { useDigest, useDigestSpeak } from "../data/api";

/**
 * The 0.1 listen view: the digest text, the rendered `/digest.wav` when there is one,
 * and the button that asks Kokoro for a fresh render. Kokoro takes a few seconds, so
 * the audio element only appears once `/api/digest` reports `audio: true` (the query
 * polls); the query string on the src busts the browser cache after a re-render.
 */
export default function Listen() {
  const digest = useDigest();
  const speak = useDigestSpeak();
  const [stamp, setStamp] = useState(() => Date.now());

  if (digest.isPending) return <p className="text-muted">Loading…</p>;
  if (digest.isError) return <p className="text-danger">{digest.error.message}</p>;

  const text = digest.data.text ?? "";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-page-title font-semibold">Listen</h1>

      <section className="card space-y-3">
        {digest.data.audio ? (
          <audio className="w-full" controls aria-label="Digest audio" src={`/digest.wav?${stamp}`} />
        ) : (
          <p className="text-meta text-muted">no rendered digest yet</p>
        )}
        <button
          type="button"
          className="btn"
          disabled={speak.isPending}
          onClick={() => {
            speak.mutate(undefined, { onSuccess: () => setStamp(Date.now()) });
          }}
        >
          Render with Kokoro
        </button>
      </section>

      {text ? (
        <pre className="card overflow-x-auto whitespace-pre-wrap text-body text-ink-2">{text}</pre>
      ) : (
        <p className="text-muted">No digest yet — it is written after the first daily roll-up.</p>
      )}
    </div>
  );
}
