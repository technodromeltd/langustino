"""Local speech-to-text with Whisper (MLX, Apple Silicon GPU).

Utterances are short, so we skip `mlx_whisper.transcribe` (which runs a
separate language-detection encoder pass) and call `decode` once on a padded
30 s window. Language detection then reuses the same encoder output.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import mlx.core as mx
import numpy as np
from huggingface_hub import snapshot_download
from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
from mlx_whisper.decoding import DecodingOptions, decode
from mlx_whisper.load_models import load_model

from . import config

SAMPLE_RATE = 16000

# Whisper's classic hallucinations on silence / noise.
HALLUCINATIONS = re.compile(
    r"^(thank you\.?|thanks for watching!?|gracias\.?|subtítulos.*|.*amara\.org.*|you|bye\.?|\.+)$",
    re.IGNORECASE,
)


@dataclass
class Transcript:
    text: str
    language: str
    avg_logprob: float
    no_speech_prob: float
    duration: float


class STT:
    def __init__(self) -> None:
        path = snapshot_download(config.WHISPER_MODEL)
        self.model = load_model(path, dtype=mx.float16)
        self._lock = threading.Lock()

    def _decode(self, audio: np.ndarray, language: str | None):
        mel = log_mel_spectrogram(audio, n_mels=self.model.dims.n_mels)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        opts = DecodingOptions(language=language, temperature=0.0, fp16=True, without_timestamps=True)
        return decode(self.model, mel, opts)

    def warmup(self) -> None:
        self.transcribe(np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.01)

    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        """Returns None when the audio holds no usable speech."""
        duration = len(audio) / SAMPLE_RATE
        rms = float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
        if duration < 0.3 or rms < 0.003:
            return None
        audio = audio[: SAMPLE_RATE * 30]
        with self._lock:
            r = self._decode(audio, None)
            if r.language not in ("es", "en"):
                r = self._decode(audio, "es")
        text = r.text.strip()
        if not text or r.no_speech_prob > 0.6 or (HALLUCINATIONS.match(text) and (rms < 0.01 or duration < 1.2)):
            return None
        return Transcript(text, r.language, r.avg_logprob, r.no_speech_prob, duration)
