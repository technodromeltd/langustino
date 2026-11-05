"""Local speech-to-text with Whisper large-v3-turbo (MLX, Apple Silicon GPU).

Tuned for a Spanish learner with an accent:
- One encoder pass. Language detection and decoding share the same audio features.
- Language is restricted to Spanish/English and biased toward Spanish. Whisper's open detection
  labels accented Spanish as Finnish (etc.) and then transcribes it phonetically.
- The tutor's last sentence is passed as the decoding prompt. Knowing the question ("¿Cómo te
  llamas?") roughly halves word errors on accented speech.
- Model memory is wired, so macOS can't page Whisper out between turns (that caused 15-25 s stalls).

- A prompt can make Whisper "hear" the question again on unclear audio. When the result echoes it (is_echo), the
  audio is decoded again without the prompt and the more confident result wins.
- Per-word confidence comes from the same greedy decode (no extra pass): a recorder on GreedyDecoder.update keeps the
  log-probability of each chosen token. Words below UNCLEAR_P are `unclear`: maybe misheard, so the app never treats
  them as the learner's mistakes (measured: badly pronounced words 0.25-0.55, native speech >= 0.78 per word).

Tested on Spanish read by macOS's Finnish and English voices: 34% -> 17% word errors, 1.0 s -> 0.7 s.
Parakeet v3 was faster (0.3 s) but 54% on the same accented clips, and large-v3 was worse and slower.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import mlx.core as mx
import numpy as np
from huggingface_hub import snapshot_download
from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
from mlx_whisper.decoding import DecodingOptions, GreedyDecoder, decode, detect_language
from mlx_whisper.load_models import load_model
from mlx_whisper.tokenizer import get_tokenizer

from . import config

log = logging.getLogger("habla")

SAMPLE_RATE = 16000
WIRED_BYTES = 3 * 1024**3  # Whisper turbo weights (~1.6 GB) + working memory
ENGLISH_MIN_SHARE = 0.95  # choose English only when clearly English (P(en) / (P(en)+P(es)))
UNCLEAR_P = 0.5  # a word whose least likely token is below this may be misheard

# Whisper's classic hallucinations on silence / noise.
HALLUCINATIONS = re.compile(
    r"^(thank you\.?|thanks for watching!?|gracias\.?|subtítulos.*|.*amara\.org.*|you|bye\.?|\.+)$",
    re.IGNORECASE,
)


def _words(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"\w+", s))


def _longest_run(a: list[str], b: list[str]) -> int:
    """Length of the longest contiguous run of words that a and b share."""
    best, prev = 0, [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def _is_sublist(a: list[str], b: list[str]) -> bool:
    return any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


def is_echo(text: str, prompt: str | None) -> bool:
    """Whisper wrote (part of) the decoding prompt back instead of what was said. Word lists, not characters, so a
    short real answer that happens to appear in the prompt ("bien" in "Bienvenido", "un café con leche" before
    "¿Quieres algo de comer?") is not an echo. An echo is either:
    (a) most of one of the prompt's questions (>= 4 words): a shared run of >= 4 words covering >= 70% of it, or
    (b) >= 3 words that are a contiguous piece of the prompt covering >= 60% of all its words."""
    t = _words(text).split()
    if not t or not prompt:
        return False
    for sentence in re.findall(r"[^.?!]+[.?!]*", prompt):
        q = _words(sentence).split()
        if sentence.strip().endswith("?") and len(q) >= 4:
            run = _longest_run(t, q)
            if run >= 4 and run >= 0.7 * len(q):
                return True
    p = _words(prompt).split()
    return len(t) >= 3 and len(t) >= 0.6 * len(p) and _is_sublist(t, p)


def unclear_of(words: list[tuple[str, str, float]]) -> list[str]:
    """`words`: (raw text, word without punctuation, p). The words Whisper wasn't sure of (p < UNCLEAR_P), longer than
    one letter, each once. A capitalized word that doesn't start a sentence is a name: names are often unlikely
    tokens, and they are never the learner's Spanish mistakes."""
    out: list[str] = []
    initial = True
    for raw, word, p in words:
        if (word and p < UNCLEAR_P and len(word) > 1 and (initial or not word[0].isupper())
                and word.lower() not in {w.lower() for w in out}):
            out.append(word)
        if raw.strip():
            initial = raw.strip()[-1] in ".?!…"
    return out


# ---- per-token log-probabilities, recorded during the normal greedy decode ----
_recording: list | None = None  # set (under the STT lock) while a decode runs; one lazy mx scalar per decoder step
_record_s = 0.0  # time spent in the recorder (CPU side) since the last transcribe started


def _install_recorder() -> None:
    """Wrap GreedyDecoder.update once: after each step, keep the log-probability of the token it chose (batch 0, the
    filtered logits the decoder itself used; temperature 0, so it's the argmax). Lazy arrays, so the decode loop
    isn't forced to sync."""
    if getattr(GreedyDecoder.update, "_habla_recorder", False):
        return
    original = GreedyDecoder.update

    def update(self, tokens, logits, sum_logprobs):
        global _record_s
        out = original(self, tokens, logits, sum_logprobs)
        rec = _recording
        if rec is not None:
            t0 = time.perf_counter()
            try:
                row = logits[0].astype(mx.float32)
                lp = mx.take(row, out[0][0, -1]) - mx.logsumexp(row)
                mx.async_eval(lp)
                rec.append(lp)
            except Exception:  # never break the decode itself: the confidence step gives up instead
                rec.append(None)
            _record_s += time.perf_counter() - t0
        return out

    update._habla_recorder = True  # type: ignore[attr-defined]
    GreedyDecoder.update = update


@dataclass
class Transcript:
    text: str
    language: str
    avg_logprob: float
    no_speech_prob: float
    duration: float
    words: list[tuple[str, float]] = field(default_factory=list)  # (word, p of its least likely token)
    unclear: list[str] = field(default_factory=list)  # words that may be misheard (see unclear_of)


class STT:
    def __init__(self) -> None:
        mx.set_wired_limit(WIRED_BYTES)
        path = snapshot_download(config.WHISPER_MODEL)
        self.model = load_model(path, dtype=mx.float16)
        self._lock = threading.Lock()
        _install_recorder()

    def warmup(self) -> None:
        self.transcribe(np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.01)

    def _decode(self, features: mx.array, language: str, prompt: str | None):
        """One greedy decode. Returns (result, the recorded per-step log-probabilities)."""
        global _recording
        opts = DecodingOptions(language=language, temperature=0.0, fp16=True, without_timestamps=True, prompt=prompt)
        _recording = []
        try:
            r = decode(self.model, features, opts)
            steps = _recording
        finally:
            _recording = None
        return r, steps

    def _confidence(self, r, steps: list, language: str) -> list[tuple[str, str, float]]:
        """(raw text, word, p) for each word of the result: p = exp(min log-probability of its tokens). Tokens that are
        only punctuation don't count toward a word's p."""
        tokens = list(r.tokens)
        if not tokens:
            return []
        if len(steps) < len(tokens) or any(x is None for x in steps[: len(tokens)]):
            raise RuntimeError(f"recorded {len(steps)} steps for {len(tokens)} tokens")
        lps = mx.stack(steps[: len(tokens)]).tolist()
        tok = get_tokenizer(self.model.is_multilingual, num_languages=self.model.num_languages, language=language,
                            task="transcribe")
        words, word_tokens = tok.split_to_word_tokens(tokens)
        out, i = [], 0
        for raw, wt in zip(words, word_tokens, strict=True):
            mine = lps[i:i + len(wt)]
            i += len(wt)
            content = [lp for t, lp in zip(wt, mine, strict=True)
                       if (s := tok.decode([t])) and ("\ufffd" in s or re.search(r"\w", s))]
            word = re.sub(r"^\W+|\W+$", "", raw.strip())
            out.append((raw, word, math.exp(min(content or mine))))
        return out

    def transcribe(self, audio: np.ndarray, context: str | None = None) -> Transcript | None:
        """`context`: what the tutor just said. Returns None when the audio holds no usable speech."""
        duration = len(audio) / SAMPLE_RATE
        rms = float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
        if duration < 0.3 or rms < 0.003:
            return None
        audio = audio[: SAMPLE_RATE * 30]
        prompt = context[-200:] if context else None
        global _record_s
        with self._lock:
            _record_s = 0.0
            mel = log_mel_spectrogram(audio, n_mels=self.model.dims.n_mels)
            mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
            features = self.model.encoder(mel[None])
            _, probs = detect_language(self.model, features)
            p_es, p_en = probs[0].get("es", 0.0), probs[0].get("en", 0.0)
            language = "en" if p_en / max(p_en + p_es, 1e-9) > ENGLISH_MIN_SHARE else "es"
            r, steps = self._decode(features[0], language, prompt)
            # A prompt can make Whisper echo it back on unclear audio: decode again without it, keep the surer one.
            if prompt and is_echo(r.text, prompt):
                r2, steps2 = self._decode(features[0], language, None)
                log.info("STT echo retry: %r (%.2f) vs without prompt %r (%.2f)", r.text.strip(), r.avg_logprob,
                         r2.text.strip(), r2.avg_logprob)
                if r2.avg_logprob > r.avg_logprob:
                    r, steps = r2, steps2
            t0 = time.perf_counter()
            try:
                scored = self._confidence(r, steps, language)
            except Exception as e:
                log.warning("STT word confidence failed (%s): no unclear words for this utterance", e)
                scored = []
            conf_ms = (time.perf_counter() - t0) * 1000
            rec_ms = _record_s * 1000
        text = r.text.strip()
        if not text or r.no_speech_prob > 0.6 or (HALLUCINATIONS.match(text) and (rms < 0.01 or duration < 1.2)):
            return None
        words = [(w, round(p, 3)) for _, w, p in scored if w]
        unclear = unclear_of(scored)
        log.info("STT word confidence %.1fms (+%.1fms recording in the decode): %s", conf_ms, rec_ms,
                 " ".join(f"{w}:{p:.2f}" for w, p in words))
        return Transcript(text, language, r.avg_logprob, r.no_speech_prob, duration, words, unclear)
