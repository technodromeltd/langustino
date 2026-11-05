#!/usr/bin/env bash
# One command to set up (first run) and start Habla at http://localhost:8777
set -euo pipefail
cd "$(dirname "$0")"

# Optional settings (cloud LLM etc.), see .env.example
if [ -f .env ] && [ -z "${HABLA_NO_ENV:-}" ]; then set -a; . ./.env; set +a; fi  # HABLA_NO_ENV=1: ignore it

PORT="${PORT:-8777}"
MODEL="${HABLA_MODEL:-gemma4:e4b}"
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

command -v uv >/dev/null || { echo "Needs uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
command -v npm >/dev/null || { echo "Needs Node.js/npm"; exit 1; }
# LLM: an OpenAI-compatible server when HABLA_LLM_URL is set, else Ollama
if [ -n "${HABLA_LLM_URL:-}" ]; then
  LLM_LABEL="${HABLA_LLM_MODEL:-gpt-6.1-terra} via $HABLA_LLM_URL"
else
  command -v ollama >/dev/null || { echo "Needs Ollama: https://ollama.com/download"; exit 1; }
  curl -sf http://localhost:11434/api/tags >/dev/null || { echo "Ollama isn't running — open the Ollama app (or run: ollama serve)"; exit 1; }
  ollama list | awk '{print $1}' | grep -qx "$MODEL" || ollama pull "$MODEL"
  LLM_LABEL="$MODEL"
fi

# TTS voices: native Spanish Piper voices (~250 MB) + Kokoro (~120 MB)
mkdir -p server/models
PIPER_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/es"
for v in es_ES/sharvard/medium/es_ES-sharvard-medium es_MX/claude/high/es_MX-claude-high es_AR/daniela/high/es_AR-daniela-high; do
  n=$(basename "$v")
  [ -f "server/models/$n.onnx" ] || { curl -fL -o "server/models/$n.onnx" "$PIPER_URL/$v.onnx" && curl -fsL -o "server/models/$n.onnx.json" "$PIPER_URL/$v.onnx.json"; }
done
[ -f server/models/kokoro-v1.0.int8.onnx ] || curl -fL -o server/models/kokoro-v1.0.int8.onnx "$KOKORO_URL/kokoro-v1.0.int8.onnx"
[ -f server/models/voices-v1.0.bin ] || curl -fL -o server/models/voices-v1.0.bin "$KOKORO_URL/voices-v1.0.bin"

# Web client
(cd client && { [ -d node_modules ] || npm install --silent; } && npm run build --silent >/dev/null)

# Voice server (Whisper downloads ~1.6 GB from Hugging Face on first start)
cd server
uv sync --quiet
echo "Starting Habla on http://localhost:$PORT (LLM: $LLM_LABEL) — first start takes a minute"
[ -n "${HABLA_NO_OPEN:-}" ] || ( for _ in $(seq 1 120); do curl -sf "http://localhost:$PORT/api/progress" >/dev/null && { open "http://localhost:$PORT"; break; }; sleep 1; done ) &
HABLA_MODEL="$MODEL" exec uv run uvicorn habla.app:app --port "$PORT"
