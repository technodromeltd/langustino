import type { AttemptResult, Attempts } from "./types";

/** "🎙 Say it" button + the graded result (each word green or red). */
export function AttemptButton({
  attempts,
  attempt,
  label,
}: {
  attempts: Attempts;
  attempt: Parameters<Attempts["start"]>[0];
  label: string;
}) {
  const busy = attempts.active === attempt.key;
  const phase = busy ? attempts.phase : null;
  const r = attempts.results[attempt.key];
  return (
    <span className={`attempt ${r?.ok && !busy ? "ok-flash" : ""}`}>
      <button
        className={`try ${phase === "listening" ? "rec" : ""} ${phase === "checking" ? "checking" : ""}`}
        onClick={() => attempts.start(attempt)}
        disabled={busy}
      >
        {phase === "listening" ? (
          "● Listening…"
        ) : phase === "checking" ? (
          <>
            <span className="spinner" aria-hidden /> Checking…
          </>
        ) : r ? (
          r.ok ? (
            "✓ Nice — again?"
          ) : (
            "🎙 Try again"
          )
        ) : (
          `🎙 ${label}`
        )}
      </button>
      {r && !busy && <ResultLine r={r} />}
    </span>
  );
}

export function ResultLine({ r }: { r: AttemptResult }) {
  // Right, with a note (another word that also means it): the note says it all, the word-by-word line would be red.
  if (r.ok && r.note)
    return (
      <span className="result ok">
        ✓ <span className="result-note">{r.note}</span>
      </span>
    );
  return (
    <span className={`result ${r.ok ? "ok" : "bad"}`}>
      {r.ok ? "✓ " : ""}
      {r.words.map((w, i) => (
        <span key={i} className={w.ok ? "w-ok" : "w-bad"}>
          {w.w}{" "}
        </span>
      ))}
      {r.note && <span className="result-note">{r.note}</span>}
      {!r.ok && <small>{r.heard ? `I heard: “${r.heard}”` : "I didn't hear anything"}</small>}
    </span>
  );
}
