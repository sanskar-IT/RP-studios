from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Narrative Studio"
    database_url: str = "postgresql+psycopg://narrative:narrative@localhost:5432/narrative"
    cors_origins: str = "http://localhost:5173"
    provider_name: str = "heuristic"
    provider_base_url: str = ""
    provider_api_key: str = ""
    provider_model: str = ""
    provider_context_window: int = 0
    provider_max_output_tokens: int = 0
    provider_features: str = ""
    log_level: str = "INFO"
    auto_create_schema: bool = False
    memory_retrieval_limit: int = 10
    memory_candidate_pool: int = 400
    lore_token_budget: int = 2048

    model_config = SettingsConfigDict(env_file=".env", env_prefix="NARRATIVE_", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def provider_feature_list(self) -> list[str]:
        return [value.strip().casefold() for value in self.provider_features.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
