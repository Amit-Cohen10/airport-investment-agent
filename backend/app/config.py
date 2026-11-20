"""Application settings, loaded from environment variables / `.env`.

Everything that differs between environments (keys, model IDs, paths) lives here,
so the rest of the code never reads `os.environ` directly.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

LLMProvider = Literal["gemini", "groq", "openrouter", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- Data ---------------------------------------------------------------
    snapshot_dir: Path = BACKEND_DIR / "data" / "snapshot"
    raw_dir: Path = BACKEND_DIR / "data" / "raw"
    scoring_config_path: Path = BACKEND_DIR / "config" / "scoring.yaml"
    airport_notes_path: Path = BACKEND_DIR / "app" / "knowledge" / "airport_notes.yaml"

    # --- LLM ----------------------------------------------------------------
    # Ordered provider chain: the first provider with a configured key is primary,
    # the rest are fallbacks on rate limits / outages.
    llm_providers: list[LLMProvider] = Field(default=["gemini", "groq", "anthropic"])
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    llm_temperature: float = 0.1
    llm_timeout_s: float = 60.0

    # --- Agent --------------------------------------------------------------
    agent_max_steps: int = 8
    history_max_chars: int = 60_000

    # --- Live APIs ----------------------------------------------------------
    live_api_timeout_s: float = 10.0
    user_agent: str = "airport-investment-agent/1.0 (research prototype)"

    # --- API ----------------------------------------------------------------
    cors_origins: list[str] = Field(default=["http://localhost:5173", "http://127.0.0.1:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
