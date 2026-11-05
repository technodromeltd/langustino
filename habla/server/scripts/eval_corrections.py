"""Correction eval: sends fixed learner sentences to a running server and scores how Sofía handles them.

Usage (always against a TEST server, never your own on :8777):
  uv run python scripts/eval_corrections.py --url ws://localhost:8778/ws [--only 3] [--out results.json]

Each case in eval_corpus.json gets a fresh connection: start the lesson, wait for Sofía's opener, send the text,
then collect the fixes (feedback), new-word cards (teach) and the spoken reply. Prints one row per case and a
summary: fix recall, false fixes, recasts, praise on errors, echoing, and time to first audio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Before any habla import: point HABLA_DB at a throwaway file so nothing can open the real database.
os.environ["HABLA_DB"] = os.path.join(tempfile.mkdtemp(prefix="habla-eval-"), "scratch.db")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import websockets  # noqa: E402
from e2e import first_sentence, starts_with_praise  # noqa: E402

from habla.practice import grade, words  # noqa: E402

START_QUIET_S = 3.0  # after the opener's idle (servers without turn_done): this much silence means it's over
START_QUIET_MAX_S = 12.0
FEEDBACK_WAIT_S = 30.0  # after the reply's idle, how long to wait for a late feedback message
CASE_TIMEOUT_S = 180.0


def contains(haystack: str, needle: str) -> bool:
    """needle's words appear in a row in haystack (accent- and case-insensitive, whole words only)."""
    h, n = words(haystack), words(needle)
    return bool(n) and any(h[i:i + len(n)] == n for i in range(len(h) - len(n) + 1))


async def recv(ws, timeout: float) -> dict | None:
    try:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))
    except asyncio.TimeoutError:
        return None


async def run_case(url: str, case: dict) -> dict:
    res: dict = {**case, "fixes": [], "teach": [], "audio": [], "reply": None, "first_audio_ms": None}
    t0 = time.time()
    async with websockets.connect(url, max_size=50_000_000) as ws:
        await recv(ws, 10)  # ready
        await recv(ws, 10)  # progress
        await ws.send(json.dumps({"type": "start", "lesson": case["lesson"]}))
        has_turn_done = False
        # ---- opener ----
        while True:
            m = await recv(ws, 120)
            if m is None:
                raise TimeoutError("no opener from the server")
            if m["type"] == "turn_done":
                has_turn_done = True
                break
            if m["type"] == "status" and m["state"] == "idle":
                quiet_until = time.time() + START_QUIET_MAX_S
                while time.time() < quiet_until:
                    m = await recv(ws, START_QUIET_S)
                    if m is None or m["type"] == "turn_done":
                        has_turn_done = m is not None
                        break
                break
        # ---- the learner's sentence ----
        t_sent = time.time()
        await ws.send(json.dumps({"type": "text", "text": case["text"]}))
        user_id = tutor_id = None
        feedback = None
        idle_at = None
        while True:
            timeout = 120.0 if idle_at is None else max(0.1, idle_at + FEEDBACK_WAIT_S - time.time())
            m = await recv(ws, timeout)
            if m is None:
                break  # waited long enough (no feedback came)
            t = m["type"]
            if t == "user" and user_id is None:
                user_id = m["id"]
            elif t == "audio" and m.get("id") not in ("sample", "say"):
                tutor_id = tutor_id or m["id"]
                if m["id"] == tutor_id and m.get("text"):
                    res["audio"].append(m["text"])
            elif t == "tutor_done":
                tutor_id = tutor_id or m["id"]
                res["reply"] = m["reply"]
                res["first_audio_ms"] = (m.get("timing") or {}).get("first_audio_ms")
                res["timing"] = m.get("timing")
            elif t == "feedback" and m["id"] == user_id and not m.get("partial"):
                feedback = m
            elif t == "turn_done" and m.get("id") in {user_id, tutor_id} - {None}:
                break
            elif t == "status" and m["state"] == "idle" and user_id is not None:
                idle_at = idle_at or time.time()
            elif t == "error":
                res["error"] = m.get("message")
            if idle_at and not has_turn_done and feedback is not None:
                break
        res["turn_ms"] = int((time.time() - t_sent) * 1000)
    res["case_ms"] = int((time.time() - t0) * 1000)
    if feedback:
        res["fixes"] = [{"wrong": f.get("wrong", ""), "right": f.get("right", ""), "type": f.get("type", ""),
                         "why": f.get("why", "")} for f in feedback.get("mistakes", [])]
        res["teach"] = [{"english": w.get("english", ""), "spanish": w.get("spanish", "")}
                        for w in feedback.get("teach", [])]
    res["got_feedback"] = feedback is not None
    if res["reply"] is None and res["audio"]:
        res["reply"] = " ".join(res["audio"])
    return res


def score(res: dict) -> None:
    """Adds per-case verdicts to `res`."""
    reply = res.get("reply") or ""
    exp = res.get("expect_right")
    if res["kind"] in ("error", "english"):
        hit = any(contains(f["right"], exp) for f in res["fixes"])
        if res["kind"] == "english":
            hit = hit or any(contains(w["spanish"], exp) for w in res["teach"])
        res["recalled"] = hit
        res["recast"] = contains(reply, exp)
    if res["kind"] == "error":
        res["praised"] = starts_with_praise(reply)
    if res["kind"] == "correct":
        first = first_sentence(reply)
        res["echo_score"] = grade(first, res["text"])["score"] if first else 0.0
        res["echoed"] = res["echo_score"] >= 0.7


def pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" = {n / d:.0%}" if d else "")


def pick(cases: list[dict], n: int) -> list[dict]:
    """A quick subset that still covers every kind: round-robin over kinds, then back in corpus order."""
    by_kind: dict[str, list[int]] = {}
    for i, c in enumerate(cases):
        by_kind.setdefault(c["kind"], []).append(i)
    chosen: list[int] = []
    while len(chosen) < min(n, len(cases)):
        for idx in by_kind.values():
            if idx and len(chosen) < n:
                chosen.append(idx.pop(0))
    return [cases[i] for i in sorted(chosen)]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="test server WebSocket, e.g. ws://localhost:8778/ws")
    ap.add_argument("--only", type=int, default=0, help="run a quick subset of N cases (covers every kind)")
    ap.add_argument("--corpus", default=str(HERE / "eval_corpus.json"))
    ap.add_argument("--out", default=str(HERE / "eval_last.json"))
    a = ap.parse_args()
    if ":8777" in a.url:
        ap.error("refusing to run against :8777 (that's your own server); start a test server on :8778")
    cases = json.loads(Path(a.corpus).read_text())
    if a.only:
        cases = pick(cases, a.only)

    results = []
    for i, case in enumerate(cases, 1):
        try:
            res = await asyncio.wait_for(run_case(a.url, case), CASE_TIMEOUT_S)
        except (Exception, asyncio.TimeoutError) as e:  # one bad case shouldn't lose the rest
            res = {**case, "error": f"{type(e).__name__}: {e}", "fixes": [], "teach": [], "reply": None,
                   "audio": [], "first_audio_ms": None, "got_feedback": False}
        score(res)
        results.append(res)
        fixes = "; ".join(f"{f['wrong']}→{f['right']}" for f in res["fixes"]) or "-"
        if res["teach"]:
            fixes += "  teach: " + ", ".join(f"{w['english']}={w['spanish']}" for w in res["teach"])
        if res["kind"] == "correct":
            mark = "FIX!" if res["fixes"] else "ok"
        else:
            mark = "ok" if res["recalled"] else "MISS"
        print(f"[{i:2}/{len(cases)}] {res['kind']:7} {mark:4} {res['text']} | {fixes} | {res.get('reply') or ''}"
              + (f"  ERROR {res['error']}" if res.get("error") else "")
              + ("" if res["got_feedback"] else "  (no feedback)"), flush=True)

    fixable = [r for r in results if r["kind"] in ("error", "english")]
    errors = [r for r in results if r["kind"] == "error"]
    correct = [r for r in results if r["kind"] == "correct"]
    fa = [r["first_audio_ms"] for r in results if r.get("first_audio_ms") is not None]
    summary = {
        "cases": len(results),
        "failed_cases": sum(1 for r in results if r.get("error")),
        "fix_recall": [sum(r["recalled"] for r in fixable), len(fixable)],
        "fix_recall_error": [sum(r["recalled"] for r in errors), len(errors)],
        "fix_recall_english": [sum(r["recalled"] for r in fixable if r["kind"] == "english"),
                               len(fixable) - len(errors)],
        "false_fixes": [sum(len(r["fixes"]) for r in correct), len(correct)],
        "correct_cases_with_fixes": sum(1 for r in correct if r["fixes"]),
        "recast": [sum(r["recast"] for r in fixable), len(fixable)],
        "praise_on_error": [sum(r["praised"] for r in errors), len(errors)],
        "echo_on_correct": [sum(r["echoed"] for r in correct), len(correct)],
        "mean_first_audio_ms": round(sum(fa) / len(fa)) if fa else None,
    }
    print("\nSUMMARY")
    print(f"  fix recall (error+english): {pct(*summary['fix_recall'])}"
          f"   [error {pct(*summary['fix_recall_error'])}, english {pct(*summary['fix_recall_english'])}]")
    print(f"  false fixes on correct:     {summary['false_fixes'][0]} fixes over {summary['false_fixes'][1]} cases"
          f" ({summary['correct_cases_with_fixes']} cases with any)")
    print(f"  recast in spoken reply:     {pct(*summary['recast'])}")
    print(f"  praise on error:            {pct(*summary['praise_on_error'])}")
    print(f"  echo on correct:            {pct(*summary['echo_on_correct'])}")
    print(f"  mean first audio:           {summary['mean_first_audio_ms']} ms" if fa else
          "  mean first audio:           n/a")
    if summary["failed_cases"]:
        print(f"  failed cases:               {summary['failed_cases']}")
    Path(a.out).write_text(json.dumps({"url": a.url, "summary": summary, "results": results},
                                      ensure_ascii=False, indent=1))
    print(f"\nfull results: {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
