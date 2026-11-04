export type Lesson = { id: string; title: string; title_es: string; goal: string };
export type Mistake = { wrong: string; right: string; why: string };
export type Teach = { english: string; spanish: string; lemma?: string; rank?: number | null };

export type TutorMsg = {
  kind: "tutor";
  id: string;
  text: string;
  sentences: { key: string; text: string; url: string }[];
  en?: string;
  done: boolean;
  firstAudioMs?: number;
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
};

export type NoteMsg = { kind: "note"; id: string; text: string };
export type Msg = TutorMsg | UserMsg | NoteMsg;

export type Progress = {
  total: number;
  known: number;
  learning: number;
  seen: number;
  recent: { lemma: string; translation: string | null; status: string; produced: number; errors: number; taught: number }[];
  session: Record<string, number>;
};

export type ServerState = "idle" | "transcribing" | "thinking";
