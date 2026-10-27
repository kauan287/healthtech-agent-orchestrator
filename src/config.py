from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_AUDIT_SIGNING_KEY = "dev-only-audit-signing-key-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Defaults to production so a deployment that forgets to set it refuses the dev key.
    ENVIRONMENT: Literal["development", "test", "production"] = "production"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # "demo" is a deterministic rule table so the project runs without an API key.
    LLM_PROVIDER: Literal["groq", "demo"] = "groq"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # SQLAlchemy URL. sqlite:///... and postgresql+psycopg://... are supported.
    DATABASE_URL: str = "sqlite:///./healthtech.db"

    AUDIT_SIGNING_KEY: str = DEV_AUDIT_SIGNING_KEY

    @model_validator(mode="after")
    def _check_secrets(self) -> Self:
        if self.LLM_PROVIDER == "groq" and not self.GROQ_API_KEY:
            raise ValueError(
                "LLM_PROVIDER=groq requires GROQ_API_KEY. Use LLM_PROVIDER=demo to run offline."
            )
        if self.ENVIRONMENT == "production" and self.AUDIT_SIGNING_KEY == DEV_AUDIT_SIGNING_KEY:
            raise ValueError("Set AUDIT_SIGNING_KEY to a secret value in production.")
        if self.ENVIRONMENT == "production" and len(self.AUDIT_SIGNING_KEY) < 32:
            raise ValueError("AUDIT_SIGNING_KEY must be at least 32 characters in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
