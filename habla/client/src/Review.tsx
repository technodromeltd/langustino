import { useEffect, useState } from "react";
import { ResultLine } from "./AttemptView";
import type { Attempts, FixDropMsg, FixSkipMsg, ReviewItem, ReviewSkipMsg } from "./types";

const itemId = (it: ReviewItem) => (it.kind === "fix" ? `f:${it.id}` : `w:${it.lemma}`);
const answerOf = (it: ReviewItem) => (it.kind === "fix" ? it.right : it.display);
const meaningOf = (it: ReviewItem) => (it.kind === "fix" ? it.right_en : it.english);

/** Spoken review: see the English, say the Spanish. Words on a spaced schedule, plus the learner's own corrected
 *  mistakes ("Say it right"). Anything missed comes back once more at the end of the round. */
export function Review({
  attempts,
  speak,
  send,
  onExit,
}: {
  attempts: Attempts;
  speak: (text: string) => void;
  send: (m: ReviewSkipMsg | FixSkipMsg | FixDropMsg) => unknown;
  onExit: () => void;
}) {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [firstTry, setFirstTry] = useState<Record<string, boolean>>({});
  const [dropped, setDropped] = useState<Record<string, true>>({});
  const [retried, setRetried] = useState(false);
  const [stopped, setStopped] = useState(false); // Escape: the score so far

  useEffect(() => {
    fetch("/api/review")
      .then((r) => r.json())
      .then((xs: ReviewItem[]) => setItems(xs.map((x) => ({ ...x, kind: x.kind ?? "word" }) as ReviewItem)))
      .catch(() => setItems([]));
  }, []);

  const item = items?.[idx];
  const id = item ? itemId(item) : "";
  const key = item ? `rev:${idx}:${id}` : "";
  const result = key ? attempts.results[key] : undefined;
  const phase = key && attempts.active === key ? attempts.phase : null;
  const done = items !== null && (stopped || idx >= items.length);

  // Remember the first answer for each item, for the score.
  useEffect(() => {
    if (item && result && !(id in firstTry) && !dropped[id]) setFirstTry((f) => ({ ...f, [id]: result.ok }));
  }, [item, id, result, firstTry, dropped]);

  // Right answers move on by themselves (a little later when there's a note to read).
  useEffect(() => {
    if (!result?.ok) return;
    const t = window.setTimeout(next, result.note ? 2600 : 1400);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  /** Items missed on the first try, once each (a retried item is in the list twice). */
  function missedOf(list: ReviewItem[], skip = ""): ReviewItem[] {
    const seen = new Set<string>();
    return list.filter((it) => {
      const k = itemId(it);
      if (k === skip || dropped[k] || firstTry[k] !== false || seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }
  const missed = missedOf(items ?? []);

  function next(skip = "") {
    if (!items) return;
    setRevealed(false);
    const again = missedOf(items, skip);
    if (idx + 1 >= items.length && !retried && again.length) {
      setRetried(true); // one more go at the ones you missed
      setItems([...items, ...again]);
    }
    setIdx((i) => i + 1);
  }

  const say = () => {
    if (!item) return;
    if (item.kind === "fix") attempts.start({ kind: "fix", target: item.right, fix_id: item.id, key });
    else attempts.start({ kind: "review", target: item.display, lemma: item.lemma, key });
  };
  const show = () => {
    if (!item) return;
    setRevealed(true);
    if (!(id in firstTry)) {
      setFirstTry((f) => ({ ...f, [id]: false }));
      send(item.kind === "fix" ? { type: "fix_skip", id: item.id } : { type: "review_skip", lemma: item.lemma });
    }
    speak(answerOf(item));
  };
  // "This wasn't a mistake": that fix never comes back, and it doesn't count in the score.
  const drop = () => {
    if (item?.kind !== "fix") return;
    if (attempts.active === key) attempts.cancel();
    send({ type: "fix_drop", id: item.id });
    setDropped((d) => ({ ...d, [id]: true }));
    setFirstTry((f) => {
      const { [id]: _old, ...rest } = f; // eslint-disable-line @typescript-eslint/no-unused-vars
      return rest;
    });
    next(id);
  };
  // Escape: first to the score so far, then out.
  const stop = () => {
    if (done || !items?.length) return onExit();
    if (attempts.active) attempts.cancel();
    setStopped(true);
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Escape") stop();
      else if (done) return;
      else if (e.code === "Space" && !e.repeat && item && !result && attempts.active !== key) {
        e.preventDefault();
        say();
      } else if (e.code === "Enter" && (result || revealed)) next();
    };
    window.addEventListener("keydown", down);
    return () => window.removeEventListener("keydown", down);
  });

  const right = Object.values(firstTry).filter(Boolean).length;
  const total = Object.keys(firstTry).length;

  return (
    <div className="screen review">
      <header className="screen-head">
        <button className="back" onClick={onExit}>
          ← Home
        </button>
        <h2>Repaso</h2>
        {items && items.length > 0 && !done && (
          <>
            <div className="bar wide">
              <div style={{ width: `${(100 * idx) / items.length}%` }} />
            </div>
            <span className="review-count muted small">
              {Math.min(idx + 1, items.length)} / {items.length}
            </span>
          </>
        )}
      </header>

      {items === null ? (
        <p className="muted center">Loading…</p>
      ) : items.length === 0 ? (
        <div className="empty">
          <p>Nothing to review yet.</p>
          <p className="muted small">Words you learn and fixes from your lessons come back here, spaced out so they stick.</p>
          <button className="primary" onClick={onExit}>
            Start a lesson
          </button>
        </div>
      ) : done ? (
        <div className="review-done">
          <div className="big">{total === 0 ? "👋" : right === total ? "🎉" : right >= total / 2 ? "💪" : "🌱"}</div>
          <h3>{total === 0 ? "See you next time" : `${right} of ${total} right first time`}</h3>
          <p className="muted">
            {total === 0
              ? "They'll be here when you're ready."
              : right === total
                ? "Perfect — these will come back later, further apart."
                : "The ones you missed come back sooner."}
          </p>
          {missed.length > 0 && (
            <ul className="missed">
              {missed.map((m) => (
                <li key={itemId(m)}>
                  <button className="icon" onClick={() => speak(answerOf(m))} title="Hear it">
                    🔊
                  </button>
                  <b>{answerOf(m)}</b> <span className="muted">{meaningOf(m)}</span>
                </li>
              ))}
            </ul>
          )}
          <button className="primary" onClick={onExit}>
            Done
          </button>
        </div>
      ) : (
        item && (
          <div className={`card-drill ${result?.ok ? "ok-flash" : ""}`}>
            <span className="prompt-label">{item.kind === "fix" ? "Say it right:" : "How do you say…"}</span>
            <div className="prompt-en">{meaningOf(item)}</div>
            {item.kind === "fix" && item.wrong && (
              <div className="fix-not muted">
                not: <span className="strike">«{item.wrong}»</span>
              </div>
            )}
            {(result || revealed) && (
              <div className={`answer ${result?.ok ? "ok" : ""}`}>
                <button className="icon" onClick={() => speak(answerOf(item))} title="Hear it">
                  🔊
                </button>
                {answerOf(item)}
              </div>
            )}
            {result && (!result.ok || result.note) && <ResultLine r={result} />}
            {revealed && !result && item.kind === "fix" && item.why && <p className="fix-why muted small">{item.why}</p>}
            <div className="drill-actions">
              {!result && !revealed ? (
                <>
                  <button
                    className={`mic small ${phase === "listening" ? "rec" : ""} ${phase === "checking" ? "checking" : ""}`}
                    onClick={say}
                    disabled={phase === "checking"}
                    aria-label="Say it"
                  >
                    {phase === "checking" ? <span className="spinner" aria-hidden /> : "🎙"}
                  </button>
                  <button className="link" onClick={show}>
                    I don't know
                  </button>
                </>
              ) : result?.ok ? (
                <span className="cheer">¡Muy bien!</span>
              ) : (
                <>
                  <button className="link" onClick={say}>
                    Try again
                  </button>
                  <button className="primary" onClick={() => next()}>
                    Next →
                  </button>
                </>
              )}
            </div>
            <p className="muted small center">
              {phase === "listening"
                ? "Listening… I'll stop when you pause"
                : phase === "checking"
                  ? "Checking…"
                  : item.kind === "fix"
                    ? "Press Space (or the mic) and say it the right way"
                    : "Press Space (or the mic) and say it in Spanish"}
            </p>
            {item.kind === "fix" && (
              <button className="link small fix-drop" onClick={drop}>
                This wasn't a mistake
              </button>
            )}
          </div>
        )
      )}
    </div>
  );
}
