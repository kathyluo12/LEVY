"""Application settings. Secrets are read from environment only.

The ``offline`` flag drives fully local, deterministic behaviour used by tests
and demos: in-memory persistence and deterministic Jev/LLM/Web adapters. No
network access or MongoDB is required in offline mode.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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

    # --- Federal Register live ingestion ---------------------------------
    # Public API, no key required. The client identifies itself with a
    # descriptive User-Agent that includes a configurable contact string so the
    # Federal Register team can reach the operator if needed.
    federal_register_base_url: str = "https://www.federalregister.gov"
    federal_register_user_agent: str = "LEVY-TariffDesk/1.0"
    federal_register_contact: str = "levy-ops@example.com"
    federal_register_query: str = "tariff"
    # Network resilience for the Federal Register client.
    federal_register_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    federal_register_max_retries: int = Field(default=3, ge=0, le=8)

    # --- register_scout scheduled run defaults ---------------------------
    # Small, safe defaults for the recurring in-process scheduled ingestion.
    register_scout_lookback_days: int = Field(default=2, ge=1, le=30)
    register_scout_max_documents: int = Field(default=50, ge=1, le=500)
    register_scout_run_on_start: bool = True

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
