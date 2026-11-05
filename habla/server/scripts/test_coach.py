"""The teacher card is built by the director from lessons.json examples and its own state, never by the LLM: which
turns get a card, the fallbacks, no repeats, and the recap text. Scratch DB, no server, no LLM.

    cd habla/server && uv run python scripts/test_coach.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="habla-coach-")
os.environ["HABLA_DB"] = str(Path(_tmp) / "coach.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import config  # noqa: E402
from habla.director import Director, Step  # noqa: E402
from habla.tutor import CHAT_SCHEMA, CHAT_SYSTEM  # noqa: E402
from habla.vocab import Vocab  # noqa: E402

v = Vocab()
lessons = {x["id"]: x for x in json.loads(config.LESSONS_PATH.read_text())}
failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    if not ok:
        failures.append(msg)


check("coach" not in CHAT_SCHEMA["properties"] and "coach" not in CHAT_SCHEMA["required"], "CHAT_SCHEMA has coach")
check("coach" not in CHAT_SYSTEM.lower(), "CHAT_SYSTEM mentions coach")


def run(lesson_id: str, turns: int = 16) -> tuple[Director, list[tuple[str, str]]]:
    """Plays a lesson where the learner never says a target: [(kind of turn, coach text)]."""
    d = Director(lessons[lesson_id], v)
    out = []
    step = d.start_note()
    check(isinstance(step, Step) and step.note.startswith("[Session start"), f"{lesson_id}: start_note")
    out.append(("warmup", step.coach))
    for _ in range(turns):
        d.observe(set())
        if d.phase == "recap":
            break
        phase, before = d.phase, {t.lemma: t.elicited for t in d.targets}
        step = d.instruction()
        check("coach" not in step.note, f"{lesson_id}: the note still mentions coach: {step.note}")
        el = [t for t in d.targets if t.elicited != before.get(t.lemma, 0)]
        if step.intro:
            kind = "introduce"
        elif el:  # in practice, or right after an introduction
            kind = f"elicit{el[0].elicited}"
        else:
            kind = "continue" if phase in ("introduce", "practice") else phase
        out.append((kind, step.coach))
    return d, out


for lid in ("cafe", "family", "free"):
    d, turns = run(lid)
    ex = lessons[lid].get("examples", {})
    allowed = {f"Try: «{es}» — {en}" for es, en in ex.values()} | {f"Hint: you could say «{es}» ({en})"
                                                                   for es, en in ex.values()}
    allowed |= {f"Hint: try using «{t.display}» ({t.english}) in your answer." for t in d.targets}
    coaches = [c for _, c in turns if c]
    print(f"{lid}: " + " | ".join(f"{k}={c!r}" for k, c in turns if c))
    check(len(coaches) == len(set(coaches)), f"{lid}: a coach text repeats: {coaches}")
    for kind, c in turns:
        if kind in ("warmup", "continue", "elicit1", "free"):
            check(c == "", f"{lid}: {kind} turn has a card: {c!r}")
        if c:
            check(c in allowed, f"{lid}: {kind} card isn't an example or a fallback: {c!r}")
    check(any(k == "introduce" and c for k, c in turns) or lid == "free", f"{lid}: no intro card")
    check(any(k == "elicit2" and c.startswith("Hint:") for k, c in turns), f"{lid}: no hint on a 2nd elicitation")
    if lid == "free":
        check(all("could say" not in c and not c.startswith("Try:") for c in coaches), "free: example text without data")

# The recap text: only director state, the last fix, next words; never repeated.
d = Director(lessons["cafe"], v)
new = [t for t in d.targets if t.kind == "new"]
new[0].state, new[1].state, new[2].state = "used", "used", "practiced"
d.mistakes = [{"wrong": "un cerveza", "right": "una cerveza"}, {"wrong": "yo es", "right": "yo soy"}]
r = d.recap_text()
print("recap:", r)
check(r.startswith(f"You used «{new[0].display}» and «{new[1].display}» on your own."), f"recap used: {r}")
check("Remember: «yo soy» (not «yo es»)." in r and "cerveza" not in r, f"recap fix: {r}")
check(f"Next time: «{new[2].display}»" in r or "Next time: «" in r, f"recap next: {r}")
check(d.recap_text() == "", "recap text repeated")
d2 = Director(lessons["cafe"], v)
d2.targets[0].state = "introduced"
r2 = d2.recap_text()
print("recap (none used):", r2)
check(r2.startswith(f"You practised «{d2.targets[0].display}»."), f"recap practised: {r2}")
check("Remember" not in r2, f"recap without fixes: {r2}")

if failures:
    print("\nFAILED:\n  " + "\n  ".join(failures))
    sys.exit(1)
print("\nall coach tests passed")
