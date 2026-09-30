"""Application settings, loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # Data
    csv_path: Path = BASE_DIR / "data" / "support_tickets.csv"
    db_path: Path = BASE_DIR / "data" / "tickets.db"
    reference_time: str | None = None  # e.g. "2024-03-31 23:59"; default = MAX(created_at)

    # LLM
    llm_provider: Literal["groq", "ollama"] = "groq"
    llm_fallback: bool = True
    llm_timeout: float = 30.0
    llm_summary: bool = False
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # Behaviour
    aging_hours: float = 24.0
    default_row_limit: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
