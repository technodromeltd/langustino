"""Vocabulary tracking: lemmatize what was said, match to the frequency list, store events in SQLite.

Word states:
  new       never seen
  seen      the tutor used it (receptive exposure only)
  learning  taught to the learner, or they made a mistake with it
  known     the learner has said it correctly on their own, spaced out over time.

Spaced ladder: a correct use raises the streak only once the word is due (due_at has passed), and each rise
sets due_at = now + INTERVALS[streak]. A word is known at streak GRADUATE_STREAK (3), so a taught word needs
correct uses ~2 min, ~10 min and at least a day apart. A word the learner says spontaneously (not echoing
what Sofía just said or showed) starts at streak 2, due in 20 h: it becomes known on the next correct use on
another day. Echoes (words from Sofía's last reply, a hint, a coach note or a word card) never raise a streak.
Using a due known word in conversation counts as a successful review (its due_at moves forward), and so does a
Repaso drill, but a retry before the word is due again only counts as practice.
A mistake sends the word back to learning, streak 0.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import spacy

from . import config
from .practice import words as plain_words

log = logging.getLogger("habla")

DAY = 86400.0
# Review interval after the n-th spaced correct production (seconds).
INTERVALS = [120, 600, DAY, 3 * DAY, 7 * DAY, 21 * DAY, 60 * DAY]
GRADUATE_STREAK = 3
SPONTANEOUS_STREAK = 2  # a new word said on its own: one more spaced use (another day) makes it known
SPONTANEOUS_DUE = 20 * 3600
RELEARN_DELAY = 120  # a taught / mistaken word becomes due for review after this
SKIP_POS = {"DET", "PUNCT", "SPACE", "NUM", "SYM"}
# Frequent words that make poor standalone "new word" lessons (grammar words, fragments of set phrases).
NOT_TEACHABLE = {"no", "sí", "mi", "su", "tu", "más", "muy", "ya", "así", "tan", "todo", "haber", "gracia", "favor",
                 "dios", "solo", "mucho", "cómo", "dónde", "qué", "quién", "cuál", "cuánto", "por", "para", "de", "a",
                 "en", "con", "y", "o", "que", "el", "la", "yo", "tú", "él", "ser", "estar"}
# Word classes that count toward "words you can say" (small words like de, no, a don't).
CONTENT_POS = {"noun", "verb", "adjective", "adverb", "number"}
TEACH_POS = {"NOUN", "VERB", "ADJ"}  # spaCy POS of the words a taught phrase is split into
# New lesson words (free conversation, theme top-ups): these word classes, not vulgar or written-only words,
# and only words rated above 2 (of 5) for spoken usefulness.
LESSON_POS = {"noun", "verb", "adjective", "adverb"}
SKIP_REGISTERS = {"vulgar", "formal_written"}
MIN_USEFULNESS = 2

# Verb forms spaCy's small model gets wrong (sentence-initial "Salgo" -> PROPN, "salgo" -> PRON, "duermo" unknown).
# Hand-written for the irregular list verbs a learner says most; other list verbs get regular forms generated.
IRREGULAR = {
    "ser": ["soy", "eres", "es", "somos", "son", "era"],
    "estar": ["estoy", "estás", "está", "estamos", "están", "estuve"],
    "ir": ["voy", "vas", "va", "vamos", "van", "fui", "fue"],
    "tener": ["tengo", "tienes", "tiene", "tenemos", "tienen", "tuve"],
    "hacer": ["hago", "haces", "hace", "hice", "hizo"],
    "poder": ["puedo", "puedes", "puede", "pude"],
    "querer": ["quiero", "quieres", "quiere", "quise"],
    "salir": ["salgo", "sales", "sale"],
    "volver": ["vuelvo", "vuelves", "vuelve"],
    "dormir": ["duermo", "duermes", "duerme"],
    "empezar": ["empiezo", "empiezas", "empieza"],
    "conocer": ["conozco"],
    "saber": ["sé", "sabes", "sabe"],
    "venir": ["vengo", "vienes", "viene"],
    "decir": ["digo", "dices", "dice", "dije"],
    "poner": ["pongo", "pones", "pone"],
    "ver": ["veo", "ves", "vi", "vio"],
    "dar": ["doy", "das", "da", "di"],
    "pensar": ["pienso", "piensas", "piensa"],
    "preferir": ["prefiero"],
    "jugar": ["juego", "juegas", "juega"],
    "pedir": ["pido", "pides", "pide"],
    "servir": ["sirvo"],
    "sentir": ["siento"],
    "encontrar": ["encuentro"],
}
# Present (yo, tú, él, nosotros, vosotros, ellos), preterite (yo, tú, él) and gerund, by infinitive ending.
REGULAR_ENDINGS = {
    "ar": ["o", "as", "a", "amos", "áis", "an", "é", "aste", "ó", "ando"],
    "er": ["o", "es", "e", "emos", "éis", "en", "í", "iste", "ió", "iendo"],
    "ir": ["o", "es", "e", "imos", "ís", "en", "í", "iste", "ió", "iendo"],
}
# After these, a word that is both a noun/adjective and a verb form is read as the verb (no trabajo, me paso).
CLITICS = {"yo", "tú", "no", "me", "te", "se", "lo", "la", "le", "nos"}
CLAUSE_PUNCT = set(".,;:!?¡¿")
OPEN_POS = {"NOUN", "VERB", "ADJ", "PROPN"}
FILLER = NOT_TEACHABLE | {"gracias"}  # don't make "Cuenta, por favor" a two-word sentence
NOUN_FIRST = {"gusto", "solo"}  # "Gusto en conocerte", "Solo agua": the verb reading only when spaCy says verb

# Fixes to remember (the learner's own corrected mistakes) come back in Repaso: first after FIX_FIRST_DUE, then along
# INTERVALS; FIX_DONE_STREAK spaced successes retire one, and a miss brings it back in FIX_RETRY.
FIX_FIRST_DUE = 600
FIX_RETRY = 120
FIX_DONE_STREAK = 4
FIX_MAX_WORDS = 7
REVIEW_MAX = 12  # Repaso items per round
FIX_EVERY = 3  # one fix after every 3 words
DEFAULT_GOAL_SENTENCES = 20  # daily goal: sentences of practice (turns, attempts, Repaso drills)
GOAL_SENTENCE_CHOICES = (10, 20, 30, 40, 50)
GOAL_FROM_MINUTES = {2: 10, 5: 20, 10: 30, 15: 40, 20: 50}  # migration from the old minutes goal
# Place names a learner says about themselves: capitalized in Repaso (the DB stores lowercase lemmas).
PROPER_NOUNS = {"finlandia", "españa", "madrid", "barcelona", "helsinki", "méxico", "argentina", "colombia", "perú",
                "chile", "cuba", "francia", "alemania", "italia", "inglaterra", "suecia", "noruega", "europa",
                "américa", "londres", "parís", "roma", "sevilla", "valencia", "tampere", "turku"}

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
create table if not exists lessons_done (
  id integer primary key autoincrement,
  ts real not null,
  session_id text,
  lesson text not null,
  used integer not null,
  goal integer not null,
  recap text
);
create table if not exists settings (
  key text primary key,
  value text not null
);
create table if not exists fix_items (
  id integer primary key,
  right text not null,
  right_norm text unique,
  right_en text,
  wrong text,
  type text,
  why text,
  streak integer default 0,
  due_at real,
  status text default 'active',
  created_at real,
  updated_at real
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


def _gloss_key(gloss: str) -> str:
    """An English meaning, normalized for comparison: "Time " == "time", "(the) time" == "time", "a dog" == "dog"."""
    g = re.sub(r"\([^)]*\)", " ", gloss.lower())
    g = re.sub(r"[^\w\s,/'-]", " ", g)
    g = re.sub(r"^\s*(?:the|a|an)\s+", "", g)
    return " ".join(g.split()).strip(" ,/")


# Feminine nouns starting with a stressed a- take "el": el agua, el hambre.
EL_FEMININE = {"agua", "alma", "arma", "área", "aula", "hambre", "águila", "hacha", "ala", "habla", "ama", "hada"}
ARTICLES = {"el", "la", "los", "las", "un", "una", "unos", "unas", "del", "al"}


def error_words(mistakes: list[dict]) -> set[str]:
    """Words the learner got wrong: in the wrong fragment but not in the fix.
    For gender errors (la perro -> el perro) the article differs, but the word to learn is the noun after it."""
    out: set[str] = set()
    for m in mistakes:
        wrong = re.findall(r"\w+", str(m.get("wrong", "")).lower())
        # Accents aside: a written accent in the transcript is not what the learner got wrong.
        right = {_strip_accents(w) for w in re.findall(r"\w+", str(m.get("right", "")).lower())}
        for i, w in enumerate(wrong):
            if _strip_accents(w) not in right:
                out.add(w)
                if w in ARTICLES and i + 1 < len(wrong):
                    out.add(wrong[i + 1])
    return out


@dataclass
class Token:
    text: str
    lemma: str  # matched vocab lemma, or spaCy lemma if not in list
    in_list: bool
    pos: str = ""  # spaCy POS (NOUN, VERB, ...)


class Vocab:
    def __init__(self) -> None:
        rows = json.loads(config.VOCAB_PATH.read_text())
        self.words = {r["word"]: r for r in rows}
        self.by_plain = {_strip_accents(w): w for w in self.words}
        self.total_ranked = len(self.words)
        # Everyday supplement (manzana, maleta, paraguas…): tracked like list words, after the ranked list. They get
        # ranks after the last one, since a non-null rank is what marks a list word in the DB (progress, reviews).
        extra = json.loads(config.VOCAB_EXTRA_PATH.read_text()) if config.VOCAB_EXTRA_PATH.is_file() else []
        last = max((r["rank"] for r in rows if r.get("rank")), default=0)
        for r in extra:
            if r["word"] in self.words:
                continue
            last += 1
            self.words[r["word"]] = {**r, "rank": last}
            self.by_plain.setdefault(_strip_accents(r["word"]), r["word"])  # never shadows a ranked word
        self.total_extra = len(self.words) - self.total_ranked
        # Same-meaning words (vez and tiempo are both "time"): either one is right in a Repaso drill.
        self.by_gloss: dict[str, list[str]] = {}
        for w, r in self.words.items():
            key = _gloss_key(r.get("translation") or "")
            if key:
                self.by_gloss.setdefault(key, []).append(w)
        self.verb_forms, self.ambig = self._build_forms()
        self.nlp = spacy.load("es_core_news_sm", disable=["ner", "parser"])
        self.db = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._migrate()
        self._migrate_goal()
        self._sync_list()

    def _sync_list(self) -> None:
        """Words that were outside the list before it grew (1000 -> 2000 + supplement) were stored with rank null:
        give them their rank (and the list's translation if they had none), so they count like list words."""
        n = self.db.executemany(
            "update word_state set rank=?, translation=coalesce(translation, ?) where lemma=? and rank is null",
            [(w["rank"], w["translation"], w["word"]) for w in self.words.values()]).rowcount
        # The 1000 list had a typo for señor; rows store the translation they were created with.
        self.db.execute("update word_state set translation=? where lemma='señor' and translation='msiter'",
                        (self.words.get("señor", {}).get("translation", "mister, sir"),))
        self.db.commit()
        if n > 0:
            log.info("vocab: %d tracked words are now list words", n)

    def _migrate(self) -> None:
        """One-time: words marked known by the old rules (one correct use) without being taught or used twice
        go back to learning, one spaced use away from known."""
        if self.setting("migr_srs_v2", ""):
            return
        now = time.time()
        n = self.db.execute(
            "update word_state set status='learning', streak=?, due_at=?, updated_at=? "
            "where status='known' and taught=0 and produced<2", (SPONTANEOUS_STREAK, now + DAY, now)).rowcount
        self.set_setting("migr_srs_v2", "1")  # commits
        if n:
            log.info("vocab migration srs_v2: %d known words back to learning", n)

    def _migrate_goal(self) -> None:
        """One-time: the daily goal is counted in sentences now, not minutes of speaking. An old minutes goal
        becomes the nearest sentence goal."""
        old = self.db.execute("select value from settings where key='goal_minutes'").fetchone()
        if old is None or self.db.execute("select 1 from settings where key='goal_sentences'").fetchone():
            return
        try:
            minutes = float(old["value"])
        except (TypeError, ValueError):
            minutes = 5.0
        nearest = min(GOAL_FROM_MINUTES, key=lambda m: abs(m - minutes))
        self.set_setting("goal_sentences", str(GOAL_FROM_MINUTES[nearest]))  # commits
        log.info("vocab migration: daily goal %g min -> %d sentences", minutes, GOAL_FROM_MINUTES[nearest])

    # ---------- analysis ----------
    def _match(self, word: str, lemma: str, verb: bool, adj: bool = False) -> str | None:
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
        # spaCy's small model often leaves inflected adjectives as they are (deliciosa, nublados): try the -o form.
        if adj and (base := re.sub(r"(a|as|os)$", "o", word.lower())) != word.lower() and base in self.words:
            return base
        return None

    def _build_forms(self) -> tuple[dict[str, str], dict[str, str]]:
        """{surface: verb lemma} for list verbs: the hand-written irregular forms, plus regular forms of verbs
        with a stem of 3+ letters (short stems like ir/ser would produce en, o, sí). A form that is itself a listed
        noun/adjective/… (trabajo, vivo, para, como, cena) goes into the second dict, decided per sentence."""
        verbs = [w for w, r in self.words.items() if r.get("partOfSpeech") == "verb"]
        gen = [(f, lemma) for lemma in verbs if lemma in IRREGULAR for f in (lemma, *IRREGULAR[lemma])]
        for lemma in verbs:
            if lemma in IRREGULAR:
                continue
            gen.append((lemma, lemma))
            stem, end = lemma[:-2], lemma[-2:]
            if end in REGULAR_ENDINGS and len(stem) >= 3:
                gen += [(stem + e, lemma) for e in REGULAR_ENDINGS[end] if len(stem + e) > 2]
        forms: dict[str, str] = {}
        ambig: dict[str, str] = {}
        for form, lemma in gen:  # irregular first: it wins over a regular form of another verb
            info = self.words.get(form)
            if info is not None and info.get("partOfSpeech") != "verb":
                ambig.setdefault(form, lemma)
            elif info is None or form == lemma:  # never let a form shadow another listed verb
                forms.setdefault(form, lemma)
        return forms, ambig

    def _upos(self, lemma: str, pos: str) -> str:
        """The POS of the reading we chose, when it overrides spaCy's (salgo: PROPN -> VERB, cuenta: VERB -> NOUN)."""
        want = {"verb": "VERB", "noun": "NOUN", "adjective": "ADJ"}.get(self.words.get(lemma, {}).get("partOfSpeech", ""))
        if want is None or (want == "VERB" and pos == "AUX"):
            return pos
        return want

    def _resolve(self, doc: list, i: int, prefer: set[str] | frozenset[str] | None) -> tuple[str | None, str]:
        """(list lemma or None, POS) of doc[i]. Lookups use the lowercased form; spaCy ran on the original text."""
        t = doc[i]
        s, pos = t.text.lower(), t.pos_
        verb = pos in ("VERB", "AUX")
        # (a) The lesson's open targets win a tie: "Trabajo como programador" while learning trabajar. After a
        # determiner it is the noun all the same (mi trabajo), and if both readings are targets (the intro lesson
        # has trabajar and trabajo) the rules below decide.
        if prefer:
            reading = self.verb_forms.get(s) or self.ambig.get(s)
            verb_ok = reading is not None and reading in prefer and not (i > 0 and doc[i - 1].pos_ == "DET")
            self_ok = s in prefer
            if verb_ok and not self_ok:
                return reading, self._upos(reading, pos)
            if self_ok and not verb_ok:
                return s, self._upos(s, pos)
        # (b) A listed noun/adjective/… that is also a verb form: trabajo, vivo, para, cena — or one spaCy read as a
        # verb (cuenta -> contar).
        verb_reading = self.ambig.get(s)
        if verb_reading is None and verb and self.words.get(s, {}).get("partOfSpeech") not in (None, "verb"):
            m = self._match(t.text, t.lemma_, True)
            if m and m != s and self.words[m].get("partOfSpeech") == "verb":
                verb_reading = m
        if verb_reading:
            prev = doc[i - 1] if i > 0 else None
            nxt = doc[i + 1] if i + 1 < len(doc) else None
            if prev is not None and prev.pos_ == "DET":  # la cuenta, mi trabajo, mucho gusto
                return s, self._upos(s, pos)
            starts = prev is None or (prev.is_punct and all(c in CLAUSE_PUNCT for c in prev.text))
            # spaCy often tags a yo-form as a noun, or as PROPN when capitalised. At the start of a clause and
            # followed by a word that isn't a verb (a bare noun rarely starts one), or after yo/no, it is the learner
            # talking about themselves: "Trabajo como profesor", "Hola, estudio español", "Desayuno a las ocho".
            if pos in ("PROPN", "NOUN") and s.endswith("o") and s not in NOUN_FIRST:
                after_subject = prev is not None and prev.text.lower() in ("yo", "no")
                if after_subject or (starts and nxt is not None and nxt.is_alpha and nxt.pos_ not in ("VERB", "AUX")):
                    return verb_reading, "VERB"
            others = any(o.is_alpha and o.pos_ in OPEN_POS and o.text.lower() not in FILLER
                         and o.lemma_.lower() not in FILLER for j, o in enumerate(doc) if j != i)
            # The only real word ("Cuenta.", "Cuenta, por favor") is the noun — unless spaCy reads it as a yo-form
            # verb ("Vivo aquí", "Trabajo mucho").
            if not others and not (verb and s.endswith("o")):
                return s, self._upos(s, pos)
            if verb and (starts or prev.text.lower() in CLITICS):  # Vivo en Helsinki, no trabajo, me cuenta
                return verb_reading, self._upos(verb_reading, pos)
            return self._match(t.text, t.lemma_, verb, pos == "ADJ"), pos
        # (c) A known verb form spaCy's small model mis-tags: Salgo (PROPN), salgo (PRON), duermo, Conozco.
        if s in self.verb_forms:
            lemma = self.verb_forms[s]
            return lemma, self._upos(lemma, pos)
        # (d) Verbs match by lemma, other words by surface form. Then infinitives with pronouns: conocerte, llamarme.
        if (m := self._match(t.text, t.lemma_, verb, pos == "ADJ")) is not None:
            return m, pos
        return (base, "VERB") if (base := self._enclitic(s)) else (None, pos)

    def _enclitic(self, s: str) -> str | None:
        """conocerte -> conocer, dármelo -> dar: an infinitive of a list verb with one or two pronouns attached."""
        base = _strip_accents(s)
        for _ in range(2):
            m = re.fullmatch(r"(.*?(?:ar|er|ir))(me|te|se|nos|os|le|les|lo|la|los|las)", base)
            if not m:
                return None
            base = m.group(1)
            if self.words.get(base, {}).get("partOfSpeech") == "verb":
                return base
        return None

    def tokens(self, text: str, *, prefer: set[str] | frozenset[str] | None = None) -> list[Token]:
        """Words of `text` matched to the list. `prefer`: lemmas to choose when a word could be either (the lesson's
        targets the learner is working on)."""
        doc = [t for t in self.nlp(text) if not t.is_space]
        out = []
        for i, t in enumerate(doc):
            s = t.text.lower()
            if not t.is_alpha or (t.pos_ in SKIP_POS and s not in self.verb_forms and s not in self.ambig):
                continue
            m, pos = self._resolve(doc, i, prefer)
            # Words outside the list keep their surface form: spaCy's lemmas for unknown words can be junk.
            out.append(Token(t.text, m or s, m is not None, pos))
        return out

    def info(self, lemma: str) -> dict | None:
        return self.words.get(lemma)

    def glosses(self, text: str) -> dict[str, str]:
        """{word as written (lowercase): "lemma: translation"} for hover tooltips on Sofía's words."""
        out = {}
        for t in self.tokens(text):
            w = self.words.get(t.lemma) if t.in_list else None
            if w:
                head = t.lemma if t.lemma != t.text.lower() else ""
                out[t.text.lower()] = f"{head + ' — ' if head else ''}{w['translation']}"
        return out

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

    @staticmethod
    def _climb(streak: int, status: str, now: float) -> tuple[int, str, float]:
        """One spaced success: the streak rises and the next review moves out along the ladder."""
        streak += 1
        due = now + INTERVALS[min(streak, len(INTERVALS) - 1)]
        return streak, ("known" if streak >= GRADUATE_STREAK or status == "known" else "learning"), due

    def record_learner(
        self, session_id: str, text: str, mistakes: list[dict], english: list[str], *,
        echo: frozenset[str] = frozenset(), prefer: set[str] | frozenset[str] | None = None,
        example: str | None = None, skip: set[str] | frozenset[str] = frozenset(),
    ) -> dict:
        """Update states from one learner utterance. Returns {lemma: outcome} for the UI.
        `echo`: lemmas the learner saw or heard since their last utterance (Sofía's reply, a hint, a coach note,
        word cards). Saying those back is repeating, not recalling, so it doesn't raise a streak.
        `prefer`: the lesson's open targets, passed to tokens() so an ambiguous word gets the target's reading.
        `example`: the sentence the word bank shows as the example of a word said right (default: `text`). For a
        message with a mistake, the corrected sentence, so wrong Spanish never appears as an example.
        `skip`: lowercased words the speech recognizer wasn't sure of (maybe misheard): neither ok nor error."""
        now = time.time()
        english_words = {w.lower() for span in english for w in span.split()}
        wrong_words = error_words(mistakes)
        outcome: dict[str, str] = {}
        for tok in self.tokens(text, prefer=prefer):
            lw = tok.text.lower()
            if not tok.in_list or lw in english_words or lw in skip or tok.lemma in outcome:
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
            echoed = tok.lemma in echo
            if status in ("new", "seen"):
                if echoed:  # repeated, not recalled: it starts at the bottom of the ladder
                    status, streak, due = "learning", 0, now + INTERVALS[0]
                else:  # said on their own: one more spaced use, on another day, makes it known
                    status, streak, due = "learning", SPONTANEOUS_STREAK, now + SPONTANEOUS_DUE
            elif not echoed and (due is None or now >= due):  # spaced (learning), or a due review (known)
                streak, status, due = self._climb(streak, status, now)
            self.db.execute(
                "update word_state set produced=produced+1, streak=?, status=?, last_produced=?, due_at=?, updated_at=? where lemma=?",
                (streak, status, now, due, now, tok.lemma),
            )
            self._event(session_id, tok.lemma, "produced_ok", example or text)
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
            # A sentence ("yo trabajo con computadoras", "tengo dos hermanos"): learn its content words, not the
            # sentence. Short set phrases (por favor, buenos días) stay one entry.
            split = len(toks) > 2 or (len(toks) == 2 and any(t.pos in ("VERB", "AUX") for t in toks))
            if split:
                lemmas = self._phrase_lemmas(toks)
                if not lemmas:  # nothing new in it to learn
                    out.append({**it, "rank": None})
                    continue
            else:  # a word, or a short set phrase (por favor, buenos días)
                lemma = toks[0].lemma if len(toks) == 1 else re.sub(r"^(el|la|los|las|un|una)\s+", "", spanish.lower())
                lemmas = [lemma]
            for lemma in lemmas:
                r = self._row(lemma)  # make sure the word exists
                # Keep the meaning it was taught with ("the bill", not the dictionary's "account"). For a split
                # sentence its English isn't the word's meaning: only when there is nothing better.
                if it.get("english") and len(lemmas) == 1 and (not split or not r["translation"]):
                    self.db.execute("update word_state set translation=? where lemma=?", (it["english"], lemma))
                status = "learning"  # needing to be taught means it is not active vocabulary (yet)
                self.db.execute(
                    "update word_state set taught=taught+1, streak=0, status=?, due_at=?, updated_at=? where lemma=?",
                    (status, now + RELEARN_DELAY, now, lemma),
                )
                self._event(session_id, lemma, "taught", json.dumps(it, ensure_ascii=False))
            info = self.words.get(lemmas[0])
            out.append({**it, "lemma": lemmas[0], "rank": info["rank"] if info else None})
        self.db.commit()
        return out

    def _phrase_lemmas(self, toks: list[Token]) -> list[str]:
        """Up to 2 content words of a taught sentence that are worth learning (list words first)."""
        cands = [t for t in toks if t.pos in TEACH_POS and t.lemma not in NOT_TEACHABLE and self.status(t.lemma) != "known"]
        cands = [t for t in cands if t.in_list] or cands  # out-of-list words keep their surface form
        return list(dict.fromkeys(t.lemma for t in cands))[:2]

    def record_spoken(self, session_id: str, seconds: float) -> None:
        """Speaking time (shown as a tooltip; counts toward the streak)."""
        self._event(session_id, None, "spoke", f"{seconds:.1f}")
        self.db.commit()

    def record_practice(self, session_id: str, kind: str) -> None:
        """One sentence of practice, for the daily goal and the streak: a learner turn (typed or spoken; not a
        voice command or the rest of a continued turn), a graded attempt, or a Repaso drill."""
        self._event(session_id, None, "practice", kind if kind in ("turn", "attempt", "review") else "turn")
        self.db.commit()

    def record_review(self, session_id: str, lemma: str, ok: bool) -> None:
        """Result of a spoken review drill (English prompt -> say the Spanish)."""
        now = time.time()
        r = self._row(lemma)
        if ok and r["due_at"] is not None and now < r["due_at"]:
            # Not due yet (e.g. the retry of a word just missed): practice, not a spaced success.
            self.db.execute("update word_state set produced=produced+1, last_produced=?, updated_at=? where lemma=?",
                            (now, now, lemma))
            self._event(session_id, lemma, "review_practice")
            self.db.commit()
            return
        if ok:
            streak, status, due = self._climb(r["streak"], r["status"], now)
            self.db.execute(
                "update word_state set produced=produced+1, streak=?, status=?, last_produced=?, due_at=?, updated_at=? "
                "where lemma=?", (streak, status, now, due, now, lemma))
        else:
            self.db.execute(
                "update word_state set errors=errors+1, streak=0, status='learning', due_at=?, updated_at=? where lemma=?",
                (now + RELEARN_DELAY, now, lemma))
        self._event(session_id, lemma, "review_ok" if ok else "review_fail")
        self.db.commit()

    def record_mistakes(self, session_id: str, mistakes: list[dict]) -> None:
        for m in mistakes:
            self._event(session_id, None, "mistake", json.dumps(m, ensure_ascii=False))
        self.db.commit()

    def save_turn(self, session_id: str, lesson: str, speaker: str, text: str, meta: dict | None = None, *,
                  ts: float | None = None) -> None:
        """`ts` keeps a turn's rows in speaking order when they are written later, in the background."""
        self.db.execute(
            "insert into turns(ts, session_id, lesson, speaker, text, meta) values (?,?,?,?,?,?)",
            (time.time() if ts is None else ts, session_id, lesson, speaker, text, json.dumps(meta or {}, ensure_ascii=False)),
        )
        self.db.commit()

    # ---------- settings ----------
    def setting(self, key: str, default: str) -> str:
        r = self.db.execute("select value from settings where key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("insert into settings(key, value) values (?,?) on conflict(key) do update set value=excluded.value",
                        (key, value))
        self.db.commit()

    # ---------- queries ----------
    def display(self, lemma: str) -> str:
        """The word as taught: nouns with their article (el agua, la cuenta)."""
        w = self.words.get(lemma)
        if not w or not w["article"]:
            return lemma
        article = "el" if w["article"] == "la" and lemma in EL_FEMININE else w["article"]
        return f"{article} {lemma}"

    def meaning(self, word: str) -> str | None:
        """Instant answer for "¿qué significa X?": from the frequency list or words taught earlier."""
        # The word itself first ("cuenta" = the bill), then its lemma (spaCy would read "cuenta" as contar).
        toks = self.tokens(word)
        translation, lemma = None, word.lower()
        for cand in dict.fromkeys([word.lower(), *(t.lemma for t in toks)]):
            r = self.db.execute("select translation from word_state where lemma=?", (cand,)).fetchone()
            translation = (r and r["translation"]) or (self.words.get(cand) or {}).get("translation")
            if translation:
                lemma = cand
                break
        if not translation:
            return None
        head = self.display(lemma)
        return f"«{word}» = {translation}" + (f" (from «{head}»)" if _strip_accents(head) != _strip_accents(word.lower()) else "")

    def review_display(self, lemma: str) -> str:
        """The word as shown in Repaso: the first form of "caro/a", and place names capitalized (Finlandia)."""
        d = re.sub(r"(\w+)/\w+", r"\1", self.display(lemma))
        w = self.words.get(lemma) or {}
        if lemma in PROPER_NOUNS or str(w.get("partOfSpeech", "")).lower().replace("_", " ") in ("proper noun", "propn"):
            head, _, last = d.rpartition(" ")
            d = (head + " " if head else "") + last[:1].upper() + last[1:]
        return d

    def alts(self, lemma: str, english: str | None = None) -> list[str]:
        """Other list words with the same meaning (by the list's gloss, or the meaning it was taught with)."""
        keys = {_gloss_key((self.words.get(lemma) or {}).get("translation") or ""), _gloss_key(english or "")}
        out = [w for k in keys if k for w in self.by_gloss.get(k, []) if w != lemma and w not in NOT_TEACHABLE]
        return list(dict.fromkeys(out))

    def review_queue(self, limit: int = 10) -> list[dict]:
        """Spoken review drill: words that are due first, then the newest learning words as extra practice."""
        now = time.time()
        rows = self.db.execute(
            "select lemma, translation, status, due_at from word_state where status in ('learning','known') "
            f"and translation is not null and lemma not in ({','.join('?' * len(NOT_TEACHABLE))}) order by (case when due_at is null or due_at<=? then 0 else 1 end), "
            "(case when status='learning' then 0 else 1 end), due_at limit ?", (*NOT_TEACHABLE, now, limit)).fetchall()
        return [{"kind": "word", "lemma": r["lemma"], "display": self.review_display(r["lemma"]),
                 "english": r["translation"], "due": r["due_at"] is None or r["due_at"] <= now, "status": r["status"],
                 "alts": self.alts(r["lemma"], r["translation"])} for r in rows]

    def review_items(self, limit: int = REVIEW_MAX) -> list[dict]:
        """One Repaso round: word items, with a due fix after every 3 words (fixes first when fewer than 3 words
        are due)."""
        words = self.review_queue(limit)
        fixes = [{"kind": "fix", "id": f["id"], "right": f["right"], "right_en": f["right_en"], "wrong": f["wrong"] or "",
                  "why": f["why"] or "", "type": f["type"] or "other"} for f in self.fix_queue(limit)]
        if sum(1 for w in words if w["due"]) < FIX_EVERY:
            return (fixes + words)[:limit]
        out: list[dict] = []
        for i, w in enumerate(words, 1):
            out.append(w)
            if i % FIX_EVERY == 0 and fixes:
                out.append(fixes.pop(0))
        return (out + fixes)[:limit]

    # ---------- fixes to remember ----------
    def add_fixes(self, mistakes: list[dict]) -> int:
        """The learner's corrected mistakes, as Repaso items ("Say it right"). Only real fixes of 1-7 words with an
        English meaning; the same right form again moves its review closer instead of adding a row."""
        now = time.time()
        n = 0
        for m in mistakes:
            right, right_en = str(m.get("right") or "").strip(), str(m.get("right_en") or "").strip()
            kind = str(m.get("type") or "other")
            norm = " ".join(plain_words(right))
            if kind == "other" or not right_en or not 1 <= len(norm.split()) <= FIX_MAX_WORDS:
                continue
            self.db.execute(
                "insert into fix_items(right, right_norm, right_en, wrong, type, why, streak, due_at, status, created_at, "
                "updated_at) values (?,?,?,?,?,?,0,?,'active',?,?) on conflict(right_norm) do update set "
                "wrong=excluded.wrong, why=excluded.why, right_en=excluded.right_en, type=excluded.type, "
                # made again after it was retired: it's back, from the start (one the learner dropped stays dropped)
                "streak=case when status='done' then 0 else streak end, "
                "status=case when status='done' then 'active' else status end, "
                "due_at=min(coalesce(due_at, excluded.due_at), excluded.due_at), updated_at=excluded.updated_at",
                (right, norm, right_en, str(m.get("wrong") or ""), kind, str(m.get("why") or ""), now + FIX_FIRST_DUE,
                 now, now))
            n += 1
        self.db.commit()
        return n

    def fix(self, fix_id: int) -> dict | None:
        r = self.db.execute("select * from fix_items where id=?", (fix_id,)).fetchone()
        return dict(r) if r else None

    def fix_queue(self, limit: int = REVIEW_MAX) -> list[dict]:
        """Active fixes that are due, the oldest due first."""
        return [dict(r) for r in self.db.execute(
            "select * from fix_items where status='active' and due_at<=? order by due_at, id limit ?",
            (time.time(), limit))]

    def record_fix_review(self, fix_id: int, ok: bool, *, session_id: str | None = None) -> dict | None:
        """A spoken "Say it right" drill. Right once it's due: one step up the ladder (retired at streak 4); right
        again before that: practice only; wrong (or "I don't know"): from the start, back in 2 min."""
        r = self.fix(fix_id)
        if r is None:
            return None
        now = time.time()
        if ok and r["due_at"] is not None and now < r["due_at"]:
            self._event(session_id, None, "fix_practice", r["right"])
            self.db.commit()
            return r
        if ok:
            streak = r["streak"] + 1
            due = now + INTERVALS[min(streak, len(INTERVALS) - 1)]
            status = "done" if streak >= FIX_DONE_STREAK else "active"
        else:
            streak, due, status = 0, now + FIX_RETRY, "active"
        self.db.execute("update fix_items set streak=?, due_at=?, status=?, updated_at=? where id=?",
                        (streak, due, status, now, fix_id))
        self._event(session_id, None, "fix_ok" if ok else "fix_fail", r["right"])
        self.db.commit()
        return self.fix(fix_id)

    def drop_fix(self, fix_id: int, *, session_id: str | None = None) -> None:
        """"This wasn't a mistake": never asked again."""
        r = self.fix(fix_id)
        if r is None:
            return
        self.db.execute("update fix_items set status='dropped', updated_at=? where id=?", (time.time(), fix_id))
        self._event(session_id, None, "fix_drop", r["right"])
        self.db.commit()

    def word_list(self) -> list[dict]:
        """Word bank: everything taught or used, newest first, with the learner's own sentence when there is one."""
        rows = self.db.execute(
            "select w.lemma, w.translation, w.status, w.produced, w.errors, w.taught, w.rank, w.updated_at, "
            "(select detail from events e where e.lemma=w.lemma and e.kind='produced_ok' order by e.id desc limit 1) "
            "as example from word_state w where w.status in ('learning','known') order by w.updated_at desc").fetchall()
        return [{**dict(r), "display": self.display(r["lemma"])} for r in rows if r["lemma"] not in NOT_TEACHABLE]

    def stats(self, goal_minutes: float = 5.0, goal_sentences: int = DEFAULT_GOAL_SENTENCES) -> dict:
        """Daily goal (sentences of practice), streak and due reviews."""
        days = {r["d"] for r in self.db.execute(
            "select distinct date(ts, 'unixepoch', 'localtime') d from events where kind in ('practice','spoke')")}
        today = time.strftime("%Y-%m-%d")
        # Streak = consecutive days with practice, ending today (or yesterday, if today hasn't started yet).
        d = date.today() if today in days else date.today() - timedelta(days=1)
        streak = 0
        while d.isoformat() in days:
            streak += 1
            d -= timedelta(days=1)
        seconds = self.db.execute(
            "select coalesce(sum(cast(detail as real)), 0) s from events where kind='spoke' "
            "and date(ts, 'unixepoch', 'localtime') = ?", (today,)).fetchone()["s"]
        sentences = self.db.execute(
            "select count(*) n from events where kind='practice' and date(ts, 'unixepoch', 'localtime') = ?",
            (today,)).fetchone()["n"]
        due = self.db.execute(
            "select count(*) n from word_state where status in ('learning','known') and translation is not null "
            f"and lemma not in ({','.join('?' * len(NOT_TEACHABLE))}) and (due_at is null or due_at<=?)",
            (*NOT_TEACHABLE, time.time())).fetchone()["n"]
        due += self.db.execute("select count(*) n from fix_items where status='active' and due_at<=?",
                               (time.time(),)).fetchone()["n"]
        return {"today_seconds": round(seconds), "goal_minutes": goal_minutes, "today_sentences": sentences,
                "goal_sentences": goal_sentences, "streak": streak, "due_reviews": due}

    def mistake_patterns(self, days: int = 30, session_id: str | None = None) -> list[dict]:
        """Most frequent kinds of mistakes, with the latest example of each."""
        rows = self.db.execute(
            "select detail from events where kind='mistake' and ts>=? " + ("and session_id=? " if session_id else "")
            + "order by id desc", (time.time() - days * DAY, *([session_id] if session_id else []))).fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            m = json.loads(r["detail"])
            t = m.get("type") or "other"
            g = groups.setdefault(t, {"type": t, "count": 0, "example": m})
            g["count"] += 1
        return sorted(groups.values(), key=lambda g: -g["count"])

    def status(self, lemma: str) -> str:
        r = self.db.execute("select status from word_state where lemma=?", (lemma,)).fetchone()
        return r["status"] if r else "new"

    @staticmethod
    def lesson_worthy(w: dict) -> bool:
        """A list word worth teaching as a new lesson word: a content word, not a grammar word, not vulgar or
        written-only, and useful when speaking (ranks 1-1000 have no register / usefulness: they all pass)."""
        useful = w.get("spoken_usefulness")
        return (w["word"] not in NOT_TEACHABLE and w["partOfSpeech"] in LESSON_POS
                and w.get("register") not in SKIP_REGISTERS and (useful is None or useful > MIN_USEFULNESS))

    def _have(self) -> set[str]:
        return {r["lemma"] for r in self.db.execute("select lemma from word_state where status in ('learning','known')")}

    def next_common(self, limit: int) -> list[str]:
        """Most frequent content words the learner doesn't have yet (for lessons without a theme).
        The everyday supplement comes after the ranked list."""
        have = self._have()
        out = []
        for w in sorted(self.words.values(), key=lambda r: r["rank"]):
            if w["word"] not in have and self.lesson_worthy(w):
                out.append(w["word"])
                if len(out) >= limit:
                    break
        return out

    def theme_words(self, themes: list[str], limit: int, exclude: Iterable[str] = ()) -> list[str]:
        """New words from the list's themes (food_drink, home…), most useful in speech first, then by frequency.
        Tops up a themed lesson's own word pool."""
        if not themes:
            return []
        have = self._have() | set(exclude)
        cands = [w for w in self.words.values()
                 if w.get("theme") in themes and w["word"] not in have and self.lesson_worthy(w)]
        cands.sort(key=lambda w: (-(w.get("spoken_usefulness") or 3), w["rank"]))
        return [w["word"] for w in cands[:limit]]

    def save_lesson(self, session_id: str, recap: dict) -> None:
        self.db.execute(
            "insert into lessons_done(ts, session_id, lesson, used, goal, recap) values (?,?,?,?,?,?)",
            (time.time(), session_id, recap["lesson"], recap["used"], recap["goal"], json.dumps(recap, ensure_ascii=False)),
        )
        self.db.commit()

    def review_words(self, limit: int = 4, due_only: bool = True) -> list[dict]:
        """Words to review in a lesson: due words first (learning before known, then most overdue), content words
        only. With due_only=False, not-yet-due learning words follow (a new lesson reviews last lesson's words even
        if it starts right after)."""
        now = time.time()
        rows = self.db.execute(
            "select lemma, translation from word_state where status in ('learning','known') and rank is not null "
            "and ((due_at is null or due_at<=?) or (? = 0 and status='learning')) "
            "order by (case when due_at is null or due_at<=? then 0 else 1 end), "
            "(case when status='learning' then 0 else 1 end), due_at",
            (now, int(due_only), now),
        ).fetchall()
        return [dict(r) for r in rows if self.is_content(r["lemma"])][:limit]

    def translation(self, lemma: str) -> str | None:
        """The meaning the word was taught with (word_state), if it has a row with one."""
        r = self.db.execute("select translation from word_state where lemma=?", (lemma,)).fetchone()
        return (r["translation"] or None) if r else None

    def is_content(self, lemma: str) -> bool:
        """Counts toward "words you can say": a list word of a content class, not a grammar word."""
        w = self.words.get(lemma)
        return bool(w) and lemma not in NOT_TEACHABLE and w["partOfSpeech"] in CONTENT_POS

    def progress(self, session_id: str | None = None) -> dict:
        counts = {r["status"]: r["n"] for r in self.db.execute(
            "select status, count(*) n from word_state where rank is not null group by status")}
        known = sum(1 for r in self.db.execute("select lemma from word_state where status='known' and rank is not null")
                    if self.is_content(r["lemma"]))
        recent = [dict(r) for r in self.db.execute(
            "select lemma, translation, status, produced, errors, taught from word_state "
            "where status in ('learning','known') order by updated_at desc limit 40")]
        session = {}
        if session_id:
            session = {r["kind"]: r["n"] for r in self.db.execute(
                "select kind, count(*) n from events where session_id=? group by kind", (session_id,))}
        lessons = {r["lesson"]: {"times": r["n"], "best_used": r["used"], "goal": r["goal"]} for r in self.db.execute(
            "select lesson, count(*) n, max(used) used, max(goal) goal from lessons_done group by lesson")}
        return {
            "lessons": lessons,
            "total": len(self.words),
            "total_ranked": self.total_ranked,  # the frequency list (2000)
            "total_extra": self.total_extra,  # everyday supplement words
            "total_content": sum(1 for w in self.words if self.is_content(w)),
            "known": known,  # content words only: the headline number
            "known_all": counts.get("known", 0),
            "learning": counts.get("learning", 0),
            "seen": counts.get("seen", 0),
            "recent": recent,
            "session": session,
        }
