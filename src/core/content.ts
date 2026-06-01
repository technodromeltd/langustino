export type LanguageCode = "en" | "es" | string;

export type CardType = "word" | "phrase" | "verb";

export interface ExampleSentence {
  source: string;
  target: string;
}

export interface VerbConjugations {
  present?: Record<string, string>;
  preterite?: Record<string, string>;
  imperfect?: Record<string, string>;
  future?: Record<string, string>;
  notes?: string[];
}

export interface CardDetails {
  pronunciation?: string;
  notes?: string[];
  conjugations?: VerbConjugations;
}

export interface ContentCard {
  id: string;
  rank: number;
  type: CardType;
  source: string;
  target: string;
  partOfSpeech: string;
  examples?: ExampleSentence[];
  details?: CardDetails;
  tags?: string[];
}

export interface ContentSet {
  id: string;
  title: string;
  sourceLanguage: LanguageCode;
  targetLanguage: LanguageCode;
  version: number;
  cards: ContentCard[];
}

export type PracticeDirection = "source_to_target" | "target_to_source";

export function getPrompt(card: ContentCard, direction: PracticeDirection) {
  return direction === "source_to_target" ? card.source : card.target;
}

export function getAnswer(card: ContentCard, direction: PracticeDirection) {
  return direction === "source_to_target" ? card.target : card.source;
}
