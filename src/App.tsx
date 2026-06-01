import { BookOpen, Check, Flame, Info, LineChart, RotateCcw, Settings, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
import { availableSets } from "./content/registry";
import { getAnswer, getPrompt, type ContentCard, type PracticeDirection } from "./core/content";
import { loadUserState, type RecallQuality } from "./core/storage";

const userState = loadUserState();
const activeSet = availableSets.find((set) => set.id === userState.activeSetId) ?? availableSets[0];

type AppView = "today" | "stats" | "set";
type PracticeMode = "home" | "practice" | "complete";

interface PracticeItem {
  id: string;
  cardId: string;
  direction: PracticeDirection;
}

const qualityLabels: Record<RecallQuality, string> = {
  forgot: "Forgot",
  hard: "Hard",
  good: "Good",
  easy: "Easy",
};

function createStarterDeck(cards: ContentCard[]): PracticeItem[] {
  return cards.slice(0, Math.min(50, cards.length)).map((card, index) => ({
    id: `${card.id}-${index}`,
    cardId: card.id,
    direction: index % 3 === 2 ? "target_to_source" : "source_to_target",
  }));
}

export function App() {
  const starterDeck = useMemo(() => createStarterDeck(activeSet.cards), []);
  const [view, setView] = useState<AppView>("today");
  const [mode, setMode] = useState<PracticeMode>("home");
  const [deck, setDeck] = useState<PracticeItem[]>(starterDeck);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isRevealed, setIsRevealed] = useState(false);
  const [detailCard, setDetailCard] = useState<ContentCard | null>(null);
  const [qualityCounts, setQualityCounts] = useState<Record<RecallQuality, number>>({
    forgot: 0,
    hard: 0,
    good: 0,
    easy: 0,
  });

  const currentItem = deck[currentIndex];
  const currentCard = activeSet.cards.find((card) => card.id === currentItem?.cardId) ?? activeSet.cards[0];
  const progress = deck.length ? Math.min(100, Math.round((currentIndex / deck.length) * 100)) : 0;
  const answeredCount = Object.values(qualityCounts).reduce((sum, count) => sum + count, 0);

  function startPractice() {
    setDeck(createStarterDeck(activeSet.cards));
    setCurrentIndex(0);
    setIsRevealed(false);
    setQualityCounts({ forgot: 0, hard: 0, good: 0, easy: 0 });
    setMode("practice");
    setView("today");
  }

  function answerCard(quality: RecallQuality) {
    const repeatForgotten = quality === "forgot" && currentItem;
    const nextDeckLength = deck.length + (repeatForgotten ? 1 : 0);

    setQualityCounts((counts) => ({
      ...counts,
      [quality]: counts[quality] + 1,
    }));

    setDeck((items) => {
      if (!repeatForgotten) {
        return items;
      }

      const repeatAt = Math.min(currentIndex + 4, items.length);
      const repeat: PracticeItem = {
        ...currentItem,
        id: `${currentItem.cardId}-repeat-${Date.now()}`,
      };
      const next = [...items];
      next.splice(repeatAt, 0, repeat);
      return next;
    });

    setIsRevealed(false);
    setCurrentIndex((index) => {
      const nextIndex = index + 1;
      if (nextIndex >= nextDeckLength) {
        setMode("complete");
        return index;
      }

      return nextIndex;
    });
  }

  function resetPractice() {
    setDeck(createStarterDeck(activeSet.cards));
    setCurrentIndex(0);
    setIsRevealed(false);
    setMode("home");
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
          currentIndex={currentIndex}
          currentItem={currentItem}
          deckLength={deck.length}
          isRevealed={isRevealed}
          mode={mode}
          progress={progress}
          qualityCounts={qualityCounts}
          onAnswer={answerCard}
          onOpenDetail={setDetailCard}
          onReset={resetPractice}
          onReveal={() => setIsRevealed(true)}
          onStart={startPractice}
        />
      ) : view === "stats" ? (
        <StatsView answeredCount={answeredCount} qualityCounts={qualityCounts} />
      ) : (
        <SetView />
      )}

      <section className="quick-stats" aria-label="Progress summary">
        <div>
          <BookOpen size={19} />
          <span>{activeSet.cards.length} words</span>
        </div>
        <div>
          <LineChart size={19} />
          <span>{answeredCount} reviewed</span>
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
  currentIndex: number;
  currentItem: PracticeItem | undefined;
  deckLength: number;
  isRevealed: boolean;
  mode: PracticeMode;
  progress: number;
  qualityCounts: Record<RecallQuality, number>;
  onAnswer: (quality: RecallQuality) => void;
  onOpenDetail: (card: ContentCard) => void;
  onReset: () => void;
  onReveal: () => void;
  onStart: () => void;
}

function TodayView({
  answeredCount,
  currentCard,
  currentIndex,
  currentItem,
  deckLength,
  isRevealed,
  mode,
  progress,
  qualityCounts,
  onAnswer,
  onOpenDetail,
  onReset,
  onReveal,
  onStart,
}: TodayViewProps) {
  if (mode === "complete") {
    return (
      <section className="practice-stage complete-stage" aria-label="Session complete">
        <div className="practice-copy">
          <p className="set-label">Daily session complete</p>
          <h2>Nice work.</h2>
          <p>
            You reviewed {answeredCount} cards. Forgotten words were folded back into the session
            so the weak spots got one more pass.
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
          Back to today
        </button>
      </section>
    );
  }

  if (mode === "practice" && currentItem) {
    const prompt = getPrompt(currentCard, currentItem.direction);
    const answer = getAnswer(currentCard, currentItem.direction);
    const directionLabel =
      currentItem.direction === "source_to_target" ? "English to Spanish" : "Spanish to English";

    return (
      <section className="practice-workspace" aria-label="Flipcard practice">
        <div className="session-line">
          <span>
            {currentIndex + 1} / {deckLength}
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
          <span className="card-hint">{isRevealed ? currentCard.partOfSpeech : "Tap to reveal"}</span>
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
        <p className="set-label">{activeSet.title}</p>
        <h2>Today's practice is ready.</h2>
        <p>
          Start with {Math.min(50, activeSet.cards.length)} fast flipcards. You will see prompts in
          both directions and repeat missed words inside the same session.
        </p>
      </div>
      <button className="primary-action" type="button" onClick={onStart}>
        <Sparkles size={20} />
        Start daily practice
      </button>
    </section>
  );
}

function StatsView({
  answeredCount,
  qualityCounts,
}: {
  answeredCount: number;
  qualityCounts: Record<RecallQuality, number>;
}) {
  return (
    <section className="plain-panel" aria-label="Stats">
      <p className="set-label">This session</p>
      <h2>Recall snapshot</h2>
      <div className="stat-list">
        <span>Reviewed</span>
        <strong>{answeredCount}</strong>
        <span>Forgot</span>
        <strong>{qualityCounts.forgot}</strong>
        <span>Strong answers</span>
        <strong>{qualityCounts.good + qualityCounts.easy}</strong>
      </div>
    </section>
  );
}

function SetView() {
  const verbs = activeSet.cards.filter((card) => card.type === "verb").length;

  return (
    <section className="plain-panel" aria-label="Content set">
      <p className="set-label">{activeSet.sourceLanguage.toUpperCase()} to {activeSet.targetLanguage.toUpperCase()}</p>
      <h2>{activeSet.title}</h2>
      <div className="stat-list">
        <span>Cards loaded</span>
        <strong>{activeSet.cards.length}</strong>
        <span>Verb detail cards</span>
        <strong>{verbs}</strong>
        <span>First word</span>
        <strong>{activeSet.cards[0]?.target ?? "None"}</strong>
      </div>
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
