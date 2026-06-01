import type { ContentCard, ContentSet, PracticeDirection } from "./content";
import {
  addDays,
  createDirectionProgress,
  createRecallCounts,
  type CardProgress,
  type DailySessionState,
  type DailyActivity,
  type RecallQuality,
  type StoredPracticeItem,
  todayKey,
  type UserState,
} from "./storage";

const directions: PracticeDirection[] = ["source_to_target", "target_to_source"];

export function createCardProgress(introducedAt: string | null = null): CardProgress {
  return {
    introducedAt,
    directions: {
      source_to_target: createDirectionProgress(),
      target_to_source: createDirectionProgress(),
    },
  };
}

export function getCardProgress(state: UserState, cardId: string) {
  return state.cards[cardId] ?? null;
}

export function planDailySession(set: ContentSet, state: UserState, dateKey = todayKey()): DailySessionState {
  const targetTotal = state.settings.dailyNewTarget + state.settings.dailyReviewTarget;
  const dueReviews = collectDueReviews(set.cards, state, dateKey);
  const reviewLimit = dueReviews.length > state.settings.dailyReviewTarget ? targetTotal : state.settings.dailyReviewTarget;
  const selectedReviews = dueReviews.slice(0, reviewLimit);
  const newSlots = Math.max(0, targetTotal - selectedReviews.length);
  const selectedNew = collectNewCards(set.cards, state).slice(0, newSlots);
  const items = [...selectedReviews, ...selectedNew].map((item, index) => ({
    ...item,
    id: `${dateKey}-${index}-${item.cardId}-${item.direction}`,
  }));

  return {
    id: `${set.id}-${dateKey}`,
    date: dateKey,
    setId: set.id,
    items,
    currentIndex: 0,
    completed: false,
    completedAt: null,
    introducedCount: selectedNew.length,
    reviewCount: selectedReviews.length,
    repeatCount: 0,
    answeredCount: 0,
    recallCounts: createRecallCounts(),
  };
}

export function getTodaySession(set: ContentSet, state: UserState, dateKey = todayKey()) {
  if (state.session?.setId === set.id && state.session.date === dateKey) {
    return state.session;
  }

  return null;
}

export function startDailySession(set: ContentSet, state: UserState, dateKey = todayKey()): UserState {
  const existing = getTodaySession(set, state, dateKey);

  if (existing && !existing.completed) {
    return state;
  }

  return {
    ...state,
    session: planDailySession(set, state, dateKey),
  };
}

export function applyRecall(
  state: UserState,
  item: StoredPracticeItem,
  quality: RecallQuality,
  dateKey = todayKey(),
): UserState {
  const cardProgress = state.cards[item.cardId] ?? createCardProgress(dateKey);
  const introducedAt = cardProgress.introducedAt ?? dateKey;
  const previous = cardProgress.directions[item.direction] ?? createDirectionProgress();
  const nextDirection = scheduleDirection(previous, quality, dateKey);

  return {
    ...state,
    cards: {
      ...state.cards,
      [item.cardId]: {
        introducedAt,
        directions: {
          ...cardProgress.directions,
          [item.direction]: nextDirection,
        },
      },
    },
  };
}

export function advanceSession(
  state: UserState,
  item: StoredPracticeItem,
  quality: RecallQuality,
  dateKey = todayKey(),
): UserState {
  if (!state.session) {
    return state;
  }

  const reviewedState = applyRecall(state, item, quality, dateKey);
  const session = reviewedState.session;

  if (!session) {
    return reviewedState;
  }

  const items = [...session.items];
  let repeatCount = session.repeatCount;
  const recallCounts = {
    ...session.recallCounts,
    [quality]: session.recallCounts[quality] + 1,
  };

  if (quality === "forgot") {
    const repeatAt = Math.min(session.currentIndex + 4, items.length);
    const repeat: StoredPracticeItem = {
      ...item,
      id: `${item.cardId}-repeat-${Date.now()}`,
      kind: "repeat",
    };
    items.splice(repeatAt, 0, repeat);
    repeatCount += 1;
  }

  const nextIndex = session.currentIndex + 1;
  const completed = nextIndex >= items.length;
  const updatedSession: DailySessionState = {
    ...session,
    items,
    currentIndex: completed ? session.currentIndex : nextIndex,
    completed,
    completedAt: completed ? new Date().toISOString() : null,
    repeatCount,
    answeredCount: session.answeredCount + 1,
    recallCounts,
  };
  const activity = updateDailyActivity(reviewedState, updatedSession, quality, dateKey);
  const streak = completed ? updateStreak(reviewedState.streak, dateKey) : reviewedState.streak;

  return {
    ...reviewedState,
    session: updatedSession,
    activity,
    streak,
  };
}

export function getLearningBucket(cardProgress: CardProgress | null) {
  if (!cardProgress?.introducedAt) {
    return "new";
  }

  const intervals = directions.map((direction) => cardProgress.directions[direction]?.intervalDays ?? 0);
  const reviews = directions.map((direction) => cardProgress.directions[direction]?.reviewCount ?? 0);

  if (reviews.every((count) => count > 0) && intervals.every((interval) => interval >= 21)) {
    return "mastered";
  }

  if (reviews.every((count) => count > 0) && intervals.every((interval) => interval >= 7)) {
    return "learned";
  }

  if (reviews.some((count) => count > 0)) {
    return "learning";
  }

  return "seen";
}

function collectDueReviews(cards: ContentCard[], state: UserState, dateKey: string): StoredPracticeItem[] {
  return cards
    .flatMap((card) => {
      const progress = state.cards[card.id];
      if (!progress?.introducedAt) {
        return [];
      }

      return directions
        .filter((direction) => {
          const directionProgress = progress.directions[direction];
          return directionProgress && directionProgress.dueDate <= dateKey;
        })
        .map((direction) => ({
          id: "",
          cardId: card.id,
          direction,
          kind: "review" as const,
          dueDate: progress.directions[direction].dueDate,
          rank: card.rank,
        }));
    })
    .sort((a, b) => a.dueDate.localeCompare(b.dueDate) || a.rank - b.rank)
    .map(({ dueDate: _dueDate, rank: _rank, ...item }) => item);
}

function collectNewCards(cards: ContentCard[], state: UserState): StoredPracticeItem[] {
  return cards
    .filter((card) => !state.cards[card.id]?.introducedAt)
    .sort((a, b) => a.rank - b.rank)
    .map((card) => ({
      id: "",
      cardId: card.id,
      direction: "source_to_target" as const,
      kind: "new" as const,
    }));
}

function scheduleDirection(
  previous: ReturnType<typeof createDirectionProgress>,
  quality: RecallQuality,
  dateKey: string,
) {
  const easeChange: Record<RecallQuality, number> = {
    forgot: -0.28,
    hard: -0.12,
    good: 0,
    easy: 0.15,
  };

  const ease = Math.min(3.2, Math.max(1.3, previous.ease + easeChange[quality]));
  const intervalDays = nextInterval(previous.intervalDays, ease, quality);

  return {
    ...previous,
    intervalDays,
    ease,
    dueDate: addDays(dateKey, intervalDays),
    reviewCount: previous.reviewCount + 1,
    lapseCount: quality === "forgot" ? previous.lapseCount + 1 : previous.lapseCount,
    lastReviewedDate: dateKey,
    lastQuality: quality,
  };
}

function nextInterval(previousInterval: number, ease: number, quality: RecallQuality) {
  if (quality === "forgot") {
    return 1;
  }

  if (quality === "hard") {
    return Math.max(1, Math.round(previousInterval ? previousInterval * 1.2 : 1));
  }

  if (quality === "good") {
    return Math.max(3, Math.round(previousInterval ? previousInterval * ease : 3));
  }

  return Math.max(5, Math.round(previousInterval ? previousInterval * ease * 1.45 : 5));
}

function updateDailyActivity(
  state: UserState,
  session: DailySessionState,
  quality: RecallQuality,
  dateKey: string,
) {
  const previous = state.activity[dateKey] ?? createDailyActivity(dateKey);
  const recallCounts = {
    ...previous.recallCounts,
    [quality]: previous.recallCounts[quality] + 1,
  };
  const next: DailyActivity = {
    ...previous,
    completed: previous.completed || session.completed,
    reviews: previous.reviews + 1,
    newIntroduced: session.introducedCount,
    repeats: session.repeatCount,
    recallCounts,
  };

  return {
    ...state.activity,
    [dateKey]: next,
  };
}

function createDailyActivity(dateKey: string): DailyActivity {
  return {
    date: dateKey,
    completed: false,
    reviews: 0,
    newIntroduced: 0,
    repeats: 0,
    recallCounts: createRecallCounts(),
  };
}

function updateStreak(streak: UserState["streak"], dateKey: string) {
  if (streak.lastCompletedDate === dateKey) {
    return streak;
  }

  const yesterday = addDays(dateKey, -1);
  const current = streak.lastCompletedDate === yesterday ? streak.current + 1 : 1;

  return {
    current,
    best: Math.max(streak.best, current),
    lastCompletedDate: dateKey,
  };
}
