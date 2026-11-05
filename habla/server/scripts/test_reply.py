"""Reply guards and the memory note (habla.reply), the persona line and the fixed system prompt (habla.tutor).
Pure Python, no server, no LLM.

    cd habla/server && uv run python scripts/test_reply.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from habla.reply import ReplyGuard, lint, memory_note, reply_questions, strip_praise  # noqa: E402
from habla.tts import OneQuestion, SentenceSplitter  # noqa: E402
from habla.tutor import CHAT_SYSTEM, PERSONA, build_system  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, got: object = None) -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got: {got!r}"))
    if not ok:
        failures.append(name)


def run(reply: str, learner: str, better: str | None) -> list[str]:
    """What gets spoken, streamed a few characters at a time the way app.respond() feeds it."""
    splitter, guard, gate = SentenceSplitter(), ReplyGuard(learner, "t0"), OneQuestion(learner)
    guard.better = better
    out: list[str] = []
    for i in range(0, len(reply), 4):
        out += gate.feed(guard.feed(splitter.feed(reply[i:i + 4])))
    return out + gate.feed(guard.feed(splitter.flush()) + guard.flush()) + gate.flush()


# ---- praise guard ----
got = run("¡Muy bien! ¿Un café? ¡Claro! ¿Algo para comer?", "Quiero una café", "Quiero un café")
check("praise dropped on a mistake", got == ["¿Un café?", "¡Claro!", "¿Algo para comer?"], got)
got = run("¡Qué bien! Tienes dos hermanos. ¿Cómo se llaman?", "Yo tiene dos hermanos", "Tengo dos hermanos")
check("«¡Qué bien!» dropped (accent-insensitive)", got[0] == "Tienes dos hermanos.", got)
got = run("QUE BIEN. Tienes dos hermanos.", "Yo tiene dos hermanos", "Tengo dos hermanos")
check("praise case-insensitive", got == ["Tienes dos hermanos."], got)
got = run("Muy bien, un café con leche. ¿Algo más?", "Quiero una café con milk", "Quiero un café con leche")
check("leading praise stripped", got[0] == "Un café con leche.", got)
check("strip «¡Perfecto, un café!»", strip_praise("¡Perfecto, un café!") == "¡Un café!", strip_praise("¡Perfecto, un café!"))
check("strip «¡Genial!¿Y tú?» in one chunk", strip_praise("¡Genial!¿Y tú?") == "¿Y tú?", strip_praise("¡Genial!¿Y tú?"))
check("«Bienvenido» is not praise", strip_praise("¡Bienvenido a Sevilla!") == "¡Bienvenido a Sevilla!")
check("«¡Qué bien que vienes!» kept", strip_praise("¡Qué bien que vienes!") == "¡Qué bien que vienes!")
got = run("¡Muy bien! ¿De dónde eres?", "Me llamo Eero", "")
check("praise kept when the message was right", got[0] == "¡Muy bien!", got)
got = run("¡Muy bien! Tú hablas español.", "Yo hablo español.", "yo hablo español")
check("praise kept when better differs only in case/punctuation", got[0] == "¡Muy bien!", got)
got = run("¡Muy bien!", "Quiero una café", "Quiero un café")
check("only sentence never dropped (praise)", got == ["¡Muy bien!"], got)
got = run("¡Muy bien! ¿Un café?", "Quiero una café", None)
check("better unknown: guards off", got[0] == "¡Muy bien!", got)
got = run("¡Muy bien! Un café.", "", "Quiero un café")
check("no learner text (start/finish): guards off", got[0] == "¡Muy bien!", got)
got = run("Un café, muy bien. ¿Algo más?", "Quiero una café", "Quiero un café")
check("praise later in the sentence untouched", got[0] == "Un café, muy bien.", got)

# ---- echo guard ----
got = run("Tu padre es alto. Mi padre es bajo. ¿Cómo es tu madre?", "Mi padre es alto", "")
check("echo dropped", got == ["Mi padre es bajo.", "¿Cómo es tu madre?"], got)
got = run("¿Ah, sí? Mi padre es bajo, pero mi hermano es muy alto. ¿Cómo es tu madre?", "Mi padre es alto", "")
check("natural reply kept", len(got) == 3 and got[0] == "¿Ah, sí?", got)
got = run("Mi padre es bajo. ¿Cómo es tu madre?", "Mi padre es alto", "")
check("a similar sentence about herself is not an echo", got[0] == "Mi padre es bajo.", got)
got = run("Ah, tu padre es muy alto. ¿Cómo es tu madre?", "Mi padre es alto", "")
check("echo with a filler and a person swap dropped", got == ["¿Cómo es tu madre?"], got)
got = run("Sí, yo también. ¿Dónde vives?", "Yo también quiero un café", "")
check("a reaction made of fillers is not an echo", got[0] == "Sí, yo también.", got)
got = run("Tienes dos hermanos. Yo tengo un hermano.", "Tengo dos hermanos", "")
check("person swap of the verb isn't matched (kept)", got[0] == "Tienes dos hermanos.", got)
got = run("Tu padre es alto. Tu padre es alto. ¿Y tu madre?", "Mi padre es alto", "")
check("only the reply's first sentence can be an echo", got[0] == "Tu padre es alto.", got)
got = run("Mi padre es alto.", "Mi padre es alto", "")
check("only sentence never dropped (echo)", got == ["Mi padre es alto."], got)
got = run("¿Tu padre es alto? ¡Qué bien! ¿Cómo se llama?", "Mi padre es alto", "")
check("echo question not dropped", got[0] == "¿Tu padre es alto?", got)
got = run("¡Hola, Eero! Encantada. ¿De dónde eres?", "Hola, soy Eero", "")
check("short greeting is not an echo", got[0] == "¡Hola, Eero!", got)
got = run("Tu padre es alto. ¿Cómo es tu madre?", "Mi padre es alto", "Mi padre es muy alto")
check("echo guard off when better is not empty", got[0] == "Tu padre es alto.", got)
got = run("¡Ah, sí! Mi padre es bajo.", "Mi padre es alto", "")
check("only the first spoken sentence is checked", got == ["¡Ah, sí!", "Mi padre es bajo."], got)

# ---- lint ----
check("lint Yo también me gusta", lint("Yo también me gusta el café.") == "A mí también me gusta el café.",
      lint("Yo también me gusta el café."))
check("lint lowercase mid-sentence", lint("¡Sí, yo también me gusta!") == "¡Sí, a mí también me gusta!",
      lint("¡Sí, yo también me gusta!"))
check("lint conoco", lint("No conoco Helsinki.") == "No conozco Helsinki.", lint("No conoco Helsinki."))
check("lint leaves «A mí también me gusta»", lint("A mí también me gusta.") == "A mí también me gusta.")
got = run("¡Qué bien! Yo también me gusta el mar. ¿Dónde vives?", "Me gusta el mar", "")
check("lint applied while streaming", got[1] == "A mí también me gusta el mar.", got)

# ---- memory note ----
check("reply_questions skips a recast", reply_questions("¿Una cerveza? ¡Claro! ¿Qué cerveza te gusta?",
      "Quiero un cerveza") == ["¿Qué cerveza te gusta?"], reply_questions("¿Una cerveza? ¡Claro! ¿Qué cerveza te gusta?",
                                                                          "Quiero un cerveza"))
check("reply_questions on a cut reply", reply_questions("Hola. ¿Dónde vives? …") == ["¿Dónde vives?"],
      reply_questions("Hola. ¿Dónde vives? …"))
check("memory note empty", memory_note([], []) == "")
note = memory_note(["¿Cómo te llamas?", "¿De dónde eres?", "¿de donde eres?", "¿Dónde vives?", "¿Qué haces?",
                    "¿Te gusta Madrid?", "¿Cuántos años tienes?"], ["My name is Eero.", "", "I am from Finland."])
check("memory note: distinct questions, deduped", note.count("«") == 6 and "¿Cómo te llamas?" in note
      and note.count("onde eres") == 1, note)
check("memory note: told joined with '; '", "They told you: My name is Eero.; I am from Finland." in note, note)
check("memory note format", note.startswith("[Already asked: «") and "— don't ask these again." in note
      and note.endswith("]") and "\n" not in note, note)
long = memory_note([], ["x" * 300, "I live in Helsinki with my wife.", "y" * 240])
check("told ≤ 400 chars, newest kept", "y" * 240 in long and "x" * 300 not in long
      and len(long) <= 400 + len("[They told you: ]"), long)
check("told: one very long fact truncated", len(memory_note([], ["z" * 800])) <= 400 + len("[They told you: ]"))
mine = memory_note([], [], ["Vivo en Madrid.", "Tengo treinta y un años.", "Vivo en Madrid."])
check("her own statements, deduped", mine == "[You already said: «Vivo en Madrid.» «Tengo treinta y un años.» — never "
      "say these again.]", mine)
from habla.reply import self_statements  # noqa: E402
check("self statements", self_statements("¡Qué bien! Vivo en Madrid y hablo español. ¿Qué idiomas hablas?")
      == ["Vivo en Madrid y hablo español."], self_statements("¡Qué bien! Vivo en Madrid y hablo español. ¿Qué idiomas hablas?"))
check("memory note: questions only", memory_note(["¿Qué tal?"], []) == "[Already asked: «¿Qué tal?» — don't ask "
      "these again.]", memory_note(["¿Qué tal?"], []))

# ---- system prompt ----
cafe = {"scenario": "You are a friendly waitress (camarera) at a small café in Sevilla.", "goal": "Order."}
a, b = build_system(cafe, "A1"), build_system(cafe, "A1")
check("system prompt deterministic", a == b)
lines = a.split("\n")
i = next(i for i, x in enumerate(lines) if x.startswith("Role-play:"))
check("persona line right after the role-play line", lines[i + 1] == PERSONA, lines[i:i + 2])
check("no memory note in the system prompt", "Already asked" not in a)
check("prompt: open question, no echo, model speaker", all(x in CHAT_SYSTEM for x in
      ("ONE open question", "do NOT repeat their sentence", "model speaker", "«Yo tiene dos hermanos»")))

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
