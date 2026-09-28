from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ProviderMessage:
    role: str
    content: str


class LLMProvider(Protocol):
    async def generate(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> str:
        ...

    def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> AsyncIterator[str]:
        ...

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        ...


class ProviderError(RuntimeError):
    pass


class HeuristicProvider:
    async def generate(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> str:
        prompt = messages[-1].content if messages else ""
        return self._response_for(prompt)

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None):
        yield await self.generate(messages, temperature=temperature, max_tokens=max_tokens)

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        prompt = messages[-1].content if messages else ""
        return self._structured_for(prompt, schema)

    def _response_for(self, prompt: str) -> str:
        return "The scene holds its breath while the next decision takes shape."

    def _structured_for(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        properties = schema.get("properties", {})
        result: dict[str, Any] = {}
        for name, definition in properties.items():
            field_type = definition.get("type")
            if field_type == "array":
                result[name] = []
            elif field_type == "boolean":
                result[name] = False
            elif field_type == "number" or field_type == "integer":
                result[name] = 0
            else:
                result[name] = ""
        if "selected_actor" in result:
            result["reason"] = "Selected from current scene participants and goals."
        return result


class ScriptedProvider:
    def __init__(
        self,
        responses: list[dict[str, Any]],
        *,
        substitutions: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = deepcopy(responses)
        self.substitutions = substitutions or {}
        self.error = error

    def _substitute(self, value: Any) -> Any:
        if isinstance(value, str):
            for key, replacement in self.substitutions.items():
                value = value.replace("{" + key + "}", replacement)
            return value
        if isinstance(value, list):
            return [self._substitute(item) for item in value]
        if isinstance(value, dict):
            return {key: self._substitute(item) for key, item in value.items()}
        return value

    async def generate(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None) -> str:
        if self.error:
            raise self.error
        if not self.responses:
            raise ProviderError("Scripted provider has no remaining responses")
        return json.dumps(self._substitute(self.responses.pop(0)))

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None):
        yield await self.generate(messages, temperature=temperature, max_tokens=max_tokens)

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        if self.error:
            raise self.error
        if not self.responses:
            raise ProviderError("Scripted provider has no remaining responses")
        return self._substitute(self.responses.pop(0))

    async def close(self) -> None:
        return None


__all__ = ["HeuristicProvider", "LLMProvider", "ProviderError", "ProviderMessage", "ScriptedProvider"]
