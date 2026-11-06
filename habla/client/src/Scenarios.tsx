import { useEffect, useState } from "react";
import type { Phrase, Scenario } from "./types";

const EXAMPLES = ["Buying fruit at the frutería", "Ordering tapas at a bar", "Asking the pharmacist for something for a cold"];

/** "Mis escenarios": describe a real situation, get a mini lesson for it, practise it, keep its cheat sheet. */
export function Scenarios({
  connected,
  speak,
  onStart,
}: {
  connected: boolean;
  speak: (text: string) => void;
  onStart: (s: Scenario, mode: "easy" | "real") => void;
}) {
  const [list, setList] = useState<Scenario[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/scenarios")
      .then((r) => r.json())
      .then((l: Scenario[]) => setList(Array.isArray(l) ? l : []))
      .catch(() => {});
  }, []);

  const build = async () => {
    const p = prompt.trim();
    if (!p || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/scenarios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: p }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Couldn't build that scenario.");
      setList((l) => [j as Scenario, ...l]);
      setOpen(j.id);
      setPrompt("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = (id: string) => {
    if (!window.confirm("Delete this scenario?")) return;
    fetch(`/api/scenarios/${id}`, { method: "DELETE" }).catch(() => {});
    setList((l) => l.filter((s) => s.id !== id));
    if (open === id) setOpen(null);
  };

  const shown = list.find((s) => s.id === open);

  return (
    <section className="scenarios">
      <h3 className="section-title">Practise a real situation</h3>
      <div className="sc-builder">
        <textarea
          rows={2}
          value={prompt}
          placeholder="Where are you going? e.g. “Going to the fruit shop, I want cherries and ripe avocados”"
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void build();
            }
          }}
          disabled={busy}
        />
        <button className="sc-build" onClick={() => void build()} disabled={busy || !prompt.trim()}>
          {busy ? "Building…" : "✨ Build scenario"}
        </button>
      </div>
      {busy && <p className="muted small">Writing your scene and phrases — about 15 seconds…</p>}
      {error && <p className="warn">{error}</p>}
      {!list.length && !busy && (
        <div className="sc-examples">
          {EXAMPLES.map((x) => (
            <button key={x} className="chip" onClick={() => setPrompt(x)}>
              {x}
            </button>
          ))}
        </div>
      )}
      {list.length > 0 && (
        <div className="sc-list">
          {list.map((s) => (
            <button key={s.id} className={`sc-tab ${open === s.id ? "on" : ""}`} onClick={() => setOpen(open === s.id ? null : s.id)}>
              <b>{s.title_es}</b>
              {(s.runs?.easy || s.runs?.real) && <span className="sc-done">✓</span>}
            </button>
          ))}
        </div>
      )}
      {shown && (
        <ScenarioSheet s={shown} speak={speak} connected={connected} onStart={onStart} onDelete={() => remove(shown.id)} />
      )}
    </section>
  );
}

/** The preview before practising, and the cheat sheet afterwards: phrases you'll hear and say, with audio. */
export function ScenarioSheet({
  s,
  speak,
  connected,
  onStart,
  onDelete,
}: {
  s: Scenario;
  speak: (text: string) => void;
  connected: boolean;
  onStart?: (s: Scenario, mode: "easy" | "real") => void;
  onDelete?: () => void;
}) {
  const said = new Set([...(s.runs?.easy?.used ?? []), ...(s.runs?.real?.used ?? [])]);
  const practised = !!(s.runs?.easy || s.runs?.real);
  const row = (p: Phrase, mine: boolean) => (
    <li key={p.es} className={mine && practised ? (said.has(p.es) ? "ok" : "todo") : ""}>
      <button className="sc-play" title="Listen" onClick={() => speak(p.es)} disabled={!connected}>
        🔊
      </button>
      <span>
        <b>{p.es}</b>
        <small>{p.en}</small>
      </span>
      {mine && practised && <i>{said.has(p.es) ? "said it" : "practise"}</i>}
    </li>
  );
  return (
    <div className="sc-sheet">
      <div className="sc-head">
        <div>
          <span className="lesson-es">{s.title_es}</span>
          <span className="lesson-goal">{s.goal}</span>
          <span className="muted small">Sofía plays {s.role.replace(/^the /i, "the ").replace(/\.$/, "")}.</span>
        </div>
        {onDelete && (
          <button className="sc-del" title="Delete scenario" onClick={onDelete}>
            🗑
          </button>
        )}
      </div>
      <div className="sc-cols">
        <div>
          <h4>You'll hear</h4>
          <ul className="sc-phrases">{s.hear.map((p) => row(p, false))}</ul>
        </div>
        <div>
          <h4>You'll say</h4>
          <ul className="sc-phrases">{s.say.map((p) => row(p, true))}</ul>
        </div>
      </div>
      {s.words.length > 0 && (
        <p className="small">
          <span className="muted">Words: </span>
          {s.words.map((w) => w.replace(":", " —")).join(" · ")}
        </p>
      )}
      {onStart && (
        <div className="sc-actions">
          <button className="sc-run" onClick={() => onStart(s, "easy")} disabled={!connected}>
            ▶ Practise <small>slow &amp; friendly</small>
          </button>
          <button className="sc-run real" onClick={() => onStart(s, "real")} disabled={!connected}>
            ▶ Realistic run <small>natural pace, surprises</small>
          </button>
        </div>
      )}
    </div>
  );
}
