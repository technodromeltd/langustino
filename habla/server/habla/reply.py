"""Deterministic guards on Sofía's spoken reply, and the per-turn memory note. Pure Python (imports only practice).

The prompt asks for natural replies (recast mistakes, never praise them, don't parrot a correct sentence, never
re-ask), but no model follows that every time, so code checks each sentence as it comes off the sentence splitter,
before it is spoken:

- praise guard: «¡Muy bien!» as the first sentence of a reply to a message that had a mistake is dropped; «Muy bien,
  …» loses only the praise.
- echo guard: a first sentence that just repeats a correct message («Mi padre es alto.» → «Tu padre es alto.») is
  dropped (not «Mi padre es bajo.»: a new word makes it her own sentence).
- lint: REPLY_LINT fixes a few errors a model speaker must not make («Yo también me gusta»).

A reply is never left silent: if every sentence was dropped, the dropped ones are spoken after all.
"""

from __future__ import annotations

import logging
import re

from .practice import grade, words

log = logging.getLogger("habla")

# Mistakes Sofía herself makes now and then: fixed in every sentence before it is spoken (case-insensitive; the
# replacement keeps the first letter's case).
REPLY_LINT = {
    r"\bYo también me gusta\b": "A mí también me gusta",
    r"\bYo también me encanta\b": "A mí también me encanta",
    r"\bconoco\b": "conozco",
}
_LINT = [(re.compile(p, re.IGNORECASE), r) for p, r in REPLY_LINT.items()]

_PRAISE = r"(?:muy bien|qu[eé] bien|perfecto|genial|excelente|bien)"
_PRAISE_ONLY = re.compile(rf"^¡?\s*{_PRAISE}\s*[!.…]*$", re.IGNORECASE)
_PRAISE_LEAD = re.compile(rf"^(¡?)\s*{_PRAISE}\s*[!.,:;…]+\s*", re.IGNORECASE)
ECHO_MIN_WORDS = 3  # «¡Hola, Eero!» or «¡Qué bien!» is a reaction, not parroting
# An echo is made of the learner's own words (practice.grade, so small slips match) plus at most these: person swaps
# and fillers («Ah, tu padre es muy alto.»). Any other new word («Mi padre es bajo.») makes it Sofía's own sentence
# about herself, which is what the prompt asks for, even though most of its words are the learner's.
ECHO_FILLER = {"tu", "tus", "su", "sus", "mi", "mis", "yo", "usted", "ustedes", "te", "ti", "ah", "oh", "eh", "vaya",
               "y", "muy", "tambien", "entonces", "vale", "claro", "si", "pues", "bueno", "que"}

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _same_case(found: str, repl: str) -> str:
    if not repl:
        return repl
    first = repl[0].upper() if found[:1].isupper() else repl[0].lower()
    return first + repl[1:]


def lint(sentence: str) -> str:
    for rx, repl in _LINT:
        sentence = rx.sub(lambda m, r=repl: _same_case(m.group(0), r), sentence)
    return sentence


def strip_praise(sentence: str) -> str | None:
    """None if the sentence is only praise («¡Muy bien!»); the rest if it opens with praise («Muy bien, un café.» →
    «Un café.»); else the sentence unchanged."""
    s = sentence.strip()
    if _PRAISE_ONLY.match(s):
        return None
    m = _PRAISE_LEAD.match(s)
    if not m:
        return sentence
    rest = s[m.end():]
    if not rest:  # «Muy bien,»: praise and nothing else
        return None
    if m.group(1) and rest.endswith("!") and "¡" not in rest:  # «¡Perfecto, un café!» → «¡Un café!»
        rest = "¡" + rest
    i = 1 if rest[0] in "¡¿" else 0
    return rest[:i] + rest[i:i + 1].upper() + rest[i + 1:]


class ReplyGuard:
    """Sits between tts.SentenceSplitter and tts.OneQuestion in app.respond(). `better` is set as soon as the reply
    JSON's better field is complete (it is written before the reply); while it is None the praise and echo guards are
    off. The praise guard checks the first sentence that would be spoken (after a dropped «¡Muy bien!», the next one
    too: «¡Muy bien! ¡Perfecto!»); the echo guard checks only the reply's very first sentence, because the sentence
    after a dropped echo is usually her own similar one («Mi padre es bajo.»)."""

    def __init__(self, learner: str = "", tid: str = "") -> None:
        self.learner = learner or ""
        self.tid = tid
        self.better: str | None = None
        self.started = False  # a sentence has been passed on
        self.echo_checked = False  # only the reply's very first sentence can be an echo
        self.dropped: list[str] = []
        self.changes: list[str] = []

    def _note(self, change: str) -> None:
        self.changes.append(change)
        log.info("turn %s reply guard: %s", self.tid, change)

    def _mistake(self) -> bool:
        return bool(self.better) and bool(self.learner.strip()) and words(self.better) != words(self.learner)

    def _first(self, s: str) -> str | None:
        if self.better is None or not self.learner.strip():
            return s
        if self._mistake():
            kept = strip_praise(s)
            if kept is None:
                self._note(f"dropped praise «{s}» (better: «{self.better}»)")
                self.dropped.append(s)
            elif kept != s:
                self._note(f"stripped praise «{s}» → «{kept}»")
            return kept
        first, self.echo_checked = not self.echo_checked, True
        if first and self.better == "" and self.is_echo(s):
            self._note(f"dropped echo «{s}» of «{self.learner}»")
            self.dropped.append(s)
            return None
        return s

    def is_echo(self, s: str) -> bool:
        if s.rstrip().endswith("?") or len(words(s)) < ECHO_MIN_WORDS:
            return False
        g = grade(s, self.learner)
        new = {x for w in g["words"] if not w["ok"] for x in words(w["w"])}
        own = [x for w in g["words"] if w["ok"] for x in words(w["w"]) if x not in ECHO_FILLER]
        return new <= ECHO_FILLER and len(own) >= 2  # «Sí, yo también.» repeats only fillers: a reaction

    def feed(self, sentences: list[str]) -> list[str]:
        out = []
        for s in sentences:
            fixed = lint(s)
            if fixed != s:
                self._note(f"lint «{s}» → «{fixed}»")
            s = fixed
            if not self.started:
                kept = self._first(s)
                if kept is None:
                    continue
                s, self.started = kept, True
            out.append(s)
        return out

    def flush(self) -> list[str]:
        """At the end of the reply: if everything was dropped, speak it after all (never a silent reply)."""
        if self.started or not self.dropped:
            return []
        self.started = True
        self._note(f"nothing left to say: restored {len(self.dropped)} dropped sentence(s)")
        out, self.dropped = self.dropped, []
        return out


# ---------- memory note: what Sofía already asked, what the learner already told her ----------
MEMORY_QUESTIONS = 8
MEMORY_TOLD = 8
MEMORY_TOLD_CHARS = 400
MEMORY_SELF = 8
# First-person words: a statement of Sofía's with one of these says something about herself.
_FIRST_PERSON = {"yo", "soy", "estoy", "tengo", "vivo", "trabajo", "mi", "mis", "me", "hablo", "quiero", "voy",
                 "fui", "estudio", "estudié", "tenía", "era", "hago", "como", "leo", "juego", "conozco"}


def self_statements(reply: str) -> list[str]:
    """Sofía's sentences about herself (first person, not questions): she must not say them again."""
    out = []
    for s in _SENTENCE.split(reply.strip()):
        s = _one_line(s)
        if s and not s.endswith("?") and len(words(s)) >= 3 and _FIRST_PERSON & set(words(s)):
            out.append(s)
    return out


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def reply_questions(reply: str, learner: str = "") -> list[str]:
    """The real questions in a reply. A recast («¿Una cerveza?» for «un cerveza») repeats the learner's words: it
    isn't a question she asked."""
    qs = [_one_line(s) for s in _SENTENCE.split(reply.strip()) if s.strip().endswith("?")]
    return [q for q in qs if not (learner.strip() and grade(q, learner)["score"] >= 0.5)]


def memory_note(asked: list[str], told: list[str], said: list[str] | None = None) -> str:
    """The per-turn note «[Already asked: «q1» «q2» — don't ask these again. They told you: …]», built from the last
    MEMORY_QUESTIONS distinct questions and the last MEMORY_TOLD things the learner said (in English, newest kept when
    it's too long). "" when there's nothing. It goes into the user message only (never the system prompt), and
    starts with "[" so Session._push_user strips it from older messages."""
    qs: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for q in reversed(asked):
        key = tuple(words(q))
        if key and key not in seen:
            seen.add(key)
            qs.insert(0, _one_line(q))
        if len(qs) == MEMORY_QUESTIONS:
            break
    facts: list[str] = []
    size = 0
    for t in reversed([_one_line(t) for t in told if t and t.strip()][-MEMORY_TOLD:]):
        add = len(t) + (2 if facts else 0)
        if size + add > MEMORY_TOLD_CHARS:
            if not facts:
                facts.append(t[:MEMORY_TOLD_CHARS - 1].rstrip() + "…")
            break
        facts.insert(0, t)
        size += add
    parts = []
    if qs:
        parts.append("Already asked: " + " ".join(f"«{q}»" for q in qs) + " — don't ask these again.")
    if facts:
        parts.append("They told you: " + "; ".join(facts))
    mine = list(dict.fromkeys(said or []))[-MEMORY_SELF:]
    if mine:
        parts.append("You already said: " + " ".join(f"«{x}»" for x in mine) + " — never say these again.")
    return f"[{' '.join(parts)}]" if parts else ""
