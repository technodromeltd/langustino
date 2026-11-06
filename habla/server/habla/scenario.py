"""Scenario builder: a mini lesson made on the fly from the learner's own situation ("going to the fruit shop").

One LLM call writes the scene, the phrases the learner will hear and say, a few key words and some real-life
surprises. Code then keeps only words that are in the vocab list (so they are tracked like lesson words) and turns
the result into a normal lesson dict that the Director runs. Scenarios are stored in the vocab DB.

Two runs: "easy" (patient, slow, the phrases are suggested) and "real" (natural pace, local expressions, surprises).
"""

from __future__ import annotations

import json
import re
import time
import uuid

from . import config

_PHRASE = {"type": "object", "properties": {"es": {"type": "string"}, "en": {"type": "string"}},
           "required": ["es", "en"]}
_WORD = {"type": "object",
         "properties": {"lemma": {"type": "string"}, "en": {"type": "string"},
                        "example_es": {"type": "string"}, "example_en": {"type": "string"}},
         "required": ["lemma", "en", "example_es", "example_en"]}
SCENARIO_SCHEMA = {
    "type": "object",
    "properties": {
        "title_es": {"type": "string"},
        "title": {"type": "string"},
        "goal": {"type": "string"},
        "role": {"type": "string"},
        "setting": {"type": "string"},
        "hear": {"type": "array", "items": _PHRASE},
        "say": {"type": "array", "items": _PHRASE},
        "words": {"type": "array", "items": _WORD},
        "twists": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_es", "title", "goal", "role", "setting", "hear", "say", "words", "twists"],
}

SYSTEM = """You design a short spoken role-play lesson for a Spanish learner (level {level}) who lives in {city}, Spain, and wants to practise a real situation from their life before doing it for real. Use the Spanish people actually speak in {city} (Spain, usted/tú as locals would use it in that place; local everyday expressions are welcome when common, but keep them understandable). Return JSON:
- "title_es": short Spanish title, e.g. "En la frutería". "title": the same in English.
- "goal": one English sentence, what the learner will be able to do, e.g. "Buy fruit by weight, ask prices and pay."
- "role": who Sofía (the tutor) plays, in English, e.g. "the friendly owner of a small fruit shop in Triana".
- "setting": one English sentence describing the scene.
- "hear": 4–6 things the other person typically says in this situation that the learner must understand (short, natural, exactly as a local says them), with English.
- "say": 6–8 short, ready-to-use phrases the learner will need to say, most useful first, simple for their level, with English. Cover the whole interaction: greeting/opening, asking, quantities or details, price, paying, closing.
- "words": 5–8 key single words for this situation (lemma: infinitive or singular noun without article; "en": short English meaning), each with one short example sentence and its English.
- "twists": 3 realistic surprises that could happen (in English), e.g. "They are out of cherries", "The shopkeeper asks if you want a bag".
Keep everything short and practical. If the request is not a real-life situation, make the closest everyday situation."""


def _clean(s: object, n: int = 200) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _phrases(items: object, n: int) -> list[dict]:
    out, seen = [], set()
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict):
            continue
        es, en = _clean(p.get("es"), 120), _clean(p.get("en"), 120)
        if es and en and es.lower() not in seen:
            seen.add(es.lower())
            out.append({"es": es, "en": en})
    return out[:n]


def build_lesson(j: dict, prompt: str, vocab) -> dict:
    """The LLM's JSON → a lesson dict for the Director. Raises ValueError if it is unusable."""
    say, hear = _phrases(j.get("say"), 8), _phrases(j.get("hear"), 6)
    if len(say) < 3:
        raise ValueError("the scenario came back without phrases")
    words, examples = [], {}
    for w in j.get("words") or []:
        if not isinstance(w, dict):
            continue
        lemma = _clean(w.get("lemma"), 40).lower()
        lemma = re.sub(r"^(el|la|los|las|un|una)\s+", "", lemma)
        if not lemma or not vocab.info(lemma) or lemma in examples:
            continue  # only list words: they are tracked like any lesson word
        en = _clean(w.get("en"), 60)
        words.append(f"{lemma}: {en}" if en else lemma)
        ex_es, ex_en = _clean(w.get("example_es"), 120), _clean(w.get("example_en"), 120)
        if ex_es and ex_en:
            examples[lemma] = [ex_es, ex_en]
    role = _clean(j.get("role"), 160) or "a friendly local"
    setting = _clean(j.get("setting"), 240)
    return {
        "id": "sc-" + uuid.uuid4().hex[:8],
        "custom": True,
        "prompt": _clean(prompt, 300),
        "created": time.time(),
        "title": _clean(j.get("title"), 60) or "My scenario",
        "title_es": _clean(j.get("title_es"), 60) or "Mi escenario",
        "goal": _clean(j.get("goal"), 200) or "Practise a real situation.",
        "role": role,
        "setting": setting,
        "scenario": f"You are {role} in {config.CITY}. {setting} The learner is the customer or visitor. Stay in the role.",
        "words": words,
        "examples": examples,
        "say": say,
        "hear": hear,
        "twists": [t for t in (_clean(x, 140) for x in (j.get("twists") or [])[:3]) if t],
    }


async def generate(llm, prompt: str, level: str, vocab) -> dict:
    from .tutor import TURN_TIMEOUT_S  # noqa: PLC0415 (tutor imports llm; keep this module light)
    j = await llm.chat_json(
        [{"role": "system", "content": SYSTEM.format(level=level, city=config.CITY)},
         {"role": "user", "content": f"The learner's situation: {prompt}"}],
        SCENARIO_SCHEMA, 1500, timeout=max(TURN_TIMEOUT_S, 60), name="scenario")
    return build_lesson(j, prompt, vocab)


def for_run(lesson: dict, mode: str) -> dict:
    """The lesson as Sofía plays it: the scene plus the phrases, and how this run is played."""
    lesson = dict(lesson, mode=mode)
    hear = "; ".join(f"«{p['es']}»" for p in lesson.get("hear", []))
    say = "; ".join(f"«{p['es']}»" for p in lesson.get("say", []))
    parts = [lesson["scenario"],
             f"Say things a real local says in this situation, such as: {hear}." if hear else "",
             f"The learner is practising phrases like: {say}. Create natural moments for them to say these "
             f"(don't say them for the learner)." if say else ""]
    if mode == "real":
        twists = "; ".join(lesson.get("twists", []))
        parts.append("REALISTIC RUN: talk at a natural pace like a real, busy local, with everyday expressions; "
                     "don't simplify much and don't wait for perfect Spanish. Bring in one or two of these "
                     f"surprises during the scene when it fits: {twists}." if twists else
                     "REALISTIC RUN: talk at a natural pace like a real, busy local.")
    else:
        parts.append("EASY RUN: be patient and friendly, speak slowly and clearly, one thing at a time.")
    lesson["scenario"] = " ".join(p for p in parts if p)
    return lesson


# ---------- storage (in the vocab DB) ----------
def _table(db) -> None:
    db.execute("create table if not exists scenarios (id text primary key, created real, data text not null)")


def save(db, lesson: dict) -> None:
    _table(db)
    db.execute("insert or replace into scenarios(id, created, data) values (?,?,?)",
               (lesson["id"], lesson.get("created", time.time()), json.dumps(lesson, ensure_ascii=False)))
    db.commit()


def load_all(db) -> list[dict]:
    _table(db)
    return [json.loads(r[0]) for r in db.execute("select data from scenarios order by created desc")]


def get(db, sid: str) -> dict | None:
    _table(db)
    r = db.execute("select data from scenarios where id=?", (sid,)).fetchone()
    return json.loads(r[0]) if r else None


def update_result(db, sid: str, mode: str, phrases: list[dict]) -> None:
    """Remember which phrases were said in the last run of each mode (the cheat sheet marks the hard ones)."""
    lesson = get(db, sid)
    if not lesson:
        return
    lesson.setdefault("runs", {})[mode] = {"at": time.time(),
                                           "used": [p["es"] for p in phrases if p.get("used")]}
    save(db, lesson)


def delete(db, sid: str) -> None:
    _table(db)
    db.execute("delete from scenarios where id=?", (sid,))
    db.commit()
