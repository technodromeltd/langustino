"""Spaced-repetition rules of vocab.py and the director's echo handling. Plain asserts, scratch DB, fake clock.

    cd habla/server && uv run python scripts/test_srs.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="habla-srs-")
os.environ["HABLA_DB"] = str(Path(_tmp) / "srs.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import config, vocab as vocab_mod  # noqa: E402
from habla.director import Director  # noqa: E402
from habla.vocab import DAY, Vocab  # noqa: E402

clock = [1_800_000_000.0]
vocab_mod.time = types.SimpleNamespace(time=lambda: clock[0], strftime=time.strftime)  # type: ignore[assignment]


def at(seconds: float) -> None:
    clock[0] = T0 + seconds


def row(v: Vocab, lemma: str) -> dict:
    r = v.db.execute("select * from word_state where lemma=?", (lemma,)).fetchone()
    return dict(r) if r else {}


T0 = clock[0]
v = Vocab()
sid = "test"


def case(name: str):
    def wrap(f):
        f()
        print(f"ok  {name}")
        return f
    return wrap


@case("(a) a word missed in Repaso can't become known in the same round")
def _a():
    at(0)
    v.record_taught(sid, [{"english": "dog", "spanish": "el perro"}])
    v.record_review(sid, "perro", False)
    at(20)
    v.record_review(sid, "perro", True)
    at(40)
    v.record_review(sid, "perro", True)
    r = row(v, "perro")
    assert r["status"] == "learning" and r["streak"] <= 1, r
    kinds = [e["kind"] for e in v.db.execute("select kind from events where lemma='perro' order by id")]
    assert kinds.count("review_practice") == 2, kinds


@case("(b) echoing Sofía's words doesn't make them known")
def _b():
    at(100)
    out = v.record_learner(sid, "Sí, quiero un café con leche", [], [], echo=frozenset({"querer", "café", "leche"}))
    assert out.get("café") == "ok", out
    for w in ("querer", "café", "leche"):
        r = row(v, w)
        assert (r["status"], r["streak"]) == ("learning", 0), (w, r)


@case("(c) a spontaneous word is known on the next use another day; a due known word moves forward")
def _c():
    at(200)
    v.record_learner(sid, "Quiero agua", [], [])
    r = row(v, "agua")
    assert (r["status"], r["streak"]) == ("learning", 2), r
    at(300)  # same day again: not due, nothing changes but the count
    v.record_learner(sid, "Quiero agua", [], [])
    assert row(v, "agua")["status"] == "learning"
    at(21 * 3600)
    v.record_learner(sid, "Quiero agua", [], [])
    r = row(v, "agua")
    assert r["status"] == "known" and r["due_at"] >= clock[0] + 3 * DAY, r
    due_before = r["due_at"]
    at(5 * DAY)
    v.record_learner(sid, "Quiero agua", [], [])
    r = row(v, "agua")
    assert r["streak"] == 4 and r["due_at"] > due_before, r
    # An echo of a due known word is not a review
    at(30 * DAY)
    v.record_learner(sid, "Agua, por favor", [], [], echo=frozenset({"agua"}))
    assert row(v, "agua")["streak"] == 4


@case("(d) a taught word needs successes ~2 min, ~10 min and a day apart")
def _d():
    base = 40 * DAY
    at(base)
    v.record_taught(sid, [{"english": "table", "spanish": "la mesa"}])
    at(base + 3 * 60)
    v.record_learner(sid, "La mesa", [], [])
    assert (row(v, "mesa")["status"], row(v, "mesa")["streak"]) == ("learning", 1)
    at(base + 5 * 60)  # not due yet
    v.record_learner(sid, "La mesa", [], [])
    assert row(v, "mesa")["streak"] == 1
    at(base + 15 * 60)
    v.record_learner(sid, "La mesa", [], [])
    assert (row(v, "mesa")["status"], row(v, "mesa")["streak"]) == ("learning", 2)
    at(base + 15 * 60 + DAY)
    v.record_learner(sid, "La mesa", [], [])
    r = row(v, "mesa")
    assert (r["status"], r["streak"]) == ("known", 3), r


@case("(e) a taught sentence is split into its content words, never stored as a phrase")
def _e():
    at(50 * DAY)
    out = v.record_taught(sid, [{"english": "I work with computers", "spanish": "yo trabajo con computadoras"}])
    assert row(v, "yo trabajo con computadoras") == {}, "phrase stored as a lemma"
    got = {r["lemma"] for r in v.db.execute("select lemma from word_state where taught>0")}
    assert got & {"trabajar", "computadora"}, got
    assert not any(" " in g for g in got), got
    assert out and out[0]["spanish"] == "yo trabajo con computadoras", out
    ev = v.db.execute("select detail from events where kind='taught' and lemma='trabajar'").fetchone()
    assert ev and "computadoras" in json.loads(ev["detail"])["spanish"]
    # A short set phrase still behaves as before (one entry)
    out = v.record_taught(sid, [{"english": "please", "spanish": "por favor"}])
    assert out[0]["lemma"] == "por favor", out


@case("(f) the headline count leaves out function words; known_all and total_content are there")
def _f():
    at(60 * DAY)
    p0 = v.progress()
    v.record_learner(sid, "de", [], [])
    at(62 * DAY)
    v.record_learner(sid, "de", [], [])
    assert row(v, "de")["status"] == "known", row(v, "de")
    p1 = v.progress()
    assert p1["known"] == p0["known"], (p0["known"], p1["known"])
    assert p1["known_all"] == p0["known_all"] + 1
    assert 0 < p1["total_content"] < p1["total"], p1["total_content"]
    assert p1["known"] >= 2  # agua, mesa


@case("(g) one-time migration: old one-use 'known' words go back to learning")
def _g():
    now = clock[0]
    v.db.execute("insert into word_state(lemma, rank, status, produced, taught, streak, updated_at) "
                 "values ('casa', 1, 'known', 1, 0, 1, ?), ('perro2', 2, 'known', 3, 0, 1, ?)", (now, now))
    v.db.execute("delete from settings where key='migr_srs_v2'")
    v.db.commit()
    v2 = Vocab()
    r = row(v2, "casa")
    assert (r["status"], r["streak"]) == ("learning", 2) and r["due_at"] == now + DAY, r
    assert row(v2, "perro2")["status"] == "known"
    assert row(v2, "agua")["status"] == "known"  # produced 4 times
    v2.db.execute("update word_state set status='known' where lemma='casa'")
    v2.db.commit()
    v3 = Vocab()  # the key is set: no second migration
    assert row(v3, "casa")["status"] == "known"


@case("(h) the director: an echo never makes a target 'used' or 'known'")
def _h():
    lessons = {lesson["id"]: lesson for lesson in json.loads(config.LESSONS_PATH.read_text())}
    d = Director(lessons["cafe"], v)
    pending = [t for t in d.targets if t.kind == "new" and t.state == "pending"]
    assert len(pending) >= 2, [(t.lemma, t.state) for t in d.targets]
    t, u = pending[0], pending[1]
    d.observe({t.lemma}, echoed=frozenset({t.lemma}))
    assert t.state == "introduced" and t.intro_turn == d.turn, t
    d.observe({t.lemma}, echoed=frozenset({t.lemma}))
    assert t.state == "practiced", t
    d.observe({t.lemma}, echoed=frozenset({t.lemma}))
    assert t.state == "practiced", t
    d.observe({t.lemma})
    assert t.state == "used", t
    d.observe({u.lemma})  # said on their own: already known, swapped out (as before)
    assert u.state == "known", u
    # A wrong echo is undone like any other upgrade
    w = next(x for x in d.targets if x.state == "pending" and x.kind == "new")
    d.observe({w.lemma}, echoed=frozenset({w.lemma}))
    d.correct({w.lemma})
    assert w.state == "pending", w


@case("(i) the 2000-word list + everyday supplement: loading, old rows, lesson word choice")
def _i():
    assert v.total_ranked == 2000 and v.total_extra >= 100, (v.total_ranked, v.total_extra)
    m = v.info("manzana")
    assert m and m["supplement"] and m["rank"] > 2000 and v.display("manzana") == "la manzana", m
    assert v.info("vino")["partOfSpeech"] == "noun" and v.display("gafas") == "las gafas"
    toks = {t.lemma: t.in_list for t in v.tokens("Quiero una manzana y un zumo de naranja")}
    assert toks.get("manzana") and toks.get("zumo") and toks.get("naranja"), toks
    p = v.progress()
    assert p["total"] == p["total_ranked"] + p["total_extra"] and p["total_content"] > 1000, p["total_content"]
    # A word tracked while it was outside the list (rank null) becomes a list word on the next start
    v.db.execute("insert into word_state(lemma, rank, status, updated_at) values ('maleta', null, 'learning', ?)",
                 (clock[0],))
    v.db.commit()
    v2 = Vocab()
    r = row(v2, "maleta")
    assert r["rank"] == v2.info("maleta")["rank"] and r["translation"] == "suitcase", r
    # Free conversation skips written-only / rarely spoken words; ranks 1-1000 (no ratings) still count
    common = v.next_common(3000)
    assert common and all(v.lesson_worthy(v.info(w)) for w in common)
    assert "mediante" not in common and "realizar" not in common and "comisión" not in common
    assert any(v.info(w)["rank"] <= 1000 for w in common) and any(v.info(w)["rank"] > 1000 for w in common)
    # Themed lessons top up their pool from the theme field
    food = v.theme_words(["food_drink"], 15)
    assert len(food) == 15 and all(v.info(w)["theme"] == "food_drink" and v.status(w) not in ("learning", "known")
                                   for w in food), food
    lessons = {lesson["id"]: lesson for lesson in json.loads(config.LESSONS_PATH.read_text())}
    for lesson in lessons.values():
        for entry in lesson.get("words", []):
            assert v.info(entry.partition(":")[0].strip()), (lesson["id"], entry)
    d = Director(lessons["cafe"], v)
    assert any((v.info(e) or {}).get("theme") == "food_drink" for e in d.pool), d.pool


print("all SRS tests passed")
