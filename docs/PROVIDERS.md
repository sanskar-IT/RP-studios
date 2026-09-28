# Providers

## Protocol

Narrative code depends on `LLMProvider`:

```python
await provider.generate(messages)
await provider.stream(messages)
await provider.structured(messages, schema)
```

The core engine does not know vendor request formats, batching, or model names.

## Included adapters

- `HeuristicProvider` for offline development and schema-shaped fallback behavior.
- `ScriptedProvider` for deterministic test fixtures and provider failure simulation.
- `OpenAICompatibleProvider` for local OpenAI-compatible servers and generic compatible HTTP endpoints. The same adapter can represent vLLM, llama.cpp-compatible gateways, Ollama-compatible gateways, and OpenRouter-compatible routes when their HTTP contract matches.

The adapter owns endpoint formatting, authentication headers, streaming chunks, structured JSON parsing, and safe error categories. It does not log request bodies or credentials. A provider failure or unusable structured response is recorded as a failed generation and never silently converted into a narrative event.

## Configuration

Set `NARRATIVE_PROVIDER=heuristic` for local deterministic operation. For a compatible endpoint set:

```text
NARRATIVE_PROVIDER=openai_compatible
NARRATIVE_PROVIDER_BASE_URL=http://localhost:8000/v1
NARRATIVE_PROVIDER_MODEL=my-model
NARRATIVE_PROVIDER_API_KEY=...
```

API keys are configuration only. They are never persisted in project, scene, generation, lore, memory, or export data and are not returned to the frontend.

The regular test suite uses fixtures only. The opt-in smoke test runs only when `NARRATIVE_PROVIDER`, `NARRATIVE_PROVIDER_BASE_URL`, and `NARRATIVE_PROVIDER_MODEL` are configured:

```text
pytest -m smoke
```

## Capability evolution

The provider protocol is intentionally small. Capability flags can be added when a concrete endpoint needs them; the narrative pipeline must remain usable with providers that do not support batching or native structured-output parameters.
