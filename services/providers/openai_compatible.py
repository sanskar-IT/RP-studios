from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from services.providers.base import ProviderError, ProviderMessage


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ProviderError("A provider base URL is required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _body(self, messages: list[ProviderMessage], temperature: float, max_tokens: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        return body

    async def generate(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> str:
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=self._body(messages, temperature, max_tokens),
        )
        if response.is_error:
            raise ProviderError(f"Provider request failed with status {response.status_code}")
        payload = response.json()
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Provider returned an invalid completion response") from exc

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> AsyncIterator[str]:
        body = self._body(messages, temperature, max_tokens)
        body["stream"] = True
        async with self.client.stream(
            "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body
        ) as response:
            if response.is_error:
                raise ProviderError(f"Provider request failed with status {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                    delta = payload["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield str(delta)
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise ProviderError("Provider returned an invalid stream chunk") from exc

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        instruction = ProviderMessage(
            role="system",
            content=(
                "Return only valid JSON matching this schema. Do not include markdown fences.\n"
                + json.dumps(schema, separators=(",", ":"))
            ),
        )
        raw = await self.generate([instruction, *messages], temperature=temperature, max_tokens=max_tokens)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Provider did not return valid structured JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("Provider structured response must be an object")
        return value

    async def close(self) -> None:
        await self.client.aclose()


class LocalOpenAICompatibleProvider(OpenAICompatibleProvider):
    pass


class GenericOpenAICompatibleProvider(OpenAICompatibleProvider):
    pass
