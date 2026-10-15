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

`NARRATIVE_PROVIDER` accepts `openai`, `openai_compatible`, `openai-compatible`, `local`, `ollama`, and `generic`. Any other value falls back to the heuristic provider without raising, so a typo is silent. Check `GET /api/providers/capabilities`: `adapter: "heuristic"` means your provider name did not match and nothing you configured is in use.

## Ollama

Ollama's OpenAI-compatible endpoint is the zero-cost path: no account, no key, no paid API. The model is your choice: an uncensored model, a larger instruction-following model, or a small fast one all work through the same variables. What matters for this engine is that the model follows a "return only JSON" instruction, since structured output is requested through the prompt, and that its context window covers the prompts the assembler builds.

```text
NARRATIVE_PROVIDER=ollama
NARRATIVE_PROVIDER_BASE_URL=http://localhost:11434/v1
NARRATIVE_PROVIDER_MODEL=<your-model>
NARRATIVE_PROVIDER_API_KEY=
NARRATIVE_PROVIDER_CONTEXT_WINDOW=4096
NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS=1024
NARRATIVE_PROVIDER_FEATURES=structured_output,json_mode,streaming
NARRATIVE_PROVIDER_TIMEOUT_SECONDS=600
```

Setup:

```text
ollama pull <your-model>
ollama serve
```

Four things account for almost every failed first connection.

**The base URL needs the `/v1` suffix.** The adapter appends `/chat/completions` to whatever you configure (`services/providers/openai_compatible.py:48`), so `http://localhost:11434` requests `http://localhost:11434/chat/completions` and returns 404.

**Leave the API key empty.** Local Ollama ignores it. The adapter omits the `Authorization` header entirely when the value is blank, which is the correct local behaviour.

**The model id must match `ollama list` exactly**, including the tag. It is sent verbatim with no aliasing or fallback.

**`NARRATIVE_PROVIDER_CONTEXT_WINDOW` must match the server's `num_ctx`.** Ollama defaults to 4096 tokens. This studio defaults to 8192 when the variable is unset, which lets the assembler build prompts larger than the server will accept. If you want a bigger window, build a derived model rather than raising the number alone:

```text
FROM <your-model>
PARAMETER num_ctx 16384
```

then `ollama create <your-model>-16k -f Modelfile` and set `NARRATIVE_PROVIDER_CONTEXT_WINDOW=16384`. Declaring a window the server does not have is worse than leaving it conservative, because the failure surfaces as a provider error mid-generation instead of a pruned prompt.

`NARRATIVE_PROVIDER_TIMEOUT_SECONDS` defaults to 120, which is short for a CPU-only machine generating a multi-thousand-token prompt. Raise it for local inference; there is no retry or backoff, so a timeout costs the whole turn.

### Known blockers on a stock local model

These were reproduced against Ollama 0.34.0. One is fixed; the rest are open.

**Fixed: the adapter used to send two leading system messages.** `structured()` prepended the schema as its own system message while the pipeline already sent `SYSTEM_RULES` as one, so every structured call went out as `[system, system, user]`. Ollama's llama.cpp chat templates accept at most one leading system message and answered with HTTP 500:

```text
Jinja Exception: System message must be at the beginning.
```

The schema instruction is now folded into the caller's leading system messages, so exactly one goes on the wire with the schema still first in the prompt (`services/providers/openai_compatible.py`). Verified against Ollama with a 4B model: the same request returns 200 and a well-formed staging object where it previously returned 500.

**Open: the adapter ignores the `reasoning` field.** Ollama returns `reasoning` alongside `content` for thinking models, and the adapter reads only `choices[0].message.content` (`services/providers/openai_compatible.py`). When a thinking model spends its budget on reasoning, `content` comes back null or empty, and the adapter raises `ProviderError: Provider returned an empty completion` rather than failing later at JSON parsing with a message that points away from the cause. The error names the fix: raise `max_tokens` or disable thinking.

**Open: structured output is prompt-injected, not negotiated.** The adapter does not send `response_format` or `json_schema`, even though Ollama supports both. Instead the schema rides in the system message and the reply goes through a bare `json.loads`, preceded only by stripping one markdown fence pair and surrounding whitespace.

On a 4B model this was the difference between a working turn and a failed one. Asked for a staging object with the instruction "Do not include markdown fences", it returned a complete, well-formed, detailed object wrapped in ```` ```json ````. That shape now parses, and a failed Performer call is retried twice with its validation errors fed back. What still fails is commentary around the object, an empty reply, or a brace-salvageable truncation: there is no brace salvage, so those turns are recorded as failed and nothing is committed. A larger or better instruction-following model will do better on the remaining shapes.

`NARRATIVE_PROVIDER_FEATURES` is declaration only. It populates the capability report and the degradation notes; it does not change the request.

## Running the provider tests

The regular suite never touches a network. It uses `ScriptedProvider` and `httpx.MockTransport` only.

```text
pytest
```

The opt-in smoke test self-skips unless a provider is configured:

```text
pytest -m smoke
```

With Ollama running and the variables above exported, that exercise makes one real completion against the configured endpoint and closes it cleanly. It asserts connectivity and nothing else: it does not exercise the pipeline, does not measure narrative quality, and does not check knowledge isolation.

`tests/test_local_smoke.py` is the deeper opt-in scenario. It builds three characters with separate private knowledge, possesses one of them, runs one turn per character through the real `NarrativePipeline`, and checks the engine's guarantees against a live model. Its assertions cover the prompt assembler and the event pipeline. Its model-dependent output is reported rather than asserted, because a green run says nothing about whether the prose is good or whether the model itself respected the knowledge boundary. Read the report; do not treat the exit code as a quality signal.

## Termination diagnostics

`tests/test_local_diagnostics.py` (marker `diagnose`) drives the same scenario
through a logging transport that records what the production adapter discards:
per call, `finish_reason`, `usage` token counts, latency, the raw reply, and
transport failures. Run it with the same six variables as the smoke test, plus
an output dir:

```text
pytest -m diagnose
```

```text
NARRATIVE_DIAG_OUT=/tmp/narrative-diag
```

Records default to the OS temp dir, never the repo. One JSONL file per run;
one line per event (`run`, `call`, `turn`, `generation`, `run_end`). The
production path is untouched: the transport observes and re-emits, and the
provider returns byte-identical results. Native Ollama `done_reason` is not
observable through the OpenAI-compatible endpoint, so it is not recorded;
`finish_reason` is mapped verbatim from it and is the reliable equivalent.
Authorization headers and key material are redacted before anything is
written, and unit tests pin both the capture and the redaction without a
network.

A sanitized `call` record looks like this (placeholders in angle brackets):

```json
{"type": "call", "seq": 2, "turn": "5b41665d", "attempt": 1,
 "method": "POST", "url": "<base>/v1/chat/completions",
 "request_headers": {"content-type": "application/json", "content-length": "12345"},
 "model": "<model>", "message_sizes": [{"role": "system", "chars": 412}, {"role": "user", "chars": 9876}],
 "prompt_hash": "9f2c41ab77d01330", "temperature": 0.2, "max_tokens": null,
 "latency_s": 12.8, "status": 200,
 "finish_reason": "stop",
 "usage": {"prompt_tokens": 2390, "completion_tokens": 562, "total_tokens": 2952},
 "raw_chars": 2414, "raw": "<full reply text>",
 "error": null, "run_id": "diag-20261015-124357"}
```

Read `finish_reason` first: `length` near the output budget is output-limit
exhaustion, `stop` on a mid-object reply is the model stopping early, and
prompt plus completion tokens against `num_ctx` settles context pressure. An
`error` with no body near the timeout is a transport failure, not a model
failure.

## Capability evolution

The provider protocol is intentionally small. Capability flags can be added when a concrete endpoint needs them; the narrative pipeline must remain usable with providers that do not support batching or native structured-output parameters.
