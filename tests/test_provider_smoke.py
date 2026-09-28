from __future__ import annotations

import os

import pytest

from services.providers.base import ProviderMessage
from services.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_configured_openai_compatible_endpoint():
    provider_name = os.getenv("NARRATIVE_PROVIDER", "heuristic").casefold()
    base_url = os.getenv("NARRATIVE_PROVIDER_BASE_URL", "")
    model = os.getenv("NARRATIVE_PROVIDER_MODEL", "")
    if provider_name in {"heuristic", ""} or not base_url or not model:
        pytest.skip("Set NARRATIVE_PROVIDER, NARRATIVE_PROVIDER_BASE_URL, and NARRATIVE_PROVIDER_MODEL for the live smoke test")
    provider = OpenAICompatibleProvider(
        base_url,
        model,
        os.getenv("NARRATIVE_PROVIDER_API_KEY") or None,
    )
    try:
        response = await provider.generate(
            [ProviderMessage(role="user", content="Return the single word ready.")],
            temperature=0,
            max_tokens=8,
        )
    finally:
        await provider.close()
    assert response.strip()
