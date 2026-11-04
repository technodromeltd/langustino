"""Local text-to-speech with Kokoro (int8 ONNX, CPU) — ~6x faster than realtime on M1 Pro."""

from __future__ import annotations

import io
import re
import threading

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

from . import config


def clean_for_speech(text: str) -> str:
    text = re.sub(r"[\"“”«»*_()\[\]]", "", text)
    text = text.replace("'", "").replace("‘", "").replace("’", "")
    return re.sub(r"\s+", " ", text).strip()


class TTS:
    def __init__(self) -> None:
        self.kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        self._lock = threading.Lock()

    def warmup(self) -> None:
        self.synth("Hola.")

    def synth(self, text: str, speed: float | None = None) -> bytes:
        """Returns a 24 kHz mono 16-bit WAV file."""
        text = clean_for_speech(text)
        if not text:
            return b""
        with self._lock:
            samples, sr = self.kokoro.create(
                text, voice=config.TUTOR_VOICE, speed=speed or config.TTS_SPEED, lang="es"
            )
        # Small tail of silence so consecutive sentences don't run together.
        samples = np.concatenate([samples, np.zeros(int(sr * 0.12), dtype=samples.dtype)])
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()


_SENTENCE_END = re.compile(r"([.!?…]+)(\s+|$)")


class SentenceSplitter:
    """Feeds streamed text, yields speakable sentences as soon as they are complete."""

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = _SENTENCE_END.search(self.buf)
            # Require trailing whitespace so we don't split "¿Qué?" mid-stream before the next char arrives.
            if not m or m.end(2) == m.end(1):
                break
            sentence = self.buf[: m.end(1)].strip()
            self.buf = self.buf[m.end() :]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []
