"""Continuations in app.py: a learner who goes on talking before hearing Sofía's answer. Session.reopen() takes
the first part (and her unheard answer) back out of the history, and learner_said(continuation=...) redoes the
turn under the same id. No server, no models, no LLM: respond() is replaced by a stub that either commits a reply
or leaves the turn the way a cut with played=0 does. Also the stale-attempt age check's constant.

    cd habla/server && uv run python scripts/test_continue.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["HABLA_DB"] = str(Path(tempfile.mkdtemp(prefix="habla-continue-")) / "continue.db")  # before importing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import app as A  # noqa: E402
from habla.director import Director  # noqa: E402
from habla.vocab import Vocab  # noqa: E402

A.E.vocab = Vocab()
A.E.tts = SimpleNamespace(default="test-voice")
failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


MODE = {"respond": "cut"}  # "cut": like a cancel with played=0 before the reply was written; "commit": written
CALLS: list[dict] = []


async def fake_respond(self: A.Session, t_end, user_turn, user_text, english, intro=None, recap=False,
                       check_text=None, echo=frozenset(), prefer=frozenset(), coach="", unclear=frozenset(),
                       clarify=False) -> None:
    self.turn_no += 1
    tid = f"t{self.turn_no}"
    CALLS.append({"tid": tid, "user_turn": user_turn, "user_text": user_text, "check_text": check_text})
    text = check_text if check_text is not None else user_text
    rec = A.TurnRecord(tid=tid, user_turn=user_turn, user_text=text, english=english, reply="", coach=coach,
                       learner_en="", director=self.director, sid=self.id, lesson_id=self.lesson["id"], upgraded={},
                       recap=recap, t_end=t_end)
    if MODE["respond"] == "commit":
        rec.reply = "¡Claro! ¿Algo más?"
        entry = {"role": "assistant", "content": rec.history_entry()}
        self.history.append(entry)
        self.last_reply = rec.reply
        self.last_commit = A.Commit(rec, entry, [rec.reply])
        CALLS[-1]["rec"] = rec
    else:  # what respond()'s CancelledError path leaves when nothing was heard
        for t in intro or []:
            t.state, t.intro_turn = "pending", None
        self.unanswered = rec


A.Session.respond = fake_respond  # type: ignore[method-assign]

OPENER = "¡Hola! Bienvenido al café. ¿Qué quieres tomar?"


def session() -> A.Session:
    s = A.Session(FakeWS())  # type: ignore[arg-type]
    s.lesson = A.LESSONS["cafe"]
    s.director = Director(s.lesson, A.E.vocab)
    s.director.start_note()
    s.history = [{"role": "user", "content": "[Session start]"},
                 {"role": "assistant", "content": json.dumps({"reply": OPENER})}]
    s.last_reply = OPENER
    return s


def users(s: A.Session) -> list[dict]:
    return [m for m in s.ws.sent if m["type"] == "user"]  # type: ignore[attr-defined]


def user_msgs(s: A.Session) -> list[str]:
    return [m["content"] for m in s.history if m["role"] == "user"]


async def say(s: A.Session, text: str, cont=None, n=None) -> None:
    await s.learner_said(text, "es", time.time(), continuation=cont, cont_n=n)


async def main() -> None:
    # (a) cut before her answer was written, then the continuation
    s = session()
    MODE["respond"] = "cut"
    await say(s, "Yo quiero")
    turn = s.director.turn
    o = s.reopen()
    check(o is not None and o.user_turn == "u1", "a: the cut turn can be reopened")
    check(s.unanswered is None, "a: the cut turn isn't left unanswered")
    check(user_msgs(s) == ["[Session start]"], f"a: first part taken out of the history: {user_msgs(s)}")
    MODE["respond"] = "commit"
    await say(s, "Yo quiero un café con leche", cont=o, n=1)
    u = users(s)
    check([m["id"] for m in u] == ["u1", "u1"] and u[1].get("replace") and u[1].get("cont") == 1
          and not u[0].get("replace"), f"a: same id, replace + cont on the 2nd: {u}")
    msgs = user_msgs(s)
    check(len(msgs) == 2 and msgs[1].startswith("Yo quiero un café con leche") and "Yo quiero\n" not in msgs[1],
          f"a: one user message with the whole sentence: {msgs}")
    check(s.director.turn == turn, f"a: the director didn't count a turn ({turn} -> {s.director.turn})")
    check(CALLS[-1]["user_turn"] == "u1" and CALLS[-1]["check_text"] is None, f"a: answered as u1: {CALLS[-1]}")
    check(s.clarified == [False], f"a: one clarified entry per learner turn: {s.clarified}")
    # a normal next turn gets a new id
    await say(s, "Sí, gracias")
    check(users(s)[-1]["id"] == "u4" and not users(s)[-1].get("replace"),  # u1, t2 (cut), t3, then u4
          f"a: next turn gets a new id: {users(s)[-1]['id']}")

    # (b) her answer was already written (the learner was talking over its audio): it's dropped
    s = session()
    MODE["respond"] = "commit"
    await say(s, "Yo quiero")
    rec = CALLS[-1]["rec"]
    o = s.reopen()
    await asyncio.sleep(0.01)  # the tutor_dropped side task
    check(o is not None and rec.dropped, "b: reopened, her written answer marked dropped (no analysis)")
    check(any(m["type"] == "tutor_dropped" and m["id"] == rec.tid for m in s.ws.sent),  # type: ignore[attr-defined]
          "b: tutor_dropped sent for her unheard answer")
    check(s.last_reply == OPENER and s.last_commit is None, f"b: last_reply restored: {s.last_reply!r}")
    check([m["role"] for m in s.history] == ["user", "assistant"], f"b: history back to before: {s.history}")
    await say(s, "Yo quiero un café con leche", cont=o, n=1)
    msgs = user_msgs(s)
    check(len(msgs) == 2 and msgs[1].count("Yo quiero") == 1, f"b: no duplicated fragment: {msgs}")
    check(sum(1 for m in s.history if m["role"] == "assistant") == 2, "b: one answer to the opener, one to the turn")

    # (c) the first part had been merged with an earlier unanswered message: that one is kept, once
    s = session()
    MODE["respond"] = "cut"
    await say(s, "Hola")  # cut: unanswered u1, its message stays last in the history
    await say(s, "Yo quiero")  # merged into u1's message, and cut again
    check(s.unanswered is not None and s.unanswered.user_text == "Hola Yo quiero", "c: setup merged")
    o = s.reopen()
    check(o is not None and s.unanswered is not None and s.unanswered.user_turn == "u1",
          "c: the earlier unanswered turn is back")
    check(user_msgs(s)[-1].split("\n")[0] == "Hola" and "Yo quiero" not in user_msgs(s)[-1],
          f"c: first part taken off the merged message: {user_msgs(s)}")
    MODE["respond"] = "commit"
    await say(s, "Yo quiero un café", cont=o, n=1)
    last = user_msgs(s)[-1]
    check(last.count("Hola") == 1 and last.count("Yo quiero") == 1 and len(user_msgs(s)) == 2,
          f"c: one merged message, nothing twice: {user_msgs(s)}")
    check(CALLS[-1]["check_text"] == "Hola Yo quiero un café", f"c: checked together: {CALLS[-1]['check_text']}")
    merged = [m for m in s.ws.sent if m["type"] == "feedback" and m.get("merged")]  # type: ignore[attr-defined]
    check(all(m["id"] == "u1" for m in merged), "c: merged feedback only for u1")

    # (d) a third part: continue the continuation
    s = session()
    MODE["respond"] = "cut"
    await say(s, "Yo quiero")
    turn = s.director.turn
    o = s.reopen()
    await say(s, "Yo quiero un café", cont=o, n=1)
    o = s.reopen()
    check(o is not None and o.user_turn == "u1" and o.observed, "d: the continuation can be continued")
    MODE["respond"] = "commit"
    await say(s, "Yo quiero un café con leche", cont=o, n=2)
    check([m["id"] for m in users(s)] == ["u1"] * 3 and s.director.turn == turn and len(user_msgs(s)) == 2,
          f"d: still one turn: {[m['id'] for m in users(s)]}, director turn {s.director.turn}")

    # (e) nothing to continue
    s = session()
    check(s.reopen() is None, "e: no open turn: None")
    MODE["respond"] = "commit"
    await say(s, "Yo quiero")
    s.history.append({"role": "user", "content": "something else"})  # the history moved on
    check(s.reopen() is None, "e: history moved on: None")
    s = session()
    await say(s, "Yo quiero")
    s.open_turn = None  # what help() / start() / finish() / a new utterance do (help needs the LLM)
    check(s.reopen() is None, "e: after a hint: None")

    # (f) a voice command isn't a turn to continue, and is flagged for the tail-only fallback
    s = session()
    await say(s, "¿Cómo?")
    check(s.open_turn is None and s.answered_cmd, "f: voice command: no open turn, answered_cmd set")

    check(A.ATTEMPT_MAX_AGE_S == 20, "an attempt older than 20 s is stale")


asyncio.run(main())
if failures:
    print("\nFAILED:\n  " + "\n  ".join(failures))
    sys.exit(1)
print("\nall continuation tests passed")
