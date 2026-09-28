from __future__ import annotations

import json

import httpx
import pytest

from services.providers.base import ProviderError, ProviderMessage
from services.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_openai_compatible_adapter_formats_request_and_returns_content():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "A staged reply"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("http://localhost:8000/v1", "local-model", client=client)
    result = await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert result == "A staged reply"
    assert requests[0].url.path == "/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "local-model"


@pytest.mark.asyncio
async def test_openai_compatible_adapter_streams_content_chunks():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"choices":[{"delta":{"content":"one"}}]}\n\ndata: {"choices":[{"delta":{"content":" two"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("http://localhost:8000/v1", "local-model", client=client)
    chunks = [chunk async for chunk in provider.stream([ProviderMessage("user", "Continue")])]
    await provider.close()
    assert chunks == ["one", " two"]


@pytest.mark.asyncio
async def test_structured_adapter_rejects_non_json():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("http://localhost:8000/v1", "local-model", client=client)
    with pytest.raises(ProviderError):
        await provider.structured([ProviderMessage("user", "Return JSON")], {"type": "object"})
    await provider.close()
