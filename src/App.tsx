import { BookOpen, Check, Flame, Info, LineChart, RotateCcw, Settings, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
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
  const [detailCard, setDetailCard] = useState<ContentCard | null>(null);

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

  function answerCard(quality: RecallQuality) {
    if (!currentItem) {
      return;
    }

    updateAndSave(advanceSession(userState, currentItem, quality, today));
    setIsRevealed(false);
  }

  function resetPractice() {
    const nextState: UserState = {
      ...userState,
      session: null,
    };
    updateAndSave(nextState);
    setIsRevealed(false);
  }

  return (
    <main className="app-shell">
      <section className="topbar" aria-label="Langustino status">
        <div>
          <p className="eyebrow">Langustino</p>
          <h1>{view === "today" ? "Spanish practice, ready now." : "Your Spanish map."}</h1>
        </div>
        <button className="icon-button" type="button" aria-label="Settings">
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
          onReveal={() => setIsRevealed(true)}
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
  onAnswer: (quality: RecallQuality) => void;
  onOpenDetail: (card: ContentCard) => void;
  onReset: () => void;
  onReveal: () => void;
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
  onReveal,
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
          onClick={isRevealed ? undefined : onReveal}
          aria-label={isRevealed ? "Card answer revealed" : "Reveal answer"}
        >
          <span className="direction-label">{directionLabel}</span>
          <span className="card-term">{isRevealed ? answer : prompt}</span>
          <span className="card-hint">
            {isRevealed ? `${currentCard.partOfSpeech} · ${currentItem.kind}` : "Tap to reveal"}
          </span>
        </button>

        <div className="practice-actions" aria-label="Recall quality">
          {(["forgot", "hard", "good", "easy"] as RecallQuality[]).map((quality) => (
            <button key={quality} type="button" disabled={!isRevealed} onClick={() => onAnswer(quality)}>
              {qualityLabels[quality]}
            </button>
          ))}
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
