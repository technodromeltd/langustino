"""Deterministic learning helpers: grading spoken attempts, voice commands, levels, mistake patterns.

None of this needs the LLM, so it is instant and predictable.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def words(s: str) -> list[str]:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.findall(r"\w+", s)


# ---------- numbers ----------
# The speech recognizer writes numbers as digits ("Tengo 25 años"); targets spell them out.
_UNITS = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
          "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
          "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho",
          "veintinueve"]
_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_ROUND = {100: "cien", 200: "doscientos", 300: "trescientos", 500: "quinientos", 1000: "mil"}
_NUMBER = re.compile(r"(?<!\w)\d{1,3}(?:[.,]\d{3})+(?!\w)|(?<!\w)\d+(?!\w)")


def number_words(n: int) -> str | None:
    """Spanish for 0-100, 200, 300, 500 and 1000 (treinta y uno: several words); None for anything else."""
    if 0 <= n < 30:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] + (f" y {_UNITS[unit]}" if unit else "")
    return _ROUND.get(n)


def spell_numbers(s: str) -> str:
    """Digits -> Spanish words ("25" -> "veinticinco", "1.000" / "1,000" -> "mil"); other numbers stay as they are."""
    def repl(m: re.Match) -> str:
        return number_words(int(re.sub(r"[.,]", "", m.group(0)))) or m.group(0)

    return _NUMBER.sub(repl, s)


# "1" is spoken uno, un or una depending on the noun, so a spelled-out uno matches either. un and una stay different:
# that's a gender correction ("un cerveza" -> "una cerveza").
_ONE = {"uno": {"un", "una"}}


def _close(a: str, b: str) -> bool:
    # Tolerate small transcription slips inside longer words, but never in the ending: gender and verb
    # endings are exactly what a correction is about (empresario != empresaria, hablo != habla).
    if a == b or b in _ONE.get(a, ()) or a in _ONE.get(b, ()):
        return True
    return len(a) > 5 and a[-2:] == b[-2:] and SequenceMatcher(None, a, b).ratio() >= 0.8


def grade(target: str, heard: str) -> dict:
    """Aligns what was heard with the target, word by word (accent-insensitive, small slips allowed).
    Returns {"score": 0..1, "words": [{"w": target word as written, "ok": bool}]}."""
    target, heard = spell_numbers(target), spell_numbers(heard)
    shown = re.findall(r"\w+", target)  # same tokenization as words(), keeping accents for display
    tw, hw = words(target), words(heard)
    # Longest common subsequence with fuzzy equality, so one missed word doesn't fail the rest.
    n, m = len(tw), len(hw)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if _close(tw[i], hw[j]) else max(dp[i + 1][j], dp[i][j + 1])
    ok = [False] * n
    i = j = 0
    while i < n and j < m:
        if _close(tw[i], hw[j]):
            ok[i] = True
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    score = sum(ok) / n if n else 0.0
    return {"score": round(score, 2), "words": [{"w": w, "ok": k} for w, k in zip(shown, ok, strict=True)]}


# Articles by (gender, number): "un perro" for «el perro» is fine, "la perro" isn't.
_ARTICLE = {"el": "ms", "un": "ms", "del": "ms", "al": "ms", "la": "fs", "una": "fs", "los": "mp", "unos": "mp",
            "las": "fp", "unas": "fp"}


def article_mismatch(target_display: str, heard: str) -> str | None:
    """For a target with an article («el perro»): the heard "article noun" when the article said directly before the
    noun has another gender or number ("la perro"). None if it matches, or if the noun was said without one."""
    tw = words(target_display)
    if len(tw) < 2 or tw[0] not in ("el", "la", "los", "las", "un", "una"):
        return None
    hw = words(heard)
    surface = re.findall(r"\w+", heard.lower())
    if len(surface) != len(hw):  # keep the accents for the note when the tokenizations line up
        surface = hw
    for j in range(1, len(hw)):
        if _close(tw[1], hw[j]) and hw[j - 1] in _ARTICLE and _ARTICLE[hw[j - 1]] != _ARTICLE[tw[0]]:
            return f"{surface[j - 1]} {surface[j]}"
    return None


# ---------- voice commands (answered instantly, without the LLM) ----------
_REPEAT = re.compile(r"^(repite(lo)?|repita|otra vez|puedes repetir(lo)?|me lo repites|como|perdon|que|"
                     r"repeat( that| please)?|again|say (it|that) again|sorry|pardon|what)( por favor)?$")
_SLOWER = re.compile(r"^(mas despacio|despacio|mas lento|habla mas despacio|puedes hablar mas despacio|"
                     r"slower|slow down|more slowly|speak slower)( por favor| please)?$")
_MEANING = re.compile(r"^(?:que significa|que quiere decir|what does|what is|whats)\s+(?:la palabra\s+|the word\s+)?"
                      r"([a-zñ]+)(?:\s+mean|\s+en ingles|\s+in english)?$")


def command(text: str) -> tuple[str, str] | None:
    """("repeat", "") | ("slower", "") | ("meaning", word) | None"""
    plain = " ".join(words(text))
    if _REPEAT.match(plain):
        return ("repeat", "")
    if _SLOWER.match(plain):
        return ("slower", "")
    m = _MEANING.match(plain)
    if m:
        return ("meaning", m.group(1))
    return None


# ---------- levels ----------
LEVELS = {
    "A0": {
        "label": "Total beginner",
        "speed": 0.82,
        "prompt": "The learner is a total beginner. Use short, very simple sentences (about 8 words) with only the "
                  "most common words, the way you would on someone's first day, and repeat key words. Still react to "
                  "what they said.",
        "show_translation": True,
    },
    "A1": {
        "label": "Beginner",
        "speed": 0.9,
        "prompt": "The learner is a beginner (A1). Use short, simple sentences (10 words or fewer), everyday words, "
                  "mostly the present tense.",
        "show_translation": False,
    },
    "A2": {
        "label": "Elementary",
        "speed": 0.97,
        "prompt": "The learner is elementary (A2). Use simple, natural sentences about everyday topics; present and "
                  "simple past are fine.",
        "show_translation": False,
    },
    "B1": {
        "label": "Intermediate",
        "speed": 1.04,
        "prompt": "The learner is intermediate (B1). Speak natural conversational Spanish with varied vocabulary and "
                  "tenses, still clear and friendly.",
        "show_translation": False,
    },
}


def suggest_level(known_words: int) -> str:
    # A0 (very slow, tiny sentences) is opt-in only: suggesting it to a new user made Sofía sound simplistic.
    return "A1" if known_words < 200 else "A2" if known_words < 500 else "B1"


# ---------- mistake patterns ----------
MISTAKE_TYPES = {
    "gender": ("Gender & articles", "Every noun is masculine or feminine, and articles and adjectives match it: "
               "el perro bonito, la casa bonita."),
    "verb_form": ("Verb endings", "Verbs change with the person: yo hablo, tú hablas, él habla, nosotros hablamos."),
    "tense": ("Past vs present", "Finished past actions use the preterite: ayer fui, comí, hablé."),
    "ser_estar": ("Ser vs estar", "ser = what something is (identity, origin, job); estar = how or where it is "
                  "(feelings, states, location): soy Eero, estoy cansado."),
    "por_para": ("Por vs para", "para = goal or recipient (para ti, para comer); por = cause, exchange, through "
                 "(gracias por, por la mañana)."),
    "preposition": ("Prepositions", "Spanish often uses different small words than English: pienso en ti, "
                    "voy a Madrid, soy de Finlandia."),
    "word_choice": ("Word choice", "The word exists but isn't the natural one here; learn it as part of a phrase."),
    "word_order": ("Word order", "Adjectives usually follow the noun (un coche rojo), and pronouns go before the "
                   "verb (lo quiero)."),
    "missing_word": ("Missing words", "Spanish needs some small words English drops, like the article in "
                     "me gusta el café."),
    "other": ("Other", ""),
}
