"""End-to-end smoke test: talk to a running server with synthetic learner speech.

Usage:
  uv run python scripts/e2e.py [--lesson intro] [--say "Hola, me llamo Ana"] [--wav file.wav] [--text "..."]

--say uses macOS `say` (Paulina for Spanish). Prefix a part with "en:" to speak it with
an English voice, e.g. --say "Quiero ir al|en:market|mañana".

--simulate N plays N turns with an LLM pretending to be a beginner (typed, no STT), to run
through a whole lesson; --finish then presses "Finish lesson" if the lesson hasn't ended.
--mutate (with --simulate) breaks every 3rd simulated sentence in code (article gender, 3rd-person verb, or a
dropped article) so there is always something to correct; each one prints "MUTATED: <orig> → <sent>".

--barge sends each --text/--say after the first 300 ms after the previous turn's first audio, without waiting
(talking over Sofía), and ends with "PASS"/"FAIL": did every learner turn still get its feedback?

--say-split 'Yo quiero|un café con leche' is a learner who pauses mid-sentence and goes on before Sofía answers
(hands-free): part 1 is sent as a normal utterance, then 400 ms later {"type":"continue"} and ONE frame with both
parts' audio. It prints the USER lines ("USER (replace)" for the continued one) and the TUTOR lines, and ends with
"PASS"/"FAIL": one user id whose final text has both parts, and exactly one tutor reply that wasn't dropped.

Simulated runs, and any run with 3+ learner turns, end with "SUMMARY ..." lines: echo rate, yes/no question
share, repeated questions, learner words per turn, mutation fixes and target coverage. Always pass --url
ws://localhost:8778/ws (a test server) when measuring, so your own lessons and stats stay untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

# habla.practice is pure Python, but point HABLA_DB at a throwaway file anyway so no import can open the real DB.
os.environ["HABLA_DB"] = os.path.join(tempfile.mkdtemp(prefix="habla-e2e-"), "scratch.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from habla import config  # noqa: E402
from habla.llm import LLM  # noqa: E402
from habla.practice import grade, words  # noqa: E402

config.load_env_file()  # the simulated learner uses the same LLM backend as run.sh

LEARNER = """You play a beginner Spanish learner (A1) from Finland in a spoken lesson with a tutor.
Answer the tutor's last message with ONE short sentence of simple Spanish (max 12 words).
Be a realistic beginner: about every third answer has a typical mistake (wrong gender, wrong
conjugation, ser/estar), and sometimes you use one English word when you don't know the Spanish.
When the tutor teaches you a word or asks you to use one, try to use it. Output only your sentence."""


def synth(spec: str) -> np.ndarray:
    parts = []
    with tempfile.TemporaryDirectory() as d:
        for i, p in enumerate(spec.split("|")):
            voice, text = ("Samantha", p[3:]) if p.startswith("en:") else (os.environ.get("E2E_VOICE", "Paulina"), p)
            aiff = Path(d) / f"{i}.aiff"
            wav = Path(d) / f"{i}.wav"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)], check=True)
            a, _ = sf.read(wav, dtype="float32")
            parts.append(a)
    return np.concatenate(parts)


def load_wav(path: str) -> np.ndarray:
    a, sr = sf.read(path, dtype="float32")
    assert sr == 16000, "wav must be 16 kHz mono"
    return a


_LEARNER_LLM: LLM | None = None


async def learner_reply(history: list[dict], model: str) -> str:
    """The simulated learner. Uses the same backend as the server: HABLA_LLM_* (from the environment or habla/.env)
    for an OpenAI-compatible server, else Ollama with the server's model."""
    global _LEARNER_LLM
    if _LEARNER_LLM is None:
        _LEARNER_LLM = LLM.from_config(model)
    said = await _LEARNER_LLM.chat_text([{"role": "system", "content": LEARNER}, *history], timeout=60,
                                        max_tokens=0 if _LEARNER_LLM.backend == "ollama" else 80)
    return said.strip('"')


# ---------- text helpers shared with eval_corrections.py ----------
PRAISE = ("muy bien", "que bien", "perfecto", "genial", "excelente")
WH_WORDS = {"que", "como", "donde", "cuando", "cuanto", "cuanta", "cuantos", "cuantas", "quien", "quienes",
            "cual", "cuales"}  # "por qué" is covered by "que"


def plain(s: str) -> str:
    """Lowercase, accents stripped (ñ survives as n, which is fine for matching)."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p]


def first_sentence(text: str) -> str:
    ss = sentences(text)
    return ss[0] if ss else ""


def starts_with_praise(reply: str) -> bool:
    return " ".join(words(reply)).startswith(PRAISE)


def is_echo(reply: str, learner: str) -> bool:
    first = first_sentence(reply)
    return bool(first and learner) and grade(first, learner)["score"] >= 0.7


def questions(reply: str) -> list[str]:
    return [x for x in sentences(reply) if "?" in x]


def is_yes_no(question: str) -> bool:
    return not WH_WORDS & set(words(question))


# ---------- --mutate: break a learner sentence in a predictable way ----------
ARTICLE_FLIP = {"el": "la", "la": "el", "un": "una", "una": "un", "los": "las", "las": "los"}
VERB_3RD = {"soy": "es", "tengo": "tiene", "quiero": "quiere", "vivo": "vive", "voy": "va", "trabajo": "trabaja",
            "como": "come"}


def _match_case(new: str, old: str) -> str:
    return new.capitalize() if old[:1].isupper() else new


def mutate(sentence: str, prefer: int = 0) -> tuple[str, str] | None:
    """Returns (sent sentence, expected right fragment) or None if no rule applies.
    Rules: 0 flip the first article, 1 first 1st-person verb -> 3rd person, 2 drop the first article.
    `prefer` picks which rule is tried first (the others follow in order). Rule 0 matches whenever rule 2 does, so
    with a fixed order the "missing article" rule could never fire; rotating keeps all three mistake kinds."""
    toks = re.findall(r"\w+|\W+", sentence)
    idx = [i for i, t in enumerate(toks) if re.match(r"\w", t)]

    def fragment(k: int) -> str:  # the changed word and the word after it, as the learner should have said it
        nxt = next((toks[j] for j in idx if j > k), "")
        return f"{toks[k]} {nxt}".strip()

    for rule in [(prefer + r) % 3 for r in range(3)]:
        table = VERB_3RD if rule == 1 else ARTICLE_FLIP
        k = next((i for i in idx if toks[i].lower() in table), None)
        if k is None:
            continue
        expect = fragment(k)
        out = toks[:]
        if rule == 2:
            out[k] = ""
            if k + 1 < len(out) and out[k + 1].isspace():
                out[k + 1] = ""
            sent = "".join(out).strip()
            sent = sent[:1].upper() + sent[1:] if toks[k][:1].isupper() else sent
        else:
            out[k] = _match_case(table[toks[k].lower()], toks[k])
            sent = "".join(out)
        return sent, expect
    return None


# ---------- one exchange ----------
class Run:
    """Everything a run saw, for the end-of-run summary."""

    def __init__(self) -> None:
        self.turns: list[dict] = []  # {"learner", "user_id", "reply", "mutated_expect"}
        self.feedback: dict[str, dict] = {}  # user turn id -> feedback message
        self.all_replies: list[str] = []  # every Sofía reply, start and finish included
        self.lesson_state: dict | None = None
        self.has_turn_done = False  # newer servers send turn_done after the whole turn (feedback included)
        self.replies: dict[str, str] = {}  # tutor turn id -> reply (the last tutor_done wins: a cut-off reply)
        self.turn_done: set[str] = set()


def _show(m: dict, dt: str, out: dict, run: Run) -> None:
    t = m["type"]
    if t == "user":
        out["user_id"], out["user_text"] = m.get("id"), m["text"]
        flag = " (replace)" if m.get("replace") else ""
        print(f"{dt} USER{flag}  [{m['language']}] {m['text']}  english={m['english']}  stt={m['stt_ms']}ms"
              + (f"  cont={m['cont']}" if m.get("cont") is not None else ""))
        if m.get("unclear"):
            print(f"{dt} UNCLEAR: {', '.join(m['unclear'])}")
    elif t == "coach":
        print(f"{dt} COACH {m['text']}")
    elif t == "user_en":
        print(f"{dt} USER_EN {m['text']}")
    elif t == "feedback":
        if not m.get("partial"):
            run.feedback[m["id"]] = m
        flag = " (early)" if m.get("partial") else " (merged)" if m.get("merged") else " (unchecked)" if m.get("unchecked") else " (clarify)" if m.get("clarify") else ""
        print(f"{dt} FEEDBACK{flag} mistakes={m['mistakes']} teach={m['teach']} words={m.get('words')}" + (f" better={m['better']!r}" if m.get('better') else ""))
    elif t == "tutor_dropped":
        print(f"{dt} TUTOR_DROPPED {m.get('id')}")
    elif t == "audio":
        if m.get("id") not in ("say", "sample"):
            out.setdefault("tutor_id", m.get("id"))  # idle can come just before tutor_done
        print(f"{dt} AUDIO #{m['seq']} {m['text']!r} ({len(m['wav']) * 3 // 4 // 1000} kB)")
    elif t == "tutor_done":
        out["reply"], out["tutor_id"] = m["reply"], m.get("id")
        run.replies[m.get("id")] = m["reply"]
        print(f"{dt} TUTOR {m['reply']}" + (f"\n              EN: {m['reply_en']}" if m.get("reply_en") else "")
              + f"\n              timing={m['timing']}")
    elif t == "tutor_en":
        print(f"{dt} TUTOR_EN {m['reply_en']}")
    elif t == "introduce":
        print(f"{dt} INTRODUCE " + ", ".join(f"{w['display']} ({w['english']})" for w in m["words"]))
    elif t == "lesson_state":
        out["phase"] = m["phase"]
        run.lesson_state = m
        states = " ".join(f"{w['lemma']}:{w['state']}" + (f"/e{w['elicited']}" if w.get("elicited") else "")
                          for w in m["targets"])
        print(f"{dt} LESSON phase={m['phase']} used={m['used']}/{m['goal']}  {states}")
    elif t == "recap":
        out["recap"] = m
        print(f"{dt} RECAP used={m['used']}/{m['goal']} said_ok={m['said_ok']} turns={m['turns']} "
              f"fixes={[f['right'] for f in m['mistakes']]} review_next={m['review_next']}")
    elif t == "progress":
        print(f"{dt} PROGRESS known={m['known']} learning={m['learning']} seen={m['seen']} session={m['session']}")
    elif t == "turn_done":
        run.has_turn_done = True
        run.turn_done.add(m.get("id"))
        print(f"{dt} TURN_DONE {m.get('id')}")
    elif t in ("ignored", "error"):
        print(f"{dt} {t.upper()} {m}")


async def turn(ws, payload, label: str, run: Run | None = None) -> dict:
    """Runs one exchange; returns what happened (tutor reply, lesson state, recap).
    Ends at the turn's turn_done. Status idle comes first (right after the audio): a turn without a reply
    (voice command, hint, nothing heard) ends there; otherwise it waits up to 20 s more for turn_done."""
    run = run or Run()
    out: dict = {}
    t0 = time.time()
    if isinstance(payload, bytes):
        await ws.send(payload)
    else:
        await ws.send(json.dumps(payload))
    print(f"\n>>> {label}")
    idle_at: float | None = None
    while True:
        timeout = None if idle_at is None else max(0.0, idle_at + 20 - time.time())
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        except asyncio.TimeoutError:
            print("              (no turn_done within 20 s of idle)")
            return out
        _show(m, f"{(time.time() - t0) * 1000:6.0f}ms", out, run)
        t = m["type"]
        if t == "turn_done" and m.get("id") in {out.get("user_id"), out.get("tutor_id")} - {None}:
            return out
        if t == "status" and m["state"] == "idle" and idle_at is None:
            if "tutor_id" not in out:  # no LLM reply this turn: nothing more will come
                return out
            idle_at = time.time()


async def barge(ws, payloads: list[tuple[object, str, str | None]], run: Run) -> bool:
    """Talk over Sofía: send each payload 300 ms after the previous turn's first audio, without waiting for the
    turn to end. Then wait for the analysis of every turn and check each learner turn got its feedback."""
    t0 = time.time()
    seen_audio: set[str] = set()
    first_audio: asyncio.Queue[str] = asyncio.Queue()
    user_ids: list[str] = []
    texts: dict[str, str | None] = {}

    async def reader() -> None:
        while True:
            m = json.loads(await ws.recv())
            _show(m, f"{(time.time() - t0) * 1000:6.0f}ms", {}, run)
            if m["type"] == "user":
                user_ids.append(m["id"])
                texts[m["id"]] = m["text"]
            elif m["type"] == "audio" and m["id"] not in ("say", "sample") and m["id"] not in seen_audio:
                seen_audio.add(m["id"])
                first_audio.put_nowait(m["id"])

    task = asyncio.create_task(reader())
    try:
        for i, (payload, label, _learner) in enumerate(payloads):
            print(f"\n>>> {label}" + ("  (barging in)" if i else ""))
            await ws.send(payload if isinstance(payload, bytes) else json.dumps(payload))
            if i < len(payloads) - 1:
                try:
                    await asyncio.wait_for(first_audio.get(), 60)
                except asyncio.TimeoutError:
                    print("              (no audio within 60 s, sending the next one anyway)")
                await asyncio.sleep(0.3)
        deadline = time.time() + 120
        while time.time() < deadline:  # every learner turn analysed, and the last reply too
            last_tid = f"t{int(user_ids[-1][1:]) + 1}" if user_ids else None
            if len(user_ids) >= len(payloads) and all(u in run.feedback for u in user_ids) \
                    and (last_tid is None or last_tid in run.turn_done):
                break
            await asyncio.sleep(0.2)
    finally:
        task.cancel()
    for u in user_ids:
        tid = f"t{int(u[1:]) + 1}"  # learner turn uN gets tutor turn t(N+1)
        run.turns.append({"learner": texts.get(u) or "", "user_id": u, "reply": run.replies.get(tid),
                          "mutated_expect": None})
    missing = [u for u in user_ids if u not in run.feedback]
    ok = not missing and len(user_ids) == len(payloads)
    print(f"\n{'PASS' if ok else 'FAIL'} barge: feedback for {len(user_ids) - len(missing)}/{len(payloads)} learner turns"
          + (f", missing {missing}" if missing else ""))
    return ok


def pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


async def say_split(ws, spec: str, run: Run) -> bool:
    """A learner who pauses mid-sentence: part 1 as a normal utterance, then (400 ms later, before Sofía's answer
    can start) a continuation: {"type":"continue"} and part 1 + part 2 as one frame. Waits for the continued turn's
    turn_done. PASS: one user id, its final text has both parts, one tutor reply that wasn't dropped."""
    first, rest = spec.split("|", 1)
    pcm1, pcm2 = pcm16(synth(first)), pcm16(synth(rest))
    t0 = time.time()
    user_ids: list[str] = []
    texts: dict[str, str] = {}
    replies: dict[str, str] = {}
    dropped: set[str] = set()
    final: dict[str, str] = {}  # "user": the continued turn's id, "tutor": the id of Sofía's answer to it
    done = asyncio.Event()

    async def reader() -> None:
        while True:
            m = json.loads(await ws.recv())
            _show(m, f"{(time.time() - t0) * 1000:6.0f}ms", {}, run)
            t = m["type"]
            if t == "user" and not m.get("command"):
                if m["id"] not in user_ids:
                    user_ids.append(m["id"])
                texts[m["id"]] = m["text"]
                if m.get("cont") == 1:
                    final["user"] = m["id"]
            elif t in ("tutor_delta", "audio", "introduce") and "user" in final and m.get("id") not in ("say", "sample"):
                final.setdefault("tutor", m["id"])  # everything of the cut answer came before the continued user
            elif t == "tutor_done":
                replies[m["id"]] = m["reply"]
            elif t == "tutor_dropped":
                dropped.add(m["id"])
            elif t == "turn_done" and m.get("id") == final.get("tutor"):
                done.set()
            elif t == "ignored" and m.get("cont") == 1:
                done.set()

    task = asyncio.create_task(reader())
    try:
        print(f"\n>>> say-split part 1: {first}")
        await ws.send(pcm1)
        await asyncio.sleep(0.4)
        print(f"\n>>> say-split continue: {first} + {rest}")
        await ws.send(json.dumps({"type": "continue", "prev": len(pcm1) // 2, "n": 1}))
        await ws.send(pcm1 + pcm2)
        try:
            await asyncio.wait_for(done.wait(), 90)
        except asyncio.TimeoutError:
            print("              (no turn_done for the continued turn within 90 s)")
        await asyncio.sleep(0.5)  # a late tutor_dropped / tutor_done
    finally:
        task.cancel()
    kept = [t for t in replies if t not in dropped]
    uid = final.get("user")
    text = plain(texts.get(uid, "")) if uid else ""

    def covered(part: str) -> float:
        ws_ = plain(part).split()
        return sum(w in text.split() for w in ws_) / max(1, len(ws_))

    ok = len(user_ids) == 1 and uid == user_ids[0] and covered(first) >= 0.5 and covered(rest) >= 0.5 and len(kept) == 1
    if uid:
        run.turns.append({"learner": texts[uid], "user_id": uid, "reply": replies.get(final.get("tutor", "")),
                          "mutated_expect": None})
    print(f"\n{'PASS' if ok else 'FAIL'} say-split: user ids {user_ids}, final text {texts.get(uid or '', '')!r}, "
          f"tutor replies kept {kept}, dropped {sorted(dropped)}")
    return ok


async def drain(ws, run: Run, quiet_s: float = 5.0) -> None:
    """Late messages (e.g. feedback sent after idle) still count; stop after `quiet_s` of silence."""
    while True:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), quiet_s))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return
        _show(m, "  late", {}, run)


def summary(run: Run, mutate_on: bool) -> None:
    turns = [t for t in run.turns if t.get("reply") is not None]
    if not turns:
        print("\nSUMMARY no learner turns with a reply")
        return
    print()
    echo = sum(is_echo(t["reply"], t["learner"]) for t in turns)
    print(f"SUMMARY echo rate: {echo}/{len(turns)} = {echo / len(turns):.0%} (first sentence repeats the learner)")
    finals = [qs[-1] for t in turns if (qs := questions(t["reply"]))]
    yn = sum(is_yes_no(q) for q in finals)
    print(f"SUMMARY yes/no questions: {yn}/{len(finals)}" + (f" = {yn / len(finals):.0%}" if finals else "")
          + f" (replies without a question: {len(turns) - len(finals)})")
    asked: list[str] = []
    dups = []
    for t in run.all_replies:
        for q in questions(t):
            if any(grade(q, e)["score"] >= 0.8 for e in asked):
                dups.append(q)
            asked.append(q)
    print(f"SUMMARY duplicate questions: {len(dups)}/{len(asked)}" + (f"  {dups}" if dups else ""))
    n_words = [len(words(t["learner"])) for t in turns]
    print(f"SUMMARY learner words per turn: {sum(n_words) / len(n_words):.1f}")
    if mutate_on:
        muts = [t for t in turns if t.get("mutated_expect")]
        fixed = word_fixed = praised = 0
        for t in muts:
            rights = [plain(f.get("right", "")) for f in run.feedback.get(t.get("user_id") or "", {}).get("mistakes", [])]
            exp = plain(t["mutated_expect"])
            fixed += any(exp in r for r in rights)
            word_fixed += any(exp.split()[0] in words(r) for r in rights)
            praised += starts_with_praise(t["reply"])
        print(f"SUMMARY mutated turns fixed: {fixed}/{len(muts)} (fix contains the expected fragment; "
              f"changed word alone: {word_fixed}/{len(muts)})")
        print(f"SUMMARY mutated turns praised: {praised}/{len(muts)}")
    ls = run.lesson_state
    targets = (ls or {}).get("targets")
    if not targets or "kind" not in targets[0]:
        print("SUMMARY new targets left pending/introduced: n/a")
        print("SUMMARY review targets never elicited: n/a")
        return
    new = [x for x in targets if x["kind"] == "new"]
    left = [x["lemma"] for x in new if x.get("state") in ("pending", "introduced")]
    print(f"SUMMARY new targets left pending/introduced: {len(left)}/{len(new)}" + (f"  {left}" if left else ""))
    review = [x for x in targets if x["kind"] == "review"]
    if review and "elicited" not in review[0]:
        print("SUMMARY review targets never elicited: n/a")
    else:
        never = [x["lemma"] for x in review if x.get("elicited") == 0]
        print(f"SUMMARY review targets never elicited: {len(never)}/{len(review)}" + (f"  {never}" if never else ""))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8777/ws")
    ap.add_argument("--lesson", default="intro")
    ap.add_argument("--say", action="append", default=[])
    ap.add_argument("--wav", action="append", default=[])
    ap.add_argument("--text", action="append", default=[])
    ap.add_argument("--help-turn", action="store_true")
    ap.add_argument("--simulate", type=int, default=0)
    ap.add_argument("--mutate", action="store_true", help="with --simulate: break every 3rd learner sentence")
    ap.add_argument("--finish", action="store_true")
    ap.add_argument("--barge", action="store_true", help="send each --text/--say 300 ms into the previous reply")
    ap.add_argument("--say-split", metavar="'PART1|PART2'",
                    help="say PART1, then 400 ms later send {type:continue} and PART1+PART2 as one frame (a learner "
                         "who goes on talking before Sofía answers); PASS if it stays one turn with one reply")
    a = ap.parse_args()
    if a.mutate and not a.simulate:
        ap.error("--mutate only works with --simulate")
    if a.say_split is not None and "|" not in a.say_split:
        ap.error("--say-split needs two parts: 'Yo quiero|un café con leche'")
    run = Run()

    async def learner_turn(payload, label: str, learner: str | None = None, expect: str | None = None) -> dict:
        res = await turn(ws, payload, label, run)
        if res.get("reply") is not None:
            run.all_replies.append(res["reply"])
        text = learner if learner is not None else res.get("user_text")
        if text is not None and res.get("user_id"):
            run.turns.append({"learner": text, "user_id": res["user_id"], "reply": res.get("reply"),
                              "mutated_expect": expect})
        return res

    async with websockets.connect(a.url, max_size=50_000_000) as ws:
        ready = json.loads(await ws.recv())
        print("model:", ready["model"])
        await ws.recv()  # progress
        res = await learner_turn({"type": "start", "lesson": a.lesson}, f"start lesson {a.lesson}")
        if a.barge:
            payloads: list[tuple[object, str, str | None]] = [
                ((np.clip(synth(spec), -1, 1) * 32767).astype("<i2").tobytes(), f"say: {spec}", None) for spec in a.say
            ] + [({"type": "text", "text": t}, f"text: {t}", t) for t in a.text]
            ok = await barge(ws, payloads, run)
            if len(run.turns) >= 3:
                summary(run, False)
            sys.exit(0 if ok else 1)
        if a.say_split:
            ok = await say_split(ws, a.say_split, run)
            if not (a.say or a.wav or a.text or a.simulate):
                sys.exit(0 if ok else 1)
        for spec in a.say:
            pcm = (np.clip(synth(spec), -1, 1) * 32767).astype("<i2").tobytes()
            await learner_turn(pcm, f"say: {spec}")
        for w in a.wav:
            await learner_turn((np.clip(load_wav(w), -1, 1) * 32767).astype("<i2").tobytes(), f"wav: {w}")
        for t in a.text:
            await learner_turn({"type": "text", "text": t}, f"text: {t}", learner=t)
        if a.help_turn:
            await learner_turn({"type": "help"}, "help")
        sim: list[dict] = [{"role": "user", "content": res.get("reply", "")}]
        n_mut = since_mut = 0
        for _ in range(a.simulate):
            said = await learner_reply(sim, ready["model"])
            expect = None
            since_mut += 1
            if a.mutate and since_mut >= 3:  # every 3rd sentence; if no rule fits, try the next one
                m = mutate(said, prefer=n_mut % 3)
                if m:
                    print(f"\nMUTATED: {said} → {m[0]}   (expect: {m[1]})")
                    said, expect = m
                    n_mut += 1
                    since_mut = 0
                else:
                    print(f"\nMUTATED: (no rule applies, sent unchanged, trying the next one) {said}")
            sim.append({"role": "assistant", "content": said})
            res = await learner_turn({"type": "text", "text": said}, f"learner: {said}", learner=said, expect=expect)
            sim.append({"role": "user", "content": res.get("reply", "")})
            if "recap" in res:
                break
        if a.finish and "recap" not in res:
            await learner_turn({"type": "finish"}, "finish lesson")
        if a.simulate or len(run.turns) >= 3:
            if any(t["user_id"] not in run.feedback for t in run.turns):
                await drain(ws, run)
            summary(run, a.mutate)


if __name__ == "__main__":
    asyncio.run(main())
