import { BookOpen, Check, Flame, Info, LineChart, RotateCcw, Settings, Sparkles, X } from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { availableSets } from "./content/registry";
import { getAnswer, getPrompt, type ContentCard, type ContentSet } from "./core/content";
import {
  advanceSession,
  getCardProgress,
  getLearningBucket,
  getTodaySession,
  planDailySession,
  startDailySession,
} from "./core/scheduler";
import {
  loadUserState,
  saveUserState,
  todayKey,
  type DailySessionState,
  type RecallCounts,
  type RecallQuality,
  type StoredPracticeItem,
  type UserState,
} from "./core/storage";

type AppView = "today" | "stats" | "set";
type PracticeMode = "home" | "practice" | "complete";

const qualityLabels: Record<RecallQuality, string> = {
  forgot: "Forgot",
  hard: "Hard",
  good: "Good",
  easy: "Easy",
};

export function App() {
  const [userState, setUserState] = useState<UserState>(() => loadUserState());
  const [view, setView] = useState<AppView>("today");
  const [isRevealed, setIsRevealed] = useState(false);
  const [cardStartedAt, setCardStartedAt] = useState(() => Date.now());
  const [revealedAt, setRevealedAt] = useState<number | null>(null);
  const [detailCard, setDetailCard] = useState<ContentCard | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const activeSet = useMemo(
    () => availableSets.find((set) => set.id === userState.activeSetId) ?? availableSets[0],
    [userState.activeSetId],
  );
  const today = todayKey();
  const session = getTodaySession(activeSet, userState, today);
  const mode: PracticeMode = session?.completed ? "complete" : session ? "practice" : "home";
  const currentItem = session?.items[session.currentIndex];
  const currentCard = activeSet.cards.find((card) => card.id === currentItem?.cardId) ?? activeSet.cards[0];
  const progress = session?.items.length
    ? Math.min(100, Math.round((session.currentIndex / session.items.length) * 100))
    : 0;
  const sessionRecallCounts = session?.recallCounts ?? { forgot: 0, hard: 0, good: 0, easy: 0 };
  const answeredCount = session?.answeredCount ?? userState.activity[today]?.reviews ?? 0;
  const previewSession = useMemo(() => planDailySession(activeSet, userState, today), [activeSet, today, userState]);
  const summary = useMemo(() => summarizeSet(activeSet, userState, today), [activeSet, today, userState]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: userState.settings.reduceMotion ? "auto" : "smooth" });
  }, [mode, userState.settings.reduceMotion]);

  useEffect(() => {
    setCardStartedAt(Date.now());
    setRevealedAt(null);
  }, [currentItem?.id]);

  function updateAndSave(nextState: UserState) {
    setUserState(nextState);
    saveUserState(nextState);
  }

  function startPractice() {
    const nextState = startDailySession(activeSet, userState, today);
    updateAndSave(nextState);
    setIsRevealed(false);
    setView("today");
  }

  function revealCard() {
    setIsRevealed(true);
    setRevealedAt((existing) => existing ?? Date.now());
  }

  function toggleCard() {
    if (isRevealed) {
      setIsRevealed(false);
      return;
    }

    revealCard();
  }

  function answerCard(remembered: boolean) {
    if (!currentItem) {
      return;
    }

    const recallMs = (revealedAt ?? Date.now()) - cardStartedAt;
    const quality = qualityFromAnswer(remembered, recallMs);
    updateAndSave(advanceSession(userState, currentItem, quality, today));
    setIsRevealed(false);
    setRevealedAt(null);
  }

  function resetPractice() {
    const nextState: UserState = {
      ...userState,
      session: null,
    };
    updateAndSave(nextState);
    setIsRevealed(false);
  }

  function updateSettings(settings: UserState["settings"]) {
    updateAndSave({
      ...userState,
      settings,
    });
  }

  function resetProgress() {
    if (!window.confirm("Reset all local Langustino progress on this device?")) {
      return;
    }

    const fresh = loadUserState();
    fresh.cards = {};
    fresh.session = null;
    fresh.activity = {};
    fresh.streak = {
      current: 0,
      best: 0,
      lastCompletedDate: null,
    };
    updateAndSave(fresh);
    setSettingsOpen(false);
    setIsRevealed(false);
  }

  return (
    <main className="app-shell">
      <section className="topbar" aria-label="Langustino status">
        <div>
          <p className="eyebrow">Langustino</p>
          <h1>{view === "today" ? "Spanish practice, ready now." : "Your Spanish map."}</h1>
        </div>
        <button className="icon-button" type="button" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
          <Settings size={20} />
        </button>
      </section>

      {view === "today" ? (
        <TodayView
          answeredCount={answeredCount}
          currentCard={currentCard}
          currentItem={currentItem}
          isRevealed={isRevealed}
          mode={mode}
          previewSession={previewSession}
          progress={progress}
          qualityCounts={sessionRecallCounts}
          session={session}
          onAnswer={answerCard}
          onOpenDetail={setDetailCard}
          onReset={resetPractice}
          onToggleCard={toggleCard}
          onStart={startPractice}
        />
      ) : view === "stats" ? (
        <StatsView summary={summary} />
      ) : (
        <SetView activeSet={activeSet} />
      )}

      <section className="quick-stats" aria-label="Progress summary">
        <div>
          <BookOpen size={19} />
          <span>{summary.learned + summary.mastered} learned</span>
        </div>
        <div>
          <LineChart size={19} />
          <span>{summary.dueToday} due</span>
        </div>
        <div>
          <Flame size={19} />
          <span>{userState.streak.current} day streak</span>
        </div>
      </section>

      <nav className="bottom-nav" aria-label="Primary">
        <button className={view === "today" ? "active" : ""} type="button" onClick={() => setView("today")}>
          Today
        </button>
        <button className={view === "stats" ? "active" : ""} type="button" onClick={() => setView("stats")}>
          Stats
        </button>
        <button className={view === "set" ? "active" : ""} type="button" onClick={() => setView("set")}>
          Set
        </button>
      </nav>

      {detailCard ? <CardDetail card={detailCard} onClose={() => setDetailCard(null)} /> : null}
      {settingsOpen ? (
        <SettingsSheet
          settings={userState.settings}
          onClose={() => setSettingsOpen(false)}
          onResetProgress={resetProgress}
          onUpdateSettings={updateSettings}
        />
      ) : null}
    </main>
  );
}

interface TodayViewProps {
  answeredCount: number;
  currentCard: ContentCard;
  currentItem: StoredPracticeItem | undefined;
  isRevealed: boolean;
  mode: PracticeMode;
  previewSession: DailySessionState;
  progress: number;
  qualityCounts: RecallCounts;
  session: DailySessionState | null;
  onAnswer: (remembered: boolean) => void;
  onOpenDetail: (card: ContentCard) => void;
  onReset: () => void;
  onToggleCard: () => void;
  onStart: () => void;
}

function TodayView({
  answeredCount,
  currentCard,
  currentItem,
  isRevealed,
  mode,
  previewSession,
  progress,
  qualityCounts,
  session,
  onAnswer,
  onOpenDetail,
  onReset,
  onToggleCard,
  onStart,
}: TodayViewProps) {
  if (mode === "complete" && session) {
    return (
      <section className="practice-stage complete-stage" aria-label="Session complete">
        <div className="practice-copy">
          <p className="set-label">Daily session complete</p>
          <h2>Nice work.</h2>
          <p>
            You finished {session.items.length} prompts, including {session.introducedCount} new
            words, {session.reviewCount} scheduled reviews, and {session.repeatCount} extra repeats.
          </p>
        </div>
        <div className="quality-strip" aria-label="Session recall breakdown">
          {Object.entries(qualityCounts).map(([quality, count]) => (
            <span key={quality}>
              {qualityLabels[quality as RecallQuality]} {count}
            </span>
          ))}
        </div>
        <button className="primary-action" type="button" onClick={onReset}>
          <RotateCcw size={20} />
          Plan another session
        </button>
      </section>
    );
  }

  if (mode === "practice" && session && currentItem) {
    const prompt = getPrompt(currentCard, currentItem.direction);
    const answer = getAnswer(currentCard, currentItem.direction);
    const visibleTerm = isRevealed ? answer : prompt;
    const directionLabel =
      currentItem.direction === "source_to_target" ? "English to Spanish" : "Spanish to English";

    return (
      <section className="practice-workspace" aria-label="Flipcard practice">
        <div className="session-line">
          <span>
            {session.currentIndex + 1} / {session.items.length}
          </span>
          <div className="progress-track" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>

        <button
          className={`flip-card ${isRevealed ? "revealed" : ""}`}
          type="button"
          onClick={onToggleCard}
          aria-label={isRevealed ? "Show prompt again" : "Reveal answer"}
        >
          <span className="direction-label">{directionLabel}</span>
          <span
            className="card-term"
            style={{ "--card-term-size": termFontSize(visibleTerm) } as CSSProperties}
          >
            {visibleTerm}
          </span>
          <span className="card-hint">
            {isRevealed
              ? `${currentCard.partOfSpeech} · ${currentItem.kind}`
              : `${currentCard.partOfSpeech} · tap to reveal`}
          </span>
        </button>

        <div className="practice-actions" aria-label="Recall quality">
          <button type="button" disabled={!isRevealed} onClick={() => onAnswer(false)}>
            {currentItem.kind === "new" ? "Didn't know" : "Forgot"}
          </button>
          <button type="button" disabled={!isRevealed} onClick={() => onAnswer(true)}>
            {currentItem.kind === "new" ? "Knew" : "Remembered"}
          </button>
        </div>

        <button className="detail-link" type="button" onClick={() => onOpenDetail(currentCard)}>
          <Info size={18} />
          Card details
        </button>
      </section>
    );
  }

  return (
    <section className="practice-stage" aria-label="Today practice">
      <div className="practice-copy">
        <p className="set-label">Daily practice</p>
        <h2>Today's practice is ready.</h2>
        <p>
          {previewSession.items.length} prompts are planned: {previewSession.introducedCount} new
          words and {previewSession.reviewCount} scheduled reviews. Progress saves on this device.
        </p>
      </div>
      <button className="primary-action" type="button" onClick={onStart}>
        <Sparkles size={20} />
        Start daily practice
      </button>
    </section>
  );
}

function StatsView({ summary }: { summary: SetSummary }) {
  return (
    <section className="plain-panel" aria-label="Stats">
      <p className="set-label">Learning state</p>
      <h2>Recall snapshot</h2>
      <div className="stat-list">
        <span>Total reviews</span>
        <strong>{summary.totalReviews}</strong>
        <span>Recall strength</span>
        <strong>{summary.recallStrength}%</strong>
        <span>Current streak</span>
        <strong>{summary.currentStreak}</strong>
        <span>Best streak</span>
        <strong>{summary.bestStreak}</strong>
        <span>Learning</span>
        <strong>{summary.learning}</strong>
        <span>Learned</span>
        <strong>{summary.learned}</strong>
        <span>Mastered</span>
        <strong>{summary.mastered}</strong>
        <span>Weak words</span>
        <strong>{summary.weakWords.join(", ") || "None"}</strong>
      </div>
    </section>
  );
}

function SetView({ activeSet }: { activeSet: ContentSet }) {
  const verbs = activeSet.cards.filter((card) => card.type === "verb").length;

  return (
    <section className="plain-panel" aria-label="Content set">
      <p className="set-label">
        {activeSet.sourceLanguage.toUpperCase()} to {activeSet.targetLanguage.toUpperCase()}
      </p>
      <h2>{activeSet.title}</h2>
      <div className="stat-list">
        <span>Cards loaded</span>
        <strong>{activeSet.cards.length}</strong>
        <span>Verb detail cards</span>
        <strong>{verbs}</strong>
        <span>First word</span>
        <strong>{activeSet.cards[0]?.target ?? "None"}</strong>
      </div>
      {activeSet.sourceNote ? <p className="source-note">{activeSet.sourceNote}</p> : null}
    </section>
  );
}

function CardDetail({ card, onClose }: { card: ContentCard; onClose: () => void }) {
  const conjugations = card.details?.conjugations;
  const conjugationEntries = conjugations
    ? Object.entries(conjugations).filter(([key, value]) => key !== "notes" && value)
    : [];

  return (
    <section className="detail-sheet" aria-label="Card details" role="dialog" aria-modal="true">
      <div className="sheet-header">
        <div>
          <p className="set-label">{card.partOfSpeech}</p>
          <h2>{card.target}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close details">
          <X size={20} />
        </button>
      </div>

      <div className="detail-pair">
        <span>English</span>
        <strong>{card.source}</strong>
      </div>

      {card.examples?.map((example) => (
        <div className="example-block" key={`${card.id}-${example.target}`}>
          <p>{example.target}</p>
          <span>{example.source}</span>
        </div>
      ))}

      {conjugationEntries.length ? (
        <div className="conjugations">
          {conjugationEntries.map(([tense, forms]) => (
            <div key={tense}>
              <h3>{tense}</h3>
              {Object.entries(forms as Record<string, string>).map(([person, form]) => (
                <p key={person}>
                  <span>{person.replace(/_/g, " ")}</span>
                  <strong>{form}</strong>
                </p>
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {card.details?.notes?.map((note) => (
        <p className="note-line" key={note}>
          <Check size={16} />
          {note}
        </p>
      ))}
    </section>
  );
}

function SettingsSheet({
  settings,
  onClose,
  onResetProgress,
  onUpdateSettings,
}: {
  settings: UserState["settings"];
  onClose: () => void;
  onResetProgress: () => void;
  onUpdateSettings: (settings: UserState["settings"]) => void;
}) {
  return (
    <section className="detail-sheet" aria-label="Settings" role="dialog" aria-modal="true">
      <div className="sheet-header">
        <div>
          <p className="set-label">Local settings</p>
          <h2>Practice setup</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close settings">
          <X size={20} />
        </button>
      </div>

      <div className="setting-row">
        <div>
          <strong>Daily new words</strong>
          <span>{settings.dailyNewTarget} per session</span>
        </div>
        <Stepper
          value={settings.dailyNewTarget}
          min={5}
          max={45}
          onChange={(dailyNewTarget) => onUpdateSettings({ ...settings, dailyNewTarget })}
        />
      </div>

      <div className="setting-row">
        <div>
          <strong>Daily reviews</strong>
          <span>{settings.dailyReviewTarget} per session</span>
        </div>
        <Stepper
          value={settings.dailyReviewTarget}
          min={5}
          max={45}
          onChange={(dailyReviewTarget) => onUpdateSettings({ ...settings, dailyReviewTarget })}
        />
      </div>

      <label className="setting-toggle">
        <span>
          <strong>Reduced motion</strong>
          <small>Use calmer transitions on this device.</small>
        </span>
        <input
          checked={settings.reduceMotion}
          type="checkbox"
          onChange={(event) => onUpdateSettings({ ...settings, reduceMotion: event.currentTarget.checked })}
        />
      </label>

      <button className="danger-action" type="button" onClick={onResetProgress}>
        Reset local progress
      </button>
    </section>
  );
}

function Stepper({
  value,
  min,
  max,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="stepper">
      <button type="button" aria-label="Decrease" disabled={value <= min} onClick={() => onChange(value - 1)}>
        -
      </button>
      <span>{value}</span>
      <button type="button" aria-label="Increase" disabled={value >= max} onClick={() => onChange(value + 1)}>
        +
      </button>
    </div>
  );
}

interface SetSummary {
  new: number;
  seen: number;
  learning: number;
  learned: number;
  mastered: number;
  dueToday: number;
  totalReviews: number;
  recallStrength: number;
  currentStreak: number;
  bestStreak: number;
  weakWords: string[];
}

function summarizeSet(set: ContentSet, state: UserState, dateKey: string): SetSummary {
  const summary: SetSummary = {
    new: 0,
    seen: 0,
    learning: 0,
    learned: 0,
    mastered: 0,
    dueToday: 0,
    totalReviews: 0,
    recallStrength: 0,
    currentStreak: state.streak.current,
    bestStreak: state.streak.best,
    weakWords: [],
  };
  const recallCounts = Object.values(state.activity).reduce<RecallCounts>(
    (counts, activity) => ({
      forgot: counts.forgot + activity.recallCounts.forgot,
      hard: counts.hard + activity.recallCounts.hard,
      good: counts.good + activity.recallCounts.good,
      easy: counts.easy + activity.recallCounts.easy,
    }),
    { forgot: 0, hard: 0, good: 0, easy: 0 },
  );
  const weakCards: Array<{ target: string; score: number }> = [];

  for (const card of set.cards) {
    const progress = getCardProgress(state, card.id);
    const bucket = getLearningBucket(progress);
    summary[bucket] += 1;

    if (!progress?.introducedAt) {
      continue;
    }

    if (Object.values(progress.directions).some((direction) => direction.dueDate <= dateKey)) {
      summary.dueToday += 1;
    }

    const score = Object.values(progress.directions).reduce(
      (total, direction) =>
        total +
        direction.lapseCount * 3 +
        (direction.lastQuality === "forgot" ? 2 : direction.lastQuality === "hard" ? 1 : 0),
      0,
    );

    if (score > 0) {
      weakCards.push({ target: card.target, score });
    }
  }

  summary.totalReviews = recallCounts.forgot + recallCounts.hard + recallCounts.good + recallCounts.easy;
  summary.recallStrength = summary.totalReviews
    ? Math.round(((recallCounts.good + recallCounts.easy) / summary.totalReviews) * 100)
    : 0;
  summary.weakWords = weakCards
    .sort((a, b) => b.score - a.score || a.target.localeCompare(b.target))
    .slice(0, 3)
    .map((card) => card.target);

  return summary;
}

function qualityFromAnswer(remembered: boolean, recallMs: number): RecallQuality {
  if (!remembered) {
    return "forgot";
  }

  if (recallMs <= 3500) {
    return "easy";
  }

  if (recallMs <= 9000) {
    return "good";
  }

  return "hard";
}

function termFontSize(term: string) {
  const segments = term
    .trim()
    .split(/[\s,;/()]+/)
    .filter(Boolean);
  const longestSegment = Math.max(term.length, ...segments.map((segment) => segment.length));
  const totalLength = term.trim().length;

  if (longestSegment >= 14 || totalLength >= 30) {
    return "2.5rem";
  }

  if (longestSegment >= 12 || totalLength >= 24) {
    return "3rem";
  }

  if (longestSegment >= 9 || totalLength >= 16) {
    return "3.65rem";
  }

  if (longestSegment >= 7 || totalLength >= 12) {
    return "4.25rem";
  }

  return "5rem";
}
