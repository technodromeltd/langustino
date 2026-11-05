"""Lemma matching in vocab.tokens(): the word the learner actually said gets the credit (la cuenta, not contar;
salgo -> salir), without generated verb forms swallowing en, sí, para or se. Scratch DB, no server, no LLM.

    cd habla/server && uv run python scripts/test_lemmas.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="habla-lemmas-")
os.environ["HABLA_DB"] = str(Path(_tmp) / "lemmas.db")  # before importing habla
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla import config  # noqa: E402
from habla.director import Director  # noqa: E402
from habla.vocab import Vocab  # noqa: E402

v = Vocab()

# (sentence, prefer, lemmas that must be there, lemmas that must not be)
REQUIRED: list[tuple[str, set[str] | None, set[str], set[str]]] = [
    ("La cuenta, por favor", None, {"cuenta"}, {"contar"}),
    ("cuenta", None, {"cuenta"}, {"contar"}),
    ("Cuenta.", None, {"cuenta"}, {"contar"}),
    ("Quiero pagar la cuenta", None, {"querer", "pagar", "cuenta"}, {"contar"}),
    ("Salgo de casa temprano", None, {"salir"}, set()),
    ("Siempre salgo de noche", None, {"salir"}, set()),
    ("Normalmente duermo mucho", None, {"dormir"}, set()),
    ("Conozco Madrid.", None, {"conocer"}, set()),
    ("Vuelvo a casa.", None, {"volver"}, set()),
    ("Trabajo como programador", {"trabajar"}, {"trabajar"}, {"trabajo"}),
    ("Mi trabajo es bueno", None, {"trabajo"}, {"trabajar"}),
    ("Mi trabajo es bueno", {"trabajar"}, {"trabajo"}, {"trabajar"}),  # a determiner beats the target
    ("Mi trabajo es bueno", {"trabajar", "trabajo"}, {"trabajo"}, {"trabajar"}),  # intro lesson: both are targets
    ("Vivo en Helsinki", None, {"en", "vivir"}, {"ir"}),
    ("Mucho gusto", None, {"gusto"}, {"gustar"}),
    ("Se llama Ana", None, {"llamar"}, {"ser"}),
    ("Es para ti", None, {"para"}, {"parar"}),
    ("Sí, gracias", None, {"sí"}, {"ser"}),
    ("Vivo aquí", None, {"vivir"}, {"vivo"}),
    ("Me cuenta una historia", None, {"contar"}, {"cuenta"}),
    ("Gusto en conocerte", None, {"gusto", "conocer"}, {"gustar"}),
    ("Solo agua, por favor", None, {"solo", "agua"}, {"soler"}),
]
INFORMATIONAL = [
    ("Como pan", None, "comer"),
    ("como tú", None, "como"),
    ("Yo ceno a las ocho", None, "cenar"),
    ("Trabajo como programador", None, "trabajar"),
    ("Hola, estudio español", None, "estudiar"),
    ("Desayuno a las ocho", None, "desayunar"),
]
# Generated forms must never claim these small words (en -> ir, o -> ir, sí -> ser, se -> saber, para -> parar).
NEVER_VERBS = {"en", "o", "sí", "si", "se", "para", "a", "de", "y"}

failures: list[str] = []


def lemmas(text: str, prefer: set[str] | None) -> list[str]:
    return [t.lemma for t in v.tokens(text, prefer=prefer) if t.in_list]


print(f"{'sentence':32} {'prefer':22} lemmas")
for text, prefer, need, avoid in REQUIRED:
    got = lemmas(text, prefer)
    ok = need <= set(got) and not (avoid & set(got))
    print(f"{'ok ' if ok else 'BAD'} {text:28} {str(sorted(prefer) if prefer else ''):22} {got}")
    if not ok:
        failures.append(f"{text!r} prefer={prefer}: got {got}, need {sorted(need)}, avoid {sorted(avoid)}")

print("\ninformational:")
for text, prefer, want in INFORMATIONAL:
    got = lemmas(text, prefer)
    print(f"{'   ' if want in got else '(-)'} {text:28} want {want:12} {got}")

# The form tables.
for w in NEVER_VERBS:
    if w in v.verb_forms:
        failures.append(f"verb_forms[{w!r}] = {v.verb_forms[w]!r}")
for w, verb in v.ambig.items():
    if v.words[w].get("partOfSpeech") == "verb" or v.words[verb].get("partOfSpeech") != "verb":
        failures.append(f"ambig[{w!r}] = {verb!r} is not noun/adjective -> verb")
for w, verb in v.verb_forms.items():
    if w in v.words and w != verb:
        failures.append(f"verb_forms[{w!r}] shadows the list word {w!r}")
assert v.verb_forms.get("salgo") == "salir" and v.ambig.get("trabajo") == "trabajar" and v.ambig.get("vivo") == "vivir"
print(f"\nforms: {len(v.verb_forms)} verb forms, {len(v.ambig)} noun/adjective-or-verb words")

# The cafe lesson: «la cuenta» as a target leaves pending when the learner says "La cuenta, por favor".
lessons = {x["id"]: x for x in json.loads(config.LESSONS_PATH.read_text())}
d = Director(lessons["cafe"], v)
cuenta = next((t for t in d.targets if t.lemma == "cuenta"), None)
if cuenta is not None:
    prefer = {t.lemma for t in d.targets if t.state in ("pending", "introduced", "practiced")}
    d.observe({t.lemma for t in v.tokens("La cuenta, por favor", prefer=prefer) if t.in_list})
    print(f"cafe: cuenta target {cuenta.state}")
    if cuenta.state == "pending":
        failures.append("cafe: «la cuenta» target still pending after 'La cuenta, por favor'")
else:
    print("cafe: cuenta is not a target in this DB state (skipped)")

# record_learner credits the noun and passes `prefer` through.
out = v.record_learner("s1", "La cuenta, por favor", [], [])
out2 = v.record_learner("s1", "Trabajo como programador", [], [], prefer={"trabajar"})
print(f"record_learner: {out} {out2}")
if "cuenta" not in out or "contar" in out or "trabajar" not in out2:
    failures.append(f"record_learner: {out} {out2}")

# `skip` (words Whisper wasn't sure of): neither ok nor error, even when a fix covers them.
out3 = v.record_learner("s1", "Yo trabajo en una oficina", [{"wrong": "una oficina", "right": "la oficina"}], [],
                        skip={"oficina"})
print(f"record_learner skip: {out3}")
if "oficina" in out3 or "trabajar" not in out3:
    failures.append(f"record_learner skip: {out3}")

if failures:
    print("\nFAILED:\n  " + "\n  ".join(failures))
    sys.exit(1)
print("\nall lemma tests passed")
