"""Deterministic detection of English words inside a learner's Spanish.

Small LLMs are unreliable at this (they flag Spanish words as English), while
word-frequency lexicons are fast and good: a word is English when it is common
in English and much rarer in Spanish. Adjacent English-leaning words are merged
into phrases ("software developer").
"""

from __future__ import annotations

import re

from wordfreq import zipf_frequency

WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ']+")
SPANISH_CHARS = re.compile(r"[áéíóúñü¿¡]")
# Loanwords Spanish speakers use daily; don't nag about them.
ALLOWED = {"ok", "okay", "internet", "email", "wifi", "hotel", "taxi", "bar", "software", "web", "app", "online", "selfie", "fútbol"}


def _score(word: str) -> tuple[float, float]:
    w = word.lower()
    return zipf_frequency(w, "en"), zipf_frequency(w, "es")


def english_spans(text: str) -> list[str]:
    words = WORD.findall(text)
    spans: list[str] = []
    cur: list[str] = []
    for w in words:
        lw = w.lower()
        if SPANISH_CHARS.search(lw) or lw in ALLOWED:
            strong = weak = False
        else:
            en, es = _score(lw)
            strong = en >= 3.0 and en - es >= 1.0
            weak = en > es + 0.1
        if strong or (cur and weak):
            cur.append(w)
        else:
            if cur:
                spans.append(" ".join(cur))
            cur = []
    if cur:
        spans.append(" ".join(cur))
    return spans
