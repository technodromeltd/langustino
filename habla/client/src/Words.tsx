import { useEffect, useState } from "react";
import { listLabel } from "./types";
import type { Progress, WordRow } from "./types";

/** "Mis palabras": every word you've learned or used, with your own sentence and tap-to-hear. */
export function Words({ speak, onExit }: { speak: (text: string) => void; onExit: () => void }) {
  const [rows, setRows] = useState<WordRow[] | null>(null);
  const [tab, setTab] = useState<"all" | "learning" | "known">("all");
  const [q, setQ] = useState("");
  const [progress, setProgress] = useState<Progress | null>(null);

  useEffect(() => {
    fetch("/api/words")
      .then((r) => r.json())
      .then(setRows)
      .catch(() => setRows([]));
    fetch("/api/progress")
      .then((r) => r.json())
      .then(setProgress)
      .catch(() => setProgress(null));
  }, []);

  const shown = (rows ?? []).filter(
    (r) =>
      (tab === "all" || r.status === tab) &&
      (!q || r.display.toLowerCase().includes(q.toLowerCase()) || (r.translation ?? "").toLowerCase().includes(q.toLowerCase())),
  );
  const count = (s: string) => (rows ?? []).filter((r) => s === "all" || r.status === s).length;

  return (
    <div className="screen words">
      <header className="screen-head">
        <button className="back" onClick={onExit}>
          ← Home
        </button>
        <h2>Mis palabras</h2>
      </header>
      <div className="words-tools">
        <div className="tabs">
          {(["all", "learning", "known"] as const).map((t) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t === "all" ? "All" : t === "learning" ? "Learning" : "Can say"} <small>{count(t)}</small>
            </button>
          ))}
        </div>
        <input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {progress && (
        <p className="muted words-note">
          You can say <b>{progress.known}</b> of {(progress.total_content ?? progress.total).toLocaleString("en-US")} words · from{" "}
          {listLabel(progress)}
        </p>
      )}
      {rows === null ? (
        <p className="muted center">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted center">{rows.length ? "No matches." : "No words yet — they appear here as you talk with Sofía."}</p>
      ) : (
        <ul className="wordbank">
          {shown.map((r) => (
            <li key={r.lemma}>
              <button className="icon" onClick={() => speak(r.display)} title="Hear it">
                🔊
              </button>
              <div className="wb-main">
                <b>{r.display}</b> <span className="muted">{r.translation}</span>
                {r.example && <div className="wb-example">“{r.example}”</div>}
              </div>
              <span className={`chip ${r.status}`}>{r.status === "known" ? "can say" : "learning"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
