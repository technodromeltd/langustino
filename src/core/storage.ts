import type { PracticeDirection } from "./content";

const STORAGE_KEY = "langustino:user-state:v1";

export type RecallQuality = "forgot" | "hard" | "good" | "easy";

export interface DirectionProgress {
  intervalDays: number;
  ease: number;
  dueDate: string;
  reviewCount: number;
  lapseCount: number;
  lastReviewedDate: string | null;
  lastQuality: RecallQuality | null;
}

export type CardProgress = {
  introducedAt: string | null;
  directions: Record<PracticeDirection, DirectionProgress>;
};

export interface UserState {
  schemaVersion: 1;
  activeSetId: string;
  cards: Record<string, CardProgress>;
  streak: {
    current: number;
    best: number;
    lastCompletedDate: string | null;
  };
  settings: {
    dailyNewTarget: number;
    dailyReviewTarget: number;
    reduceMotion: boolean;
  };
}

export function createDirectionProgress(): DirectionProgress {
  return {
    intervalDays: 0,
    ease: 2.5,
    dueDate: todayKey(),
    reviewCount: 0,
    lapseCount: 0,
    lastReviewedDate: null,
    lastQuality: null,
  };
}

export function createInitialUserState(): UserState {
  return {
    schemaVersion: 1,
    activeSetId: "spanish-1000-common-en",
    cards: {},
    streak: {
      current: 0,
      best: 0,
      lastCompletedDate: null,
    },
    settings: {
      dailyNewTarget: 35,
      dailyReviewTarget: 15,
      reduceMotion: false,
    },
  };
}

export function loadUserState(): UserState {
  if (typeof window === "undefined") {
    return createInitialUserState();
  }

  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (!stored) {
    return createInitialUserState();
  }

  try {
    const parsed = JSON.parse(stored) as UserState;
    return migrateUserState(parsed);
  } catch {
    return createInitialUserState();
  }
}

export function saveUserState(state: UserState) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function migrateUserState(state: UserState): UserState {
  if (state.schemaVersion !== 1) {
    return createInitialUserState();
  }

  return {
    ...createInitialUserState(),
    ...state,
    settings: {
      ...createInitialUserState().settings,
      ...state.settings,
    },
  };
}

export function todayKey(date = new Date()) {
  return date.toISOString().slice(0, 10);
}
