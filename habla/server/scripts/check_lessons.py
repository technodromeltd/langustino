"""Checks the hand-written lesson data in data/lessons.json: every themed lesson's word resolves in the word list and
has a short example sentence (the teacher card's «Try: …» / «Hint: …») that really uses the word. Scratch DB, no
server, no LLM. Prints every problem and exits 1 if there are any.

    cd habla/server && uv run python scripts/check_lessons.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="habla-lessons-")
os.environ["HABLA_DB"] = str(Path(_tmp) / "lessons.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import config  # noqa: E402
from habla.vocab import Vocab, _strip_accents  # noqa: E402

MAX_WORDS = 8


def uses(v: Vocab, example: str, lemma: str) -> bool:
    """The example contains the word: a token lemmatized to it, or a surface word that starts with its stem (the
    lemma minus its last 2 letters, accents aside: salgo for salir, pequeña for pequeño)."""
    if any(t.lemma == lemma for t in v.tokens(example, prefer={lemma})):
        return True
    stem = _strip_accents(lemma.lower())[:-2]
    return len(stem) >= 2 and any(_strip_accents(w).startswith(stem) for w in re.findall(r"\w+", example.lower()))


def main() -> int:
    v = Vocab()
    lessons = json.loads(config.LESSONS_PATH.read_text())
    problems: list[str] = []
    checked = 0
    for lesson in lessons:
        if lesson.get("auto_words"):
            continue
        lid = lesson.get("id", "?")
        examples = lesson.get("examples")
        if not isinstance(examples, dict):
            problems.append(f"{lid}: no examples object")
            examples = {}
        lemmas = []
        for entry in lesson.get("words", []):
            lemma = entry.partition(":")[0].strip()
            lemmas.append(lemma)
            checked += 1
            if not v.info(lemma):
                problems.append(f"{lid}/{lemma}: not in the word list (Vocab.info)")
            ex = examples.get(lemma)
            if not (isinstance(ex, list) and len(ex) == 2 and all(isinstance(x, str) and x.strip() for x in ex)):
                problems.append(f"{lid}/{lemma}: needs an example [\"<Spanish>\", \"<English>\"], got {ex!r}")
                continue
            es = ex[0]
            if not uses(v, es, lemma):
                problems.append(f"{lid}/{lemma}: example doesn't use the word: {es!r}")
            if (n := len(es.split())) > MAX_WORDS:
                problems.append(f"{lid}/{lemma}: example has {n} words (max {MAX_WORDS}): {es!r}")
        for extra in set(examples) - set(lemmas):
            problems.append(f"{lid}/{extra}: example for a word that isn't in the lesson's words")
    for p in problems:
        print("PROBLEM", p)
    print(f"{checked} words checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
