"""App settings, read from environment variables or a local .env file."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Windows consoles default to cp1252 and crash on Vietnamese output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so we don't need python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "hotel.db"))
    # Any OpenAI-compatible server: Ollama, LM Studio, llama.cpp server, vLLM...
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")  # Ollama ignores it
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
    max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "5"))
    history_messages: int = int(os.getenv("HISTORY_MESSAGES", "12"))
    hold_minutes: int = int(os.getenv("HOLD_MINUTES", "30"))


settings = Settings()
