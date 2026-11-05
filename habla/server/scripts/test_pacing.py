"""Lesson pacing in director.py: one new word per turn and elicited right after, a word Sofía didn't say is
introduced again, review words get asked for, an early goodbye gets a "the scene continues" note, and the hard
length cap. Also vocab.review_words / translation. Plain asserts, scratch DB, no server, no LLM.

    cd habla/server && uv run python scripts/test_pacing.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="habla-pacing-")
os.environ["HABLA_DB"] = str(Path(_tmp) / "pacing.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import config  # noqa: E402
from habla.director import CONTINUE_NOTE, MAX_LESSON_TURNS, Director, Step, Target  # noqa: E402
from habla.vocab import Vocab  # noqa: E402

v = Vocab()
lessons = {x["id"]: x for x in json.loads(config.LESSONS_PATH.read_text())}
failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    if not ok:
        failures.append(msg)


def lemmas(text: str) -> set[str]:
    return {t.lemma for t in v.tokens(text)}


def reply_for(step: Step) -> str:
    """A reply that does what the note asks: it says the introduced word."""
    if step.intro:
        return f"Mira, {step.intro[0].display}. ¿Y tú?"
    return "Claro. ¿Y qué más?"


def play(d: Director, learner: str, turns: int = 30, say_word: bool = True) -> list[dict]:
    """Plays a lesson. learner: "silent" (says no target) or "good" (says the word the last turn introduced or
    elicited). Returns one record per learner turn."""
    log: list[dict] = []
    step = d.start_note()
    d.saw_reply(reply_for(step), lemmas(reply_for(step)))
    prev_intro: Target | None = None
    prev_elicit: Target | None = None
    for _ in range(turns):
        said, echo = set(), frozenset()
        if learner == "good":
            if prev_intro is not None:
                said, echo = {prev_intro.lemma}, frozenset({prev_intro.lemma})  # repeats the word she just used
            elif prev_elicit is not None:
                said = {prev_elicit.lemma}  # recalls it: she didn't say it
        d.observe(said, echoed=echo)
        before = {id(t): t.elicited for t in d.targets}
        phase = d.phase
        step = d.instruction()
        el = [t for t in d.targets if t.elicited != before.get(id(t), 0)]
        log.append({"turn": d.turn, "phase": phase, "intro": [t.lemma for t in step.intro],
                    "elicit": el[0] if el else None, "elicited": el[0].elicited if el else 0, "note": step.note,
                    "coach": step.coach})
        reply = reply_for(step) if say_word else "Claro. ¿Y qué más?"
        d.saw_reply(reply, lemmas(reply))
        prev_intro = step.intro[0] if step.intro and step.intro[0].state == "introduced" else None
        prev_elicit = el[0] if el else None
        if phase == "recap":
            break
    return log


# ---- seed: two learning words and a due known word, plus some that must not come back ----
now = time.time()
v.record_taught("seed", [{"english": "to order", "spanish": "pedir"}, {"english": "to pay", "spanish": "pagar"}])
v.db.execute("update word_state set due_at=? where lemma in ('pedir','pagar')", (now + 600,))  # not due yet
for lemma, status, due in (("perro", "known", now - 60), ("comer", "known", now + 9999), ("de", "known", now - 60),
                           ("casa", "learning", now - 120)):
    v._row(lemma)
    v.db.execute("update word_state set status=?, due_at=? where lemma=?", (status, due, lemma))
v.db.commit()

# ---- review_words / translation ----
due = [w["lemma"] for w in v.review_words(limit=10)]
check(due == ["casa", "perro"], f"review_words due only: {due}")  # learning before known; grammar/not-due out
allw = [w["lemma"] for w in v.review_words(limit=10, due_only=False)]
check(allw[:2] == ["casa", "perro"] and set(allw[2:]) == {"pedir", "pagar"} and "comer" not in allw,
      f"review_words due_only=False: {allw}")
check(v.review_words(limit=1, due_only=False)[0]["lemma"] == "casa", "review_words limit")
check(v.translation("pedir") == "to order" and v.translation("nonexistentword") is None, "Vocab.translation")

# ---- targets: up to 3 review words, taught meanings ----
d = Director(lessons["family"], v)
review = [t for t in d.targets if t.kind == "review"]
check(len(review) == 3, f"family: review targets {[(t.lemma, t.english) for t in review]}")
ped = next((t for t in d.targets if t.lemma == "pedir"), None)
check(ped is None or ped.english == "to order", f"pedir is taught as 'to order': {ped}")
ped_t = d._target("pedir", "review")
check(ped_t is not None and ped_t.english == "to order", f"_target review meaning: {ped_t}")
new_t = d._target("cuenta: the bill", "new")
check(new_t is not None and new_t.english == "the bill", f"_target new keeps the lesson gloss: {new_t}")

# ---- pacing with a learner who answers: intro -> elicit that word -> intro -> ... ----
for lid in ("family", "cafe", "day", "free"):
    d = Director(lessons[lid], v)
    log = play(d, "good")
    print(f"{lid} (good): " + " ".join(
        f"{r['turn']}:{r['phase'][:3]}" + (f"+{r['intro'][0]}" if r["intro"] else "")
        + (f"?{r['elicit'].lemma}{r['elicited']}" if r["elicit"] else "") for r in log))
    for i, r in enumerate(log):
        check(len(r["intro"]) <= 1, f"{lid}: two words introduced at turn {r['turn']}")
        if r["intro"]:
            check(r["note"].startswith("[Lesson step: if it fits naturally with what they just said, use the new word «"),
                  f"{lid}: intro note: {r['note']}")
            nxt = log[i + 1] if i + 1 < len(log) else None
            if nxt and nxt["phase"] != "recap":
                check(nxt["elicit"] is not None and nxt["elicit"].lemma == r["intro"][0] and not nxt["intro"]
                      and nxt["elicited"] == 1 and nxt["coach"] == "",
                      f"{lid}: turn after introducing {r['intro'][0]} doesn't elicit it: {nxt}")
    last = log[-1]
    check(last["phase"] == "recap", f"{lid}: no recap ({last})")
    check(last["turn"] <= MAX_LESSON_TURNS, f"{lid}: recap after {last['turn']} turns")
    for t in d.targets:
        if t.kind == "review":
            check(t.elicited >= 1 or t.state == "used", f"{lid}: review {t.lemma} never asked for before the recap")
    print(f"  used {d.used_new()}/{d.goal}, review {[(t.lemma, t.elicited, t.state) for t in d.targets if t.kind == 'review']}")

# ---- the practice picks alternate kinds ----
d = Director(lessons["family"], v)
log = play(d, "silent")
picks = [r["elicit"].kind for r in log if r["phase"] == "practice" and r["elicit"] is not None]
print("family (silent) practice picks:", picks)
check(picks[:2] == ["review", "new"], f"practice picks don't alternate: {picks}")
check(log[-1]["phase"] == "recap" and log[-1]["turn"] <= MAX_LESSON_TURNS, f"silent learner: no hard cap {log[-1]}")
check(log[-1]["turn"] == MAX_LESSON_TURNS, f"silent learner should run to the cap: {log[-1]['turn']}")

# ---- a word Sofía didn't say goes back to pending and is introduced again ----
d = Director(lessons["family"], v)
d.start_note()
d.observe(set())
d.observe(set())
step = d.instruction()
check(d.phase == "introduce" and len(step.intro) == 1, f"no intro after the warmup: {d.phase} {step}")
w = step.intro[0]
missing = d.saw_reply("¡Qué bonito! ¿Tienes esposo?", lemmas("¡Qué bonito! ¿Tienes esposo?"))
check(missing == [w.lemma] and w.state == "pending" and w.intro_turn is None, f"not reset: {missing} {w}")
d.observe(set())
step2 = d.instruction()
# A word held back yields to one that wasn't, so a word that won't fit doesn't block the lesson.
check(w.deferred == 1 and len(step2.intro) == 1 and step2.intro[0] is not w, f"held-back word not yielded: {step2}")
w2 = step2.intro[0]
said = f"Sí, {w2.display.upper()}."
check(d.saw_reply(said, set()) == [] and w2.state == "introduced", "an accent/case variant of the word counts")
# Held back twice: the note gets firm («now»).
w.deferred = 2
check("in your reply now" in d._introduce(w).note, "firm note after two hold-backs")
# an echoed pending word ("introduced" by observe, not by the instruction) is never reset
d2 = Director(lessons["family"], v)
p0 = next(t for t in d2.targets if t.state == "pending")
d2.observe({p0.lemma}, echoed=frozenset({p0.lemma}))
d2.instruction()
d2.saw_reply("Vale.", set())
check(p0.state == "introduced", f"an echoed word was reset by saw_reply: {p0}")

# ---- an early goodbye: the next note says the scene continues ----
d = Director(lessons["cafe"], v)
d.start_note()
d.saw_reply("¡Hola! Bienvenido.", set())
d.observe(set())
d.instruction()
d.saw_reply("Aquí tienes tu café. ¡Adiós!", set())
d.observe(set())
n1 = d.instruction().note
check(n1.startswith(CONTINUE_NOTE + "\n["), f"no continue note after an early goodbye: {n1}")
d.saw_reply("Muy bien. ¿Algo más?", set())
d.observe(set())
check(not d.instruction().note.startswith(CONTINUE_NOTE), "continue note repeated")
for bye in ("Nos vemos.", "HASTA LUEGO", "Hasta pronto, amigo", "adios"):
    d.saw_reply(bye, set())
    check(d.no_goodbye, f"goodbye not seen: {bye}")
    d.no_goodbye = False
d.saw_reply("Nosotros vemos el mar.", set())
check(not d.no_goodbye, "'nos vemos' matched inside other words")
d.finish()
step = d.instruction()
check(d.phase == "recap" and "goodbye" in step.note and not step.note.startswith(CONTINUE_NOTE), "recap note")
d.saw_reply("¡Adiós, hasta pronto!", set())
check(not d.no_goodbye, "the recap goodbye set the flag")

# ---- a lesson with no targets still ends after the warmup (as before) ----
d = Director({"id": "x", "words": []}, v)
d.targets, d.warmup_turns = [], 1  # nothing to teach or review
d.start_note()
d.observe(set())
check(d.phase == "recap", f"empty lesson: {d.phase}")

# ---- a continuation (they went on talking before her answer) is the same turn, and its step is redone ----
d = Director(lessons["cafe"], v)
d.start_note()
d.saw_reply("¡Hola! ¿Qué tal?", set())
first = Step("", [])
for _ in range(4):  # through the warmup, up to the first introduction
    d.observe(set())
    first = d.instruction()
    if first.intro:
        break
    d.saw_reply("Claro. ¿Y qué más?", set())
check(bool(first.intro), "continuation setup: no word introduced")
w = first.intro[0] if first.intro else Target("x", "x", "x", "x", "new")
turn, pt, kind, picks = d.turn, d.phase_turns, d.last_kind, d.picks
w.state, w.intro_turn = "pending", None  # app.py: cut before she said it
d.observe(set(), continuation=True)
check((d.turn, d.phase_turns) == (turn, pt), f"continuation counted a turn: {(d.turn, d.phase_turns)} vs {(turn, pt)}")
again = d.instruction()
check([t.lemma for t in again.intro] == [w.lemma] and d.last_kind == kind == "introduce" and d.picks == picks,
      f"continuation step not redone: {[t.lemma for t in again.intro]} vs {w.lemma}")
d.saw_reply(reply_for(again), lemmas(reply_for(again)))
d.observe(set())
nxt = d.instruction()
check(not nxt.intro and w.elicited == 1, f"after a continuation the introduced word isn't elicited ({w.elicited})")
# the elicitation redone by a continuation counts once, and it is still the follow-up of the introduction
d.observe(set(), continuation=True)
redo = d.instruction()
check(not redo.intro and w.elicited == 1 and redo.note == nxt.note, f"redone elicitation: elicited {w.elicited}")
# a cut goodbye: the continuation gets the goodbye again (not "free")
d3 = Director(lessons["cafe"], v)
d3.start_note()
d3.observe(set())
d3.finish()
d3.instruction()
check(d3.recap_sent, "recap not sent")
d3.observe(set(), continuation=True)
check(d3.phase == "recap" and "goodbye" in d3.instruction().note, f"continued goodbye: phase {d3.phase}")

# ---- lesson_state carries elicited ----
st = Director(lessons["family"], v).state()
check(all("elicited" in t and "kind" in t for t in st["targets"]), "lesson_state targets lack elicited/kind")

if failures:
    print("\nFAILED:\n  " + "\n  ".join(failures))
    sys.exit(1)
print("\nall pacing tests passed")
