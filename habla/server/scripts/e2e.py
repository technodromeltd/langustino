"""End-to-end smoke test: talk to a running server with synthetic learner speech.

Usage:
  uv run python scripts/e2e.py [--lesson intro] [--say "Hola, me llamo Ana"] [--wav file.wav] [--text "..."]

--say uses macOS `say` (Paulina for Spanish). Prefix a part with "en:" to speak it with
an English voice, e.g. --say "Quiero ir al|en:market|mañana".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets


def synth(spec: str) -> np.ndarray:
    parts = []
    with tempfile.TemporaryDirectory() as d:
        for i, p in enumerate(spec.split("|")):
            voice, text = ("Samantha", p[3:]) if p.startswith("en:") else ("Paulina", p)
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


async def turn(ws, payload, label: str) -> None:
    t0 = time.time()
    if isinstance(payload, bytes):
        await ws.send(payload)
    else:
        await ws.send(json.dumps(payload))
    print(f"\n>>> {label}")
    while True:
        m = json.loads(await ws.recv())
        t = m["type"]
        dt = f"{(time.time() - t0) * 1000:6.0f}ms"
        if t == "user":
            print(f"{dt} USER  [{m['language']}] {m['text']}  english={m['english']}  stt={m['stt_ms']}ms")
        elif t == "feedback":
            print(f"{dt} FEEDBACK{' (early)' if m.get('partial') else ''} mistakes={m['mistakes']} teach={m['teach']} words={m.get('words')}")
        elif t == "audio":
            print(f"{dt} AUDIO #{m['seq']} {m['text']!r} ({len(m['wav']) * 3 // 4 // 1000} kB)")
        elif t == "tutor_done":
            print(f"{dt} TUTOR {m['reply']}\n              EN: {m['reply_en']}\n              timing={m['timing']}")
        elif t == "progress":
            print(f"{dt} PROGRESS known={m['known']} learning={m['learning']} seen={m['seen']} session={m['session']}")
        elif t in ("ignored", "error"):
            print(f"{dt} {t.upper()} {m}")
        elif t == "status" and m["state"] == "idle":
            return


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8777/ws")
    ap.add_argument("--lesson", default="intro")
    ap.add_argument("--say", action="append", default=[])
    ap.add_argument("--wav", action="append", default=[])
    ap.add_argument("--text", action="append", default=[])
    ap.add_argument("--help-turn", action="store_true")
    a = ap.parse_args()
    async with websockets.connect(a.url, max_size=50_000_000) as ws:
        ready = json.loads(await ws.recv())
        print("model:", ready["model"])
        await ws.recv()  # progress
        await turn(ws, {"type": "start", "lesson": a.lesson}, f"start lesson {a.lesson}")
        for spec in a.say:
            pcm = (np.clip(synth(spec), -1, 1) * 32767).astype("<i2").tobytes()
            await turn(ws, pcm, f"say: {spec}")
        for w in a.wav:
            await turn(ws, (np.clip(load_wav(w), -1, 1) * 32767).astype("<i2").tobytes(), f"wav: {w}")
        for t in a.text:
            await turn(ws, {"type": "text", "text": t}, f"text: {t}")
        if a.help_turn:
            await turn(ws, {"type": "help"}, "help")


if __name__ == "__main__":
    asyncio.run(main())
