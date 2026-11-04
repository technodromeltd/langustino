import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
CLIENT_DIST = ROOT.parent / "client" / "dist"

OLLAMA_URL = os.environ.get("HABLA_OLLAMA_URL", "http://localhost:11434")
# Preferred models in order; the first one installed in Ollama is used.
# gemma4:e4b (~10 GB) is the default: ~1.6 s to first audio on an M1 Pro and fits next to other apps.
# gemma4:26b (~18 GB) corrects and converses noticeably better but needs ~20 GB of free memory:
#   HABLA_MODEL=gemma4:26b ./run.sh
LLM_CANDIDATES = [m for m in [os.environ.get("HABLA_MODEL"), "gemma4:e4b", "gemma4:26b", "qwen3:8b"] if m]

WHISPER_MODEL = os.environ.get("HABLA_WHISPER", "mlx-community/whisper-large-v3-turbo")

KOKORO_MODEL = MODELS_DIR / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
TUTOR_VOICE = os.environ.get("HABLA_VOICE", "ef_dora")
TTS_SPEED = float(os.environ.get("HABLA_TTS_SPEED", "0.92"))

DB_PATH = DATA_DIR / "habla.db"
VOCAB_PATH = DATA_DIR / "vocab_es_1000.json"
LESSONS_PATH = DATA_DIR / "lessons.json"

HISTORY_TURNS = 10  # user+tutor message pairs kept in the prompt
