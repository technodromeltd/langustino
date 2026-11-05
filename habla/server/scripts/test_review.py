"""Repaso: fixes to remember (queue, ladder, drop), the combined review list, and fair grading (numbers, articles,
same-meaning words). Plain asserts, scratch DB, fake clock; Session.attempt runs with a stub speech recognizer.

    cd habla/server && uv run python scripts/test_review.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ["HABLA_DB"] = str(Path(tempfile.mkdtemp(prefix="habla-review-")) / "review.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import app as A  # noqa: E402
from habla import vocab as vocab_mod  # noqa: E402
from habla.practice import article_mismatch, grade, spell_numbers  # noqa: E402
from habla.vocab import DAY, INTERVALS, Vocab  # noqa: E402

clock = [1_800_000_000.0]
T0 = clock[0]
vocab_mod.time = types.SimpleNamespace(time=lambda: clock[0], strftime=time.strftime)  # type: ignore[assignment]
failures: list[str] = []


def at(seconds: float) -> None:
    clock[0] = T0 + seconds


def check(ok: bool, msg: str) -> None:
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


v = Vocab()
A.E.vocab = v

SOY = {"wrong": "soy cansado", "right": "estoy cansado", "type": "ser_estar",
       "why": "Use estar for how you feel right now.", "right_en": "I am tired"}

# ---------- A. fix items ----------
n = v.add_fixes([
    SOY,
    {"wrong": "la casa grande", "right": "la casa es grande", "type": "other", "why": "x", "right_en": "big"},
    {"wrong": "un cerveza", "right": "una cerveza", "type": "gender", "why": "cerveza is feminine", "right_en": ""},
    {"wrong": "x", "right": "uno dos tres cuatro cinco seis siete ocho", "type": "word_order", "why": "y",
     "right_en": "too long"},
    {"wrong": "un cerveza", "right": "una cerveza", "type": "gender", "why": "cerveza is feminine",
     "right_en": "a beer"},
])
rows = [dict(r) for r in v.db.execute("select * from fix_items order by id")]
check(n == 2 and [r["right"] for r in rows] == ["estoy cansado", "una cerveza"],
      f"add_fixes keeps real 1-7 word fixes with an English meaning: {[r['right'] for r in rows]}")
soy = rows[0]
check(soy["due_at"] == T0 + 600 and soy["streak"] == 0 and soy["status"] == "active", "a new fix is due in 10 min")
check(v.fix_queue() == [], "nothing due right away")

at(300)
v.add_fixes([{**SOY, "wrong": "Yo soy cansado", "why": "estar: a feeling"}])
r = v.fix(soy["id"])
check(r["due_at"] == T0 + 600 and r["wrong"] == "Yo soy cansado" and r["why"] == "estar: a feeling",
      "the same right form again: wrong/why updated, due stays the earlier one")
check(v.db.execute("select count(*) n from fix_items").fetchone()["n"] == 2, "no duplicate row")

at(700)
q = v.fix_queue()
check([f["right"] for f in q] == ["estoy cansado", "una cerveza"], "due fixes, oldest due first")
check(v.stats(5)["due_reviews"] == 2, f"stats.due_reviews counts due fixes: {v.stats(5)['due_reviews']}")

r = v.record_fix_review(soy["id"], True)
check(r["streak"] == 1 and r["due_at"] == clock[0] + INTERVALS[1], "right when due: streak 1, due +10 min")
r = v.record_fix_review(soy["id"], True)
check(r["streak"] == 1 and r["due_at"] == T0 + 700 + INTERVALS[1], "right again before it's due: no change")
r = v.record_fix_review(soy["id"], False)
check(r["streak"] == 0 and r["due_at"] == clock[0] + 120 and r["status"] == "active", "a miss: streak 0, due +2 min")
for i, (wait, streak) in enumerate([(120, 1), (600, 2), (DAY, 3)]):
    at(clock[0] - T0 + wait)
    r = v.record_fix_review(soy["id"], True)
    check(r["streak"] == streak and r["status"] == "active", f"spaced success {i + 1}: streak {streak}")
at(clock[0] - T0 + 3 * DAY)
r = v.record_fix_review(soy["id"], True)
check(r["streak"] == 4 and r["status"] == "done", "streak 4: done")
check(all(f["id"] != soy["id"] for f in v.fix_queue()), "a done fix isn't queued")
v.add_fixes([SOY])
r = v.fix(soy["id"])
check(r["status"] == "active" and r["streak"] == 0 and r["due_at"] == clock[0] + 600,
      "the same mistake after it was retired: active again, from the start")

beer = rows[1]["id"]
v.drop_fix(beer)
check(v.fix(beer)["status"] == "dropped" and all(f["id"] != beer for f in v.fix_queue()), "drop_fix: never queued")
v.add_fixes([{"wrong": "un cerveza", "right": "una cerveza", "type": "gender", "why": "f", "right_en": "a beer"}])
check(v.fix(beer)["status"] == "dropped", "a dropped fix stays dropped")
check(v.record_fix_review(99999, True) is None, "unknown id: nothing happens")

# ---------- combined review list ----------
at(clock[0] - T0 + 700)  # SOY due again
now = clock[0]


def learning(lemma: str, due: float) -> None:
    w = v.words[lemma]
    v.db.execute("insert or replace into word_state(lemma, rank, translation, status, streak, due_at, updated_at) "
                 "values (?,?,?,'learning',1,?,?)", (lemma, w["rank"], w["translation"], due, now))
    v.db.commit()


learning("perro", now - 10)
learning("casa", now + 3600)  # not due: extra practice
items = v.review_items()
check([i["kind"] for i in items][:1] == ["fix"] and items[0]["right"] == "estoy cansado",
      f"fewer than 3 due words: fixes first {[i.get('right') or i.get('lemma') for i in items]}")
fx = items[0]
check(set(fx) == {"kind", "id", "right", "right_en", "wrong", "why", "type"}, f"fix item shape: {sorted(fx)}")
check(all(i["kind"] == "word" and "alts" in i for i in items[1:]), "word items have kind 'word' and alts")

for lemma in ("gato", "mesa", "libro", "agua", "coche", "calle", "ciudad"):
    learning(lemma, now - 5)
for k in range(5):  # more due fixes
    v.add_fixes([{"wrong": f"x{k}", "right": f"tengo {k + 2} gatos", "type": "verb_form", "why": "w",
                  "right_en": "I have cats"}])
at(clock[0] - T0 + 700)
items = v.review_items()
kinds = "".join("f" if i["kind"] == "fix" else "w" for i in items)
check(kinds == "wwwfwwwfwwwf" and len(items) == 12, f"1 fix after every 3 words, 12 at most: {kinds}")

# ---------- B. fair grading ----------
check(grade("Tengo veinticinco años", "Tengo 25 años")["score"] == 1.0, "25 = veinticinco")
check(grade("Son tres euros", "Son 3 euros")["score"] == 1.0, "3 = tres")
check(grade("Tengo treinta y un años", "tengo 31 años")["score"] == 1.0, "31 = treinta y uno (un)")
check(grade("Cuesta mil euros", "Cuesta 1.000 euros")["score"] == 1.0 and
      grade("Cuesta mil euros", "Cuesta 1,000 euros")["score"] == 1.0, "1.000 / 1,000 = mil")
check(grade("Quiero una cerveza", "Quiero 1 cerveza")["score"] == 1.0, "1 = una")
check(grade("Quiero un café", "Quiero uno café")["score"] == 1.0, "uno = un")
check(grade("Quiero una cerveza", "Quiero un cerveza")["score"] < 0.8, "un != una: a gender fix still fails")
check(spell_numbers("100, 200, 300, 500, 2000, 7") == "cien, doscientos, trescientos, quinientos, 2000, siete",
      f"round numbers; others unchanged: {spell_numbers('100, 200, 300, 500, 2000, 7')}")
check(len(grade("Son 45 euros", "Son cuarenta y cinco euros")["words"]) == 5, "digits in a target are spelled out too")

check(article_mismatch("el perro", "la perro") == "la perro", "«el perro» heard «la perro»: mismatch")
check(article_mismatch("el perro", "perro") is None, "bare noun: no mismatch")
check(article_mismatch("el perro", "un perro") is None, "un perro for el perro: same gender, fine")
check(article_mismatch("el agua", "la agua") == "la agua", "«el agua» heard «la agua»: mismatch")
check(article_mismatch("perro", "la perro") is None, "a target without an article: never")

check("vez" in v.alts("tiempo") and "tiempo" in v.alts("vez"), f"tiempo and vez share 'time': {v.alts('tiempo')}")
check(v.review_display("finlandia") == "Finlandia" and v.review_display("caro/a") == "caro",
      "display: proper nouns capitalized, first form of caro/a")

# ---------- Session.attempt ----------
HEARD = {"text": ""}
A.E.tts = types.SimpleNamespace(default="test-voice")
A.E.stt = types.SimpleNamespace(transcribe=lambda audio, ctx: types.SimpleNamespace(text=HEARD["text"], duration=1.0))


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


async def _no_wav(self, text, speed=None) -> str:
    return ""


A.Session.synth_b64 = _no_wav  # type: ignore[method-assign]
s = A.Session(FakeWS())  # type: ignore[arg-type]


def attempt(heard: str, **a) -> dict:
    HEARD["text"] = heard
    s.ws.sent.clear()
    asyncio.run(s.attempt(None, a))
    return next(m for m in s.ws.sent if m["type"] == "attempt_result")


def wrow(lemma: str) -> dict:
    return dict(v.db.execute("select * from word_state where lemma=?", (lemma,)).fetchone())


r = attempt("la perro", kind="review", target="el perro", lemma="perro", key="k", n=1)
check(not r["ok"] and r["note"] == "«el perro», not «la perro»", f"review «el perro» heard 'la perro' fails: {r['note']}")
check(wrow("perro")["streak"] == 0, "  ... recorded as a miss")
learning("perro", clock[0] - 1)
r = attempt("Perro.", kind="review", target="el perro", lemma="perro", key="k", n=2)
check(r["ok"] and r["note"] is None, "review «el perro» heard 'Perro.' passes")
learning("tiempo", clock[0] - 1)
r = attempt("Vez.", kind="review", target="el tiempo", lemma="tiempo", key="k", n=3)
check(r["ok"] and "also right" in (r["note"] or "") and wrow("tiempo")["streak"] == 2,
      f"review «el tiempo» heard 'vez' passes as also right: {r['note']}")

at(clock[0] - T0 + DAY)
fid = soy["id"]
before = v.fix(fid)
r = attempt("Estoy cansado.", kind="fix", target="estoy cansado", fix_id=fid, key="f", n=4)
after = v.fix(fid)
check(r["ok"] and after["streak"] == before["streak"] + 1 and after["due_at"] > before["due_at"],
      "fix: the right form passes and moves its due date")
r = attempt("Soy cansado.", kind="fix", target="estoy cansado", fix_id=fid, key="f", n=5)
check(not r["ok"] and r["note"] == SOY["why"] and v.fix(fid)["streak"] == 0, "fix: the wrong form fails and shows why")

if failures:
    print(f"\n{len(failures)} FAILED")
    sys.exit(1)
print("\nall passed")
