import { BookOpen, Flame, LineChart, Settings, Sparkles } from "lucide-react";
import { availableSets } from "./content/registry";
import { loadUserState } from "./core/storage";

const userState = loadUserState();
const activeSet = availableSets.find((set) => set.id === userState.activeSetId) ?? availableSets[0];

export function App() {
  return (
    <main className="app-shell">
      <section className="topbar" aria-label="Langustino status">
        <div>
          <p className="eyebrow">Langustino</p>
          <h1>The fastest way to learn languages.</h1>
        </div>
        <button className="icon-button" type="button" aria-label="Settings">
          <Settings size={20} />
        </button>
      </section>

      <section className="practice-stage" aria-label="Today practice">
        <div className="practice-copy">
          <p className="set-label">{activeSet?.title ?? "No set loaded"}</p>
          <h2>Today's practice is ready.</h2>
          <p>
            Build a Spanish habit with 35 new words and 15 smart reviews, stored privately on this
            device.
          </p>
        </div>
        <button className="primary-action" type="button">
          <Sparkles size={20} />
          Start daily practice
        </button>
      </section>

      <section className="quick-stats" aria-label="Progress summary">
        <div>
          <BookOpen size={19} />
          <span>0 learned</span>
        </div>
        <div>
          <LineChart size={19} />
          <span>0 due</span>
        </div>
        <div>
          <Flame size={19} />
          <span>{userState.streak.current} day streak</span>
        </div>
      </section>

      <nav className="bottom-nav" aria-label="Primary">
        <button className="active" type="button">Today</button>
        <button type="button">Stats</button>
        <button type="button">Set</button>
      </nav>
    </main>
  );
}
