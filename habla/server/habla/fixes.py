"""Fix cards from a corrected sentence, deterministically.

The tutor model writes `better` (the learner's message as a native speaker would say it) before its reply. It gets
that right far more often than a separate "find the mistakes" call, which missed real errors and invented others.
The fix cards' wrong/right come from a word diff of the two sentences; the model only explains them afterwards.

Words are compared accent-, case- and punctuation-insensitively: the learner speaks, so a written accent, a capital
or a comma in the transcript is never their mistake.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .practice import MISTAKE_TYPES

# Dropping or adding these is a style choice, not a mistake (Yo quiero → Quiero, Hola, quiero → Quiero).
# Matched with their accents: «el café» (article) and «tu casa» (possessive) are not él / tú.
SUBJECT_PRONOUNS = {"yo", "tú", "él", "ella", "usted", "nosotros", "nosotras", "vosotros", "ellos", "ellas"}
FILLERS = {"hola", "bueno", "pues", "eh"}
# A lone article, pronoun, clitic or possessive reads best with the word after it (un cerveza, yo gusta, lo quiero,
# mi amigos). Matched with their accents: «sé» (I know) and «té» (tea) don't lean on anything.
LEANS_FORWARD = {"un", "una", "unos", "unas", "el", "la", "los", "las", "yo", "tú", "él", "me", "te", "se", "lo",
                 "le", "mi", "mis", "tu", "tus", "su", "sus"}
MAX_FIXES = 3
_EDGE = "".join(sorted(set(".,;:!?¡¿«»\"'()[]{}…-–—/")))


def norm(s: str) -> str:
    """Lowercase, accents stripped, punctuation removed."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", s).strip()


def _words(text: str) -> tuple[list[str], list[str]]:
    """(words as written, with punctuation stripped from their edges; normalized forms)."""
    shown, normed = [], []
    for raw in text.split():
        w = raw.strip(_EDGE)
        n = norm(w).replace(" ", "")
        if n:
            shown.append(w)
            normed.append(n)
    return shown, normed


def _style_only(words: list[str]) -> bool:
    return bool(words) and all(w.lower() in SUBJECT_PRONOUNS or norm(w) in FILLERS for w in words)


def diff_fixes(learner: str, better: str, english_spans: list[str]) -> list[dict]:
    """[{wrong, right}] for the parts of `learner` that `better` changed. [] if better is "" or says the same."""
    if not better or not better.strip():
        return []
    a_shown, a = _words(learner)
    b_shown, b = _words(better)
    if not b or a == b:
        return []
    english = {w for span in english_spans for w in norm(span).split()}
    a_low, b_low = [w.lower() for w in a_shown], [w.lower() for w in b_shown]
    # A capitalized word inside both sentences is a name: a different spelling of it (Tambérea → Tampere) is the
    # transcript mishearing a name, not the learner's mistake, so names are compared as equal.
    a_key = ["<name>" if i and w[:1].isupper() else n for i, (w, n) in enumerate(zip(a_shown, a))]
    b_key = ["<name>" if j and w[:1].isupper() else n for j, (w, n) in enumerate(zip(b_shown, b))]
    ops = SequenceMatcher(None, a_key, b_key, autojunk=False).get_opcodes()
    eq: dict[int, int] = {}  # learner word index -> better word index, for words both have
    for tag, i1, i2, j1, _j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                eq[i1 + k] = j1 + k

    # (i) Not mistakes: an added/dropped subject pronoun or filler; English words (they become word cards).
    spans: list[list[int]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        if (tag == "delete" and _style_only(a_shown[i1:i2])) or (tag == "insert" and _style_only(b_shown[j1:j2])):
            continue
        if i2 > i1 and all(w in english for w in a[i1:i2]):
            continue
        # «Yo soy cansado» → «Estoy cansado»: the fix is soy → estoy; leaving out «yo» is just style.
        while tag == "replace" and i2 - i1 > j2 - j1 and a_shown[i1].lower() in SUBJECT_PRONOUNS:
            i1 += 1
        spans.append([i1, i2, j1, j2])

    # (ii) One fix for changes that are at most one shared word apart (yo va a la → fui a la, not two cards).
    merged: list[list[int]] = []
    for s in spans:
        if merged and s[0] - merged[-1][1] <= 1 and s[2] - merged[-1][3] <= 1:
            merged[-1][1], merged[-1][3] = s[1], s[3]
        else:
            merged.append(s)

    # (iii) A one-word fix gets one neighbouring word as context: un → una reads as «un cerveza» → «una cerveza».
    for s in merged:
        i1, i2, j1, j2 = s
        if max(i2 - i1, j2 - j1) > 1:
            # A longer fix never ends on a dangling article or clitic («hermano y un» → «hermanos y una hermana»).
            ends = [w for w in (a_low[i2 - 1] if i2 > i1 else None, b_low[j2 - 1] if j2 > j1 else None) if w]
            if any(w in LEANS_FORWARD for w in ends) and eq.get(i2) == j2:
                s[1], s[3] = i2 + 1, j2 + 1
            continue
        forward = any(w in LEANS_FORWARD for w in a_low[i1:i2] + b_low[j1:j2])
        can_next = eq.get(i2) == j2
        can_prev = i1 > 0 and eq.get(i1 - 1) == j1 - 1
        if i1 == i2 and can_next and can_prev:  # an added word reads best between its neighbours (poquito de español)
            s[0], s[1], s[2], s[3] = i1 - 1, i2 + 1, j1 - 1, j2 + 1
        elif can_next and (forward or not can_prev):
            s[1], s[3] = i2 + 1, j2 + 1
        elif can_prev:
            s[0], s[2] = i1 - 1, j1 - 1

    # (iv) Shown with the better sentence's spelling for the words both share, so a context word never differs by
    # an accent or a capital («Vivó en» → «Vivo en»). Nothing that only differs in writing, nothing to an empty fix.
    out: list[dict] = []
    for i1, i2, j1, j2 in merged:
        wrong = " ".join(b_shown[eq[i]] if i in eq else a_shown[i] for i in range(i1, i2))
        right = " ".join(b_shown[j1:j2])
        # A capital that only marks where one of the sentences starts («soy cansado» → «Estoy cansado»).
        if right and wrong and j1 == 0 and i1 > 0 and wrong[0].islower():
            right = right[0].lower() + right[1:]
        elif right and wrong and i1 == 0 and j1 > 0 and right[0].islower():
            wrong = wrong[0].lower() + wrong[1:]
        if not wrong or not right or norm(wrong) == norm(right):
            continue
        out.append({"wrong": wrong, "right": right})
    return out[:MAX_FIXES]


# The model's reasoning about the learner or itself, not a tip for them (coach notes and fix explanations).
META = re.compile(r"\b(the learner|learner's|the student|this is a (good )?(chance|opportunity)|i will|i should|"
                  r"lesson step|context is missing|based on common|i will correct|as an ai)\b", re.IGNORECASE)
MAX_FIX_WORDS = 7


def build_mistakes(pairs: list[dict], explanations: object) -> list[dict]:
    """The fix cards: wrong/right from diff_fixes, type/why/right_en from the check's explanation numbered n == i+1
    (type "other" and no why if it has none)."""
    by_n: dict[int, dict] = {}
    for k, e in enumerate(explanations if isinstance(explanations, list) else []):
        if not isinstance(e, dict):
            continue
        try:
            n = int(e.get("n"))
        except (TypeError, ValueError):
            n = k + 1  # no usable number: the k-th explanation is for the k-th correction
        by_n.setdefault(n, e)
    out = []
    for i, p in enumerate(pairs):
        e = by_n.get(i + 1, {})
        kind = str(e.get("type") or "other")
        out.append({"wrong": p["wrong"], "right": p["right"], "type": kind if kind in MISTAKE_TYPES else "other",
                    "why": str(e.get("why") or "").strip(), "right_en": str(e.get("right_en") or "").strip()})
    return out


def clean_mistakes(mistakes: list[dict], english: list[str], shown: list[str] | tuple[str, ...] = ()) -> list[dict]:
    """Drop fix cards that would teach nothing or mislead:
    - wrong == right apart from accents, capitals and punctuation (a transcript artefact, not a mistake)
    - "mistakes" that are just the learner's English words (those become word cards instead)
    - style-only changes (type "other" with no explanation), and explanations that are the model talking to itself
    - "fixing" a form that an earlier fix in this session gave as the right one (`shown`: their normalized rights)
    - long rewrites (more than MAX_FIX_WORDS words): not a fix the learner can take in."""
    en_words = {w for span in english for w in norm(span).split()}
    seen = set(shown)
    out = []
    for m in mistakes:
        wrong, right = str(m.get("wrong") or ""), str(m.get("right") or "")
        if not (wrong and right) or norm(wrong) == norm(right):
            continue
        if en_words & set(norm(wrong).split()):
            continue
        why = str(m.get("why") or "")
        if (m.get("type") or "other") == "other" and not why.strip():
            continue
        if META.search(why):
            continue
        if norm(wrong) in seen:
            continue
        if len(norm(wrong).split()) > MAX_FIX_WORDS:
            continue
        out.append(m)
    return out
