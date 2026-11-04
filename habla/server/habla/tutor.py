"""The tutor LLM: one structured call per turn.

The model fills, in order: reply -> mistakes -> teach -> reply_en.
`reply` comes first so we can stream it out of the partial JSON straight into
TTS (lowest time-to-first-audio). The analysis fields are generated while the
first sentences are already playing, and drive the correction cards + tracking.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from . import config

_PAIR = {
    "type": "object",
    "properties": {"english": {"type": "string"}, "spanish": {"type": "string"}},
    "required": ["english", "spanish"],
}
_MISTAKE = {
    "type": "object",
    "properties": {"wrong": {"type": "string"}, "right": {"type": "string"}, "why": {"type": "string"}},
    "required": ["wrong", "right", "why"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "mistakes": {"type": "array", "items": _MISTAKE},
        "teach": {"type": "array", "items": _PAIR},
        "reply_en": {"type": "string"},
    },
    "required": ["reply", "mistakes", "teach", "reply_en"],
}
OPTIONS = {"temperature": 0.4, "num_ctx": 8192}  # identical on every call, or Ollama reloads the model

SYSTEM = """You are Sofía, a warm, patient Spanish tutor from Madrid in a live spoken conversation with a beginner (A1–A2) English speaker. Your reply is read aloud by text-to-speech and shown as subtitles.

Each learner message ends with a note in [brackets] from the app: the English words it detected in their speech, words to review, or a special instruction. The learner does not see these notes.

Before answering, check the learner's Spanish for real mistakes: gender/article agreement (la perro → el perro), verb conjugation and tense (yo va → yo voy, ayer yo voy → ayer yo fui), ser vs estar (soy cansado → estoy cansado), word choice. Ignore missing accents, punctuation and capitalization — it is speech transcribed by a machine.

Fill the JSON fields in this order:
1. "reply": what you say, in simple, natural Spanish, 1–3 short sentences:
   - If the learner used English, teach the most useful word: say the Spanish word clearly and ask them to use it, e.g. "En español decimos «el mercado». ¿Puedes decir la frase otra vez?"
   - Otherwise, if there was a mistake, recast it naturally and name the fix briefly, e.g. "¡Ah, fuiste a la playa! Se dice «yo fui». ¿Con quién fuiste?" Fix only the most important mistake.
   - Otherwise react to what they said like a real person, and keep the conversation going.
   - End with exactly one simple question. Remember what the learner already told you and don't ask it again.
   - Use words a beginner knows. Never use English in the reply. No emojis, no markdown, no lists.
2. "mistakes": the real mistakes in the learner's Spanish: the wrong fragment exactly as they said it (short, just the wrong words), the corrected fragment, and a very short English explanation. Do NOT list English words or names. Empty list if their Spanish was fine.
3. "teach": for each English word or phrase in the note, the natural Spanish equivalent (nouns with article: "el mercado"; no explanations or parentheses). Also include the word if the learner asked how to say something. Empty list otherwise.
4. "reply_en": a natural English translation of your reply.

Keep the conversation on today's lesson, but follow the learner if they want to talk about something else."""


def build_system(lesson: dict) -> str:
    """Stable for the whole session, so Ollama can reuse the prompt's KV cache every turn."""
    parts = [SYSTEM, "", f"Today's lesson: {lesson['title']} ({lesson['title_es']}). Goal: {lesson['goal']}",
             f"Scenario: {lesson['scenario']}"]
    if lesson.get("words"):
        parts.append("Lesson words — use them yourself and create chances for the learner to say them: "
                     + ", ".join(lesson["words"]) + ".")
    return "\n".join(parts)


def review_note(review: list[dict]) -> str:
    if not review:
        return ""
    words = ", ".join(f"{w['lemma']} ({w['translation']})" if w.get("translation") else w["lemma"] for w in review)
    return f"[Review words the learner is still learning — if natural, ask something that lets them use one: {words}]"


class ReplyExtractor:
    """Pulls the value of the "reply" string out of a JSON document that is still being streamed."""

    KEY = '"reply"'

    def __init__(self) -> None:
        self.buf = ""
        self.pos: int | None = None  # index just after the opening quote of the value
        self.done = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        if self.done:
            return ""
        if self.pos is None:
            k = self.buf.find(self.KEY)
            if k < 0:
                return ""
            q = self.buf.find('"', k + len(self.KEY))
            colon = self.buf.find(":", k + len(self.KEY))
            if q < 0 or colon < 0 or q < colon:
                return ""
            self.pos = q + 1
        out = []
        i = self.pos
        while i < len(self.buf):
            c = self.buf[i]
            if c == "\\":
                if i + 1 >= len(self.buf):
                    break
                n = self.buf[i + 1]
                if n == "u":
                    if i + 6 > len(self.buf):
                        break
                    out.append(chr(int(self.buf[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                out.append({"n": " ", "t": " ", '"': '"', "\\": "\\", "/": "/"}.get(n, n))
                i += 2
                continue
            if c == '"':
                self.done = True
                i += 1
                break
            out.append(c)
            i += 1
        self.pos = i
        return "".join(out)


class Tutor:
    def __init__(self, model: str) -> None:
        self.model = model
        self.client = httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(120, connect=5))

    @staticmethod
    async def pick_model() -> str:
        async with httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=5) as c:
            tags = (await c.get("/api/tags")).json()
        installed = {m["name"] for m in tags.get("models", [])}
        for m in config.LLM_CANDIDATES:
            if m in installed or f"{m}:latest" in installed:
                return m
        raise RuntimeError(f"None of {config.LLM_CANDIDATES} installed in Ollama. Run: ollama pull gemma4:26b")

    async def stream(self, system: str, history: list[dict]) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *history],
            "stream": True,
            "think": False,
            "format": SCHEMA,
            "keep_alive": "60m",
            "options": OPTIONS,
        }
        async with self.client.stream("POST", "/api/chat", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                j = json.loads(line)
                if j.get("error"):
                    raise RuntimeError(j["error"])
                c = j.get("message", {}).get("content", "")
                if c:
                    yield c

    async def warmup(self) -> None:
        body = {"model": self.model, "messages": [{"role": "user", "content": "Hola"}], "stream": False,
                "think": False, "keep_alive": "60m", "options": {**OPTIONS, "num_predict": 1}}
        await self.client.post("/api/chat", json=body)
