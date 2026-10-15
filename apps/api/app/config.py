from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _blank_as_zero(value: str | int | None) -> int:
    """Read an unset numeric setting as 0.

    ``.env.example`` ships these fields empty, so a straight copy is the common
    case and pydantic would otherwise refuse it: an empty string is not an int.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    return int(value)


TokenCount = Annotated[int, BeforeValidator(_blank_as_zero)]


def _blank_as_default(value: str | float | None, fallback: float) -> float:
    """Read an unset numeric setting as its default rather than as zero.

    A zero timeout is not a request that should never finish; it is a config
    that was left empty in ``.env``, which is what a straight copy of
    ``.env.example`` produces.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return fallback
    parsed = float(value)
    return parsed if parsed > 0 else fallback


DEFAULT_PROVIDER_TIMEOUT_SECONDS = 120.0

ProviderTimeout = Annotated[float, BeforeValidator(lambda v: _blank_as_default(v, DEFAULT_PROVIDER_TIMEOUT_SECONDS))]


class Settings(BaseSettings):
    app_name: str = "AI Narrative Studio"
    database_url: str = "postgresql+psycopg://narrative:narrative@localhost:5432/narrative"
    cors_origins: str = "http://localhost:5173"
    provider_name: str = Field(
        default="heuristic",
        validation_alias=AliasChoices("NARRATIVE_PROVIDER_NAME", "NARRATIVE_PROVIDER"),
    )
    provider_base_url: str = ""
    provider_api_key: str = ""
    provider_model: str = ""
    provider_context_window: TokenCount = 0
    provider_max_output_tokens: TokenCount = 0
    provider_features: str = ""
    provider_timeout_seconds: ProviderTimeout = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    log_level: str = "INFO"
    auto_create_schema: bool = False
    memory_retrieval_limit: int = 10
    memory_candidate_pool: int = 400
    lore_token_budget: int = 2048

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NARRATIVE_", extra="ignore", populate_by_name=True
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def provider_feature_list(self) -> list[str]:
        return [value.strip().casefold() for value in self.provider_features.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
