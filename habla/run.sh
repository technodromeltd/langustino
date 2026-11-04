#!/usr/bin/env bash
# One command to set up (first run) and start Habla at http://localhost:8777
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8777}"
MODEL="${HABLA_MODEL:-gemma4:e4b}"
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

command -v uv >/dev/null || { echo "Needs uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
command -v npm >/dev/null || { echo "Needs Node.js/npm"; exit 1; }
command -v ollama >/dev/null || { echo "Needs Ollama: https://ollama.com/download"; exit 1; }
curl -sf http://localhost:11434/api/tags >/dev/null || { echo "Ollama isn't running — open the Ollama app (or run: ollama serve)"; exit 1; }

# LLM
ollama list | awk '{print $1}' | grep -qx "$MODEL" || ollama pull "$MODEL"

# TTS voice model (~120 MB)
mkdir -p server/models
[ -f server/models/kokoro-v1.0.int8.onnx ] || curl -fL -o server/models/kokoro-v1.0.int8.onnx "$KOKORO_URL/kokoro-v1.0.int8.onnx"
[ -f server/models/voices-v1.0.bin ] || curl -fL -o server/models/voices-v1.0.bin "$KOKORO_URL/voices-v1.0.bin"

# Web client
(cd client && { [ -d node_modules ] || npm install --silent; } && npm run build --silent >/dev/null)

# Voice server (Whisper downloads ~1.6 GB from Hugging Face on first start)
cd server
uv sync --quiet
echo "Starting Habla on http://localhost:$PORT (LLM: $MODEL) — first start takes a minute"
[ -n "${HABLA_NO_OPEN:-}" ] || ( for _ in $(seq 1 120); do curl -sf "http://localhost:$PORT/api/progress" >/dev/null && { open "http://localhost:$PORT"; break; }; sleep 1; done ) &
HABLA_MODEL="$MODEL" exec uv run uvicorn habla.app:app --port "$PORT"
