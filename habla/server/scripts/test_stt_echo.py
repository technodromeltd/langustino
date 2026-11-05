"""stt.is_echo (Whisper wrote the prompt back), stt.unclear_of, and the per-token log-probability recorder. Pure
Python: imports stt but never loads the Whisper model. No server, no LLM.

    cd habla/server && uv run python scripts/test_stt_echo.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mlx.core as mx  # noqa: E402
from mlx_whisper.decoding import GreedyDecoder  # noqa: E402
from mlx_whisper.tokenizer import get_tokenizer  # noqa: E402

from habla import stt  # noqa: E402
from habla.stt import STT, is_echo, unclear_of  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, got: object = None) -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got: {got}"))
    if not ok:
        failures.append(name)


CAFE = "Un café con leche. ¿Quieres algo de comer?"
ECHO_CASES = [
    ("bien", "¡Hola! Bienvenido al café", False),
    ("un café con leche", CAFE, False),
    ("Sí, quiero un café con leche", CAFE, False),
    ("Sí", "La cuenta, sí", False),
    ("¿Conoces a mucha gente que trabaja?", "¿Conoces a mucha gente nueva?", True),
    (CAFE, CAFE, True),
    # more
    ("Bien, gracias", "¡Hola! ¿Cómo estás hoy?", False),
    ("¿Quieres algo de comer?", CAFE, True),  # the whole question
    ("Me llamo Eero", "¿Cómo te llamas?", False),
    ("anything", None, False),
    ("", CAFE, False),
]
for text, prompt, want in ECHO_CASES:
    got = is_echo(text, prompt)
    check(f"is_echo({text!r}, {prompt!r}) == {want}", got is want, got)

# ---- unclear_of: (raw, word, p) ----
words = [(" Hola", "Hola", 0.3), (",", "", 0.9), (" me", "me", 0.95), (" yamo", "yamo", 0.31), (" Eero", "Eero", 0.2),
         (".", "", 0.99), (" Vivo", "Vivo", 0.4), (" en", "en", 0.45), (" Elsinki", "Elsinki", 0.1),
         (" y", "y", 0.2), (" yamo", "yamo", 0.3)]
got = unclear_of(words)
check("unclear: low p, >1 letter, sentence-initial capitals kept, names and repeats dropped",
      got == ["Hola", "yamo", "Vivo", "en"], got)
check("unclear: nothing below 0.5 → []", unclear_of([(" Hola", "Hola", 0.9), (" amigo", "amigo", 0.78)]) == [])

# ---- the recorder wraps GreedyDecoder.update and keeps the chosen token's log-probability ----
stt._install_recorder()
stt._install_recorder()  # idempotent
check("recorder installed once", getattr(GreedyDecoder.update, "_habla_recorder", False))
dec = GreedyDecoder(temperature=0.0, eot=9)
logits = mx.array([[0.0, 2.0, 1.0, -1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]])
stt._recording = []
toks, _, _ = dec.update(mx.array([[1, 2]]), logits, mx.zeros(1))
rec, stt._recording = stt._recording, None
want = 2.0 - math.log(sum(math.exp(x) for x in logits[0].tolist()))
check("decoder output unchanged (argmax appended)", toks.tolist() == [[1, 2, 1]], toks.tolist())
check("one log-probability per step, = log_softmax of the chosen token",
      len(rec) == 1 and abs(rec[0].item() - want) < 1e-4, [x.item() for x in rec])
dec.update(mx.array([[1, 2]]), logits, mx.zeros(1))
check("nothing recorded while inactive", stt._recording is None)

# ---- _confidence: tokens grouped into words, p = exp(min token logprob) ----
s = STT.__new__(STT)
s.model = SimpleNamespace(is_multilingual=True, num_languages=100)
tok = get_tokenizer(True, num_languages=100, language="es", task="transcribe")
tokens = tok.encode(" Hola, me llamo Eero. Vivo en Helsinki. ¿Con quién?")
lps = [math.log(0.9)] * len(tokens)
pieces = [tok.decode([t]) for t in tokens]
lps[pieces.index("amo")] = math.log(0.3)  # " ll" + "amo"
lps[pieces.index(" ¿")] = math.log(0.1)  # punctuation-only tokens don't count
steps = [mx.array(x) for x in lps] + [mx.array(0.0)]  # one extra step (the pipelined decode runs one ahead)
scored = s._confidence(SimpleNamespace(tokens=tokens), steps, "es")
by_word = {w: p for _, w, p in scored if w}
check("confidence words", list(by_word) == ["Hola", "me", "llamo", "Eero", "Vivo", "en", "Helsinki", "Con", "quién"], list(by_word))
check("a word's p is its least likely token", abs(by_word["llamo"] - 0.3) < 1e-3 and abs(by_word["me"] - 0.9) < 1e-3
      and abs(by_word["Con"] - 0.9) < 1e-3,
      by_word)
check("unclear from confidence", unclear_of(scored) == ["llamo"], unclear_of(scored))
try:
    s._confidence(SimpleNamespace(tokens=tokens), steps[:2], "es")
    check("too few recorded steps raises", False)
except RuntimeError:
    check("too few recorded steps raises", True)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
