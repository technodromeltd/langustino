"""Habla voice server: one WebSocket per conversation.

client -> server
  {"type":"start","lesson":"intro"}     start/restart a session (tutor speaks first)
  <binary>                              one utterance, 16 kHz mono int16 PCM
  {"type":"text","text":"..."}          typed utterance (same pipeline, no STT)
  {"type":"help"}                       learner is stuck: tutor suggests what to say
  {"type":"finish"}                     end the lesson now (tutor wraps up, recap follows)
  {"type":"voice","id":"piper-es-ES"}   switch Sofía's voice (a short sample is played)
  {"type":"settings", ...}              level (A0-B1), goal_sentences (daily goal), subtitles
  {"type":"attempt","kind":..,"target":..}  the NEXT binary frame is a graded attempt, not a turn:
                                        review (say the Spanish for an English prompt), repair (say a
                                        correction), shadow (repeat Sofía's sentence)
  {"type":"speak","text":..,"slow":bool}   read a word or sentence aloud (tap-to-hear)
  {"type":"review_skip","lemma":..}     "I don't know" in the review drill
  {"type":"fix_skip","id":..}           "I don't know" on a fix to remember (kind "fix" attempt: target = the right form,
                                        fix_id = its id)
  {"type":"fix_drop","id":..}           "This wasn't a mistake": that fix never comes back
  {"type":"cancel","played":N}          learner interrupted: stop the current reply. `played` = how many of its
                                        sentences had started playing (the rest is dropped from the history)
  {"type":"heard","id":..,"played":N}   learner stopped the playback of a reply that's already written (nothing to
                                        cancel): cut it in the history to the sentences that had started playing
  {"type":"continue","prev":N,"n":K}    the NEXT binary frame is the last utterance (its first N samples) plus what
                                        the learner said after it, before hearing any of Sofía's answer: the turn
                                        is redone with the whole thing under the same user id (user has replace:true).
                                        The user (or ignored) message for it carries cont:K
  {"type":"attempt_cancel"}             the learner left a pending attempt (an attempt older than 20 s is ignored too)

server -> client
  ready, status, user, user_en (English of what the learner said), feedback (fix / new-word cards),
  tutor_delta, audio, coach (teacher card, written by the director from lessons.json; sent before the reply
  streams, or with the recap during the analysis), tutor_done (reply + word glosses, sent when the reply is
  written; reply_en is "" here), progress, ignored, error,
  lesson_state (phase + target words), introduce (word cards for a tutor turn), recap,
  attempt_result (graded attempt + audio of the target), hint (hint ladder / instant meaning),
  replay (voice command: repeat Sofía's last reply, optionally slower),
  tutor_en {id, reply_en} (translation, from the background analysis), turn_done {id} (tutor turn id: the
  analysis of that turn is finished, so its feedback / recap / progress have been sent),
  tutor_dropped {id} (the reply was cut off before any of it was heard: remove its bubble).
  feedback may carry merged:true (checked together with the next message) or unchecked:true (no usable check:
  no cards, and not counted as said right), or clarify:true (the audio was too unclear: Sofía asked them to repeat,
  nothing was graded).
  user carries unclear: [words the speech recognizer wasn't sure of]. They are never fixes and never tracked.
  user with replace:true (a continuation) replaces that id's bubble: its text, and any feedback it had so far.
  attempt_result echoes the attempt's key and n, and may carry a note ("«el perro», not «la perro»", "also right — …",
  or a missed fix's why).

A turn has two parts. A (cancellable): the reply streams into TTS; once it is written it goes into the history
and tutor_done is sent; status goes idle right after the last audio (maybe just before tutor_done).
B (_analyze, a background task that is never
cancelled): grammar check (or just a translation), DB writes, feedback, tutor_en, lesson_state, recap, progress,
turn_done. B's LLM calls wait while a reply is streaming and are cancelled and retried when a new one starts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from difflib import SequenceMatcher
from typing import Any

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import scenario
from .english import english_spans
from .fixes import build_mistakes, clean_mistakes, diff_fixes
from .stt import STT
from .tts import TTS, OneQuestion, SentenceSplitter
from .director import Director, Target
from .practice import LEVELS, MISTAKE_TYPES, article_mismatch, command, grade, suggest_level
from .reply import ReplyGuard, memory_note, reply_questions, self_statements
from .tutor import TURN_TIMEOUT_S, FieldStream, Tutor, build_system, teacher_messages
from .vocab import DEFAULT_GOAL_SENTENCES, GOAL_SENTENCE_CHOICES, Vocab, _strip_accents

log = logging.getLogger("habla")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

LESSONS = {lesson["id"]: lesson for lesson in json.loads(config.LESSONS_PATH.read_text())}
STT_POOL = ThreadPoolExecutor(1, thread_name_prefix="stt")
TTS_POOL = ThreadPoolExecutor(1, thread_name_prefix="tts")


def _tips() -> bool:
    """Does the reply call write its own teacher card? (Not before the engines are loaded, e.g. in unit tests.)"""
    tutor = getattr(E, "tutor", None)
    return bool(tutor and tutor.tips)


class Engines:
    stt: STT
    tts: TTS
    tutor: Tutor
    vocab: Vocab


E = Engines()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    t0 = time.time()
    llm = await Tutor.pick_model()
    E.tutor = Tutor(llm)
    E.vocab = Vocab()
    E.stt = await loop.run_in_executor(STT_POOL, STT)
    E.tts = await loop.run_in_executor(TTS_POOL, TTS)
    await asyncio.gather(
        loop.run_in_executor(STT_POOL, E.stt.warmup),
        loop.run_in_executor(TTS_POOL, E.tts.warmup),
        E.tutor.warmup(),
    )
    log.info("ready in %.1fs — LLM=%s (%s%s) STT=%s voice=%s", time.time() - t0, llm.model, llm.backend,
             f", reasoning={llm.reasoning}" if llm.backend == "openai" and llm.reasoning else "",
             config.WHISPER_MODEL, E.tts.default)
    yield
    await llm.aclose()


app = FastAPI(lifespan=lifespan)


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", s).strip()


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_ASKED = re.compile(r"(how do you say|como se dice|what is .* in spanish|como se llama)")


def clean_teach(teach: list[dict], learner_text: str, english: list[str]) -> list[dict]:
    """Keep only words the learner said in English, or asked how to say. The model sometimes adds words it is
    introducing, or Spanish words the learner used correctly (mercado → mercado)."""
    said, asked = _norm(" ".join(english)), bool(_ASKED.search(_norm(learner_text)))
    return [
        t for t in teach
        if t.get("english") and t.get("spanish") and _norm(t["english"]) != _norm(t["spanish"])
        and ((said and _norm(t["english"]) in said) or (asked and _norm(t["english"]) in _norm(learner_text)))
    ]


def progress_payload(session_id: str | None = None) -> dict:
    """Vocabulary progress + daily goal/streak + mistake patterns, sent after every turn."""
    p = E.vocab.progress(session_id)
    patterns = [
        {**g, "label": MISTAKE_TYPES.get(g["type"], MISTAKE_TYPES["other"])[0],
         "tip": MISTAKE_TYPES.get(g["type"], MISTAKE_TYPES["other"])[1]}
        for g in E.vocab.mistake_patterns() if g["type"] != "other"
    ][:3]
    return {**p, "stats": E.vocab.stats(_goal_minutes(), _goal_sentences()), "patterns": patterns}


def _goal_minutes() -> float:
    try:
        return float(E.vocab.setting("goal_minutes", "5"))
    except ValueError:
        return 5.0


def _goal_sentences() -> int:
    """The daily goal: sentences of practice (learner turns, graded attempts, Repaso drills)."""
    try:
        return max(1, int(float(E.vocab.setting("goal_sentences", str(DEFAULT_GOAL_SENTENCES)))))
    except ValueError:
        return DEFAULT_GOAL_SENTENCES


def current_settings() -> dict:
    known = E.vocab.progress()["known_all"]  # all known words: the content-only count would lower the level
    return {
        "level": E.vocab.setting("level", suggest_level(known)),
        "suggested_level": suggest_level(known),
        "goal_minutes": _goal_minutes(),
        "goal_sentences": _goal_sentences(),
        "goal_choices": list(GOAL_SENTENCE_CHOICES),
        "subtitles": E.vocab.setting("subtitles", "always"),
        "levels": [{"id": k, "label": v["label"]} for k, v in LEVELS.items()],
    }


@dataclass
class TurnRecord:
    """What the background analysis of one tutor turn needs, captured when the reply is written.
    `reply` can still shrink (the learner barges in during playback) until the DB rows are written."""

    tid: str
    user_turn: str | None
    user_text: str | None  # None: start / finish turn (no learner message, so only a translation)
    english: list[str]
    reply: str
    coach: str
    learner_en: str
    director: Director | None
    sid: str
    lesson_id: str
    upgraded: dict[str, str]
    recap: bool
    t_end: float
    dropped: bool = False  # interrupted before any of it was heard: no analysis
    echo: frozenset[str] = frozenset()  # lemmas shown to the learner before they spoke (repeating isn't recalling)
    prefer: frozenset[str] = frozenset()  # the lesson's open targets: their reading wins for ambiguous words
    # The learner's message as a native speaker would say it ("" = it was right; None = the model's output didn't
    # have it, so nothing is known about the message). The fix cards are diff_fixes(user_text, better).
    better: str | None = None
    own_text: str | None = None  # a merged turn's newest message alone (better may cover only that one)
    unclear: frozenset[str] = frozenset()  # lowercased words Whisper wasn't sure of: never a fix, never tracked
    clarify: bool = False  # too unclear to grade (Sofía asks them to repeat it): no fixes, no word tracking

    def history_entry(self) -> str:
        return json.dumps({"learner_en": self.learner_en, "better": self.better or "", "reply": self.reply},
                          ensure_ascii=False)


@dataclass
class OpenTurn:
    """The latest learner turn, until the next one starts. While none of Sofía's answer to it has been heard, the
    learner can still go on talking ({"type":"continue"}): the whole utterance then replaces this turn, under the
    same id. Filled in as learner_said gets further (a turn cut off early has only its id)."""

    user_turn: str
    echo: frozenset[str] = frozenset()
    prev_reply: str = ""  # last_reply before this turn (her unheard answer to it is taken back out)
    merged: TurnRecord | None = None  # an earlier unanswered turn that was merged into this one
    msg: dict | None = None  # its history message
    prev_content: str | None = None  # that message's content before this turn was added to it (None: a new message)
    observed: bool = False  # the director observed it: a continuation re-observes it without counting a new turn
    clarified: bool = False  # it has an entry in Session.clarified
    intro: list[Target] = field(default_factory=list)  # words its answer was told to introduce


@dataclass
class Commit:
    """The latest reply in the history, so a learner who stops the playback can still cut it to what they heard."""

    rec: TurnRecord
    entry: dict
    audio_sent: list[str]


ANALYSIS_TRIES = 4  # yields of one analysis before it's logged as a warning
CLARIFY_MIN_UNCLEAR = 2  # unclear words in one utterance that make Sofía ask them to repeat...
CLARIFY_LOGPROB = -0.6  # ...or Whisper's avg_logprob below this (accented speech is about -0.3, native -0.03)
CLARIFY_GAP = 3  # no clarification if one of the last 3 learner turns had one
ATTEMPT_MAX_AGE_S = 20  # an "attempt" whose audio hasn't come by then is stale: the audio is a normal utterance
SAMPLE_RATE = 16000


def clarify_note(unclear: list[str]) -> str:
    part = f' ("{", ".join(unclear)}")' if unclear else ""
    return (f"[Part of their message was unclear to the app{part}. Ask them naturally to repeat or confirm that part, "
            "e.g. «¿Perdón? ¿Con quién?» or «¿Dices que …?», instead of answering it. Do not say you don't "
            "understand.]")


def has_word(fragment: str, words: frozenset[str]) -> bool:
    """`fragment` contains one of `words` (whole words, case and accents aside)."""
    plain = {_norm(w) for w in words}
    return any(w in plain for w in _norm(fragment).split())


class Session:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.id = uuid.uuid4().hex[:12]
        self.lesson = LESSONS["intro"]
        self.history: list[dict] = []
        self.task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        self.turn_no = 0
        self.director: Director | None = None
        self.last_reply = ""  # what Sofía said last: context for transcribing the learner's answer
        self.voice = E.tts.default
        self.level = current_settings()["level"]
        self.system = build_system(self.lesson, self.level, tip=_tips())
        self.help_level = 0
        self.pending_attempt: dict | None = None  # {"data": the attempt message, "at": time.time()}
        self.continuing: dict | None = None  # {"prev": samples}: the next binary frame continues the open turn
        self.reopening = False  # cancelling a turn that a continuation replaces: its cut answer gets no analysis
        self.open_turn: OpenTurn | None = None  # the latest learner turn (see OpenTurn)
        self.answered_cmd = False  # the latest utterance was a voice command (answered instantly, no turn)
        self.side_tasks: set[asyncio.Task] = set()
        self.closed = False  # the socket is gone: sends become no-ops (background analysis may still finish)
        self.chat_idle = asyncio.Event()  # no reply is streaming: background LLM calls may run
        self.chat_idle.set()
        self.check_tasks: set[asyncio.Task] = set()  # background LLM calls in flight
        self.yielded: set[asyncio.Task] = set()  # ...that were cancelled for a new reply (retry them)
        self.analysis_lock = asyncio.Lock()
        self.cancel_played: int | None = None  # from {"type":"cancel","played":N}
        self.unanswered: TurnRecord | None = None  # a learner turn cut off before any of Sofía's answer was heard
        self.leaving = False  # the learner is switching lessons: the old lesson's bubbles are gone, send no fixes
        self.last_commit: Commit | None = None  # the latest reply written to the history (for {"type":"heard"})
        self.pending_heard: dict[str, int] = {}  # "heard" that came before its reply was written
        # Spanish the learner saw or heard since their last utterance (replies, hints, coach notes, word cards).
        # Words from it that they say back are echoes: they don't count as recalled.
        self.recent_shown: list[str] = []
        self.fixes_shown: list[str] = []  # normalized right forms of the fix cards sent in this lesson
        # Memory for the per-turn note (reply.memory_note), keyed by tutor turn id so a cut or dropped reply can be
        # corrected: the questions Sofía asked (as heard) and what the learner told her (learner_en).
        self.asked: list[tuple[str, str]] = []
        self.told: list[tuple[str, str]] = []
        self.said: list[tuple[str, str]] = []  # what Sofía said about herself (reply.self_statements)
        self.system_hash: str | None = None  # of the session's system prompt: it must not change between turns
        self.clarified: list[bool] = []  # per learner turn: did Sofía ask them to repeat (unclear audio)?

    def shown(self, text: str) -> None:
        if text:
            self.recent_shown.append(text)

    def remember(self, rec: TurnRecord) -> None:
        """Record what this committed reply asked (only the part that was heard) and what the learner told her."""
        self.forget(rec.tid)
        self.asked += [(rec.tid, q) for q in reply_questions(rec.reply, rec.user_text or "")]
        if rec.learner_en.strip():
            self.told.append((rec.tid, rec.learner_en.strip()))
        self.said += [(rec.tid, x) for x in self_statements(rec.reply)]
        self.asked, self.told, self.said = self.asked[-30:], self.told[-30:], self.said[-30:]

    def forget(self, tid: str) -> None:
        self.asked = [a for a in self.asked if a[0] != tid]
        self.told = [t for t in self.told if t[0] != tid]
        self.said = [t for t in self.said if t[0] != tid]

    @property
    def speed(self) -> float:
        return LEVELS.get(self.level, LEVELS["A1"])["speed"]

    def side(self, coro) -> None:
        """Run something that must not cancel (or be cancelled by) the conversation turn."""
        t = asyncio.create_task(self._guard(coro))
        self.side_tasks.add(t)
        t.add_done_callback(self.side_tasks.discard)

    async def synth_b64(self, text: str, speed: float | None = None) -> str:
        wav = await asyncio.get_running_loop().run_in_executor(TTS_POOL, E.tts.synth, text, self.voice,
                                                               speed or self.speed)
        return base64.b64encode(wav).decode()

    async def send(self, **msg) -> bool:
        """Never raises: a background task finishing after a disconnect just gets False."""
        if self.closed:
            return False
        text = json.dumps(msg, ensure_ascii=False)
        try:
            async with self.send_lock:
                await self.ws.send_text(text)
            return True
        except Exception as e:
            self.closed = True
            log.debug("socket closed, dropping %s and later sends: %r", msg.get("type"), e)
            return False

    def _push_user(self, content: str) -> tuple[dict, str | None]:
        # App notes ([English detected], [Lesson step]) only matter for the next reply. Strip them from older
        # messages so the history reads as a clean conversation (only the latest message changes, so the
        # prompt cache for everything before it stays valid).
        for m in self.history:
            if m["role"] == "user" and "\n[" in m["content"]:
                m["content"] = "\n".join(line for line in m["content"].split("\n") if not line.startswith("["))
        if self.history and self.history[-1]["role"] == "user":  # previous turn was interrupted
            msg, prev = self.history[-1], self.history[-1]["content"]
            msg["content"] += "\n" + content
        else:
            msg, prev = {"role": "user", "content": content}, None
            self.history.append(msg)
        # Trim in big chunks: every trim changes the prompt prefix and costs a full re-prefill (~3-5 s),
        # so do it rarely instead of dropping one message per turn.
        max_msgs = config.history_turns() * 2
        if len(self.history) > max_msgs:
            self.history = self.history[-(max_msgs // 2):]
            while self.history and self.history[0]["role"] != "user":
                self.history.pop(0)
        return msg, prev

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass

    def run(self, coro) -> None:
        self.task = asyncio.create_task(self._guard(coro))

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # surface to UI instead of silently dying
            log.exception("turn failed")
            await self.send(type="error", message=str(e))
            await self.send(type="status", state="idle")

    # ---------- turns ----------
    async def start(self, lesson_id: str, mode: str = "easy") -> None:
        self.leaving = False
        self.last_commit, self.pending_heard = None, {}
        self.open_turn = None
        custom = scenario.get(E.vocab.db, lesson_id) if lesson_id.startswith("sc-") else None
        self.mode = "real" if mode == "real" else "easy"
        self.lesson = scenario.for_run(custom, self.mode) if custom else LESSONS.get(lesson_id, LESSONS["intro"])
        self.history = []
        self.flush_unanswered()
        self.fixes_shown = []
        self.asked, self.told, self.said, self.system_hash = [], [], [], None
        self.id = uuid.uuid4().hex[:12]
        self.director = Director(self.lesson, E.vocab)
        self.level = current_settings()["level"]
        focus = [f"{p['label']} ({p['tip']})" for p in progress_payload()["patterns"] if p["count"] >= 2][:2]
        self.system = build_system(self.lesson, self.level, focus, tip=_tips())  # fixed for the session: keeps the prompt cache
        await self.send(type="lesson_state", **self.director.state())
        step = self.director.start_note()
        self._push_user(step.note)
        await self.respond(t_end=time.time(), user_turn=None, user_text=None, english=[], intro=step.intro,
                           coach=step.coach)

    async def set_voice(self, voice_id: str, sample: bool = True) -> None:
        self.voice = E.tts.voice(voice_id).id
        if not sample:
            return
        await self.send(type="audio", id="sample", seq=0, text="",
                        wav=await self.synth_b64("¡Hola! Soy Sofía. ¿Te gusta esta voz?"))

    async def speak(self, text: str, slow: bool = False) -> None:
        await self.send(type="audio", id="say", seq=0, text=text,
                        wav=await self.synth_b64(text, self.speed * (0.75 if slow else 1)))

    def update_settings(self, data: dict) -> None:
        if data.get("level") in LEVELS:
            self.level = data["level"]  # speech speed changes now; the prompt with the next lesson
            E.vocab.set_setting("level", self.level)
        if data.get("goal_minutes"):
            try:
                E.vocab.set_setting("goal_minutes", str(float(data["goal_minutes"])))
            except (TypeError, ValueError):
                pass
        if data.get("goal_sentences"):
            try:
                n = int(float(data["goal_sentences"]))
            except (TypeError, ValueError):
                n = 0
            if 1 <= n <= 200:
                E.vocab.set_setting("goal_sentences", str(n))
        if data.get("subtitles") in ("always", "after", "tap"):
            E.vocab.set_setting("subtitles", data["subtitles"])

    async def ask_teacher(self, audio: np.ndarray | None, a: dict) -> None:
        """A question about a card ("is it still OK to say…?"), by voice (`audio`) or typed (a["text"]). The answer
        is in English, shown on the card and read aloud: English with an English voice, «Spanish» with Sofía's."""
        key, n = a.get("key"), a.get("n")
        card, said = str(a.get("card") or "")[:600], str(a.get("said") or "")[:300]
        thread = a.get("thread") if isinstance(a.get("thread"), list) else []
        question = str(a.get("text") or "").strip()
        if audio is not None:
            tr = await asyncio.get_running_loop().run_in_executor(
                STT_POOL, E.stt.transcribe, audio, f"A question to a Spanish teacher about: {card[:120]}")
            question = tr.text.strip() if tr else ""
        if not question:
            await self.send(type="teacher_answer", key=key, n=n, question="", answer="", clips=[],
                            error="I didn't catch the question — try again.")
            return
        await self.send(type="teacher_question", key=key, n=n, question=question)
        try:
            answer = await E.tutor.llm.chat_text(teacher_messages(card, said, thread, question, self.level),
                                                 max_tokens=400, timeout=40, temperature=0.4)
        except Exception as e:  # noqa: BLE001 - shown to the learner, the lesson goes on
            log.warning("teacher answer failed: %s", e)
            await self.send(type="teacher_answer", key=key, n=n, question=question, answer="", clips=[],
                            error="The teacher couldn't answer right now — try again.")
            return
        answer = re.sub(r"[*_#`]", "", answer).strip()
        clips: list[str] = []
        loop = asyncio.get_running_loop()
        for i, part in enumerate(re.split(r"«([^»]+)»", answer)):
            if not part.strip():
                continue
            if i % 2:  # «Spanish»: Sofía's voice
                clips.append(await self.synth_b64(part, speed=0.9))
            else:
                wav = await loop.run_in_executor(TTS_POOL, E.tts.synth_english, part)
                if wav:
                    clips.append(base64.b64encode(wav).decode())
        await self.send(type="teacher_answer", key=key, n=n, question=question, answer=answer, clips=clips)

    async def attempt(self, audio: np.ndarray, a: dict) -> None:
        """A graded spoken attempt (review drill, saying a correction, shadowing a sentence)."""
        if a.get("kind") == "ask":
            await self.ask_teacher(audio, a)
            return
        kind, target = a.get("kind", "shadow"), str(a.get("target", ""))
        # No tutor context here: biasing Whisper toward the target would make it "hear" the right answer.
        context = "Una palabra o frase en español." if kind in ("review", "fix") else None
        tr = await asyncio.get_running_loop().run_in_executor(STT_POOL, E.stt.transcribe, audio, context)
        heard = tr.text if tr else ""
        if tr:
            E.vocab.record_spoken(self.id, tr.duration)
            E.vocab.record_practice(self.id, "review" if kind in ("review", "fix") else "attempt")
        g = grade(target, heard)
        ok = g["score"] >= 0.8
        note = None
        if kind == "review" and a.get("lemma"):
            lemma = str(a["lemma"])
            heard_lemmas = {t.lemma for t in E.vocab.tokens(heard, prefer={lemma})}
            # The bare word counts too ("Cuenta." for «la cuenta»), accents aside: the article alone shouldn't fail it.
            plain = {_strip_accents(w) for w in re.findall(r"\w+", heard.lower())}
            ok = lemma in heard_lemmas or _strip_accents(lemma.lower()) in plain or g["score"] >= 0.6
            if ok and (said := article_mismatch(target, heard)):  # "la perro" for «el perro»
                ok, note = False, f"«{target}», not «{said}»"
            elif not ok:
                # Another word with the same meaning (vez for «el tiempo», both "time") is right too.
                alts = E.vocab.alts(lemma, E.vocab.translation(lemma))
                if alts and (set(alts) & heard_lemmas or {_strip_accents(w) for w in alts} & plain):
                    ok, note = True, f"also right — today we're practising «{target}»"
            E.vocab.record_review(self.id, a["lemma"], ok)
        elif kind == "fix" and _is_int(a.get("fix_id")):
            fix = E.vocab.record_fix_review(a["fix_id"], ok, session_id=self.id)
            if fix and not ok and fix.get("why"):
                note = fix["why"]
        elif kind == "repair" and ok:
            E.vocab._event(self.id, None, "repaired", target)
            E.vocab.db.commit()
        await self.send(type="attempt_result", kind=kind, key=a.get("key"), n=a.get("n"), target=target, heard=heard,
                        ok=ok, score=g["score"], words=g["words"], note=note, wav=await self.synth_b64(target))
        await self.send(type="progress", **progress_payload(self.id))

    def flush_unanswered(self) -> None:
        """A learner turn cut off before Sofía was heard, with no next message to merge it into (finish, new
        lesson, disconnect): check it on its own, so it still gets its feedback and its DB row."""
        rec, self.unanswered = self.unanswered, None
        if rec is not None:
            self.side(self._analyze(rec))

    async def finish(self) -> None:
        if not self.director:
            return
        self.open_turn = None
        self.flush_unanswered()
        self.director.finish()
        step = self.director.instruction()
        self._push_user(f"[The learner pressed 'Finish lesson'. learner_en is empty.]\n{step.note}")
        await self.respond(t_end=time.time(), user_turn=None, user_text=None, english=[], recap=True,
                           coach=step.coach)

    async def help(self) -> None:
        """Hint ladder: each press gives more (key word -> sentence starter -> full example). Resets when the
        learner speaks. Doesn't touch the conversation history."""
        self.help_level += 1
        self.open_turn = None
        await self.send(type="status", state="thinking")
        words = [t.display for t in self.director.targets if t.state not in ("used", "known")] if self.director else []
        h = await E.tutor.hint(self.last_reply, self.help_level, self.lesson, words)
        label = {1: "Key word", 2: "Start with", 3: "You could say"}[min(self.help_level, 3)]
        text = f"{label}: «{h['spanish']}» — {h['english']}" if h.get("spanish") else "Try answering with a few words."
        self.shown(h.get("spanish", ""))
        await self.send(type="hint", level=min(self.help_level, 3), text=text, spanish=h.get("spanish", ""))
        await self.send(type="status", state="idle")

    async def utterance(self, audio: np.ndarray, continuation: OpenTurn | None = None, prev_s: float = 0.0,
                        cont_n: int | None = None) -> None:
        """`continuation`: `audio` is that turn's audio plus what the learner said after it (`prev_s` seconds of it
        were the first part, already counted as spoken time). `cont_n`: the client's number for the "continue"
        this audio came with (echoed as `cont` in the user / ignored message, even when it became a new turn)."""
        tag = {"cont": cont_n} if cont_n is not None else {}
        if continuation is None:
            self.open_turn, self.answered_cmd = None, False  # a new turn: the last one can't be continued now
        else:  # until learner_said gets to it, the learner can go on talking and still continue this turn
            self.open_turn = dc_replace(continuation, msg=None, prev_content=None, merged=None, clarified=False,
                                        intro=[])
        t_end = time.time()
        await self.send(type="status", state="transcribing")
        loop = asyncio.get_running_loop()
        tr = await loop.run_in_executor(STT_POOL, E.stt.transcribe, audio, self.last_reply)
        stt_ms = int((time.time() - t_end) * 1000)
        if tr is None:
            if continuation is not None:  # (its first part had speech: this shouldn't happen)
                log.warning("turn %s: no speech in its continued audio", continuation.user_turn)
                self.open_turn = None
                await self.send(type="feedback", id=continuation.user_turn, mistakes=[], teach=[], words={},
                                unchecked=True)  # its bubble mustn't stay at "checking…"
            await self.send(type="ignored", reason="no speech detected", **tag)
            await self.send(type="status", state="idle")
            return
        log.info("STT %dms [%s] avg_logprob %.2f %s%s%s", stt_ms, tr.language, tr.avg_logprob, tr.text,
                 f"  unclear: {', '.join(tr.unclear)}" if tr.unclear else "",
                 f"  (continues {continuation.user_turn})" if continuation is not None else "")
        E.vocab.record_spoken(self.id, max(0.0, tr.duration - prev_s))
        await self.learner_said(tr.text, tr.language, t_end, stt_ms, unclear=tr.unclear, avg_logprob=tr.avg_logprob,
                                continuation=continuation, cont_n=cont_n)

    async def learner_said(self, text: str, language: str, t_end: float, stt_ms: int = 0, *,
                           unclear: list[str] | None = None, avg_logprob: float = 0.0,
                           continuation: OpenTurn | None = None, cont_n: int | None = None) -> None:
        """`unclear`: words the speech recognizer wasn't sure of (typed text has none). `avg_logprob`: Whisper's.
        `continuation`: the learner went on talking before hearing any of Sofía's answer to that turn, and `text`
        is the whole thing. Session.reopen() has already taken the turn (and her unheard answer) back out of the
        history: it keeps its id, its bubble is replaced, and the director doesn't count a new turn. `cont_n`: see
        utterance()."""
        tag = {"cont": cont_n} if cont_n is not None else {}
        if continuation is None:
            self.turn_no += 1
            turn_id = f"u{self.turn_no}"
            self.open_turn = None
            # Word-level detection, not Whisper's language tag: accented Spanish sometimes gets tagged "en".
            cmd = command(text)
            if cmd and await self.voice_command(turn_id, text, *cmd, tag=tag):
                self.answered_cmd = True
                return
            E.vocab.record_practice(self.id, "turn")  # the daily goal (a continuation's turn was counted already)
        else:
            turn_id = continuation.user_turn
        # Targets still being worked on: "Trabajo como…" is trabajar while trabajar is a target (states before
        # this turn's observe).
        prefer = frozenset(t.lemma for t in self.director.targets
                           if t.state in ("pending", "introduced", "practiced")) if self.director else frozenset()
        # What they saw or heard before saying this (a voice command above keeps it: nothing new was said). A
        # continuation keeps its first part's: what was shown since is her answer they talked over.
        echo = continuation.echo if continuation is not None else \
            frozenset(t.lemma for t in E.vocab.tokens(" ".join(self.recent_shown), prefer=prefer) if t.in_list)
        self.recent_shown = []
        o = self.open_turn = OpenTurn(turn_id, echo=echo, prev_reply=self.last_reply)
        self.help_level = 0
        english = english_spans(text)
        unclear = list(unclear or [])
        skip = frozenset(w.lower() for w in unclear)  # maybe misheard: never a fix, never counted as said
        # Too unclear to answer: Sofía asks them to repeat that part, like a real person would. At most one in a while.
        clarify = ((len(unclear) >= CLARIFY_MIN_UNCLEAR or avg_logprob < CLARIFY_LOGPROB)
                   and not any(self.clarified[-CLARIFY_GAP:]))
        await self.send(type="user", id=turn_id, text=text, language=language, english=english, stt_ms=stt_ms,
                        unclear=unclear, **({"replace": True} if continuation is not None else {}), **tag)
        check_text = None
        if self.unanswered:  # the last turn was cut off before Sofía said anything: check both together
            old, self.unanswered = self.unanswered, None
            o.merged = old
            check_text = f"{old.user_text} {text}"
            echo |= old.echo
            prefer |= old.prefer
            skip |= old.unclear
            await self.send(type="feedback", id=old.user_turn, mistakes=[], teach=[], words={}, merged=True)
        note = (f"[English detected: {', '.join(repr(s) for s in english)}]" if english else "[English detected: none]")
        # Code-built memory, so she doesn't re-ask (user note only: the system prompt stays fixed for the session)
        memo = memory_note([q for _, q in self.asked], [t for _, t in self.told], [x for _, x in self.said])
        step, intro, coach, recap = "", [], "", False
        if self.director:
            # Lemmatizing is deterministic and instant, so the lesson can react to this turn before the LLM runs.
            en_words = {w.lower() for span in english for w in span.split()}
            said = set() if clarify else {
                t.lemma for t in E.vocab.tokens(text, prefer=prefer)
                if t.in_list and t.text.lower() not in en_words and t.text.lower() not in skip}
            if not clarify and (got := self.director.observe_text(text)):  # scenario phrases said on their own
                log.info("turn %s: phrases said: %s", turn_id, "; ".join(got))
                self.side(self.send(type="phrase_used", phrases=got))
            self.director.observe(said, echoed=echo, continuation=continuation is not None and continuation.observed)
            o.observed = True
            recap = self.director.phase == "recap"
            if recap or not clarify:  # a clarification turn has no lesson step: the lesson waits a turn
                step, intro, coach = self.director.instruction()
        asked = clarify and not recap
        self.clarified.append(asked)
        o.clarified, o.intro = True, list(intro)
        if asked:
            step = clarify_note(unclear)
            log.info("turn %s: unclear speech (avg_logprob %.2f, unclear: %s): Sofía asks to repeat — "
                     "clarification %d of %d learner turns in this session", turn_id, avg_logprob,
                     ", ".join(unclear) or "-", sum(self.clarified), len(self.clarified))
        o.msg, o.prev_content = self._push_user("\n".join(x for x in (text, note, memo, step) if x))
        if continuation is not None:
            log.info("turn %s continued: %s", turn_id, text)
        await self.respond(t_end=t_end, user_turn=turn_id, user_text=text, english=english, intro=intro, recap=recap,
                           check_text=check_text, echo=echo, prefer=prefer, coach=coach, unclear=skip,
                           clarify=clarify)

    async def voice_command(self, turn_id: str, text: str, kind: str, word: str, tag: dict | None = None) -> bool:
        """Answered instantly, without the LLM and without entering the conversation history."""
        tag = tag or {}
        if kind == "meaning":
            answer = E.vocab.meaning(word)
            if not answer:
                return False  # unknown word: let Sofía answer it in the conversation
            await self.send(type="user", id=turn_id, text=text, language="es", english=[], stt_ms=0, command=kind,
                            **tag)
            self.shown(word)
            await self.send(type="hint", level=0, text=answer)
        else:
            await self.send(type="user", id=turn_id, text=text, language="es", english=[], stt_ms=0, command=kind,
                            **tag)
            await self.send(type="replay", slow=kind == "slower")
        await self.send(type="status", state="idle")
        return True

    async def respond(
        self,
        t_end: float,
        user_turn: str | None,
        user_text: str | None,
        english: list[str],
        intro: list[Target] | None = None,
        recap: bool = False,
        check_text: str | None = None,
        echo: frozenset[str] = frozenset(),
        prefer: frozenset[str] = frozenset(),
        coach: str = "",
        unclear: frozenset[str] = frozenset(),
        clarify: bool = False,
    ) -> None:
        """PART A of a turn (cancellable): stream the reply into TTS, commit it, play it. The analysis (PART B)
        runs in _analyze, in the background. `coach`: the director's teacher card for this turn ("" = none)."""
        self.turn_no += 1
        tid = f"t{self.turn_no}"
        self.cancel_played = None
        await self.send(type="status", state="thinking")
        # A new word may wait for a natural opening: its word card and example card are only sent once the reply
        # is final and really uses the word (saw_reply).
        # With a cloud model the reply call writes its own card (a sample answer to her question): the director's
        # card is then only a fallback, decided once the reply is final.
        tips = _tips()
        intro_coach = coach if intro and not tips else ""
        if coach and not intro and not tips:
            await self.send(type="coach", id=tid, text=coach)
            # Its «Spanish examples» (the rest is English, which could match Spanish words: come, son, red)
            self.shown(" ".join(re.findall(r"«([^»]*)»", coach)) or coach)
        system = self.system
        h = hashlib.sha1(system.encode()).hexdigest()[:12]
        if self.system_hash not in (None, h):
            log.warning("turn %s: system prompt changed mid-session (%s → %s): prompt cache lost", tid,
                        self.system_hash, h)
        self.system_hash = h
        log.debug("turn %s system prompt %s", tid, h)
        reply_f, learner_f, better_f = FieldStream("reply"), FieldStream("learner_en"), FieldStream("better")
        own_text = user_text
        # A question that says the learner's own words back is a recast, not a second question: it is spoken.
        heard_text = (check_text if check_text is not None else user_text) or ""
        splitter, gate = SentenceSplitter(), OneQuestion(heard_text)
        # Praise of a mistake, a parroted first sentence, REPLY_LINT: fixed before TTS, so the history holds what
        # was actually spoken.
        guard = ReplyGuard(heard_text, tid)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        timing: dict[str, int] = {}
        spoken: list[str] = []  # sentences of the reply, as written
        audio_sent: list[str] = []  # sentences whose audio reached the client (index == audio seq)
        first_audio = asyncio.Event()
        audio_finished = False
        loop = asyncio.get_running_loop()
        d = self.director
        upgraded = dict(d._upgraded) if d else {}
        prev_reply = self.last_reply
        if check_text is not None:
            user_text, english = check_text, english_spans(check_text)

        def ms() -> int:
            return int((time.time() - t_end) * 1000)

        async def speaker() -> None:
            nonlocal audio_finished
            seq = 0
            try:
                while (sentence := await queue.get()) is not None:
                    wav = await loop.run_in_executor(TTS_POOL, E.tts.synth, sentence, self.voice, self.speed)
                    if not wav:
                        continue
                    timing.setdefault("first_audio_ms", ms())
                    if await self.send(type="audio", id=tid, seq=seq, text=sentence,
                                       wav=base64.b64encode(wav).decode()):
                        audio_sent.append(sentence)
                    first_audio.set()
                    seq += 1
            finally:
                first_audio.set()
            # All of the reply's audio is out (the queue closes when the reply field ends): it's the learner's
            # turn now, even if the model is still closing the JSON.
            audio_finished = True
            timing["audio_done_ms"] = ms()
            await self.send(type="status", state="idle")

        async def say(sentences: list[str]) -> None:
            for sentence in sentences:
                spoken.append(sentence)
                self.shown(sentence)  # on screen as it streams, even if the audio gets cut
                queue.put_nowait(sentence)
                await self.send(type="tutor_delta", id=tid, text=sentence + " ")

        def record(reply: str, learner_en: str) -> TurnRecord:
            return TurnRecord(tid=tid, user_turn=user_turn, user_text=user_text, english=english, reply=reply,
                              coach=coach, learner_en=learner_en, director=d, sid=self.id,
                              lesson_id=self.lesson["id"], upgraded=upgraded, recap=recap, t_end=t_end, echo=echo,
                              prefer=prefer, better=better,
                              own_text=own_text if check_text is not None else None, unclear=unclear,
                              clarify=clarify)

        def saw_reply(text: str) -> None:
            """The reply is final: the director checks that a new word was really said, and for an early goodbye."""
            if d is None:
                return
            nonlocal coach
            lemmas = {t.lemma for t in E.vocab.tokens(text, prefer=frozenset(t.lemma for t in intro or []))}
            if missing := d.saw_reply(text, lemmas):
                log.info("turn %s intro waits for a better moment: %s", tid, ", ".join(missing))
            said_words = [t for t in intro or [] if t.lemma not in missing]
            if said_words:
                E.vocab.record_taught(self.id, [{"english": t.english, "spanish": t.display} for t in said_words])
                self.side(self.send(type="introduce", id=tid, words=[t.__dict__ for t in said_words]))
                self.shown(" ".join(t.display for t in said_words))
            if tips and intro and coach and not said_words:
                d.unsend(coach)  # the word waits: its example card may come with it later
                coach = ""
            if intro_coach:
                if said_words:
                    self.side(self.send(type="coach", id=tid, text=intro_coach))
                    self.shown(" ".join(re.findall(r"«([^»]*)»", intro_coach)) or intro_coach)
                else:
                    d.unsend(intro_coach)
                    coach = ""
            if d.no_goodbye:
                log.info("turn %s early goodbye: the next note says the scene continues", tid)

        speak_task = asyncio.create_task(speaker())
        raw, learner_en, flushed = "", "", False
        better_parts: list[str] = []
        better: str | None = None  # known once its field is complete (it is written before the reply)
        rec: TurnRecord | None = None
        entry: dict | None = None
        try:
            # ---- conversation: learner_en -> better -> reply (spoken as it streams) ----
            self.chat_idle.clear()
            for t in self.check_tasks:  # a background check would slow this reply down: it retries later
                self.yielded.add(t)
                t.cancel()
            try:
                deadline = time.time() + TURN_TIMEOUT_S
                async for chunk in E.tutor.stream(system, self.history):
                    if time.time() > deadline:
                        log.warning("turn %s: generation timed out, using what was produced", tid)
                        break
                    timing.setdefault("llm_first_token_ms", ms())
                    raw += chunk
                    learner_en += learner_f.feed(chunk)
                    if user_turn and learner_f.done and "user_en_ms" not in timing:
                        timing["user_en_ms"] = ms()  # show the translation of what they said right away
                        await self.send(type="user_en", id=user_turn, text=learner_en)
                    better_parts.append(better_f.feed(chunk))
                    if better_f.done and better is None:
                        better = guard.better = "".join(better_parts).strip()
                    delta = reply_f.feed(chunk)
                    if reply_f.pos is not None:
                        timing.setdefault("reply_start_ms", ms())
                    if delta:
                        await say(gate.feed(guard.feed(splitter.feed(delta))))
                    if reply_f.done and not flushed:
                        flushed = True
                        await say(gate.feed(guard.feed(splitter.flush()) + guard.flush()) + gate.flush())
                        queue.put_nowait(None)  # no more audio: idle can go out while the JSON closes
            finally:
                self.chat_idle.set()
            if not flushed:
                await say(gate.feed(guard.feed(splitter.flush()) + guard.flush()) + gate.flush())
                queue.put_nowait(None)
            reply = " ".join(spoken)
            tip_es = tip_en = ""
            try:
                parsed = json.loads(raw)
                if isinstance(parsed.get("better"), str):
                    better = parsed["better"].strip()
                if tips and "?" in reply and isinstance(parsed.get("tip_es"), str):
                    tip_es = parsed["tip_es"].strip().strip("«»\"")
                    tip_en = parsed.get("tip_en").strip() if isinstance(parsed.get("tip_en"), str) else ""
            except (json.JSONDecodeError, AttributeError):  # truncated (runaway / timeout): what was spoken still stands
                log.warning("bad JSON from model (%d chars): %s", len(raw), raw[:300])
            timing["llm_done_ms"] = ms()

            # ---- the reply is written: commit it now, so an interruption can't lose it ----
            rec = record(reply, learner_en)
            entry = {"role": "assistant", "content": rec.history_entry()}
            self.history.append(entry)
            self.last_reply = reply
            self.last_commit = Commit(rec, entry, audio_sent)
            self.remember(rec)
            saw_reply(reply)
            if tips:
                if tip_es:
                    if coach and d is not None:
                        d.unsend(coach)  # the lesson example wasn't shown: it can come another time
                    coach = f"Try: «{tip_es}»" + (f" — {tip_en}" if tip_en else "")
                if coach:
                    self.side(self.send(type="coach", id=tid, text=coach))
                    self.shown(" ".join(re.findall(r"«([^»]*)»", coach)) or coach)
            rec.coach = coach
            if (p := self.pending_heard.pop(tid, None)) is not None:  # playback was stopped while she was writing
                self.heard(tid, p, notify=False)
            self.side(self._analyze(rec))
            await first_audio.wait()  # usually already there; keeps first_audio_ms in tutor_done's timing
            await self.send(type="tutor_done", id=tid, reply=rec.reply, reply_en="", coach=coach,  # maybe cut
                            glosses=E.vocab.glosses(rec.reply), timing=timing)
            await speak_task  # it sends status idle
            log.info("turn %s timing %s", tid, timing)
        except asyncio.CancelledError:
            speak_task.cancel()
            self.pending_heard.pop(tid, None)
            played = len(audio_sent) if self.cancel_played is None else max(0, min(self.cancel_played, len(audio_sent)))
            # All of it was heard (cut while the model was closing the JSON): no "…".
            whole = audio_finished and played == len(audio_sent)
            heard = " ".join(audio_sent[:played]) + ("" if whole else " …") if played else ""
            said = _norm(heard)
            for t in intro or []:  # cut before the new word was spoken: introduce it again next turn
                if not said or not any(w and w in said for w in (_norm(t.display), _norm(t.lemma))):
                    t.state, t.intro_turn = "pending", None
            log.info("turn %s interrupted: %d/%d sentences heard", tid, played, len(spoken))
            # A goodbye always gets its recap, even when nothing of it was heard. Not when the learner is still
            # talking (a continuation): the lesson step, the recap included, is redone for the whole message.
            recap_kept = recap and not self.reopening
            if rec is None:  # cut off while the reply was still being written
                if played or recap_kept:
                    rec = record(heard, learner_en)
                    if played:
                        entry = {"role": "assistant", "content": rec.history_entry()}
                        self.history.append(entry)
                        self.last_reply = heard
                        self.last_commit = Commit(rec, entry, audio_sent)
                        self.remember(rec)
                        saw_reply(heard)
                        rec.coach = coach
                    self.side(self._analyze(rec))
                elif user_turn and user_text is not None:
                    self.unanswered = record("", learner_en)
            elif entry is not None and not whole and played < len(spoken):  # committed, not all of it heard
                if played or recap_kept:
                    rec.reply = heard
                    if played:
                        entry["content"] = rec.history_entry()
                        self.last_reply = heard
                        self.remember(rec)
                    else:
                        self._drop_entry(entry)
                        self.forget(tid)
                        self.last_reply = prev_reply
                        self.last_commit = None
                else:
                    rec.dropped = True
                    self._drop_entry(entry)
                    self.forget(tid)
                    self.last_reply = prev_reply
                    self.last_commit = None
                    if user_turn and user_text is not None:
                        self.unanswered = record("", learner_en)
            if coach and d is not None and not played:  # its bubble is taken off the screen: the card can come again
                d.unsend(coach)
            if self.leaving:  # a new lesson is starting: its chat is empty, don't put this reply into it
                pass
            elif not played:  # nothing of it was heard: take the streamed text off the screen
                self.side(self.send(type="tutor_dropped", id=tid))
            elif rec is not None and rec.reply and not rec.dropped:  # the bubble shows what she actually said
                self.side(self.send(type="tutor_done", id=tid, reply=rec.reply, reply_en="", coach=rec.coach,
                                    glosses=E.vocab.glosses(rec.reply), timing=timing))
            raise
        except Exception:
            speak_task.cancel()
            raise

    def heard(self, tid: str, played: int, notify: bool = True) -> None:
        """The learner stopped the playback of a reply that is already written (the server was idle, so there
        was nothing to cancel): keep only the sentences they heard in the history (and the DB, if the analysis
        hasn't written it yet). played == 0 is ignored: the reply stays."""
        c = self.last_commit
        if c is None or c.rec.tid != tid:
            if self.task and not self.task.done() and tid == f"t{self.turn_no}":
                self.pending_heard[tid] = played  # still being written: cut it once it's committed
            return
        if not 0 < played < len(c.audio_sent) or c.rec.dropped:
            return
        heard = " ".join(c.audio_sent[:played]) + " …"
        if c.rec.reply == heard:
            return
        log.info("turn %s playback stopped: %d/%d sentences heard", tid, played, len(c.audio_sent))
        if self.last_reply == c.rec.reply:
            self.last_reply = heard
        c.rec.reply = heard
        c.entry["content"] = c.rec.history_entry()
        self.remember(c.rec)
        if notify:  # the bubble shows what she actually said
            self.side(self.send(type="tutor_done", id=tid, reply=heard, reply_en="", coach=c.rec.coach,
                                glosses=E.vocab.glosses(heard)))

    def reopen(self) -> OpenTurn | None:
        """{"type":"continue"} (after the running turn was cancelled with played=0): the learner went on talking
        before hearing any of Sofía's answer to their last message. Take that message back out of the history,
        with her answer if it was already written, so the whole utterance replaces it under the same id.
        None if there is no such turn (e.g. it was still being transcribed): the audio is then a new turn."""
        o, self.open_turn = self.open_turn, None
        if o is None:
            return None
        c = None
        if o.msg is not None:
            i = next((k for k, m in enumerate(self.history) if m is o.msg), None)
            after = self.history[i + 1:] if i is not None else []
            if i is None or any(m["role"] == "user" for m in after) or (
                    after and (len(after) != 1 or self.last_commit is None or self.last_commit.entry is not after[0])):
                log.warning("turn %s: can't be continued (the history moved on)", o.user_turn)
                return None
            c = self.last_commit if after else None
        if c is not None:  # her answer was written (and its audio sent), but they were talking over it
            rec = c.rec
            rec.dropped = True  # its analysis, if it hasn't run yet, never will
            self._drop_entry(c.entry)
            self.forget(rec.tid)
            self.last_reply, self.last_commit = o.prev_reply, None
            if rec.director is not None:
                if rec.coach:
                    rec.director.unsend(rec.coach)
                for t in o.intro:  # not heard: introduce it again
                    if t.state == "introduced" and t.intro_turn == rec.director.turn:
                        t.state, t.intro_turn = "pending", None
            if not self.leaving:
                self.side(self.send(type="tutor_dropped", id=rec.tid))
            log.info("turn %s dropped: the learner went on talking before hearing it", rec.tid)
        if o.msg is not None:
            if o.prev_content is None:
                self._drop_entry(o.msg)
            else:
                o.msg["content"] = o.prev_content
        if self.unanswered is not None and self.unanswered.user_turn == o.user_turn:
            self.unanswered = None  # left unanswered by the cut: the continuation is answered instead
        if o.merged is not None:
            self.unanswered = o.merged  # merged into the continuation again
        if o.clarified and self.clarified:
            self.clarified.pop()
        return o

    def _drop_entry(self, entry: dict) -> None:
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i] is entry:
                del self.history[i]
                return

    async def _llm_when_idle(self, make_coro: Callable[[], Awaitable[Any]], tid: str) -> Any:
        """Run a background LLM call only while no reply is streaming. A reply that starts meanwhile cancels it
        (so the learner never waits behind a check), and it is retried afterwards, as often as it takes: every
        retry needs a new reply to have started, so it ends once the learner pauses (or leaves). (Letting a late
        try run alongside the next reply instead delayed that reply's first word by ~3 s.)
        None only if the call itself failed."""
        yields = 0
        while True:
            await self.chat_idle.wait()
            t = asyncio.create_task(make_coro())
            self.check_tasks.add(t)
            try:
                await asyncio.wait({t})
            finally:
                self.check_tasks.discard(t)
            if t.cancelled():
                if t in self.yielded:
                    self.yielded.discard(t)
                    yields += 1
                    log.log(logging.WARNING if yields == ANALYSIS_TRIES else logging.INFO,
                            "turn %s: analysis yielded to a new reply (%d), retrying", tid, yields)
                    continue
                return None
            try:
                return t.result()
            except Exception as e:
                log.warning("turn %s: analysis LLM call failed: %r", tid, e)
                return None

    @staticmethod
    def _fix_pairs(rec: TurnRecord) -> list[dict]:
        """The fix cards' wrong/right: a word diff of what they said against `better`. For a merged turn, `better`
        may cover only the newest message: diff whichever text it is closer to."""
        better = rec.better or ""
        learner = rec.user_text or ""
        if better and rec.own_text and rec.own_text != learner:
            def close(t: str) -> float:
                return SequenceMatcher(None, _norm(t).split(), _norm(better).split(), autojunk=False).ratio()
            if close(rec.own_text) > close(learner):
                learner = rec.own_text
        return diff_fixes(learner, better, english_spans(learner))

    async def _analyze(self, rec: TurnRecord) -> None:
        """PART B of a turn: never cancelled from outside, one at a time. All DB writes happen before any send,
        so a closed socket can't lose them."""
        try:
            async with self.analysis_lock:
                if rec.dropped:
                    return
                t0 = time.time()
                learner = rec.user_text
                pairs: list[dict] = []
                call = "translate"
                if learner is not None and rec.better is not None and not rec.clarify:
                    pairs = self._fix_pairs(rec)
                    if rec.unclear and (kept := [p for p in pairs if not has_word(p["wrong"], rec.unclear)]) != pairs:
                        # Maybe Whisper misheard it: that is not the learner's mistake.
                        log.info("turn %s: %d fix(es) dropped: built on unclear audio (%s)", rec.tid,
                                 len(pairs) - len(kept), ", ".join(sorted(rec.unclear)))
                        pairs = kept
                if learner is None or rec.clarify or (rec.better is not None and not pairs and not rec.english):
                    # Start / goodbye, a message that was right with no English in it (nothing to explain), or one
                    # too unclear to grade (Sofía asked them to repeat it).
                    if learner is not None:
                        call = "translate (unclear speech, not graded)" if rec.clarify else \
                            "translate (said right, no check)"
                    reply_en = (await self._llm_when_idle(lambda: E.tutor.translate(rec.reply), rec.tid) or "") \
                        if rec.reply else ""
                    check: dict = {"explanations": [], "teach": []} if learner is not None else {}
                elif rec.better is None:
                    # The reply's JSON had no usable "better": nothing is known about the message.
                    call = "translate (no better: unchecked)"
                    reply_en = (await self._llm_when_idle(lambda: E.tutor.translate(rec.reply), rec.tid) or "") \
                        if rec.reply else ""
                    check = {}
                else:
                    call = "check"
                    got = await self._llm_when_idle(
                        lambda: E.tutor.check(learner, rec.english, rec.reply, pairs, sorted(rec.unclear)), rec.tid)
                    check = got if isinstance(got, dict) else {}
                    reply_en = str(check.get("reply_en") or "")
                if rec.dropped:
                    return
                llm_ms = int((time.time() - t0) * 1000)
                # No usable check (the call failed, its JSON was broken, or the reply had no "better"): that is not
                # a clean turn. Don't count the words as said right, and don't show "Bien dicho".
                unchecked = learner is not None and not isinstance(check.get("explanations"), list)
                if unchecked:
                    log.warning("turn %s: no usable check, feedback marked unchecked", rec.tid)
                ok = learner is not None and not unchecked and not rec.clarify
                mistakes = clean_mistakes(build_mistakes(pairs, check.get("explanations")), rec.english,
                                          self.fixes_shown) if ok else []
                teach = clean_teach(check.get("teach") or [], learner, rec.english) if ok else []
                if ok and len(mistakes) < len(pairs):
                    log.info("turn %s: %d of %d fixes dropped by the filters", rec.tid, len(pairs) - len(mistakes),
                             len(pairs))

                # ---- DB first ----
                sid, d = rec.sid, rec.director
                taught = E.vocab.record_taught(sid, teach) if teach else []
                # The word bank shows the sentence as an example: the corrected one if this one had a mistake.
                outcome = E.vocab.record_learner(sid, learner, mistakes, rec.english, echo=rec.echo,
                                                 prefer=rec.prefer, example=rec.better if mistakes else None,
                                                 skip=rec.unclear) \
                    if ok else {}
                if mistakes:
                    E.vocab.record_mistakes(sid, mistakes)
                    E.vocab.add_fixes(mistakes)  # they come back in Repaso as "Say it right"
                if rec.reply:
                    E.vocab.record_heard(sid, rec.reply)
                summary, recap_coach = None, ""
                if d is not None:
                    d.correct({lemma for lemma, v in outcome.items() if v == "error"}, upgraded=rec.upgraded)
                    d.record_turn(mistakes, taught)
                    if rec.recap:
                        summary = d.recap()
                        E.vocab.save_lesson(sid, summary)
                        if d.lesson.get("custom"):
                            scenario.update_result(E.vocab.db, d.lesson["id"], d.lesson.get("mode", "easy"),
                                                   summary["phrases"])
                        # The recap's teacher card: built now, so it includes this last turn's check (the earlier
                        # turns' analyses ran before this one, under the lock).
                        recap_coach = d.recap_text()
                        if recap_coach:
                            rec.coach = recap_coach
                if learner:
                    meta = ({"english": rec.english, "mistakes": mistakes} | ({"unchecked": True} if unchecked else {})
                            | ({"unclear": sorted(rec.unclear)} if rec.unclear else {})
                            | ({"clarify": True} if rec.clarify else {}))
                    E.vocab.save_turn(sid, rec.lesson_id, "learner", learner, meta, ts=rec.t_end)
                if rec.reply:
                    E.vocab.save_turn(sid, rec.lesson_id, "tutor", rec.reply,
                                      {"reply_en": reply_en, "coach": rec.coach}, ts=rec.t_end + 0.001)

                # ---- then the client ----
                if rec.user_turn:
                    self.fixes_shown += [_norm(m["right"]) for m in mistakes]
                    await self.send(type="feedback", id=rec.user_turn, mistakes=mistakes, teach=taught,
                                    words=outcome, learner_en=rec.learner_en, better=rec.better or "",
                                    **({"unchecked": True} if unchecked else {}),
                                    **({"clarify": True} if rec.clarify else {}))
                if reply_en and rec.reply:
                    await self.send(type="tutor_en", id=rec.tid, reply_en=reply_en)
                current = d is not None and d is self.director  # not if a new lesson started meanwhile
                if current:
                    await self.send(type="lesson_state", **d.state())
                if recap_coach and current and not self.leaving:
                    self.shown(" ".join(re.findall(r"«([^»]*)»", recap_coach)))
                    await self.send(type="coach", id=rec.tid, text=recap_coach)
                if summary is not None and current:
                    await self.send(type="recap", **summary)
                if sid == self.id:  # an old lesson's numbers would replace "This session" in the new one
                    await self.send(type="progress", **progress_payload(sid))
                await self.send(type="turn_done", id=rec.tid)
                log.info("turn %s analysis: %s %dms, %d mistakes, %d taught", rec.tid,
                         f"{call} (unchecked)" if unchecked and call == "check" else call, llm_ms,
                         len(mistakes), len(taught))
        except Exception:
            log.exception("analysis of turn %s failed", rec.tid)
            if rec.user_turn:  # don't leave the bubble at "checking…"
                await self.send(type="feedback", id=rec.user_turn, mistakes=[], teach=[], words={}, unchecked=True)
            await self.send(type="turn_done", id=rec.tid)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    s = Session(ws)
    await s.send(
        type="ready",
        model=E.tutor.model,
        voices=[{"id": v.id, "label": v.label} for v in E.tts.voices],
        voice=s.voice,
        lessons=[{k: lesson[k] for k in ("id", "title", "title_es", "goal")} for lesson in LESSONS.values()],
        settings=current_settings(),
    )
    await s.send(type="progress", **progress_payload())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                audio = np.frombuffer(msg["bytes"], dtype="<i2").astype(np.float32) / 32768.0
                cont, s.continuing = s.continuing, None
                if s.pending_attempt:
                    a, s.pending_attempt = s.pending_attempt, None
                    age = time.time() - a["at"]
                    if age <= ATTEMPT_MAX_AGE_S:
                        s.side(s.attempt(audio, a["data"]))
                        continue
                    log.info("attempt %r is %.0f s old: ignored, this audio is a normal utterance",
                             a["data"].get("key"), age)
                if cont is not None:
                    # The learner went on talking before hearing Sofía's answer: the frame is the last utterance
                    # plus the rest. None of the running answer was heard.
                    s.cancel_played, s.reopening = 0, True
                    try:
                        await s.cancel()
                    finally:
                        s.reopening = False
                    prev, n = cont["prev"], cont["n"]
                    if (o := s.reopen()) is not None:
                        s.run(s.utterance(audio, continuation=o, prev_s=prev / SAMPLE_RATE, cont_n=n))
                        continue
                    if s.answered_cmd and 0 < prev < len(audio):  # the first part was a voice command: done
                        audio = audio[prev:]
                    log.info("continue: no learner turn to continue, the audio is a new turn")
                    s.run(s.utterance(audio, cont_n=n))
                    continue
                await s.cancel()
                s.run(s.utterance(audio))
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "start":
                s.leaving = True
                await s.cancel()
                s.run(s.start(str(data.get("lesson") or "intro"), str(data.get("mode") or "easy")))
            elif kind == "text" and data.get("text", "").strip():
                await s.cancel()
                s.run(s.learner_said(data["text"].strip(), "es", time.time()))
            elif kind == "help":
                await s.cancel()
                s.run(s.help())
            elif kind == "attempt":
                s.pending_attempt = {"data": data, "at": time.time()}
            elif kind == "ask_text" and str(data.get("text") or "").strip():  # a typed question about a card
                s.side(s.ask_teacher(None, data))
            elif kind == "attempt_cancel":  # the learner left the attempt: its audio won't come
                s.pending_attempt = None
            elif kind == "continue":
                prev, n = data.get("prev"), data.get("n")
                s.continuing = {"prev": prev if _is_int(prev) and prev > 0 else 0, "n": n if _is_int(n) else None}
            elif kind == "review_skip" and data.get("lemma"):  # "I don't know" in the review drill
                E.vocab.record_review(s.id, str(data["lemma"]), False)
            elif kind == "fix_skip" and _is_int(data.get("id")):  # "I don't know" on a fix to remember
                E.vocab.record_fix_review(data["id"], False, session_id=s.id)
            elif kind == "fix_drop" and _is_int(data.get("id")):  # "This wasn't a mistake"
                E.vocab.drop_fix(data["id"], session_id=s.id)
            elif kind == "speak" and data.get("text"):
                s.side(s.speak(str(data["text"])[:300], bool(data.get("slow"))))
            elif kind == "settings":
                s.update_settings(data)
                await s.send(type="settings", **current_settings())
            elif kind == "voice":
                await s.set_voice(str(data.get("id", "")), sample=not data.get("silent"))
            elif kind == "finish":
                await s.cancel()
                s.run(s.finish())
            elif kind == "cancel":
                played = data.get("played")
                s.cancel_played = played if isinstance(played, int) and not isinstance(played, bool) else None
                await s.cancel()
                await s.send(type="status", state="idle")
            elif kind == "heard":
                played = data.get("played")
                if isinstance(played, int) and not isinstance(played, bool) and isinstance(data.get("id"), str):
                    s.heard(data["id"], played)
    except WebSocketDisconnect:
        pass
    finally:
        await s.cancel()
        s.flush_unanswered()  # its DB row is still written


@app.get("/api/progress")
async def progress() -> JSONResponse:
    return JSONResponse(progress_payload())


@app.get("/api/scenarios")
async def scenarios() -> JSONResponse:
    return JSONResponse(scenario.load_all(E.vocab.db))


@app.post("/api/scenarios")
async def new_scenario(req: Request) -> JSONResponse:
    prompt = str((await req.json()).get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "Describe the situation first."}, status_code=400)
    try:
        lesson = await scenario.generate(E.tutor.llm, prompt, current_settings()["level"], E.vocab)
    except Exception as e:  # noqa: BLE001 (shown to the learner)
        log.warning("scenario generation failed: %s", e)
        return JSONResponse({"error": "Couldn't build that scenario — try again or describe it differently."},
                            status_code=502)
    scenario.save(E.vocab.db, lesson)
    log.info("scenario %s: %s (%d phrases, %d words)", lesson["id"], lesson["title_es"], len(lesson["say"]),
             len(lesson["words"]))
    return JSONResponse(lesson)


@app.delete("/api/scenarios/{sid}")
async def delete_scenario(sid: str) -> JSONResponse:
    scenario.delete(E.vocab.db, sid)
    return JSONResponse({"ok": True})


@app.get("/api/review")
async def review() -> JSONResponse:
    return JSONResponse(E.vocab.review_items())


@app.get("/api/words")
async def word_bank() -> JSONResponse:
    return JSONResponse(E.vocab.word_list())


if config.CLIENT_DIST.exists():
    app.mount("/", StaticFiles(directory=config.CLIENT_DIST, html=True), name="client")
