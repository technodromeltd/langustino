"""Lesson director: code owns the lesson flow, the LLM gets one short instruction per turn.

Phases:  warmup -> introduce -> practice -> recap -> free
  warmup     1 learner turn (2 if there are words to review); review words get worked in
  introduce  one new word per turn, shown as a word card, and the next turn elicits THAT word (its first elicitation):
             introduce W1 -> elicit W1 -> introduce W2 -> elicit W2 ... A word Sofía didn't actually say in her reply
             (saw_reply) goes back to pending and is introduced again.
  practice   role-play; every other turn Sofía is told which open target to elicit, alternating review and new words
  recap      the goal is reached (4 of 5 new words used, and every review word asked for at least once),
             MAX_PRACTICE_TURNS practice turns, MAX_LESSON_TURNS learner turns in all, or the learner pressed Finish
  free       keep chatting after the lesson

A goodbye in Sofía's reply before the recap («¡Adiós!») gets a "the scene continues" line in front of the next note.

Target word states:  pending -> introduced -> practiced (repeated right after the intro) -> used (said on their own)
Only "used" counts toward the goal, so parroting doesn't complete a lesson. A pending word the learner
already says correctly becomes "known" and is swapped for the next word in the pool, so every lesson
teaches genuinely new words. An echo (a word Sofía just said or showed) counts as a repetition at most:
pending -> introduced, introduced -> practiced; it never makes a target "used" or "known".

The teacher card ("coach") is written here, not by the LLM: an example sentence from lessons.json `examples` when a word
is introduced, a hint on its second elicitation, and the recap summary (recap_text). A card is never shown twice in a
lesson.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import NamedTuple

from .vocab import EL_FEMININE, Vocab

NEW_PER_LESSON = 5
MAX_REVIEW = 3
INTRO_PER_TURN = 1  # new words per introduce turn (the intro -> elicit alternation is built around one)
MAX_PRACTICE_TURNS = 14
MAX_LESSON_TURNS = 20  # hard cap: the recap comes at this learner turn, whatever the phase
GOAL_RATIO = 0.8  # use 4 of 5 new words on your own to finish the lesson
LESSON_PHASES = ("warmup", "introduce", "practice")
GOODBYES = ("adios", "hasta luego", "hasta pronto", "nos vemos")  # accent-stripped, lowercase
CONTINUE_NOTE = "[The scene continues — do not say goodbye yet; carry on the role-play naturally.]"


def _plain(s: str) -> str:
    """Lowercase, no accents, punctuation as spaces, single-spaced: for whole-word matching."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def _contains(text: str, phrase: str) -> bool:
    """Whole-word match of `phrase` in `text`, both already _plain()."""
    return bool(phrase) and f" {phrase} " in f" {text} "


@dataclass
class Target:
    lemma: str
    display: str  # with article: "la cuenta"
    english: str
    pos: str
    kind: str  # "new" | "review"
    state: str = "pending"
    intro_turn: int | None = None
    elicited: int = 0
    deferred: int = 0  # turns Sofía held the word back because it didn't fit the conversation yet

    def quoted(self) -> str:
        return f"«{self.display}» ({self.english})"


class Step(NamedTuple):
    """What the next tutor turn gets: the note for the LLM, the words it introduces (word cards) and the teacher card
    text ("" = no card). The coach text is built by code from the lesson data, never by the LLM."""

    note: str
    intro: list[Target]
    coach: str = ""


class Director:
    def __init__(self, lesson: dict, vocab: Vocab) -> None:
        self.lesson = lesson
        self.vocab = vocab
        self.phase = "warmup"
        self.turn = 0  # learner turns
        self.phase_turns = 0
        self.started = time.time()
        self.pool: list[str] = []  # remaining candidate words, used to replace already-known targets
        self.targets = self._pick_targets()
        self.warmup_turns = 2 if any(t.kind == "review" for t in self.targets) else 1
        self.mistakes: list[dict] = []
        self.taught: list[dict] = []
        self.said_ok: set[str] = set()
        self._upgraded: dict[str, str] = {}
        self.recap_sent = False
        # Hand-written example sentences: {lemma: [Spanish, English]}. Words without one (theme top-ups, the free
        # lesson) get the fallback texts.
        self.examples: dict[str, list[str]] = lesson.get("examples") or {}
        self.coach_sent: set[str] = set()  # teacher-card texts already shown in this lesson
        # Pacing: what the last instruction did ("warmup" | "introduce" | "elicit" | "continue" | "recap" | "free"),
        # which word it introduced, and the practice elicitation counter (even: review first, odd: new first).
        self.last_kind = ""
        self.last_intro: Target | None = None
        self.picks = 0
        self._intro_now: list[Target] = []  # introduced by the latest instruction: saw_reply checks them
        self.no_goodbye = False  # Sofía said goodbye before the recap: the next note says the scene goes on
        self._pacing: tuple | None = None  # pacing state before the latest learner turn (see observe)

    # ---------- target selection ----------
    def _target(self, entry: str, kind: str) -> Target | None:
        word, _, gloss = (p.strip() for p in entry.partition(":"))
        info = self.vocab.info(word)
        if not info:
            return None
        article = info["article"]
        if article == "la" and word in EL_FEMININE:  # el agua, el hambre: stressed a- takes "el"
            article = "el"
        display = f"{article} {word}".strip()
        # A review word keeps the meaning it was taught with (word_state: pedir = "to order", not the list's "to
        # ask"). A new word takes the lesson's gloss: its word_state row may only hold the list's translation.
        taught = self.vocab.translation(word) if kind == "review" else None
        return Target(word, display, taught or gloss or info["translation"], info["partOfSpeech"], kind)

    def _pick_targets(self) -> list[Target]:
        if self.lesson.get("auto_words"):
            pool = self.vocab.next_common(NEW_PER_LESSON * 3)
        else:
            pool = list(self.lesson.get("words", []))
            # Themed lessons top up their own pool from the list's theme field (cafe -> food_drink), so they keep
            # teaching new words once the hand-picked ones are learned.
            listed = {e.partition(":")[0].strip() for e in pool}
            pool += self.vocab.theme_words(self.lesson.get("themes", []), NEW_PER_LESSON * 3, exclude=listed)
        new: list[Target] = []
        review: list[Target] = []
        for entry in pool:
            status = self.vocab.status(entry.partition(":")[0].strip())
            if status == "known":
                continue
            kind = "review" if status == "learning" else "new"
            bucket = review if kind == "review" else new
            limit = MAX_REVIEW if kind == "review" else NEW_PER_LESSON
            if len(bucket) < limit and (t := self._target(entry, kind)):
                bucket.append(t)
            elif kind == "new":
                self.pool.append(entry)
        have = {t.lemma for t in new + review}
        for w in self.vocab.review_words(limit=MAX_REVIEW * 2, due_only=False):
            if len(review) >= MAX_REVIEW:
                break
            if w["lemma"] not in have and (t := self._target(w["lemma"], "review")):
                review.append(t)
        return new + review

    # ---------- state ----------
    @property
    def new(self) -> list[Target]:
        return [t for t in self.targets if t.kind == "new" and t.state != "known"]

    @property
    def goal(self) -> int:
        return math.ceil(GOAL_RATIO * len(self.new))

    def used_new(self) -> int:
        return sum(1 for t in self.new if t.state == "used")

    def _replace(self, known: Target) -> None:
        """The learner already knew this word: teach the next one from the pool instead."""
        have = {t.lemma for t in self.targets}
        while self.pool:
            entry = self.pool.pop(0)
            if entry.partition(":")[0].strip() not in have and (t := self._target(entry, "new")):
                self.targets.insert(self.targets.index(known) + 1, t)
                return

    def _go(self, phase: str) -> None:
        self.phase = phase
        self.phase_turns = 0

    def observe(self, lemmas_ok: set[str], *, echoed: frozenset[str] = frozenset(), continuation: bool = False) -> None:
        """A learner turn arrived. `lemmas_ok` is what they said (provisionally correct, before the LLM checks it).
        `echoed`: lemmas they saw or heard since their last turn (Sofía's reply, hints, coach notes, word cards).
        `continuation`: the same turn again, longer (they went on talking before hearing her answer): no new turn,
        and the pacing goes back to before the cut answer's instruction, so that instruction is simply redone."""
        if continuation:
            self._restore_pacing()
        else:
            self.turn += 1
            self.phase_turns += 1
            self._upgraded = {}  # a continuation keeps the first part's upgrades (correct() may undo them)
            self._pacing = self._pacing_now()
        for t in self.targets:
            if t.lemma not in lemmas_ok:
                continue
            if t.lemma in echoed:  # repeated, not recalled
                if t.state == "pending":
                    new, t.intro_turn = "introduced", self.turn
                elif t.state == "introduced":
                    new = "practiced"
                else:
                    continue
            elif t.state == "introduced":
                new = "practiced" if t.intro_turn == self.turn - 1 else "used"
            elif t.state == "pending":
                new = "used" if t.kind == "review" else "known"
            elif t.state == "practiced":
                new = "used"
            else:
                continue
            self._upgraded[t.lemma] = t.state
            t.state = new
        for t in [t for t in self.targets if t.state == "known" and self._upgraded.get(t.lemma) == "pending"]:
            self._replace(t)
        self.said_ok |= lemmas_ok

        if self.phase == "warmup" and self.phase_turns >= self.warmup_turns:
            self._go("introduce" if self._pending_new() else "practice")
        elif self.phase == "introduce" and not self._pending_new() and self._followup() is None:
            self._go("practice")
        if self.phase == "practice" and (
            not self.targets or self._goal_reached() or self.phase_turns > MAX_PRACTICE_TURNS
        ):
            self._go("recap")
        if self.phase in LESSON_PHASES and self.turn >= MAX_LESSON_TURNS:
            self._go("recap")
        elif self.phase == "recap" and self.recap_sent:
            self._go("free")

    def _pacing_now(self) -> tuple:
        return (self.last_kind, self.last_intro, self.picks, {t.lemma: t.elicited for t in self.targets},
                self.recap_sent, self.no_goodbye)

    def _restore_pacing(self) -> None:
        if self._pacing is None:
            return
        self.last_kind, self.last_intro, self.picks, elicited, self.recap_sent, self.no_goodbye = self._pacing
        for t in self.targets:
            t.elicited = elicited.get(t.lemma, t.elicited)

    def _goal_reached(self) -> bool:
        """Enough new words used on their own, and every review word asked for at least once (or already used)."""
        new_ok = self.used_new() >= max(self.goal, 1) if self.new else True
        review_ok = all(t.elicited >= 1 or t.state in ("used", "known") for t in self.targets if t.kind == "review")
        return new_ok and review_ok

    def _followup(self) -> Target | None:
        """The word the last turn introduced, if this turn should elicit it (still being learned)."""
        t = self.last_intro
        if self.last_kind == "introduce" and t is not None and t.state in ("introduced", "practiced"):
            return t
        return None

    def saw_reply(self, reply_text: str, reply_lemmas: set[str]) -> list[str]:
        """Sofía's reply for this turn is final. A word introduced in it that she didn't actually say goes back to
        pending (it is introduced again next turn); returns those lemmas. A goodbye before the recap sets a flag:
        the next note tells her the scene goes on."""
        plain = _plain(reply_text)
        missing = []
        for t in self._intro_now:
            noun = _plain(re.sub(r"^(el|la|los|las)\s+", "", t.display))
            if (t.state == "introduced" and t.intro_turn == self.turn and t.lemma not in reply_lemmas
                    and not _contains(plain, noun) and not _contains(plain, _plain(t.lemma))):
                t.state, t.intro_turn = "pending", None
                t.deferred += 1
                missing.append(t.lemma)
        self._intro_now = []
        if self.phase not in ("recap", "free") and any(_contains(plain, g) for g in GOODBYES):
            self.no_goodbye = True
        return missing

    def correct(self, error_lemmas: set[str], *, upgraded: dict[str, str] | None = None) -> None:
        """The LLM found mistakes: undo upgrades for target words the learner got wrong this turn.
        `upgraded` is that turn's snapshot of `_upgraded`: the check runs in the background, so a newer
        learner turn may already have replaced it."""
        for lemma, prev in (self._upgraded if upgraded is None else upgraded).items():
            if lemma in error_lemmas:
                # If it had been swapped out as "known", the replacement stays and this word is a target again.
                next(t for t in self.targets if t.lemma == lemma).state = prev
        self.said_ok -= error_lemmas

    def finish(self) -> None:
        self._go("recap")

    def _pending_new(self) -> list[Target]:
        return [t for t in self.new if t.state == "pending"]

    # ---------- teacher card ----------
    def example(self, t: Target) -> tuple[str, str] | None:
        ex = self.examples.get(t.lemma)
        if isinstance(ex, list) and len(ex) == 2 and all(isinstance(x, str) and x.strip() for x in ex):
            return ex[0].strip(), ex[1].strip()
        return None

    def _once(self, text: str) -> str:
        """`text`, unless it was already shown in this lesson."""
        if not text or text in self.coach_sent:
            return ""
        self.coach_sent.add(text)
        return text

    def unsend(self, text: str) -> None:
        """The turn that carried this card was dropped before any of it was heard: it may be shown again."""
        self.coach_sent.discard(text)

    def _intro_coach(self, words: list[Target]) -> str:
        for t in words:
            if ex := self.example(t):
                return self._once(f"Try: «{ex[0]}» — {ex[1]}")
        return ""  # the word cards already show the meanings

    def _hint_coach(self, t: Target) -> str:
        if ex := self.example(t):
            return self._once(f"Hint: you could say «{ex[0]}» ({ex[1]})")
        return self._once(f"Hint: try using «{t.display}» ({t.english}) in your answer.")

    @staticmethod
    def _join(items: list[str]) -> str:
        q = [f"«{x}»" for x in items]
        return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]

    def recap_text(self) -> str:
        """The end-of-lesson teacher card, in English, from the director's own state only: the words used on their
        own (or practised), the last correction, and what comes next. Call it once the lesson's turns are checked."""
        parts = []
        used = [t for t in self.targets if t.state == "used"][:3]
        practised = [t for t in self.targets if t.state in ("practiced", "introduced")][:3]
        if used:
            parts.append(f"You used {self._join([t.display for t in used])} on your own.")
        elif practised:
            parts.append(f"You practised {self._join([t.display for t in practised])}.")
        if self.mistakes:
            m = self.mistakes[-1]
            right, wrong = str(m.get("right") or "").strip(), str(m.get("wrong") or "").strip()
            if right:
                parts.append(f"Remember: «{right}»" + (f" (not «{wrong}»)." if wrong else "."))
        mentioned = {t.lemma for t in used or practised}
        open_ = [t for t in self.targets if t.state not in ("used", "known")]
        nxt = ([t for t in open_ if t.lemma not in mentioned] + [t for t in open_ if t.lemma in mentioned])[:2]
        if nxt:
            parts.append(f"Next time: {self._join([t.display for t in nxt])}.")
        return self._once(" ".join(parts))

    # ---------- instructions ----------
    def start_note(self) -> Step:
        step = self.instruction()
        return step._replace(note=(
            "[Session start, no learner message: learner_en is empty. In reply, greet the learner warmly as Sofía, "
            "then step into the role-play.]\n" + step.note
        ))

    def instruction(self) -> Step:
        """The next turn: what Sofía does in character (the note), the words introduced in it, and the teacher
        card. The role-play stays natural; explicit teaching goes to the card, and only when it helps (scaffolding:
        the first attempt at a word gets no hint). The recap card is not here: it needs the last turn's check, so
        the app builds it with recap_text() after that turn's analysis."""
        self._intro_now = []
        kind, step = self._next()
        self.last_kind = kind
        if self.no_goodbye:
            self.no_goodbye = False
            if self.phase in LESSON_PHASES:
                step = step._replace(note=f"{CONTINUE_NOTE}\n{step.note}")
        return step

    def _introduce(self, t: Target) -> Step:
        t.state, t.intro_turn = "introduced", self.turn
        self.last_intro = t
        self._intro_now = [t]
        if t.deferred < 2:
            note = (f"[Lesson step: if it fits naturally with what they just said, use the new word {t.quoted()} in "
                    f"your reply and ask a question they could answer with it. If it doesn't fit yet, don't force "
                    f"it: follow up on their answer and steer gently toward a topic where it will fit.]")
        else:
            note = (f"[Lesson step: use the new word {t.quoted()} in your reply now, in a sentence that connects to "
                    f"what you're talking about, and ask a question they could answer with it.]")
        return Step(
            note,
            [t],
            self._intro_coach([t]),
        )

    def _elicit(self, t: Target) -> Step:
        t.elicited += 1
        if t.elicited == 1:  # let them try on their own first
            return Step(f"[Lesson step: if it follows naturally from their answer, ask a question whose answer would "
                        f"use {t.quoted()}; don't say that word yourself. Following up on what they said comes first: "
                        f"never jump to an unrelated topic just for the word.]", [])
        return Step(f"[Lesson step: when it fits the conversation, ask a question they could answer with "
                    f"{t.quoted()}.]", [], self._hint_coach(t) if t.elicited == 2 else "")

    def _pick(self, open_: list[Target]) -> Target:
        """Practice elicitations alternate kinds: even picks prefer a review word, odd picks a new one (falling
        back to the other kind); the least-asked word of that kind first."""
        want = "review" if self.picks % 2 == 0 else "new"
        self.picks += 1
        return min([t for t in open_ if t.kind == want] or open_, key=lambda t: t.elicited)

    def _next(self) -> tuple[str, Step]:
        review = [t for t in self.targets if t.kind == "review" and t.state != "used"]
        review_txt = (" If it fits, create a chance for them to use " + " or ".join(t.quoted() for t in review) + ".") \
            if review else ""
        cont = Step("[Lesson step: continue the role-play with a natural next question that follows from what the "
                    "learner just said.]", [])

        if self.phase == "warmup":
            return "warmup", Step(f"[Lesson step: start the role-play with an easy question.{review_txt}]", [])

        if self.phase in ("introduce", "practice") and (t := self._followup()) is not None:
            return "elicit", self._elicit(t)  # right after its introduction: use it while it's fresh

        if self.phase == "introduce":
            pending = self._pending_new()
            # The word held back least often first, so one word that won't fit doesn't block the others.
            return ("introduce", self._introduce(min(pending, key=lambda t: t.deferred))) if pending \
                else ("continue", cont)

        if self.phase == "practice":
            open_ = [t for t in self.targets if t.state not in ("used", "known")]
            if not open_ or self.phase_turns % 2 == 0:  # every other turn: just keep the conversation going
                return "continue", cont
            if pending := self._pending_new():  # a replacement for a word they knew, arrived during practice
                return "introduce", self._introduce(pending[0])
            return "elicit", self._elicit(self._pick(open_))

        if self.phase == "recap":
            self.recap_sent = True
            return "recap", Step("[Lesson step: the lesson is over. In reply, say a warm goodbye in character, in "
                                 "1–2 sentences, with no question.]", [])

        return "free", Step(f"[Free conversation after the lesson: just chat naturally.{review_txt}]", [])

    # ---------- reporting ----------
    def record_turn(self, mistakes: list[dict], taught: list[dict]) -> None:
        self.mistakes += mistakes
        self.taught += taught

    def state(self) -> dict:
        return {
            "phase": self.phase,
            "targets": [asdict(t) for t in self.targets],
            "goal": self.goal,
            "used": self.used_new(),
        }

    def recap(self) -> dict:
        seen, fixes = set(), []
        for m in self.mistakes:
            if m["right"].lower() not in seen:
                seen.add(m["right"].lower())
                fixes.append(m)
        return {
            "lesson": self.lesson["id"],
            "targets": [asdict(t) for t in self.targets],
            "goal": self.goal,
            "used": self.used_new(),
            "said_ok": len(self.said_ok),
            "turns": self.turn,
            "minutes": round((time.time() - self.started) / 60, 1),
            "mistakes": fixes,
            "taught": list({w["spanish"].lower(): w for w in self.taught}.values()),
            "review_next": list(dict.fromkeys(
                [t.display for t in self.targets if t.state not in ("used", "known")]
                + [w["spanish"] for w in self.taught]
            )),
        }
