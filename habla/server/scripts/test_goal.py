"""The daily goal in sentences: practice events (typed / spoken turns, graded attempts; not voice commands or the
rest of a continued turn), stats(), the streak, settings, and the one-time migration from the old minutes goal.
Plain asserts, scratch DB; respond() and the speech recognizer are stubs (no server, no models, no LLM).

    cd habla/server && uv run python scripts/test_goal.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

DB = Path(tempfile.mkdtemp(prefix="habla-goal-")) / "goal.db"
os.environ["HABLA_DB"] = str(DB)  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import app as A  # noqa: E402
from habla.director import Director  # noqa: E402
from habla.vocab import SCHEMA, Vocab  # noqa: E402

failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


# ---------- migration: an old minutes goal becomes the nearest sentence goal ----------
for minutes, want in (("10", "30"), ("2", "10"), ("12", "30"), ("20.0", "50"), ("abc", "20")):
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    db.execute("delete from settings")
    db.execute("insert into settings(key, value) values ('goal_minutes', ?)", (minutes,))
    db.commit()
    db.close()
    v = Vocab()
    check(v.setting("goal_sentences", "") == want, f"migration: goal_minutes {minutes} -> {want} sentences "
                                                   f"(got {v.setting('goal_sentences', '')!r})")
    v.db.close()
db = sqlite3.connect(DB)
db.execute("delete from settings")
db.execute("insert into settings(key, value) values ('goal_minutes', '15'), ('goal_sentences', '10')")
db.commit()
db.close()
v = Vocab()
check(v.setting("goal_sentences", "") == "10", "migration: an existing goal_sentences is kept")
v.db.execute("delete from settings")
v.db.commit()
v.db.close()
v = Vocab()
check(v.setting("goal_sentences", "") == "", "migration: nothing to migrate without goal_minutes")
A.E.vocab = v

# ---------- stats ----------
st = v.stats()
check(st["today_sentences"] == 0 and st["goal_sentences"] == 20 and st["streak"] == 0
      and {"today_seconds", "goal_minutes", "due_reviews"} <= set(st), f"empty stats: {st}")
v.record_practice("s", "turn")
v.record_practice("s", "attempt")
v.record_practice("s", "review")
st = v.stats(goal_sentences=3)
check(st["today_sentences"] == 3 and st["goal_sentences"] == 3 and st["streak"] == 1, f"3 practice events: {st}")
check(st["today_seconds"] == 0, "typed practice adds no speaking seconds")
# a day with only practice (typed) still counts toward the streak
yday = time.time() - 86400
v.db.execute("insert into events(ts, session_id, lemma, kind, detail) values (?, 's', null, 'practice', 'turn')",
             (yday,))
v.db.execute("insert into events(ts, session_id, lemma, kind, detail) values (?, 's', null, 'spoke', '3.0')",
             (yday - 86400,))
v.db.commit()
check(v.stats()["streak"] == 3, f"streak counts practice and spoke days: {v.stats()['streak']}")
check(v.stats()["today_sentences"] == 3, "yesterday's practice isn't today's")

# ---------- settings ----------
A.E.tts = SimpleNamespace(default="test-voice")
s0 = A.Session(SimpleNamespace())  # type: ignore[arg-type]
s0.update_settings({"goal_sentences": 40})
check(A.current_settings()["goal_sentences"] == 40 and A.progress_payload()["stats"]["goal_sentences"] == 40,
      "settings: goal_sentences saved and used by progress")
s0.update_settings({"goal_sentences": "nope"})
s0.update_settings({"goal_sentences": 0})
s0.update_settings({"goal_sentences": 5000})
check(A.current_settings()["goal_sentences"] == 40, "settings: invalid goals are ignored")
check(A.current_settings()["goal_choices"] == [10, 20, 30, 40, 50], "settings: goal choices")


# ---------- which learner turns count ----------
class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


async def fake_respond(self, t_end, user_turn, user_text, english, **kw) -> None:
    self.turn_no += 1


async def _no_wav(self, text, speed=None) -> str:
    return ""


A.Session.respond = fake_respond  # type: ignore[method-assign]
A.Session.synth_b64 = _no_wav  # type: ignore[method-assign]
A.E.stt = SimpleNamespace(transcribe=lambda audio, ctx: SimpleNamespace(text="el perro", duration=1.0))


def today() -> int:
    return v.stats()["today_sentences"]


async def main() -> None:
    s = A.Session(FakeWS())  # type: ignore[arg-type]
    s.lesson = A.LESSONS["cafe"]
    s.director = Director(s.lesson, v)
    s.director.start_note()
    s.history = [{"role": "user", "content": "[Session start]"},
                 {"role": "assistant", "content": json.dumps({"reply": "¡Hola! ¿Qué quieres tomar?"})}]
    n = today()
    await s.learner_said("Quiero un café", "es", time.time())  # typed (same path as a spoken turn after STT)
    check(today() == n + 1, "a typed turn counts")
    await s.learner_said("¿Qué significa perro?", "es", time.time())
    check(today() == n + 1, "a voice command doesn't count")
    o = A.OpenTurn("u9", echo=frozenset(), prev_reply="")
    await s.learner_said("Quiero un café con leche", "es", time.time(), continuation=o)
    check(today() == n + 1, "the rest of a continued turn doesn't count again")
    await s.attempt(None, {"kind": "review", "target": "el perro", "lemma": "perro", "key": "k", "n": 1})
    await s.attempt(None, {"kind": "shadow", "target": "el perro", "key": "k2", "n": 2})
    check(today() == n + 3, "graded attempts count")
    kinds = [r["detail"] for r in v.db.execute("select detail from events where kind='practice' order by id")]
    check(kinds[-3:] == ["turn", "review", "attempt"], f"practice details: {kinds[-3:]}")


asyncio.run(main())

if failures:
    print(f"\n{len(failures)} FAILED")
    sys.exit(1)
print("\nall passed")
