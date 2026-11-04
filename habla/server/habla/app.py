"""Habla voice server: one WebSocket per conversation.

client -> server
  {"type":"start","lesson":"intro"}     start/restart a session (tutor speaks first)
  <binary>                              one utterance, 16 kHz mono int16 PCM
  {"type":"text","text":"..."}          typed utterance (same pipeline, no STT)
  {"type":"help"}                       learner is stuck: tutor suggests what to say
  {"type":"cancel"}                     learner interrupted: stop the current reply

server -> client
  ready, status, user, feedback, tutor_delta, audio, tutor_done, progress, ignored, error
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .english import english_spans
from .stt import STT
from .tts import TTS, SentenceSplitter
from .tutor import ReplyExtractor, Tutor, build_system, review_note
from .vocab import Vocab

log = logging.getLogger("habla")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

LESSONS = {lesson["id"]: lesson for lesson in json.loads(config.LESSONS_PATH.read_text())}
STT_POOL = ThreadPoolExecutor(1, thread_name_prefix="stt")
TTS_POOL = ThreadPoolExecutor(1, thread_name_prefix="tts")


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
    model = await Tutor.pick_model()
    E.tutor = Tutor(model)
    E.vocab = Vocab()
    E.stt = await loop.run_in_executor(STT_POOL, STT)
    E.tts = await loop.run_in_executor(TTS_POOL, TTS)
    await asyncio.gather(
        loop.run_in_executor(STT_POOL, E.stt.warmup),
        loop.run_in_executor(TTS_POOL, E.tts.warmup),
        E.tutor.warmup(),
    )
    log.info("ready in %.1fs — LLM=%s STT=%s voice=%s", time.time() - t0, model, config.WHISPER_MODEL, config.TUTOR_VOICE)
    yield


app = FastAPI(lifespan=lifespan)


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", s).strip()


def clean_mistakes(mistakes: list[dict], english: list[str]) -> list[dict]:
    """Drop non-mistakes the model sometimes emits: wrong == right modulo accents/punctuation,
    or "mistakes" that are just the English words (those become 'teach' cards instead)."""
    en_words = {w for span in english for w in _norm(span).split()}
    out = []
    for m in mistakes:
        if not (m.get("wrong") and m.get("right")) or _norm(m["wrong"]) == _norm(m["right"]):
            continue
        if en_words & set(_norm(m["wrong"]).split()):
            continue
        out.append(m)
    return out


class Session:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.id = uuid.uuid4().hex[:12]
        self.lesson = LESSONS["intro"]
        self.history: list[dict] = []
        self.task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        self.turn_no = 0

    async def send(self, **msg) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    def _push_user(self, content: str) -> None:
        if self.history and self.history[-1]["role"] == "user":  # previous turn was interrupted
            self.history[-1]["content"] += "\n" + content
        else:
            self.history.append({"role": "user", "content": content})
        max_msgs = config.HISTORY_TURNS * 2
        if len(self.history) > max_msgs:
            self.history = self.history[-max_msgs:]
            while self.history and self.history[0]["role"] != "user":
                self.history.pop(0)

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
    async def start(self, lesson_id: str) -> None:
        self.lesson = LESSONS.get(lesson_id, LESSONS["intro"])
        self.history = []
        self.id = uuid.uuid4().hex[:12]
        self._push_user(
            "[Session start. Greet the learner warmly as Sofía, say in one short simple sentence what you will "
            "practice today, and ask the first easy question. mistakes and teach are empty.]"
        )
        await self.respond(t_end=time.time(), user_turn=None, user_text=None, english=[])

    async def help(self) -> None:
        self._push_user(
            "[The learner is stuck and pressed the help button. In reply, give one short, simple example sentence "
            "they could say to answer your last question, inside «», and encourage them to say it out loud. "
            "mistakes and teach are empty.]"
        )
        await self.respond(t_end=time.time(), user_turn=None, user_text=None, english=[])

    async def utterance(self, audio: np.ndarray) -> None:
        t_end = time.time()
        await self.send(type="status", state="transcribing")
        loop = asyncio.get_running_loop()
        tr = await loop.run_in_executor(STT_POOL, E.stt.transcribe, audio)
        stt_ms = int((time.time() - t_end) * 1000)
        if tr is None:
            await self.send(type="ignored", reason="no speech detected")
            await self.send(type="status", state="idle")
            return
        log.info("STT %dms [%s] %s", stt_ms, tr.language, tr.text)
        await self.learner_said(tr.text, tr.language, t_end, stt_ms)

    async def learner_said(self, text: str, language: str, t_end: float, stt_ms: int = 0) -> None:
        self.turn_no += 1
        turn_id = f"u{self.turn_no}"
        english = english_spans(text, whole_utterance_english=(language == "en"))
        await self.send(type="user", id=turn_id, text=text, language=language, english=english, stt_ms=stt_ms)
        note = (f"[English detected: {', '.join(repr(s) for s in english)}]" if english else "[English detected: none]")
        review = review_note(E.vocab.review_words())
        self._push_user("\n".join(x for x in (text, note, review) if x))
        await self.respond(t_end=t_end, user_turn=turn_id, user_text=text, english=english)

    async def respond(self, t_end: float, user_turn: str | None, user_text: str | None, english: list[str]) -> None:
        self.turn_no += 1
        tid = f"t{self.turn_no}"
        await self.send(type="status", state="thinking")
        system = build_system(self.lesson)
        extractor, splitter = ReplyExtractor(), SentenceSplitter()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        timing: dict[str, int] = {}
        loop = asyncio.get_running_loop()

        def ms() -> int:
            return int((time.time() - t_end) * 1000)

        async def speaker() -> None:
            seq = 0
            while (sentence := await queue.get()) is not None:
                wav = await loop.run_in_executor(TTS_POOL, E.tts.synth, sentence)
                if not wav:
                    continue
                timing.setdefault("first_audio_ms", ms())
                await self.send(type="audio", id=tid, seq=seq, text=sentence, wav=base64.b64encode(wav).decode())
                seq += 1

        speak_task = asyncio.create_task(speaker())
        raw = ""
        spoken = ""
        feedback_sent = False
        try:
            async for chunk in E.tutor.stream(system, self.history):
                timing.setdefault("llm_first_token_ms", ms())
                raw += chunk
                delta = extractor.feed(chunk)
                if extractor.pos is not None:
                    timing.setdefault("reply_start_ms", ms())
                if user_turn and not feedback_sent and '"reply_en"' in raw:
                    feedback_sent = True
                    await self._early_feedback(raw, user_turn, english)
                if delta:
                    spoken += delta
                    await self.send(type="tutor_delta", id=tid, text=delta)
                    for s in splitter.feed(delta):
                        queue.put_nowait(s)
                if extractor.done and splitter.buf:
                    for s in splitter.flush():
                        queue.put_nowait(s)
            for s in splitter.flush():
                queue.put_nowait(s)
            queue.put_nowait(None)

            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("bad JSON from model: %s", raw[:500])
                result = {"mistakes": [], "teach": [], "reply": spoken, "reply_en": ""}
            reply = result.get("reply", "").strip()
            mistakes = clean_mistakes(result.get("mistakes", []), english) if user_turn else []
            teach = result.get("teach", []) if user_turn else []
            timing["llm_done_ms"] = ms()
            await speak_task
            timing["audio_done_ms"] = ms()
        except asyncio.CancelledError:
            speak_task.cancel()
            raise

        # ---- tracking ----
        sid = self.id
        taught = E.vocab.record_taught(sid, teach) if teach else []
        outcome = E.vocab.record_learner(sid, user_text, mistakes, english) if user_text else {}
        if mistakes:
            E.vocab.record_mistakes(sid, mistakes)
        E.vocab.record_heard(sid, reply)
        if user_text:
            E.vocab.save_turn(sid, self.lesson["id"], "learner", user_text, {"english": english, "mistakes": mistakes})
        E.vocab.save_turn(sid, self.lesson["id"], "tutor", reply, {"reply_en": result.get("reply_en")})
        self.history.append({"role": "assistant", "content": raw})

        if user_turn:
            await self.send(type="feedback", id=user_turn, mistakes=mistakes, teach=taught, words=outcome)
        await self.send(type="tutor_done", id=tid, reply=reply, reply_en=result.get("reply_en", ""), timing=timing)
        await self.send(type="progress", **E.vocab.progress(sid))
        await self.send(type="status", state="idle")
        log.info("turn %s timing %s", tid, timing)

    async def _early_feedback(self, raw: str, user_turn: str, english: list[str]) -> None:
        """mistakes/teach are complete once "reply_en" starts: show the cards before the translation is done."""
        k = raw.find('"reply_en"')
        head = raw[:k].rstrip().rstrip(",") + "}"
        try:
            j = json.loads(head)
        except json.JSONDecodeError:
            return
        await self.send(type="feedback", id=user_turn, mistakes=clean_mistakes(j.get("mistakes", []), english),
                        teach=j.get("teach", []), words={}, partial=True)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    s = Session(ws)
    await s.send(
        type="ready",
        model=E.tutor.model,
        lessons=[{k: lesson[k] for k in ("id", "title", "title_es", "goal")} for lesson in LESSONS.values()],
    )
    await s.send(type="progress", **E.vocab.progress())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                audio = np.frombuffer(msg["bytes"], dtype="<i2").astype(np.float32) / 32768.0
                await s.cancel()
                s.run(s.utterance(audio))
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "start":
                await s.cancel()
                s.run(s.start(data.get("lesson", "intro")))
            elif kind == "text" and data.get("text", "").strip():
                await s.cancel()
                s.run(s.learner_said(data["text"].strip(), "es", time.time()))
            elif kind == "help":
                await s.cancel()
                s.run(s.help())
            elif kind == "cancel":
                await s.cancel()
                await s.send(type="status", state="idle")
    except WebSocketDisconnect:
        pass
    finally:
        await s.cancel()


@app.get("/api/progress")
async def progress() -> JSONResponse:
    return JSONResponse(E.vocab.progress())


if config.CLIENT_DIST.exists():
    app.mount("/", StaticFiles(directory=config.CLIENT_DIST, html=True), name="client")
