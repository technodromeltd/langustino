"""The one-question gate in tts.OneQuestion: a recast question («¿Una cerveza? ¡Claro!») is spoken, a second real
question is dropped. Pure Python (imports tts but loads no voice), no server, no LLM.

    cd habla/server && uv run python scripts/test_onequestion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla.tts import OneQuestion, SentenceSplitter  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, got: object) -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got: {got}"))
    if not ok:
        failures.append(name)


def run(reply: str, learner: str = "") -> tuple[list[str], list[str]]:
    """(sentences emitted by feed() while streaming, sentences emitted at flush), streamed a few characters at a
    time the way app.respond() feeds it."""
    splitter, gate = SentenceSplitter(), OneQuestion(learner)
    early: list[str] = []
    for i in range(0, len(reply), 4):
        early += gate.feed(splitter.feed(reply[i:i + 4]))
    late = gate.feed(splitter.flush()) + gate.flush()
    return early, late


early, late = run("¿Un café con leche? ¡Claro! ¿Algo para comer?")
check("recast kept: all 3 sentences", early + late == ["¿Un café con leche?", "¡Claro!", "¿Algo para comer?"],
      (early, late))
check("recast: first two spoken before the reply ends", early[:2] == ["¿Un café con leche?", "¡Claro!"], early)

early, late = run("¿Una cerveza? Muy bien. ¿Quieres algo más?")
check("«¿Una cerveza? Muy bien. ¿Quieres algo más?» all kept",
      early + late == ["¿Una cerveza?", "Muy bien.", "¿Quieres algo más?"], (early, late))

early, late = run("¿Qué tal? ¿De dónde eres?")
check("two real questions: only the last", early + late == ["¿De dónde eres?"], (early, late))

early, late = run("¡Hola! ¿Cómo te llamas? ¿De dónde eres? Yo soy de Madrid.")
check("statement after two questions is kept, first question dropped",
      early + late == ["¡Hola!", "¿De dónde eres?", "Yo soy de Madrid."], (early, late))

early, late = run("¿Quieres un café con leche y pan con tomate? ¡Perfecto! ¿Algo más?", "Quiero un café con leche y pan")
check("long recast that repeats the learner is an echo (grade)",
      early[:2] == ["¿Quieres un café con leche y pan con tomate?", "¡Perfecto!"] and late == ["¿Algo más?"],
      (early, late))

early, late = run("Muy bien. ¿Dónde trabajas?")
check("a single question is kept", early + late == ["Muy bien.", "¿Dónde trabajas?"], (early, late))

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
