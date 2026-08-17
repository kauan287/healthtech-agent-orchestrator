"""
Application settings loaded from environment variables or .env file.
Uses pydantic-settings for type-safe configuration.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    APP_NAME: str = "HealthTech Agent Orchestrator"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Groq LLM ───────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = ""

    # ── Database ───────────────────────────────────────────────────────────────
    # Full SQLAlchemy connection URL. Override in production via env var.
    # docker-compose provides a Postgres instance matching these defaults.
    DATABASE_URL: str = (
        "postgresql+psycopg2://healthtech:healthtech@localhost:5432/healthtech_db"
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()
