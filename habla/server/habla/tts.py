"""Local text-to-speech with several Spanish voices; the learner picks one by ear in the app.

Engines:
  piper   native Spanish VITS voices (rhasspy/piper-voices), run directly with onnxruntime.
          (The piper-tts macOS wheel ignores its espeak data path, so we phonemize with the
          espeak-ng that kokoro-onnx already ships, and map phonemes to ids ourselves.)
  say     macOS built-in voices (Mónica es-ES, Paulina es-MX). Native accents, no download.
          Install "Enhanced"/"Premium" variants in System Settings > Accessibility > Spoken
          Content for better quality; they show up here automatically.
  kokoro  the original voice: smooth, but its Spanish has an English accent.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import espeakng_loader
import numpy as np
import onnxruntime as ort
import soundfile as sf
from phonemizer.backend import EspeakBackend
from phonemizer.backend.espeak.wrapper import EspeakWrapper

from . import config
from .practice import grade, words

EspeakWrapper.set_library(espeakng_loader.get_library_path())
EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
_espeak_lock = threading.Lock()  # espeak-ng has process-global state


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    engine: str  # piper | say | kokoro
    name: str  # piper model file stem, macOS voice name, or kokoro voice id
    speaker: int | None = None


VOICES = [
    Voice("piper-es-ES", "Lucía · Spain", "piper", "es_ES-sharvard-medium", speaker=1),
    Voice("piper-es-MX", "Ximena · Mexico", "piper", "es_MX-claude-high"),
    Voice("piper-es-AR", "Daniela · Argentina", "piper", "es_AR-daniela-high"),
    Voice("say-monica", "Mónica · Spain (macOS)", "say", "Mónica"),
    Voice("say-paulina", "Paulina · Mexico (macOS)", "say", "Paulina"),
    Voice("kokoro-dora", "Dora (Kokoro, English accent)", "kokoro", "ef_dora"),
]


def clean_for_speech(text: str) -> str:
    text = re.sub(r"[\"“”«»*_()\[\]]", "", text)
    text = text.replace("'", "").replace("‘", "").replace("’", "")
    return re.sub(r"\s+", " ", text).strip()


def _wav(samples: np.ndarray, sr: int) -> bytes:
    # Small tail of silence so consecutive sentences don't run together.
    samples = np.concatenate([samples.astype(np.float32), np.zeros(int(sr * 0.12), dtype=np.float32)])
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class PiperModel:
    def __init__(self, path: Path, speaker: int | None) -> None:
        cfg = json.loads(Path(f"{path}.json").read_text())
        self.ids = cfg["phoneme_id_map"]
        self.sr = cfg["audio"]["sample_rate"]
        self.inf = cfg["inference"]
        self.speaker = speaker
        self.backend = EspeakBackend(cfg["espeak"]["voice"], preserve_punctuation=True, with_stress=True)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.has_sid = any(i.name == "sid" for i in self.sess.get_inputs())

    def synth(self, text: str, speed: float) -> tuple[np.ndarray, int]:
        with _espeak_lock:
            phonemes = self.backend.phonemize([text], strip=True)[0]
        ids = [*self.ids["^"], *self.ids["_"]]
        for ch in phonemes:
            if ch in self.ids:
                ids += [*self.ids[ch], *self.ids["_"]]
        ids += self.ids["$"]
        feed = {
            "input": np.array([ids], dtype=np.int64),
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": np.array([self.inf["noise_scale"], self.inf["length_scale"] / speed, self.inf["noise_w"]],
                               dtype=np.float32),
        }
        if self.has_sid:
            feed["sid"] = np.array([self.speaker or 0], dtype=np.int64)
        return self.sess.run(None, feed)[0].squeeze(), self.sr


class TTS:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, object] = {}
        installed_say = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        self.voices = [
            v for v in VOICES
            if (v.engine == "piper" and (config.MODELS_DIR / f"{v.name}.onnx").exists())
            or (v.engine == "say" and re.search(rf"^{re.escape(v.name)}\s", installed_say, re.M))
            or (v.engine == "kokoro" and config.KOKORO_MODEL.exists())
        ]
        if not self.voices:
            raise RuntimeError("No TTS voices available — run ./run.sh to download them")
        ids = [v.id for v in self.voices]
        self.default = config.TUTOR_VOICE if config.TUTOR_VOICE in ids else ids[0]

    def voice(self, voice_id: str | None) -> Voice:
        return next((v for v in self.voices if v.id == voice_id), None) or self.voice(self.default)

    def warmup(self) -> None:
        self.synth("Hola.")

    def _model(self, v: Voice):
        if v.id not in self._models:
            if v.engine == "piper":
                self._models[v.id] = PiperModel(config.MODELS_DIR / f"{v.name}.onnx", v.speaker)
            elif v.engine == "kokoro":
                from kokoro_onnx import Kokoro

                self._models[v.id] = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        return self._models.get(v.id)

    def synth(self, text: str, voice_id: str | None = None, speed: float | None = None) -> bytes:
        """Returns a mono 16-bit WAV file."""
        text = clean_for_speech(text)
        if not text:
            return b""
        v = self.voice(voice_id)
        speed = speed or config.TTS_SPEED
        with self._lock:
            if v.engine == "say":
                with tempfile.TemporaryDirectory() as d:
                    out = Path(d) / "o.wav"
                    rate = str(int(175 * speed))
                    subprocess.run(["say", "-v", v.name, "-r", rate, "-o", str(out), "--data-format=LEI16@22050", text],
                                   check=True, timeout=30)
                    samples, sr = sf.read(out, dtype="float32")
            elif v.engine == "piper":
                samples, sr = self._model(v).synth(text, speed)
            else:
                samples, sr = self._model(v).create(text, voice=v.name, speed=speed, lang="es")
        return _wav(samples, sr)


    def synth_english(self, text: str, speed: float = 1.0) -> bytes:
        """English speech (the teacher's answers) with a macOS English voice. b"" if none is installed."""
        text = re.sub(r"[\"“”*_()\[\]]", "", text).replace("’", "'")
        text = re.sub(r"\s+", " ", text).strip()
        if not text or not config.ENGLISH_VOICE:
            return b""
        with self._lock, tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.wav"
            try:
                subprocess.run(["say", "-v", config.ENGLISH_VOICE, "-r", str(int(185 * speed)), "-o", str(out),
                                "--data-format=LEI16@22050", text], check=True, timeout=30, capture_output=True)
            except (subprocess.SubprocessError, OSError):
                return b""
            samples, sr = sf.read(out, dtype="float32")
        return _wav(samples, sr)


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


_INTERROGATIVES = {"que", "como", "donde", "adonde", "cuando", "cuanto", "cuanta", "cuantos", "cuantas", "quien",
                   "quienes", "cual", "cuales"}  # accent-insensitive ("por qué" is caught by "que")


class OneQuestion:
    """At most one real question per reply (small models often ask two). Sentences are spoken immediately until the
    first real question; after that they are held until the reply ends, and at the end only the last real question
    is kept, with every held statement.

    An echo question is not a real question: it is Sofía saying the learner's idea back («¿Una cerveza? ¡Claro!»),
    the recast that corrects them. It is spoken right away and never starts holding. A question is an echo if it
    repeats at least half of what the learner said (practice.grade), or if it is the reply's first question, has at
    most 5 words and no question word («¿Un café con leche?», not «¿Qué tal?»). Only the first question gets the short
    rule, so «¿Una cerveza? Muy bien. ¿Quieres algo más?» keeps its real question at the end."""

    def __init__(self, learner_text: str = "") -> None:
        self.learner = learner_text
        self.held: list[tuple[str, bool]] = []  # (sentence, is a real question)
        self.holding = False
        self.questions = 0  # questions seen so far

    def _echo(self, q: str) -> bool:
        first = self.questions == 0
        self.questions += 1
        w = words(q)
        if first and len(w) <= 5 and not _INTERROGATIVES & set(w):
            return True
        return bool(self.learner.strip()) and grade(q, self.learner)["score"] >= 0.5

    def feed(self, sentences: list[str]) -> list[str]:
        out = []
        for s in sentences:
            real = s.rstrip().endswith("?") and not self._echo(s)
            if real:
                self.holding = True
            if self.holding:
                self.held.append((s, real))
            else:
                out.append(s)
        return out

    def flush(self) -> list[str]:
        real = [i for i, (_, r) in enumerate(self.held) if r]
        keep = [s for i, (s, _) in enumerate(self.held) if i not in real[:-1]]
        self.held, self.holding = [], False
        return keep
