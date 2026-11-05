import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
CLIENT_DIST = ROOT.parent / "client" / "dist"

# Cloud LLM through an OpenAI-compatible server (e.g. a local Codex proxy). When HABLA_LLM_URL is set it is used
# instead of Ollama; see habla/.env.example. run.sh loads habla/.env.
#   HABLA_LLM_URL       e.g. http://127.0.0.1:8317/v1 (the server's /chat/completions is used)
#   HABLA_LLM_MODEL     gpt-6.1-terra (default) or gpt-6-luna (faster)
#   HABLA_LLM_KEY       sent as "Authorization: Bearer ..."
#   HABLA_LLM_REASONING "none" (default) keeps the first token at ~1-1.5 s; higher effort makes it 2-4 s.
#                       Empty leaves reasoning_effort out of the request.


def _llm_settings() -> tuple[str, str, str, str, str]:
    url = os.environ.get("HABLA_LLM_URL", "").strip().rstrip("/")
    return (url, os.environ.get("HABLA_LLM_MODEL", "").strip() or "gpt-6.1-terra",
            os.environ.get("HABLA_LLM_KEY", "").strip(), os.environ.get("HABLA_LLM_REASONING", "none").strip(),
            "openai" if url else "ollama")


LLM_URL, LLM_MODEL, LLM_KEY, LLM_REASONING, LLM_BACKEND = _llm_settings()

OLLAMA_URL = os.environ.get("HABLA_OLLAMA_URL", "http://localhost:11434")
# Preferred models in order; the first one installed in Ollama is used.
# gemma4:e4b (~10 GB) is the default: ~1.6 s to first audio on an M1 Pro and fits next to other apps.
# gemma4:26b (~18 GB) corrects and converses noticeably better but needs ~20 GB of free memory:
#   HABLA_MODEL=gemma4:26b ./run.sh
LLM_CANDIDATES = [m for m in [os.environ.get("HABLA_MODEL"), "gemma4:e4b", "gemma4:26b", "qwen3:8b"] if m]

WHISPER_MODEL = os.environ.get("HABLA_WHISPER", "mlx-community/whisper-large-v3-turbo")

KOKORO_MODEL = MODELS_DIR / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
TUTOR_VOICE = os.environ.get("HABLA_VOICE", "piper-es-ES")  # default voice id, see tts.VOICES
TTS_SPEED = float(os.environ.get("HABLA_TTS_SPEED", "0.92"))

DB_PATH = Path(os.environ.get("HABLA_DB", DATA_DIR / "habla.db"))  # HABLA_DB lets tests use a scratch DB
# The 2000 most frequent words (ranks 1-1000 = vocab_es_1000.json; 1001-2000 add theme, examples, chunk, register
# and spoken_usefulness), then everyday words the frequency source misses (rank null, "supplement": true).
VOCAB_PATH = DATA_DIR / "vocab_es_2000.json"
VOCAB_EXTRA_PATH = DATA_DIR / "vocab_es_extra.json"
LESSONS_PATH = DATA_DIR / "lessons.json"

HISTORY_TURNS = 10  # user+tutor message pairs kept in the prompt on Ollama (trims cost a slow local re-prefill)
# A cloud model gets the whole lesson (a lesson is ~30 exchanges at most); this is only a runaway guard.
HISTORY_TURNS_CLOUD = 80


def history_turns() -> int:
    return HISTORY_TURNS_CLOUD if LLM_BACKEND == "openai" else HISTORY_TURNS
ENGLISH_VOICE = os.environ.get("HABLA_ENGLISH_VOICE", "Samantha")  # macOS voice for the teacher's English answers
L1 = os.environ.get("HABLA_L1", "Finnish")  # the learner's first language, for fix explanations


def load_env_file(path: Path = ROOT.parent / ".env") -> None:
    """For scripts started without run.sh: read KEY=VALUE lines from habla/.env into os.environ (existing values
    win) and refresh the LLM settings above. The server itself gets these from run.sh. HABLA_NO_ENV=1 skips it,
    as in run.sh."""
    global LLM_URL, LLM_MODEL, LLM_KEY, LLM_REASONING, LLM_BACKEND
    if not path.is_file() or os.environ.get("HABLA_NO_ENV"):
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    LLM_URL, LLM_MODEL, LLM_KEY, LLM_REASONING, LLM_BACKEND = _llm_settings()
