"""The tutor LLM: two small structured calls per turn instead of one big one.

1. Conversation (streamed):  learner_en -> better -> reply
   - learner_en: what the learner said, in English. Makes the model understand them before answering, and
     powers the hover translation.
   - better: the learner's message as a native speaker would say it ("" if it was right). Written before the
     reply, so the reply can recast it. The fix cards are a word diff of it (fixes.diff_fixes), not the model's
     own list of mistakes: a separate "find the mistakes" call missed half the real errors and invented others.
   - reply: Sofía in character in the role-play (spoken). Reacts to what they said (recasts a mistake, or reacts to
     its content instead of parroting a correct sentence), asks one open question. Never talks about
     "the lesson". Code backs this up: reply.ReplyGuard drops praise of a mistake and a parroted first sentence, and
     the per-turn note lists what she already asked and what they already told her (reply.memory_note).
   The teacher card (example sentences, hints, the recap summary) is not written by the model: the director builds
   it from the hand-checked examples in lessons.json and its own lesson state (director.Step.coach, recap_text).
2. Explanation check (in the background after the reply is written; it yields to the next reply): it gets the
   corrections from the diff and only explains them (type, why, right_en), plus teach (Spanish for English words)
   and reply_en. A correct turn with no English skips it and only gets a translation (`translate`), as do turns
   with no learner message (start, goodbye).

A small model (Gemma 4 E4B) asked to do all of this in one call followed half the rules: it broke the
role-play with teacher talk, asked several questions, and invented corrections.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from . import config
from .llm import LLM
from .practice import LEVELS, MISTAKE_TYPES

_PAIR = {
    "type": "object",
    "properties": {"english": {"type": "string"}, "spanish": {"type": "string"}},
    "required": ["english", "spanish"],
}
_EXPLANATION = {
    "type": "object",
    "properties": {
        "n": {"type": "integer"},
        "type": {"type": "string", "enum": list(MISTAKE_TYPES)},
        "why": {"type": "string"},
        "right_en": {"type": "string"},
    },
    "required": ["n", "type", "why", "right_en"],
}
# Field order matters: the model writes them in this order, so `better` is settled before the reply recasts it.
CHAT_SCHEMA = {
    "type": "object",
    "properties": {"learner_en": {"type": "string"}, "better": {"type": "string"}, "reply": {"type": "string"}},
    "required": ["learner_en", "better", "reply"],
}
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {"type": "array", "items": _EXPLANATION},
        "teach": {"type": "array", "items": _PAIR},
        "reply_en": {"type": "string"},
    },
    "required": ["explanations", "teach", "reply_en"],
}
# On a cloud model the same call also writes the teacher card: a sample answer to the question in "reply". It comes
# after "reply", so it never delays the speech.
CHAT_TIP_SCHEMA = {
    "type": "object",
    "properties": {**CHAT_SCHEMA["properties"], "tip_es": {"type": "string"}, "tip_en": {"type": "string"}},
    "required": [*CHAT_SCHEMA["required"], "tip_es", "tip_en"],
}
TIP_RULES = """Two more JSON fields, written after "reply" (shown on screen as a tip, never spoken):
- "tip_es": one natural sample answer the learner could give to the question at the end of your reply, in the first person, 4–10 words, at their level, true to what they told you so far where possible. If the lesson step names a word or phrase to use, use it when it fits the answer naturally. "" if your reply has no question.
- "tip_en": its English translation ("" if tip_es is "")."""
TRANSLATE_SCHEMA = {"type": "object", "properties": {"reply_en": {"type": "string"}}, "required": ["reply_en"]}
# Runaway guards: small models sometimes loop inside a JSON string until the context is full (minutes).
# MAX_TOKENS is num_predict on Ollama (llm.OLLAMA_OPTIONS keeps num_ctx identical on every call, or Ollama
# reloads the model) and max_tokens on an OpenAI-compatible server.
MAX_TOKENS = 400
TURN_TIMEOUT_S = 40

CHAT_SYSTEM = """You are Sofía, a warm, curious Spanish speaker. You talk out loud with a beginner who is learning Spanish, playing the role-play described below. What you say in "reply" is spoken by text-to-speech. Talk like a real person in a real conversation: listen to what they said and react to it.

The learner's message is speech transcribed by a machine and may contain small transcription errors: guess what they meant. Lines in [brackets] come from the app and the learner does not see them: English words detected in their speech, the questions you already asked and what they already told you, and the lesson step for this turn.

JSON fields:
- "learner_en": the learner's message in English, translated closely so any Spanish mistake stays visible. "" if there is no learner message.
- "better": the learner's last message as a native speaker would say it at their level: fix grammar and replace English words with Spanish, otherwise keep their words. Exactly "" if it was already correct or there is no learner message. Ignore spelling, accents and punctuation: it is speech.
- "reply": you, in character, in 1–3 short sentences of simple Spanish:
  1. React to exactly what they just said (if they asked something, answer it).
     - If better is not "": start by saying the corrected part back naturally, e.g. «un cerveza» → «¿Una cerveza? ¡Claro!»; «Soy de Finland» → «¡Ah, de Finlandia!». Never praise a sentence that had a mistake.
     - If better is "": do NOT repeat their sentence. React to its content like an interested friend: a short reaction («¡Qué interesante!», «¿Ah, sí?») or a brief comment on what they said. Talk about yourself only when it fits naturally (they asked you, or a short comparison makes the talk flow) — most turns are about them, not you.
  2. Follow the lesson step, but the conversation comes first: if the step doesn't fit what they just said, follow up on their answer instead.
  Never repeat something you already said in this conversation (about yourself or anything else): read your earlier replies first.
  3. End with ONE open question (qué, cómo, dónde, cuándo, por qué, cuántos…) that needs a full-sentence answer, not sí/no. Never ask again what you already asked, and never ask for something they already told you. No question if the lesson step says so.

Examples (learner → better → reply):
- «Quiero un cerveza» → «Quiero una cerveza» → «¿Una cerveza? ¡Claro! ¿Qué cerveza te gusta?»
- «Yo tiene dos hermanos» → «Tengo dos hermanos» → «¡Ah, tienes dos hermanos! ¿Cómo se llaman?»
- «Estudiamos juntos en Tampere» → "" → «¡Qué bonito! ¿Qué estudiabais?»
- «Me gusta la beach» → «Me gusta la playa» → «¿La playa? ¡A mí también! ¿Qué playa te gusta más?»

Rules for "reply": you are a model speaker: always use articles (el pan, una manzana), correct verb forms and natural Spanish; drop subject pronouns as natives do (tengo, not yo tengo). Stay in your role-play character; never talk about the lesson, words, exercises or practicing, and never ask quiz questions about Spanish («¿Qué pregunta usamos para…?», «¿Cómo se dice…?»): ask real questions about their life or the scene. Never comment on spelling or writing — this is speech. No English, emojis or markdown."""

CHECK_SYSTEM = """You explain corrections to a beginner Spanish learner. You get what the learner said (speech transcribed by a machine), numbered corrections of it (wrong → right) that are already decided, the English words they used, and a tutor sentence to translate. Return JSON:
- "explanations": one item per correction, in order. "n" = the correction's number; "type" = gender (articles/agreement), verb_form (wrong ending for the person), tense, ser_estar, por_para, preposition, word_choice, word_order, missing_word or other; "why" = 1–2 short, friendly English sentences, written to the learner ("you"), explaining the rule; "right_en" = what the corrected words mean in English. Explain only the given corrections: never add, change or question one. Use type "other" if a correction is only style, not a mistake, and then leave "why" empty. Never mention spelling, accents or punctuation: it is speech. Empty list if there are no corrections.
- "teach": for each English word or phrase they used, the natural Spanish (nouns with article: "el mercado"). Also the word they asked how to say, if any. Empty list otherwise.
- "reply_en": natural English translation of the tutor sentence.

The learner's first language is {l1}.{l1_note}"""
L1_NOTES = {"Finnish": " Finnish has no articles and no grammatical gender, so explain articles and gender simply."}


def check_system(l1: str | None = None) -> str:
    l1 = l1 or config.L1
    return CHECK_SYSTEM.format(l1=l1, l1_note=L1_NOTES.get(l1, ""))


# One constant line (the system prompt must not change during a session: prompt cache). The facts are for small
# talk, so she can share something about herself instead of parroting the learner; a scene role wins over them.
PERSONA = ("About you, outside any role-play character: Sofía, 31, from Madrid, works in a bookshop, has a younger "
           "brother Pablo, likes cooking and walking in el Retiro. When the scene gives you a role or job (e.g. the "
           "café waitress), keep that role for the scene and use these facts only for small talk. Say each fact at most once per conversation, and only when it fits.")


def build_system(lesson: dict, level: str = "A1", focus: list[str] | None = None, tip: bool = False) -> str:
    """Stable for the whole session, so Ollama can reuse the prompt's KV cache every turn.
    Per-turn lesson steps come in the user-message note."""
    parts = [CHAT_SYSTEM, *([TIP_RULES] if tip else []), "", LEVELS.get(level, LEVELS["A1"])["prompt"], f"Role-play: {lesson['scenario']}", PERSONA,
             f"Lesson goal (background for you, never to be said in character): {lesson['goal']}"]
    if focus:
        parts.append("The learner often struggles with: " + "; ".join(focus) + ". Now and then, create natural "
                     "chances in the role-play to use these correctly, and recast their errors.")
    return "\n".join(parts)


def check_prompt(said: str, english: list[str], reply: str, pairs: list[dict], unclear: list[str] | None = None) -> str:
    # The tutor's question is deliberately left out: the corrections are already decided, so only the learner's
    # own sentence matters for explaining them.
    fixes = [f"{i}. «{p['wrong']}» → «{p['right']}»" for i, p in enumerate(pairs, 1)]
    return "\n".join([
        f"Learner said: {said or '(nothing)'}",
        "Corrections (wrong → right):" + ("\n" + "\n".join(fixes) if fixes else " none"),
        f"English words they used: {', '.join(english) if english else 'none'}",
        *([f"Unclear audio (maybe misheard, never a mistake): {', '.join(unclear)}"] if unclear else []),
        f"Tutor sentence to translate: {reply}",
    ])


class FieldStream:
    """Pulls the value of one string field out of a JSON document that is still being streamed."""

    def __init__(self, key: str) -> None:
        self.key = f'"{key}"'
        self.buf = ""
        self.pos: int | None = None  # index just after the opening quote of the value
        self.done = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        if self.done:
            return ""
        if self.pos is None:
            k = self.buf.find(self.key)
            if k < 0:
                return ""
            q = self.buf.find('"', k + len(self.key))
            colon = self.buf.find(":", k + len(self.key))
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


HINT_SCHEMA = {"type": "object", "properties": {"spanish": {"type": "string"}, "english": {"type": "string"}},
               "required": ["spanish", "english"]}


class Tutor:
    """The tutor's LLM calls. Every request goes through llm.LLM (Ollama, or an OpenAI-compatible server when
    HABLA_LLM_URL is set)."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.model = llm.model

    @staticmethod
    async def pick_model() -> LLM:
        """The configured cloud model when HABLA_LLM_URL is set, else the first installed Ollama candidate."""
        if config.LLM_BACKEND == "openai":
            return LLM.from_config()
        async with httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=5) as c:
            tags = (await c.get("/api/tags")).json()
        installed = {m["name"] for m in tags.get("models", [])}
        for m in config.LLM_CANDIDATES:
            if m in installed or f"{m}:latest" in installed:
                return LLM.from_config(m)
        raise RuntimeError(f"None of {config.LLM_CANDIDATES} installed in Ollama. Run: ollama pull gemma4:26b")

    @property
    def tips(self) -> bool:
        """The reply call also writes the teacher card (cloud models; Gemma keeps the hand-checked lesson examples)."""
        return self.llm.backend == "openai"

    @property
    def chat_schema(self) -> dict:
        return CHAT_TIP_SCHEMA if self.tips else CHAT_SCHEMA

    def stream(self, system: str, history: list[dict]) -> AsyncIterator[str]:
        """Chunks of the CHAT_SCHEMA JSON as it is written. app.py enforces TURN_TIMEOUT_S."""
        return self.llm.stream_json([{"role": "system", "content": system}, *history], self.chat_schema, MAX_TOKENS,
                                    name="tutor_reply")

    async def check(self, said: str, english: list[str], reply: str, pairs: list[dict],
                    unclear: list[str] | None = None) -> dict:
        """Explanations for the given corrections (fixes.diff_fixes), Spanish for the English words, and the reply's
        translation. Returns {} if the model's output is unusable."""
        return await self.llm.chat_json(
            [{"role": "system", "content": check_system()},
             {"role": "user", "content": check_prompt(said, english, reply, pairs, unclear)}],
            CHECK_SCHEMA, MAX_TOKENS, timeout=TURN_TIMEOUT_S, name="grammar_check")

    async def translate(self, text: str) -> str:
        """English for a tutor line with no learner message to check (session start, goodbye)."""
        j = await self.llm.chat_json(
            [{"role": "system", "content": "Translate the Spanish into natural, simple English. Return JSON."},
             {"role": "user", "content": text}],
            TRANSLATE_SCHEMA, MAX_TOKENS, timeout=TURN_TIMEOUT_S, name="translation")
        return str(j.get("reply_en") or "").strip()

    async def hint(self, asked: str, level: int, lesson: dict, words: list[str]) -> dict:
        """Hint ladder for a stuck learner: 1 = a key word, 2 = a sentence starter, 3 = a full example answer.
        Returns {"spanish", "english"}: a structured answer, because a small model asked for free text tends to
        explain the question instead of giving Spanish to say."""
        what = {
            1: 'spanish = ONE useful word or 2-3 word phrase for the answer (not a sentence); english = its meaning.',
            2: 'spanish = the first half of an answer, ending with "…"; english = how to finish it.',
            3: "spanish = one complete, simple answer to the question; english = its meaning.",
        }[max(1, min(level, 3))]
        j = await self.llm.chat_json(
            [{"role": "system", "content": "You help a beginner Spanish learner answer a question in a spoken "
              f"role-play ({lesson['scenario']}). Return JSON with simple Spanish the learner can say."},
             {"role": "user", "content": f"Question: {asked or '(start of the conversation)'}\n"
              f"Useful words: {', '.join(words) or 'any'}\n{what}"}],
            HINT_SCHEMA, MAX_TOKENS, timeout=TURN_TIMEOUT_S, name="hint")
        try:
            return {"spanish": j["spanish"].strip().strip("«»"), "english": j["english"].strip()}
        except (KeyError, AttributeError):
            return {}

    async def warmup(self) -> None:
        await self.llm.warmup()


# ---------- Ask the teacher: questions about a card (fix, word, teacher tip) ----------
TEACHER_SYSTEM = """You are a friendly, expert Spanish teacher. A beginner (first language {l1}, level {level}) is in a speaking lesson and asks you about one card from it. They ask by voice, and your answer is read aloud.

Answer in English like a good tutor talking: 2–5 short sentences, no lists, markdown or emojis.
- If they ask whether something is OK, start with a clear verdict: «Yes, that's fine», «People would understand you, but it isn't natural» or «No, that's wrong» — then say why, simply.
- Put every Spanish word, phrase or example in «guillemets», e.g. «hago ejercicio».
- Give one short extra example when it helps.
- If the card itself was wrong or too strict (what they said was fine), say so honestly.
- Their question is transcribed speech and may have small errors: guess what they meant. If it isn't about the card, answer it briefly anyway if it's about Spanish."""


def teacher_messages(card: str, said: str, thread: list[dict], question: str, level: str) -> list[dict]:
    msgs = [{"role": "system", "content": TEACHER_SYSTEM.format(l1=config.L1, level=level)}]
    ctx = f"The card: {card}" + (f"\nWhat the learner said in the conversation: «{said}»" if said else "")
    first = True
    for qa in thread[-6:]:
        q, a = str(qa.get("q") or "").strip(), str(qa.get("a") or "").strip()
        if q and a:
            msgs += [{"role": "user", "content": (ctx + "\n\n" if first else "") + q},
                     {"role": "assistant", "content": a}]
            first = False
    msgs.append({"role": "user", "content": (ctx + "\n\n" if first else "") + question})
    return msgs
