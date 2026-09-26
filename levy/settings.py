"""Application settings. Secrets are read from environment only.

The ``offline`` flag drives fully local, deterministic behaviour used by tests
and demos: in-memory persistence and deterministic Jev/LLM/Web adapters. No
network access or MongoDB is required in offline mode.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEVY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime mode
    offline: bool = True

    # MongoDB (only used when offline is False)
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "levy"

    # OpenRouter / Jev
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai"
    jev_model: str = "typesafe/jev-1.13"
    jev_timeout_seconds: float = 2.0

    # Web providers
    firecrawl_api_key: str = ""
    tavily_api_key: str = ""

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Budgets
    default_question_budget_usd: float = 5.0

    # Seed
    seed_on_start: bool = False

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key) and not self.offline

    @property
    def has_firecrawl(self) -> bool:
        return bool(self.firecrawl_api_key) and not self.offline

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key) and not self.offline


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests)."""
    get_settings.cache_clear()
