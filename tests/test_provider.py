from __future__ import annotations

import json

import httpx
import pytest

from services.narrative.pipeline import SYSTEM_RULES
from services.providers.base import ProviderError, ProviderMessage
from services.providers.openai_compatible import OpenAICompatibleProvider

BASE_URL = "http://localhost:11434/v1"
MODEL = "local-model"


def completion(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def build(handler, base_url: str = BASE_URL, api_key: str | None = None) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(base_url, MODEL, api_key, client=client)


@pytest.mark.asyncio
async def test_openai_compatible_adapter_formats_request_and_returns_content():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion("A staged reply")

    provider = build(handler)
    result = await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert result == "A staged reply"
    assert requests[0].url.path == "/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == MODEL


@pytest.mark.asyncio
async def test_openai_compatible_adapter_streams_content_chunks():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"choices":[{"delta":{"content":"one"}}]}\n\ndata: {"choices":[{"delta":{"content":" two"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = build(handler)
    chunks = [chunk async for chunk in provider.stream([ProviderMessage("user", "Continue")])]
    await provider.close()
    assert chunks == ["one", " two"]


@pytest.mark.asyncio
async def test_structured_adapter_rejects_non_json():
    async def handler(request: httpx.Request) -> httpx.Response:
        return completion("not json")

    provider = build(handler)
    with pytest.raises(ProviderError):
        await provider.structured([ProviderMessage("user", "Return JSON")], {"type": "object"})
    await provider.close()


def test_a_base_url_is_required():
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider("", MODEL)


@pytest.mark.parametrize(
    "configured",
    ["http://localhost:11434/v1", "http://localhost:11434/v1/", "http://localhost:11434/v1///"],
)
@pytest.mark.asyncio
async def test_a_trailing_slash_in_the_base_url_does_not_double_the_separator(configured):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion("ok")

    provider = build(handler, base_url=configured)
    await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert requests[0].url.path == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_an_api_key_becomes_a_bearer_header():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion("ok")

    provider = build(handler, api_key="sk-local-secret")
    await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert requests[0].headers["Authorization"] == "Bearer sk-local-secret"


@pytest.mark.asyncio
async def test_no_api_key_means_no_authorization_header():
    """Local servers ignore the header but do not need it.

    An empty credential must not become ``Bearer ``, which some local servers
    reject outright.
    """
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion("ok")

    provider = build(handler, api_key=None)
    await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_generation_sends_model_temperature_and_optional_max_tokens():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion("ok")

    provider = build(handler)
    await provider.generate([ProviderMessage("user", "Continue")], temperature=0.2)
    await provider.generate([ProviderMessage("user", "Continue")], temperature=0.2, max_tokens=64)
    await provider.close()
    first = json.loads(requests[0].content)
    second = json.loads(requests[1].content)
    assert first["temperature"] == 0.2
    assert "max_tokens" not in first
    assert second["max_tokens"] == 64


@pytest.mark.asyncio
async def test_a_non_2xx_response_becomes_a_provider_error_that_quotes_the_status():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"error": "model not found: no-secret-details"})

    provider = build(handler)
    with pytest.raises(ProviderError) as caught:
        await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert "404" in str(caught.value)
    assert "no-secret-details" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": {"message": {"content": "wrong shape"}}},
    ],
)
@pytest.mark.asyncio
async def test_an_unreadable_completion_becomes_a_provider_error(payload):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider = build(handler)
    with pytest.raises(ProviderError):
        await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()


@pytest.mark.parametrize("content", [None, "", "   "])
@pytest.mark.asyncio
async def test_an_empty_completion_becomes_a_provider_error_that_names_the_cause(content):
    """A null, empty, or blank content raises instead of stringifying.

    This used to assert the old behaviour, where ``str(None)`` turned a null
    content into the literal string ``"None"``. A thinking model that puts its
    whole reply in a reasoning field and returns ``content: null`` then looked
    like a completion of the word "None", and the turn failed downstream at
    ``json.loads`` with a message about structured JSON that pointed away from
    the real cause. Raising here keeps the real cause in the error.
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = build(handler)
    with pytest.raises(ProviderError) as caught:
        await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()
    assert "empty completion" in str(caught.value)


@pytest.mark.asyncio
async def test_structured_output_keeps_the_caller_system_text_and_the_schema():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured(
        [
            ProviderMessage("system", "Execute the approved scene."),
            ProviderMessage("user", "The storm continues."),
        ],
        {"type": "object", "properties": {"prose": {"type": "string"}}},
    )
    await provider.close()
    messages = json.loads(requests[0].content)["messages"]
    assert len(messages) == 2
    assert '"prose":{"type":"string"}' in messages[0]["content"]
    assert "Execute the approved scene." in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "The storm continues."}


@pytest.mark.parametrize(
    "raw",
    [
        "```json\n{\"prose\": \"fenced\"}\n```",
        "Here is the JSON you asked for:\n{\"prose\": \"preceded\"}",
        "",
        "   ",
    ],
)
@pytest.mark.asyncio
async def test_structured_output_rejects_anything_json_loads_will_not_take(raw):
    """Pinned current behaviour, not a wish.

    A local model that wraps its reply in a markdown fence fails the generation
    today. That is worth knowing precisely, so the test records the fence and the
    commentary case as failures: if a later change makes them succeed, this test
    is what says so on purpose rather than by accident.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return completion(raw)

    provider = build(handler)
    with pytest.raises(ProviderError):
        await provider.structured([ProviderMessage("user", "Return JSON")], {"type": "object"})
    await provider.close()


@pytest.mark.asyncio
async def test_structured_output_rejects_valid_json_that_is_not_an_object():
    async def handler(request: httpx.Request) -> httpx.Response:
        return completion("[1, 2, 3]")

    provider = build(handler)
    with pytest.raises(ProviderError) as caught:
        await provider.structured([ProviderMessage("user", "Return JSON")], {"type": "object"})
    await provider.close()
    assert "must be an object" in str(caught.value)


@pytest.mark.asyncio
async def test_structured_output_sends_exactly_one_leading_system_message():
    """The request shape Ollama requires, and the one the pipeline produces.

    The schema instruction used to arrive as its own leading system message on
    top of ``SYSTEM_RULES``, so every structured call went out with two and
    Ollama answered HTTP 500 with ``Jinja Exception: System message must be at
    the beginning.`` The instruction is now folded into the caller's system
    message, which keeps the schema first in the prompt and leaves one system
    message on the wire.
    """
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured(
        [
            ProviderMessage("system", SYSTEM_RULES),
            ProviderMessage("user", "The storm continues."),
        ],
        {"type": "object"},
    )
    await provider.close()
    messages = json.loads(requests[0].content)["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert '"type":"object"' in messages[0]["content"]
    assert SYSTEM_RULES in messages[0]["content"]
    assert messages[0]["content"].index('"type":"object"') < messages[0]["content"].index(SYSTEM_RULES)


@pytest.mark.asyncio
async def test_structured_output_still_prepends_a_system_message_when_there_is_none():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured(
        [ProviderMessage("user", "Return JSON")],
        {"type": "object"},
    )
    await provider.close()
    messages = json.loads(requests[0].content)["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert '"type":"object"' in messages[0]["content"]


@pytest.mark.asyncio
async def test_structured_output_collapses_several_leading_system_messages():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured(
        [
            ProviderMessage("system", "First rule."),
            ProviderMessage("system", "Second rule."),
            ProviderMessage("user", "Return JSON"),
        ],
        {"type": "object"},
    )
    await provider.close()
    messages = json.loads(requests[0].content)["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "First rule." in messages[0]["content"]
    assert "Second rule." in messages[0]["content"]


@pytest.mark.asyncio
async def test_structured_output_does_not_mutate_the_callers_messages():
    original = [ProviderMessage("system", SYSTEM_RULES), ProviderMessage("user", "Continue.")]

    async def handler(request: httpx.Request) -> httpx.Response:
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured(original, {"type": "object"})
    await provider.close()
    assert [message.content for message in original] == [SYSTEM_RULES, "Continue."]


@pytest.mark.asyncio
async def test_structured_output_does_not_ask_the_endpoint_for_a_schema():
    """The adapter never negotiates structured output.

    Ollama supports ``response_format`` and this adapter does not use it, so the
    request must stay a plain chat completion. This test exists so that adding it
    is a deliberate, reviewed change rather than a silent wire change.
    """
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"prose": "ok"}')

    provider = build(handler)
    await provider.structured([ProviderMessage("user", "Return JSON")], {"type": "object"})
    await provider.close()
    body = json.loads(requests[0].content)
    assert "response_format" not in body
    assert set(body) == {"model", "messages", "temperature"}


@pytest.mark.asyncio
async def test_streaming_rejects_a_non_2xx_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = build(handler)
    with pytest.raises(ProviderError):
        [chunk async for chunk in provider.stream([ProviderMessage("user", "Continue")])]
    await provider.close()


@pytest.mark.asyncio
async def test_streaming_rejects_a_malformed_chunk():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: {not json\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = build(handler)
    with pytest.raises(ProviderError):
        [chunk async for chunk in provider.stream([ProviderMessage("user", "Continue")])]
    await provider.close()


@pytest.mark.asyncio
async def test_a_connection_failure_surfaces_as_an_httpx_error_not_a_provider_error():
    """Documented gap, pinned so closing it is a decision rather than a surprise.

    A refused connection or a read timeout escapes the adapter as an
    ``httpx.HTTPError``. The pipeline catches it incidentally, so nothing is
    broken today, but the adapter's own error contract does not cover it.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = build(handler)
    with pytest.raises(httpx.ConnectError):
        await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()


@pytest.mark.asyncio
async def test_a_read_timeout_surfaces_as_an_httpx_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider = build(handler)
    with pytest.raises(httpx.ReadTimeout):
        await provider.generate([ProviderMessage("user", "Continue")])
    await provider.close()


def test_the_configured_timeout_is_carried_into_the_client():
    provider = OpenAICompatibleProvider(BASE_URL, MODEL, timeout=900.0)
    assert provider.timeout == 900.0
    assert provider.client.timeout.connect == 900.0