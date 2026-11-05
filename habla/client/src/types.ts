export type Lesson = { id: string; title: string; title_es: string; goal: string };
export type Mistake = { wrong: string; right: string; why: string; right_en?: string; type?: string };
export type Teach = { english: string; spanish: string; lemma?: string; rank?: number | null };

export type TargetState = "pending" | "introduced" | "practiced" | "used" | "known";
export type Target = {
  lemma: string;
  display: string;
  english: string;
  pos: string;
  kind: "new" | "review";
  state: TargetState;
  intro_turn?: number | null; // learner turn it was introduced in
  elicited?: number; // times Sofía was told to ask for it
};
export type Phase = "warmup" | "introduce" | "practice" | "recap" | "free";
export type LessonState = { phase: Phase; targets: Target[]; goal: number; used: number };
export type Recap = {
  lesson: string;
  targets: Target[];
  goal: number;
  used: number;
  said_ok: number;
  turns: number;
  minutes: number;
  mistakes: Mistake[];
  taught: Teach[];
  review_next: string[];
};

export type TutorMsg = {
  kind: "tutor";
  id: string;
  text: string;
  words?: Target[]; // new words introduced in this reply
  sentences: { key: string; text: string; url: string }[];
  en?: string;
  done: boolean;
  firstAudioMs?: number;
  glosses?: Record<string, string>; // word as written (lowercase) -> "lemma — translation"
  coach?: string; // teacher guide (English), shown as a separate card, not spoken
};

export type UserMsg = {
  kind: "user";
  id: string;
  text: string;
  english: string[];
  mistakes: Mistake[];
  teach: Teach[];
  words: Record<string, "ok" | "error">;
  analyzed: boolean;
  en?: string; // English translation of what the learner said
  command?: string; // "repeat" | "slower" | "meaning": answered instantly, not a conversation turn
  merged?: boolean; // cut off before Sofía answered: checked together with the next message
  unchecked?: boolean; // the grammar check failed: no cards and no "Bien dicho"
  better?: string; // the message as a native speaker would say it ("" if it was right); fix cards are diffs of it
  unclear?: string[]; // words the speech recognizer wasn't sure of (maybe misheard): never fixes
  clarify?: boolean; // too unclear to grade: Sofía asked them to repeat, no cards and no "Bien dicho"
};

export type NoteMsg = { kind: "note"; id: string; text: string };
export type RecapMsg = { kind: "recap"; id: string; recap: Recap };
export type HintMsg = { kind: "hint"; id: string; text: string; spanish?: string; level: number };
export type Msg = TutorMsg | UserMsg | NoteMsg | RecapMsg | HintMsg;

export type Attempt = {
  kind: "review" | "fix" | "repair" | "shadow" | "ask";
  target: string;
  key: string;
  card?: string; // ask: the card the question is about
  said?: string; // ask: what the learner said in the conversation
  thread?: QA[]; // ask: the earlier questions and answers about this card
  lemma?: string; // review: the word
  fix_id?: number; // fix: the fix to remember
  n?: number; // set by the app: which attempt this is, so a result for one that was left is ignored
};
export type AttemptPhase = "listening" | "checking";
export type AttemptResult = {
  key: string;
  kind: string;
  target: string;
  heard: string;
  ok: boolean;
  score: number;
  words: { w: string; ok: boolean }[];
  note?: string | null; // "«el perro», not «la perro»", "also right — …", or why a fix was missed
};
/** Lets any component record a graded spoken attempt. */
export type Attempts = {
  start: (a: Attempt) => void;
  cancel: () => void; // leave the attempt being recorded / graded
  active: string | null; // key being recorded / graded
  phase: AttemptPhase | null; // of the active attempt: recording it, or waiting for the grade
  results: Record<string, AttemptResult>;
  threads: Record<string, QA[]>; // questions to the teacher, per card key
  askText: (a: { key: string; card: string; said?: string }, text: string) => void;
};
/** A question to the teacher about a card, and its answer. */
export type QA = { q: string; a: string; pending?: boolean; error?: string };

export type Settings = {
  level: string;
  suggested_level: string;
  goal_minutes?: number; // the old daily goal (migrated to goal_sentences on the server)
  goal_sentences: number; // daily goal: sentences of practice (turns, attempts, Repaso drills)
  goal_choices?: number[];
  subtitles: "always" | "after" | "tap";
  levels: { id: string; label: string }[];
};
export type Stats = {
  today_seconds: number;
  goal_minutes: number;
  today_sentences: number;
  goal_sentences: number;
  streak: number;
  due_reviews: number;
};
export type Pattern = { type: string; label: string; tip: string; count: number; example: Mistake };
export type ReviewWord = {
  kind: "word";
  lemma: string;
  display: string;
  english: string;
  due: boolean;
  status: string;
  alts?: string[]; // same-meaning words that also count
};
/** One of the learner's own corrected mistakes: "Say it right". */
export type ReviewFix = { kind: "fix"; id: number; right: string; right_en: string; wrong: string; why: string; type: string };
export type ReviewItem = ReviewWord | ReviewFix;

/** Client -> server messages of the Repaso drill. */
export type ReviewSkipMsg = { type: "review_skip"; lemma: string };
export type FixSkipMsg = { type: "fix_skip"; id: number };
export type FixDropMsg = { type: "fix_drop"; id: number };
export type WordRow = {
  lemma: string;
  display: string;
  translation: string | null;
  status: string;
  produced: number;
  errors: number;
  taught: number;
  example: string | null;
};

export type Progress = {
  total: number;
  total_content?: number; // list words that count toward "words you can say" (no de, no, a…)
  total_ranked?: number; // the frequency list (2000)
  total_extra?: number; // everyday supplement words (manzana, maleta…)
  known: number; // known content words
  known_all?: number; // every known list word

  learning: number;
  seen: number;
  recent: { lemma: string; translation: string | null; status: string; produced: number; errors: number; taught: number }[];
  session: Record<string, number>;
  lessons: Record<string, { times: number; best_used: number; goal: number }>;
  stats?: Stats;
  patterns?: Pattern[];
};

/** Where the words come from: "the 2,000 most common Spanish words + 126 everyday ones". */
export function listLabel(p: Progress): string {
  const ranked = (p.total_ranked ?? p.total).toLocaleString("en-US");
  return `the ${ranked} most common Spanish words` + (p.total_extra ? ` + ${p.total_extra} everyday ones` : "");
}

export const MISTAKE_LABEL: Record<string, string> = {
  gender: "Gender",
  verb_form: "Verb ending",
  tense: "Tense",
  ser_estar: "Ser / estar",
  por_para: "Por / para",
  preposition: "Preposition",
  word_choice: "Word choice",
  word_order: "Word order",
  missing_word: "Missing word",
};

export type ServerState = "idle" | "transcribing" | "thinking";
