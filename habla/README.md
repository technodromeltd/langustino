# Habla — talk Spanish with a local AI tutor

An end-to-end MVP to check whether a **fully local, voice-first Spanish tutor** feels good enough to learn with.
You talk to Sofía out loud. She answers in simple Spanish, corrects your mistakes by saying them back correctly, and
when you fall back to English she teaches you the Spanish word. Every word you say is tracked against the
1000 most common Spanish words.

Separate from the Langustino flashcard app in the repo root; nothing is shared with it except a copy of the word list.

## Run it

```bash
./habla/run.sh
```

Opens http://localhost:8777 in your browser. Use Chrome or Safari, because the in-app browser pane blocks the mic. The first run pulls `gemma4:e4b` with Ollama (about 10 GB),
the Kokoro voice (about 120 MB) and Whisper (about 1.6 GB). You need Ollama running, plus `uv` and Node installed.

For better tutoring, use the larger model. It needs about 20 GB of free memory:

```bash
HABLA_MODEL=gemma4:26b ./habla/run.sh
```

### Using it
- **Hold Space** (or hold the mic button) and speak. Release to send. A quick tap toggles recording on and off instead.
- **∞ Hands-free:** just talk, and the app detects when you stop. Use headphones for this mode. The mic pauses while Sofía speaks.
- Press Space while Sofía is talking to **interrupt** her.
- **🛟 ¿Qué digo?** makes Sofía suggest a sentence you could say.
- **🐢 Slower** plays all speech at 0.8×. Each reply also has **Replay**, **Slow** and **EN** (translation) buttons, and you can click any sentence to hear it again.
- Your transcript marks English words in amber and mistakes with a red underline. Under it you get *New word* and *Fix* cards.
- The side panel shows the session (words said right, new words, fixes) and your overall vocabulary.

## How it works

```
Browser (React)                         Voice server (Python, FastAPI, one WebSocket)
───────────────                         ─────────────────────────────────────────────
mic → AudioWorklet → 16 kHz PCM  ──►    Whisper large-v3-turbo (MLX, GPU)       ~0.7 s
  (push-to-talk / energy VAD,           English-word detector (wordfreq lexicon) ~1 ms
   350 ms pre-roll)                     Gemma 4 via Ollama, JSON-schema output, streamed:
                                          reply ──► sentence splitter ──► Kokoro TTS (CPU)
transcript, cards, audio queue  ◄──       mistakes, teach, reply_en ──► cards + tracking
                                        spaCy lemmatizer → SQLite word states (FSRS-lite)
```

Design choices, and what testing showed:

| Decision | Why |
|---|---|
| **One LLM call per turn, `reply` field first** | The reply is pulled out of the partial JSON and spoken while the model is still writing `mistakes`, `teach` and `reply_en`. Putting the analysis *before* the reply improved small models a little, but added 1–2 s of silence. |
| **English detection is deterministic** (`english.py`) | Every LLM tested flagged ordinary Spanish words as "English". A word-frequency lexicon (English common, Spanish rare) is instant and accurate. Its result goes to the LLM as a note. |
| **Single-pass Whisper decode** | `mlx_whisper.transcribe` spends an extra encoder pass on language detection. Calling `decode` once on a padded window does the detection on the same features: 1.4 s → 0.7 s. Mixed "Soy de Finland y trabajo como software developer" is transcribed faithfully, and learner errors ("yo va", "la perro") are **not** silently auto-corrected. |
| **Kokoro int8 on CPU** | About 6× faster than realtime, and it leaves the GPU to Whisper and the LLM. The fp32 model was slower than realtime. |
| **Stable system prompt** | The lesson goes in the system prompt. Per-turn data (English words, review words) goes in the user note, so Ollama reuses the prompt cache. |
| **Same `num_ctx` on every call** | A warm-up call with different options made Ollama reload the model mid-conversation, causing 10 s+ stalls. |

### Measured on an M1 Pro, 32 GB (synthetic speech, end of speech → first audio)

| LLM | STT | First audio | Quality |
|---|---|---|---|
| `gemma4:e4b` (default) | 0.7 s | **1.5–1.8 s** | Catches the common errors (gender, tense, ser/estar, agreement). Phrasing is sometimes a bit clunky. |
| `gemma4:26b` (MoE) | 1.0 s | 2.4–3.3 s | Noticeably more natural, with better explanations. Under memory pressure, STT spikes to 4–12 s. |
| `qwen3:8b` | — | — | Rejected: invented mistakes and flagged Spanish words as English. |

## Vocabulary tracking

`server/data/habla.db` (SQLite) tracks each lemma of the 1000-word list:
`new → seen` (Sofía said it) `→ learning` (taught to you, or you made a mistake with it) `→ known` (you said it correctly).
A learning word has to be used correctly twice, each time after its review interval, before it counts as known.
Repeating it right after being taught doesn't count. Words that are due go into the next turn's note so Sofía works
them into her questions. Words outside the list (e.g. *mariposa*) are tracked too.

## Layout

```
habla/
  run.sh                 setup + start
  server/habla/app.py    WebSocket session, turn pipeline, timing
          stt.py         Whisper (MLX), hallucination guards
          tts.py         Kokoro + streaming sentence splitter
          tutor.py       prompt, JSON schema, streaming reply extractor
          english.py     English-in-Spanish detector
          vocab.py       lemmatizing, word states, SQLite
  server/data/           vocab_es_1000.json, lessons.json (6 scenarios)
  server/scripts/e2e.py  drives a live server with synthetic speech (macOS `say`) and prints timings
  client/src/            React UI: App.tsx, audio.ts (mic, VAD, player)
```

Test the server without a browser:

```bash
cd habla/server && uv run python scripts/e2e.py \
  --say "Hola, me llamo Ana. Soy de|en:Finland" --say "Ayer yo va a la playa" --help-turn
```

## Known gaps (next steps)
- **Only the first 1000 words** are in the list. Extending to 2000 means re-running the importer from the original app on a larger source.
- **Hands-free VAD is energy-based.** It works with headphones or in a quiet room. Silero VAD would be the upgrade.
- **No pronunciation feedback yet.** Whisper hears accented speech generously.
- **Desktop only.** Phone mic access needs HTTPS (mkcert or Tailscale) to the Mac.
- Spaced repetition is a simple interval ladder, not full FSRS. There are no session summaries or streaks yet.
