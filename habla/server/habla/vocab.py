"""Vocabulary tracking: lemmatize what was said, match to the frequency list, store events in SQLite.

Word states:
  new       never seen
  seen      the tutor used it (receptive exposure only)
  learning  taught to the learner, or they made a mistake with it
  known     the learner says it correctly. A learning word graduates after being used
            correctly twice, each time after its review interval has passed (so
            parroting right after being taught doesn't count).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass

import spacy

from . import config

DAY = 86400.0
# Review interval after the n-th spaced correct production (seconds).
INTERVALS = [120, 600, DAY, 3 * DAY, 7 * DAY, 21 * DAY, 60 * DAY]
GRADUATE_STREAK = 2
RELEARN_DELAY = 120  # a taught / mistaken word becomes due for review after this
SKIP_POS = {"DET", "PUNCT", "SPACE", "NUM", "SYM"}

SCHEMA = """
create table if not exists word_state (
  lemma text primary key,
  rank integer,
  translation text,
  status text not null default 'new',
  heard integer not null default 0,
  produced integer not null default 0,
  errors integer not null default 0,
  taught integer not null default 0,
  streak integer not null default 0,
  last_produced real,
  due_at real,
  updated_at real
);
create table if not exists events (
  id integer primary key autoincrement,
  ts real not null,
  session_id text,
  lemma text,
  kind text not null,
  detail text
);
create table if not exists turns (
  id integer primary key autoincrement,
  ts real not null,
  session_id text,
  lesson text,
  speaker text not null,
  text text not null,
  meta text
);
"""


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


ARTICLES = {"el", "la", "los", "las", "un", "una", "unos", "unas", "del", "al"}


def error_words(mistakes: list[dict]) -> set[str]:
    """Words the learner got wrong: in the wrong fragment but not in the fix.
    For gender errors (la perro -> el perro) the article differs, but the word to learn is the noun after it."""
    out: set[str] = set()
    for m in mistakes:
        wrong = re.findall(r"\w+", str(m.get("wrong", "")).lower())
        right = set(re.findall(r"\w+", str(m.get("right", "")).lower()))
        for i, w in enumerate(wrong):
            if w not in right:
                out.add(w)
                if w in ARTICLES and i + 1 < len(wrong):
                    out.add(wrong[i + 1])
    return out


@dataclass
class Token:
    text: str
    lemma: str  # matched vocab lemma, or spaCy lemma if not in list
    in_list: bool


class Vocab:
    def __init__(self) -> None:
        rows = json.loads(config.VOCAB_PATH.read_text())
        self.words = {r["word"]: r for r in rows}
        self.by_plain = {_strip_accents(w): w for w in self.words}
        self.nlp = spacy.load("es_core_news_sm", disable=["ner", "parser"])
        self.db = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- analysis ----------
    def _match(self, word: str, lemma: str, verb: bool) -> str | None:
        # Verbs match by lemma (va -> ir); other words by surface form first (muy, not mucho).
        cands = (lemma.lower(), word.lower()) if verb else (word.lower(), lemma.lower())
        for cand in cands:
            if cand in self.words:
                return cand
            if cand.endswith("se") and cand[:-2] in self.words:  # llamarse -> llamar
                return cand[:-2]
            plain = _strip_accents(cand)
            # Accent-insensitive only for longer words: el/él, tu/tú, si/sí, mi/mí are different words.
            if len(plain) >= 4 and plain in self.by_plain:
                return self.by_plain[plain]
        return None

    def tokens(self, text: str) -> list[Token]:
        out = []
        for t in self.nlp(text):
            if not t.is_alpha or t.pos_ in SKIP_POS:
                continue
            m = self._match(t.text, t.lemma_, t.pos_ in ("VERB", "AUX"))
            # Words outside the list keep their surface form: spaCy's lemmas for unknown words can be junk.
            out.append(Token(t.text, m or t.text.lower(), m is not None))
        return out

    def info(self, lemma: str) -> dict | None:
        return self.words.get(lemma)

    # ---------- state updates ----------
    def _row(self, lemma: str) -> sqlite3.Row:
        r = self.db.execute("select * from word_state where lemma=?", (lemma,)).fetchone()
        if r is None:
            w = self.words.get(lemma, {})
            self.db.execute(
                "insert into word_state(lemma, rank, translation, updated_at) values (?,?,?,?)",
                (lemma, w.get("rank"), w.get("translation"), time.time()),
            )
            r = self.db.execute("select * from word_state where lemma=?", (lemma,)).fetchone()
        return r

    def _event(self, session_id: str, lemma: str, kind: str, detail: str = "") -> None:
        self.db.execute(
            "insert into events(ts, session_id, lemma, kind, detail) values (?,?,?,?,?)",
            (time.time(), session_id, lemma, kind, detail),
        )

    def record_heard(self, session_id: str, text: str) -> None:
        now = time.time()
        for lemma in {t.lemma for t in self.tokens(text) if t.in_list}:
            r = self._row(lemma)
            status = "seen" if r["status"] == "new" else r["status"]
            self.db.execute(
                "update word_state set heard=heard+1, status=?, updated_at=? where lemma=?", (status, now, lemma)
            )
        self.db.commit()

    def record_learner(
        self, session_id: str, text: str, mistakes: list[dict], english: list[str]
    ) -> dict:
        """Update states from one learner utterance. Returns {lemma: outcome} for the UI."""
        now = time.time()
        english_words = {w.lower() for span in english for w in span.split()}
        wrong_words = error_words(mistakes)
        outcome: dict[str, str] = {}
        for tok in self.tokens(text):
            lw = tok.text.lower()
            if not tok.in_list or lw in english_words or tok.lemma in outcome:
                continue
            r = self._row(tok.lemma)
            if lw in wrong_words:
                self.db.execute(
                    "update word_state set errors=errors+1, streak=0, status='learning', due_at=?, updated_at=? where lemma=?",
                    (now + RELEARN_DELAY, now, tok.lemma),
                )
                self._event(session_id, tok.lemma, "produced_err", text)
                outcome[tok.lemma] = "error"
                continue
            streak, status, due = r["streak"], r["status"], r["due_at"]
            if status == "learning":
                if due is None or now >= due:  # spaced, not parroting
                    streak += 1
                    due = now + INTERVALS[min(streak, len(INTERVALS) - 1)]
                if streak >= GRADUATE_STREAK:
                    status = "known"
            elif status != "known":  # new/seen word produced spontaneously
                status, streak, due = "known", 1, now + INTERVALS[1]
            self.db.execute(
                "update word_state set produced=produced+1, streak=?, status=?, last_produced=?, due_at=?, updated_at=? where lemma=?",
                (streak, status, now, due, now, tok.lemma),
            )
            self._event(session_id, tok.lemma, "produced_ok", text)
            outcome[tok.lemma] = "ok"
        self.db.commit()
        return outcome

    def record_taught(self, session_id: str, items: list[dict]) -> list[dict]:
        """Words the tutor taught (from English fallbacks or questions). Adds them to the learning queue."""
        now = time.time()
        out = []
        for it in items:
            spanish = re.sub(r"\(.*?\)", "", str(it.get("spanish", ""))).strip(" .,;«»\"'")
            if not spanish:
                continue
            it = {**it, "spanish": spanish}
            toks = self.tokens(spanish)
            lemma = toks[0].lemma if len(toks) == 1 else re.sub(r"^(el|la|los|las|un|una)\s+", "", spanish.lower())
            r = self._row(lemma)
            if r["translation"] is None:
                self.db.execute("update word_state set translation=? where lemma=?", (it.get("english"), lemma))
            status = "learning"  # needing to be taught means it is not active vocabulary (yet)
            self.db.execute(
                "update word_state set taught=taught+1, streak=0, status=?, due_at=?, updated_at=? where lemma=?",
                (status, now + RELEARN_DELAY, now, lemma),
            )
            self._event(session_id, lemma, "taught", json.dumps(it, ensure_ascii=False))
            info = self.words.get(lemma)
            out.append({**it, "lemma": lemma, "rank": info["rank"] if info else None})
        self.db.commit()
        return out

    def record_mistakes(self, session_id: str, mistakes: list[dict]) -> None:
        for m in mistakes:
            self._event(session_id, None, "mistake", json.dumps(m, ensure_ascii=False))
        self.db.commit()

    def save_turn(self, session_id: str, lesson: str, speaker: str, text: str, meta: dict | None = None) -> None:
        self.db.execute(
            "insert into turns(ts, session_id, lesson, speaker, text, meta) values (?,?,?,?,?,?)",
            (time.time(), session_id, lesson, speaker, text, json.dumps(meta or {}, ensure_ascii=False)),
        )
        self.db.commit()

    # ---------- queries ----------
    def review_words(self, limit: int = 4) -> list[dict]:
        """Learning words that are due: the tutor should try to get the learner to use them."""
        rows = self.db.execute(
            "select lemma, translation from word_state where status='learning' and (due_at is null or due_at<=?) "
            "order by due_at limit ?",
            (time.time(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def progress(self, session_id: str | None = None) -> dict:
        counts = {r["status"]: r["n"] for r in self.db.execute(
            "select status, count(*) n from word_state where rank is not null group by status")}
        recent = [dict(r) for r in self.db.execute(
            "select lemma, translation, status, produced, errors, taught from word_state "
            "where status in ('learning','known') order by updated_at desc limit 40")]
        session = {}
        if session_id:
            session = {r["kind"]: r["n"] for r in self.db.execute(
                "select kind, count(*) n from events where session_id=? group by kind", (session_id,))}
        return {
            "total": len(self.words),
            "known": counts.get("known", 0),
            "learning": counts.get("learning", 0),
            "seen": counts.get("seen", 0),
            "recent": recent,
            "session": session,
        }
