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
        timeout=float(os.getenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", "").strip() or 120.0),
    )
    try:
        response = await provider.generate(
            [ProviderMessage(role="user", content="Return the single word ready.")],
            temperature=0,
            # Reasoning models emit a thinking block before the answer and the
            # adapter reads only the answer. At 8 tokens the thinking block
            # consumed the entire budget, the response came back empty, and the
            # endpoint looked broken when it was answering perfectly well.
            max_tokens=512,
        )
    finally:
        await provider.close()
    assert response.strip()
