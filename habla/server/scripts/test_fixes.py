"""Fix cards from the tutor's corrected sentence (habla/fixes.py): the diff spans, and the filters that keep
transcript artefacts, style changes and the model's own reasoning off the cards. Pure Python, no server, no LLM.

    cd habla/server && uv run python scripts/test_fixes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla.fixes import build_mistakes, clean_mistakes, diff_fixes  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, got: object) -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got: {got}"))
    if not ok:
        failures.append(name)


def fx(learner: str, better: str, english: list[str] | None = None) -> list[dict]:
    return diff_fixes(learner, better, english or [])


# ---- the spec's cases ----
r = fx("Yo quiero un cerveza", "Quiero una cerveza")
check("un cerveza → una cerveza", r == [{"wrong": "un cerveza", "right": "una cerveza"}], r)

r = fx("Sí, yo gusta comer comida española", "Sí, me gusta comer comida española")
check("yo gusta → me gusta (only that)", r == [{"wrong": "yo gusta", "right": "me gusta"}], r)

r = fx("Quiero comer manzana", "Quiero comer una manzana")
check("manzana → una manzana", r in ([{"wrong": "manzana", "right": "una manzana"}],
                                     [{"wrong": "comer manzana", "right": "comer una manzana"}]), r)

r = fx("Ayer yo va a la playa", "Ayer fui a la playa")
check("yo va → fui", len(r) == 1 and "fui" in r[0]["right"].split(), r)

r = fx("Mi madre es simpática", "")
check("better '' → no fixes", r == [], r)

r = fx("Hola, quiero agua", "Quiero agua")
check("dropped greeting is not a mistake", r == [], r)

# ---- written accents, capitals and punctuation are never a mistake (the learner speaks) ----
r = fx("Vivó en Helsinki", "Vivo en Helsinki.")
check("accent only (Vivó → Vivo)", r == [], r)
r = fx("me llamo juan", "Me llamo Juan.")
check("capitals and punctuation only", r == [], r)
r = fx("Mi madre es simpática", "Mi madre es simpática.")
check("a copy of a correct sentence", r == [], r)
r = fx("Vivó Helsinki", "Vivo en Helsinki")
check("context word shown with the corrected spelling",
      len(r) == 1 and r[0]["wrong"].lower() in ("vivo", "helsinki", "vivo helsinki") and "Vivó" not in r[0]["wrong"], r)

# ---- pronouns, articles, English ----
r = fx("Yo soy de Finlandia", "Soy de Finlandia")
check("dropped yo is not a mistake", r == [], r)
r = fx("Me gusta café", "Me gusta el café")
check("missing article «el» is a mistake (not the pronoun él)", r == [{"wrong": "gusta café", "right": "gusta el café"}], r)
r = fx("Quiero un café con milk", "Quiero un café con leche", ["milk"])
check("an English word is a word card, not a fix", r == [], r)
r = fx("Quiero una café con milk", "Quiero un café con leche", ["milk"])
check("only the real mistake next to an English word", r == [{"wrong": "una café", "right": "un café"}], r)
r = fx("Tengo dos hermano y un hermana", "Tengo dos hermanos y una hermana")
check("two fixes one word apart merge into one",
      len(r) == 1 and "hermanos" in r[0]["right"] and "una hermana" in r[0]["right"], r)
r = fx("La perro es grande y yo es cansado y tu casa es bonita y los gato son negro",
       "El perro es grande y yo estoy cansado y tu casa es bonita y los gatos son negros")
check("at most 3 fixes", len(r) == 3, r)

# ---- explanations and cleaning ----
pairs = [{"wrong": "un cerveza", "right": "una cerveza"}, {"wrong": "manzana", "right": "una manzana"}]
ms = build_mistakes(pairs, [{"n": 2, "type": "missing_word", "why": "Use an article here.", "right_en": "an apple"},
                            {"n": 1, "type": "gender", "why": "Cerveza is feminine.", "right_en": "a beer"}])
check("explanations matched by n", [m["type"] for m in ms] == ["gender", "missing_word"]
      and ms[0]["wrong"] == "un cerveza" and ms[1]["right_en"] == "an apple", ms)
ms = build_mistakes(pairs, [{"n": 1, "type": "bogus", "why": "x", "right_en": ""}])
check("unknown type → other; missing explanation → other, no why",
      ms[0]["type"] == "other" and ms[1] == {**pairs[1], "type": "other", "why": "", "right_en": ""}, ms)

base = {"wrong": "un cerveza", "right": "una cerveza", "type": "gender", "why": "Cerveza is feminine.", "right_en": ""}
check("a good fix stays", clean_mistakes([base], []) == [base], None)
check("'context is missing' never reaches the client",
      clean_mistakes([{**base, "why": "The context is missing, so I guess."}], []) == [], None)
check("'as an AI' dropped", clean_mistakes([{**base, "why": "As an AI, I think this is fine."}], []) == [], None)
check("style-only (other, no why) dropped", clean_mistakes([{**base, "type": "other", "why": ""}], []) == [], None)
check("other with a why stays", len(clean_mistakes([{**base, "type": "other"}], [])) == 1, None)
check("accent-only fix dropped",
      clean_mistakes([{**base, "wrong": "Vivó", "right": "vivo"}], []) == [], None)
check("re-fixing an already shown right form dropped",
      clean_mistakes([{**base, "wrong": "una cerveza", "right": "la cerveza"}], [], ["una cerveza"]) == [], None)
check("more than 7 words dropped",
      clean_mistakes([{**base, "wrong": "a b c d e f g h", "right": "a b c"}], []) == [], None)
check("English in the wrong part dropped",
      clean_mistakes([{**base, "wrong": "un milk", "right": "una leche"}], ["milk"]) == [], None)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
