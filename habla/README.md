# Habla — talk Spanish with a local AI tutor

An end-to-end MVP to check whether a **fully local, voice-first Spanish tutor** feels good enough to learn with.
You talk to Sofía out loud. She answers in simple Spanish, corrects your mistakes by saying them back correctly, and
when you fall back to English she teaches you the Spanish word. Every word you say is tracked against the
2000 most common Spanish words, plus 126 everyday words the frequency list misses (manzana, maleta, paraguas).
Everything runs on your Mac, except that the LLM can optionally be a cloud model behind an OpenAI-compatible server.

Separate from the Langustino flashcard app in the repo root; nothing is shared with it except a copy of the word list.

## Run it

```bash
./habla/run.sh
```

Opens http://localhost:8777 in your browser. Use Chrome or Safari, because the in-app browser pane blocks the mic. The first run pulls `gemma4:e4b` with Ollama (about 10 GB),
the voices (about 370 MB) and Whisper (about 1.6 GB). You need Ollama running, plus `uv` and Node installed.

For better tutoring, use the larger model. It needs about 20 GB of free memory:

```bash
HABLA_MODEL=gemma4:26b ./habla/run.sh
```

To run Sofía on a cloud model through an OpenAI-compatible server (for example a local Codex proxy), copy
`habla/.env.example` to `habla/.env` and fill it in. `run.sh` loads it and then skips Ollama. Without
`HABLA_LLM_URL`, Ollama is used as before, and `HABLA_NO_ENV=1 ./habla/run.sh` ignores the file. Keep
`HABLA_LLM_REASONING=none`: replies start in about 1–1.5 s (`gpt-6-luna` is a bit faster than `gpt-6.1-terra`),
while higher reasoning makes them start 2–4 s later. Every LLM call goes through `server/habla/llm.py`, which
uses strict `json_schema` output and falls back to `json_object` if the server doesn't support it.

### Using it
- **Home** shows your streak, today's sentences against your daily goal (every answer you say or type, each
  graded attempt and each Repaso drill counts one; voice commands don't), the words you can say, and your
  focus areas (your most frequent kinds of mistakes). Pick a lesson. The home screen marks finished lessons and
  suggests the next one.
- **🔁 Repaso**: a 2-minute spoken review. You see the English, say the Spanish, and it's graded as you speak.
  Words come back on a spaced schedule; ones you miss come back again at the end of the round. Your own corrected
  mistakes come back too, as *Say it right* cards (the English, and «not: yo soy cansado» struck through); *This
  wasn't a mistake* removes one. Escape shows the score so far; Escape again leaves.
- **📒 Mis palabras**: every word you've learned, with the sentence you first used it in, and 🔊 to hear it.
- **⚙ Settings**: your level (A0–B1: how simply and slowly Sofía speaks), listening mode (show Sofía's text
  always, only after she's spoken, or only when tapped), your daily goal (10–50 sentences, default 20), her voice,
  and *Sounds* (a soft chime when you use a lesson word on your own; stored in this browser).
- **Using a lesson word on your own** shows "✓ ¡Usaste «la cuenta»! 1/3", a green ring on its chip and a short
  chime, once per word per lesson.
- **Narrow windows** (under 860 px): the conversation takes the whole width and 📊 in the header opens the session
  panel as a bottom sheet (Escape, the backdrop or 📊 closes it). Under 600 px the dock buttons are icon-only.
- **Hold Space** (or hold the mic button) and speak. Release to send. A quick tap toggles recording on and off instead.
- **∞ Hands-free:** just talk, and the app detects when you stop. Use headphones for this mode. The mic pauses while Sofía speaks.
  It waits longer at beginner levels, so you can stop to find a word: a pause ends what you're saying after 1.8 s at
  A0, 1.5 s at A1 (about 0.6 s longer than before), 1.2 s at A2 and 0.9 s at B1. "Still listening… take your time"
  shows while it waits. If you go on talking before Sofía's answer starts, it's still the same message: the app sends
  everything you said as one, and she answers the whole sentence.
- Press Space while Sofía is talking to **interrupt** her.
- **🛟 ¿Qué digo?** makes Sofía suggest a sentence you could say.
- **🔊 Voice** (top right) switches Sofía's voice and plays a sample. Three native Piper voices (Spain, Mexico,
  Argentina), the macOS voices Mónica and Paulina, and the original Kokoro voice. Your choice is remembered.
  For better macOS voices, download Mónica/Marisol *Enhanced* or *Premium* in System Settings → Accessibility → Spoken Content.
- **🗣 Repeat** under any reply is shadowing: she says a sentence, you repeat it, and each word is checked.
- **🎙 Say it right** on a fix card: say the correction out loud. Saying it right away is what makes it stick.
- **🛟 Hint** is a three-step ladder: a key word, then a sentence starter, then a full example.
- **Voice commands** answer instantly without the LLM: *¿Cómo? / Repite* (replay), *Más despacio* (replay
  slower), *¿Qué significa cuenta?* (meaning).
- **🐢 Slower** plays all speech at 0.8×. Each reply also has **Replay**, **Slow** and **EN** (translation) buttons, and you can click any sentence to hear it again.
- Your transcript marks English words in amber and mistakes with a red underline. Under it you get *New word* and *Fix*
  cards. Each fix shows the correction, what it means in English, and why your version was wrong.
- **Hover to translate:** hover over your message to see what you actually said in English. Hover over Sofía's message
  for its translation, or over a single word for its meaning ("decimos → decir — to say"). On touch screens, tap your
  message or use **EN**.
- The side panel shows the session (words said right, new words, fixes) and your overall vocabulary.

## How it works

```
Browser (React)                         Voice server (Python, FastAPI, one WebSocket)
───────────────                         ─────────────────────────────────────────────
mic → AudioWorklet → 16 kHz PCM  ──►    Whisper large-v3-turbo (MLX, GPU)       ~0.7 s
  (push-to-talk / energy VAD,           English-word detector (wordfreq lexicon) ~1 ms
   350 ms pre-roll)                     LLM (llm.py): Gemma 4 via Ollama, or an
                                          OpenAI-compatible server; JSON-schema output, streamed:
                                          reply ──► sentence splitter ──► Piper TTS (CPU)  ~0.1 s
transcript, cards, audio queue  ◄──     then, in the background (never cancelled, yields to the next reply):
                                          fixes = word diff of you vs `better` (fixes.py, instant),
                                          then explanations, teach, reply_en ──► cards + tracking
                                        spaCy lemmatizer → SQLite word states (FSRS-lite)
```

Design choices, and what testing showed:

| Decision | Why |
|---|---|
| **One LLM call per turn: `learner_en` → `react` → `lesson` → analysis** | A small model with a single `reply` field either answered the learner *or* followed the lesson step, and kept asking things you had already told it. Separate fields enforce both: `react` responds to what you said (and recasts mistakes), `lesson` does the lesson step. The short `learner_en` first makes the model understand you before it answers. Both spoken fields are streamed out of the partial JSON into TTS. Mistakes (with `why` written *before* `right`), new words and translations come from a second, background call. |
| **Fix cards come from a corrected sentence, not a list of mistakes** | The reply JSON has a `better` field (your message as a native speaker would say it, "" if it was right), written before the reply so Sofía can say the corrected part back («¿Una cerveza? ¡Claro!»). A separate "find the mistakes" call caught only half the real errors on gemma4:e4b and invented others (Yo soy → Yo estoy); `better` was right 8 of 8 times. `fixes.diff_fixes` turns it into fix cards with a word diff: accents, capitals and punctuation never count (it's speech), a dropped *yo* or *hola* isn't a mistake, English words become word cards, and one-word fixes get a neighbouring word («un cerveza» → «una cerveza»). The background check only explains the given fixes; a correct message with no English skips it. The one-question gate speaks a recast question right away instead of holding it. |
| **Sofía talks like a person, and code checks it** (`reply.py`) | She echoed most correct sentences back, asked yes/no questions that got 3-word answers, and re-asked things. The prompt now says: recast a mistake, or react in a few words and share one short thing about herself (a fixed persona: 31, from Madrid, works in a bookshop, brother Pablo; a scene role like the café waitress wins), then one open question. Each sentence is checked before it is spoken: «¡Muy bien!» after a mistake is dropped, a first sentence that just repeats a correct message is dropped (never the whole reply), and a few errors of her own are fixed (`REPLY_LINT`: «Yo también me gusta» → «A mí también me gusta»). Each turn's note lists the last questions she asked and what you told her, so she doesn't ask again; the system prompt stays the same all session (its hash is logged). |
| **The reply is committed first, the analysis runs in the background** | Answering Sofía at a natural pace used to cancel the grammar check: the fix card vanished, the turn wasn't saved, and Sofía repeated herself. Now the reply goes into the history as soon as it is written, and status goes idle right after the last audio. The check, DB writes, feedback and translation run in a separate task that interruptions can't cancel. If a new reply starts while a check is running, the check is cancelled and retried afterwards, so the learner never waits behind it in Ollama. When you interrupt, only the sentences you heard stay in the history (with " …"). If you heard none, her reply disappears from the screen and your message is checked together with the next one (or on its own, if you finish or leave instead). A check that fails is shown as unchecked, never as "✓ Bien dicho". |
| **Hands-free waits for a hesitating beginner** (`audio.ts`, `app.py`) | Hands-free used to end your turn after 0.9 s of quiet, so «Yo quiero…» went to Sofía alone and the rest was lost. The pause that ends a turn now depends on your level (1.8 s at A0 down to 0.9 s at B1). The mic also keeps listening until her answer starts playing: if you go on talking, the app sends `continue` and your whole utterance as one frame, the server drops her unheard answer, takes your first part back out of the history, and redoes the turn under the same id (the bubble is replaced; the lesson doesn't count an extra turn). Spoken attempts (Repaso, "Say it right") show *Listening* → *Checking…* → the result, and leaving one can no longer turn your next sentence into a graded attempt (an attempt older than 20 s is ignored by the server). |
| **One LLM transport** (`llm.py`) | Every call (reply, check, hint, the e2e simulated learner) goes through one class with two backends: Ollama (`format` = the schema) or an OpenAI-compatible `/chat/completions` (strict `json_schema`, SSE streaming, one retry with `json_object` if the server rejects it). `HABLA_LLM_URL` picks the second; nothing else in the app knows which one runs. |
| **Runaway guard** | Small models sometimes loop inside a JSON string until the context is full, which took minutes. Output is capped at 400 tokens with a 45 s turn timeout, and whatever was produced is still used. |
| **English detection is deterministic** (`english.py`) | Every LLM tested flagged ordinary Spanish words as "English". A word-frequency lexicon (English common, Spanish rare) is instant and accurate. Its result goes to the LLM as a note. |
| **Whisper tuned for accented learners** (`stt.py`) | One encoder pass for both language detection and decoding. Language is limited to es/en and biased toward Spanish (open detection labelled Finnish-accented Spanish as *Finnish*). **Sofía's last sentence is passed as the prompt.** On Spanish read by macOS's Finnish and English voices, word errors fell from 34% to 17%, at 0.7 s. Learner errors ("yo va", "la perro") are kept as spoken. |
| **Unclear speech is never "corrected"** (`stt.py`) | With an accent, Whisper sometimes mishears a word, and the app then explained mistakes you never made. The normal greedy decode records each chosen token's probability (no extra pass, a few ms): a word whose least likely token is below 0.5 is *unclear* (badly pronounced words measured 0.25–0.55, native speech ≥ 0.78; names are left out). Unclear words get a dotted amber underline in your bubble, are never a fix card, and don't count for or against a word. With 2+ unclear words (or a very unsure decode, avg_logprob < −0.6), Sofía asks you to say that part again («¿Perdón? ¿Con quién?»), at most once in 4 turns, and that turn isn't graded. If the transcript repeats Sofía's prompt (most of a question, or most of what she said), it is decoded again without the prompt and the surer result is kept; short answers that just appear in it ("bien", "un café con leche") keep the prompted decode. |
| **Whisper memory is wired** | `mx.set_wired_limit` stops macOS swapping the model out between turns. Without it, the first utterance after a pause took 14–23 s on a memory-starved Mac. |
| **Whisper turbo, not Parakeet or large-v3** | Parakeet v3 was faster (0.3 s) but had 54% word errors on accented speech, and it can't take context. large-v3 was slower and worse (and hallucinated). |
| **Piper voices run directly** (`tts.py`) | Native Spanish accents and about 30× realtime on CPU. Kokoro's Spanish sounds anglicized. The `piper-tts` macOS wheel can't find its espeak data, so the app phonemizes with kokoro-onnx's espeak-ng and runs the ONNX model itself. |
| **English detected per word, not from Whisper's language tag** | Accented Spanish is sometimes tagged `en`. Treating that as "the whole sentence was English" would teach you your own sentence. |
| **Stable system prompt** (Ollama) | The lesson goes in the system prompt. Per-turn data (English words, review words) goes in the user note, so Ollama reuses the prompt cache. |
| **Same `num_ctx` on every call** (Ollama) | A warm-up call with different options made Ollama reload the model mid-conversation, causing 10 s+ stalls. |

### Measured on an M1 Pro, 32 GB (synthetic speech, end of speech → first audio)

| LLM | STT | First audio | Quality |
|---|---|---|---|
| `gemma4:e4b` (default) | 0.7 s | **1.5–1.8 s** | Catches the common errors (gender, tense, ser/estar, agreement). Phrasing is sometimes a bit clunky. |
| `gemma4:26b` (MoE) | 1.0 s | 2.4–3.3 s | Noticeably more natural, with better explanations. Under memory pressure, STT spikes to 4–12 s. |
| `qwen3:8b` | — | — | Rejected: invented mistakes and flagged Spanish words as English. |

## Learning design

| Feature | Why |
|---|---|
| Spoken review (Repaso) | Retrieval practice on a spaced schedule is one of the most reliable ways to make words stick. Saying the word, not just recognising it, trains production. |
| Say it right / shadowing | Corrective feedback works best when the learner repairs the error themselves. Shadowing builds pronunciation and fluency. Both are graded word by word (`practice.grade`), strict on endings (empresario ≠ empresaria). |
| Hint ladder | Scaffolding: give the least help that unblocks you, so you still do the retrieval. |
| Levels | Comprehensible input: Sofía's language sits at or just above your level. |
| Focus areas | Noticing: mistakes are typed (gender, ser/estar, verb ending…), your top patterns are shown with the rule, and Sofía is told to create chances to practise them. |
| Listening mode | Hiding the text until after the audio trains your ear instead of your reading. |
| Your own mistakes in Repaso | A corrected fix comes back on the same spaced ladder as a word, so the right form is retrieved again, not just read once. |
| Echoes don't count | Saying back what Sofía just said is repetition, not recall: only your own, spaced uses move a word to *known*, so the "words you can say" count stays honest. |
| Fair grading | Digits, a bare noun or a same-meaning word pass, a wrong article fails with a note: you're graded on the Spanish, not on how Whisper wrote it. |
| Teacher card from code | Hints and recaps come from hand-checked examples and what the director recorded, so they're never wrong Spanish or praise for things you didn't say. |
| Daily goal and streak | Small, visible daily progress keeps the habit going. The goal is in sentences you say, not minutes, so it counts speaking, not time on the page. A lesson word you use on your own gets a small celebration. |

## Lessons

Code runs each lesson, not the LLM (`server/habla/director.py`). Each turn, Sofía gets one short instruction for what to do next.

```
warm-up ──► new words ─────────► practice ──────► recap ──► free talk
 1–2 turns   1 per turn, asked    until 4/5 words    spoken summary
 + review    for the next turn    used + every       + recap card
             (word cards)         review word asked,
                                  max 14 (20 in all)
```

- **Targets.** Each lesson picks 5 new words from its themed pool in `data/lessons.json`, plus up to 3 words to review
  from earlier lessons (due words first, words you're still learning before known ones, with the meaning they were
  taught with). When the hand-picked words are learned, a lesson with `"themes"` tops up its pool
  from the list's `theme` field (cafe → `food_drink`), most useful in speech first. The free conversation lesson takes
  the next most frequent words you don't know yet, skipping vulgar / written-only words and words rated ≤ 2 (of 5) for
  spoken usefulness (ranks 1–1000 have no ratings and all qualify).
- **Word states:** `pending → introduced → practiced (repeated right after being taught) → used (said on your own)`.
  Only *used* counts toward the goal, so repeating after Sofía doesn't finish a lesson. A word you already say correctly
  is marked *known* and replaced by the next word in the pool.
- **Pacing.** One new word per turn: Sofía uses it in her reply, and the very next turn she gets you to say it
  (introduce → ask → introduce → ask …). If her reply didn't actually contain the word, it is introduced again. In
  practice she asks for a review word and a new word in turn, with a plain conversational turn in between. A goodbye
  before the recap («¡Adiós!») is caught by code, and the next note tells her the scene goes on. The recap comes when
  the goal is met, after 14 practice turns, or at 20 learner turns, whatever the phase.
- **Eliciting a word.** The first attempt sets up a situation where you'd need the word. If that doesn't work, she asks
  directly: «¿Puedes decir una frase con '…'?»
- **Teacher card.** The English card under Sofía's reply is written by code (`director.py`), not by the LLM, from
  hand-checked example sentences in `data/lessons.json` (`"examples": {"cuenta": ["La cuenta, por favor.", "The bill,
  please."]}`). A new word gets «Try: …» with its example, the second time Sofía elicits a word you get «Hint: you could
  say …», and the recap card sums up only what the director recorded (words you used, the last correction, what's
  next). Words without an example (theme top-ups, free conversation) get a plain «Hint: try using …» or no card. No card
  is shown twice in a lesson, and warm-up, plain follow-up and first-attempt turns get none. The model used to write
  this card and was the least reliable part: wrong Spanish, repeated tips, and praise for things you never said.
- **Recap.** Today's words with their status, words you already knew, extra words you picked up, the corrections to
  remember, and what comes back for review. Lesson results are stored in `lessons_done`.
- **Adding a lesson:** add an entry to `data/lessons.json` with a scenario, a word pool (`"pedir: to order"` overrides the gloss)
  and an `examples` sentence (≤ 8 words, using the word) for every word in the pool; `uv run python scripts/check_lessons.py`
  checks them. Optionally `"themes"` (ids from the list's `theme` field: food_drink, home, clothing, body_health, family_people,
  travel_transport, shopping_money, nature_weather, daily_actions, time_calendar, leisure_sports, places_city, work_school…).

Simulate a whole lesson. An LLM plays a beginner who makes typical mistakes and slips into English:

```bash
cd habla/server && uv run python scripts/e2e.py --lesson cafe --simulate 14 --finish
```

Measure before and after changing prompts, against a test server (not your own on :8777). `--mutate` breaks every
3rd simulated sentence so there is something to correct, and simulated runs end with `SUMMARY` lines (echoing,
yes/no questions, repeated questions, fixes on mutated turns, target coverage). `eval_corrections.py` sends the
fixed sentences in `scripts/eval_corpus.json` and scores fix recall, false fixes, recasts and praise on errors:

```bash
PORT=8778 HABLA_NO_OPEN=1 HABLA_DB=/tmp/habla-test.db ./run.sh   # test server with a scratch DB
cd habla/server && uv run python scripts/e2e.py --url ws://localhost:8778/ws --lesson family --simulate 10 --mutate --finish
cd habla/server && uv run python scripts/eval_corrections.py --url ws://localhost:8778/ws   # --only 3 for a quick check
```

## Vocabulary tracking

`server/data/habla.db` (SQLite) tracks each lemma of the word list. The list is `data/vocab_es_2000.json` (ranks
1–1000 are the old `vocab_es_1000.json`, kept for reference; ranks 1001–2000 add `theme`, `example_es/en`, `chunk`,
`register`, `spoken_usefulness`), then `data/vocab_es_extra.json`: everyday A1–A2 words missing from it (`rank: null`,
`"supplement": true`), which get ranks after 2000 when loaded. On start, words tracked earlier while they were outside
the list get their rank, so they count like list words. States:
`new → seen` (Sofía said it) `→ learning` (taught to you, or you made a mistake with it) `→ known` (you said it correctly on your own, spaced out).
- **Spaced ladder.** A correct use counts only once the word is due; each one pushes the next review out (2 min, 10 min,
  1 day, 3 days, …). A taught word is known after the third (so ~2 min, ~10 min and a day apart). A word you say on
  your own the first time is known on its next correct use on another day.
- **Echoes don't count.** Words from Sofía's last reply, a hint, a coach example or a word card that you say back are
  repetition, not recall: no step up the ladder, and a lesson target never becomes *used* that way. Using a due known
  word in conversation counts as its review. In Repaso, a retry before the word is due again is just practice.
- **"Words you can say"** counts known content words only (nouns, verbs, adjectives, adverbs, numbers; not *de, no, a*),
  out of the list's content words. `/api/progress` also has `known_all` (every known word, used for the suggested level)
  and `total_content`. A taught sentence (*yo trabajo con computadoras*) is learned as its content words (*trabajar*).
- **One-time migration** (settings key `migr_srs_v2`): words the old rules marked known after one use (not taught, used
  fewer than twice) go back to learning, one spaced use (a day later) away from known.
- **Matching words.** spaCy (`es_core_news_sm`) lemmatizes, with fixes on top: hand-written irregular forms
  (*salgo, duermo, vuelvo, conozco*) and generated regular forms of the list's verbs (only stems of 3+ letters, so *en,
  o, sí* never become verbs). A word that is both a listed noun/adjective and a verb form (*trabajo, vivo, para, cena*,
  or *cuenta* that spaCy reads as *contar*) is decided per sentence: after a determiner it's the noun (*la cuenta, mi
  trabajo*); a lesson's open targets win a tie (*Trabajo como…* while learning *trabajar*); a yo-form at the start
  of a clause or after *yo/no* is the verb; a lone word is the noun (*Cuenta.*). Infinitives with pronouns
  (*conocerte*) count as the verb. In Repaso, the bare word is enough (*Cuenta* for *la cuenta*).
- **Fair grading in Repaso** (`practice.py`, `app.py`). Digits are spelled out before comparing (*25* = *veinticinco*,
  *1.000* = *mil*; 0–100, 200, 300, 500, 1000), and a spelled-out *uno* matches *un* or *una* (but *un* ≠ *una*). A
  wrong article before the noun fails with a note (*«el perro», not «la perro»*); the bare noun still passes. Another
  list word with the same meaning passes too (*vez* for *el tiempo*, both "time"), with a note saying which word is
  being practised. Attempt results may carry that `note`.
- **Fixes to remember** (`fix_items`). Each fix card of a checked turn (not type *other*, 1–7 words, with an English
  meaning) is stored by its normalized right form and is due 10 minutes later; the same fix again only moves it closer.
  Saying it right when due climbs the word ladder (10 min, 1 day, 3 days…), and four spaced successes retire it; a miss
  or *I don't know* starts it over, due in 2 minutes. `/api/review` returns words and fixes in one list (`kind`
  `word` | `fix`): one fix after every 3 words, or the fixes first when fewer than 3 words are due, 12 at most; the
  home screen's due count includes due fixes.
- Words outside the list (e.g. *mariposa*) are tracked too. Tests: `cd habla/server && uv run python scripts/test_srs.py`
  and `scripts/test_lemmas.py`; fix cards and the one-question gate: `scripts/test_fixes.py`, `scripts/test_onequestion.py`;
  the reply guards, memory note and persona line: `scripts/test_reply.py`;
  the teacher card and lesson data: `scripts/test_coach.py`, `scripts/check_lessons.py`;
  lesson pacing (one word at a time, review words, goodbye, length cap): `scripts/test_pacing.py`;
  Whisper's echo check and per-word confidence (no model load): `scripts/test_stt_echo.py`;
  talking on before Sofía answers (one turn, same id, nothing in the history twice): `scripts/test_continue.py`;
  Repaso fixes and fair grading (numbers, articles, same-meaning words): `scripts/test_review.py`;
  the daily goal in sentences (what counts, streak, the minutes → sentences migration): `scripts/test_goal.py`.

## Layout

```
habla/
  run.sh                 setup + start
  server/habla/app.py    WebSocket session, turn pipeline, timing
          stt.py         Whisper (MLX), hallucination guards, echo check, per-word confidence
          tts.py         voices (Piper / macOS say / Kokoro) + streaming sentence splitter
          config.py      settings, HABLA_* env vars, .env loading for scripts
          llm.py         LLM transport: Ollama or OpenAI-compatible, JSON schema, streaming
          tutor.py       prompt, JSON schemas, streaming reply extractor
          director.py    lesson phases, targets, pacing, teacher card, recap
          fixes.py       fix cards from the word diff of you vs `better`
          practice.py    grading (Repaso, Say it right, shadowing), fix_items
          reply.py       guards on each spoken sentence (praise, echo, lint) + the "already asked" note
          english.py     English-in-Spanish detector
          vocab.py       lemmatizing, word states, SQLite
  server/data/           vocab_es_2000.json (+ vocab_es_extra.json supplement), lessons.json (6 scenarios)
  server/scripts/e2e.py  drives a live server with synthetic speech (macOS `say`) and prints timings
          eval_corrections.py + eval_corpus.json   correction eval (fix recall, false fixes, recasts)
  client/src/            React UI: App.tsx, audio.ts (mic, VAD, player), Review.tsx (Repaso),
                         Words.tsx (Mis palabras), SettingsPanel.tsx, AttemptView.tsx
  .env.example           cloud LLM settings (copy to .env; .env is gitignored)
```

Test the server without a browser:

```bash
cd habla/server && uv run python scripts/e2e.py \
  --say "Hola, me llamo Ana. Soy de|en:Finland" --say "Ayer yo va a la playa" --help-turn
```

A learner who pauses mid-sentence and goes on (one turn, one reply, the bubble replaced by the whole sentence):

```bash
cd habla/server && uv run python scripts/e2e.py --url ws://localhost:8778/ws --lesson cafe --say-split 'Yo quiero|un café con leche'
```

## Known gaps (next steps)
- **Not run-tested yet:** everything from the cloud backend onwards (the OpenAI-compatible backend, the 2000-word list and
  supplement, word matching, fixes from `better`, the code-built teacher card, Sofía's reply guards and persona, lesson
  pacing, unclear speech, the hesitation-friendly mic, Repaso fixes and fair grading, the sentence goal, the narrow
  layout and the chime) has unit tests (`scripts/test_*.py`) but hasn't been run against a live server, LLM or browser.
  The proxy may want `max_completion_tokens` instead of `max_tokens`; the eval numbers and latency haven't been re-measured.
- **With `.env` present, `run.sh` always uses the cloud model.** Use `HABLA_NO_ENV=1` for Ollama (e.g. eval baselines).
- **Only 2000 words + the supplement** are in the list. spaCy's small model misses some verb forms of the new words
  (*nieva*), so those uses aren't tracked. Noun/verb homographs are guessed by rule (*Como pan* still counts as *como*).
- **Hands-free VAD is energy-based.** It works with headphones or in a quiet room. Silero VAD would be the upgrade.
- **No pronunciation feedback yet.** Whisper hears accented speech generously.
- **Desktop only.** Phone mic access needs HTTPS (mkcert or Tailscale) to the Mac.
- Spaced repetition is a simple interval ladder, not full FSRS.
- The "words you can say" count now counts content words out of 2020 (it was 905), and echoes don't count, so existing
  learners see a smaller number, and a new learner's stays at 0 until the first word is used again on another day.
- E4B sometimes misses errors (e.g. *mi ciudad es muy frío*) and doesn't always follow the lesson step exactly. 26B is clearly better at both.
- Latency depends heavily on free memory: with swap full, TTS and STT get several times slower.
