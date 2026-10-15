"""Opt-in diagnostic runs against a live local model.

Run with ``pytest -m diagnose`` once ``NARRATIVE_PROVIDER``,
``NARRATIVE_PROVIDER_BASE_URL``, and ``NARRATIVE_PROVIDER_MODEL`` are set.
It drives the identical ``_run_scenario`` body as the smoke test, but every
provider call passes through a logging transport that records termination
metadata the production adapter deliberately discards: ``finish_reason``,
``usage`` token counts, latency, and transport failures.

Nothing here changes production behaviour. The transport observes and
re-emits; the provider returns byte-identical results. Records go to JSONL at
``NARRATIVE_DIAG_OUT`` (default: the OS temp dir, never the repo), one file
per run. Authorization headers and key material are redacted before anything
is written, and a unit test pins that.

Signal map (verified against Ollama's ``openai/openai.go``, not assumed):

- Native ``done_reason`` exists only on ``/api/chat`` and ``/api/generate``.
  It is not observable through the OpenAI-compatible endpoint, so this
  harness does not try. ``finish_reason`` on ``/v1/chat/completions`` is
  mapped verbatim from it and is the reliable equivalent here.
- ``usage`` carries ``prompt_tokens``/``completion_tokens`` mapped from
  ``prompt_eval_count``/``eval_count``.
- ``length`` near the output budget means output-limit exhaustion; ``stop``
  on a mid-object reply means the model stopped early; prompt plus
  completion tokens against ``num_ctx`` settles context pressure; an
  exception with no body near the timeout means transport failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from services.core.models import Generation
from services.narrative.pipeline import NarrativePipeline
from services.providers import is_configured_provider
from services.providers.base import ProviderMessage
from tests import test_local_smoke as smoke

pytestmark = pytest.mark.diagnose

DIAG_OUT_ENV = "NARRATIVE_DIAG_OUT"


class DiagContext:
    """Per-run correlation state. Turn label and attempt counting only."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.turn_label = "stage"
        self.attempt = 0
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def new_turn(self, label: str) -> None:
        self.turn_label = label
        self.attempt = 0

    def record(self, event: dict[str, Any]) -> None:
        event["run_id"] = self.run_id
        self.events.append(event)


def _sanitized_headers(headers: httpx.Headers) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() != "authorization"}


def _prompt_hash(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(
        f"{message.get('role')}:{(message.get('content') or '')}"
        for message in messages
        if isinstance(message, dict)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


class LoggingTransport(httpx.AsyncBaseTransport):
    """Observe one HTTP round trip and re-emit it unchanged.

    Wraps any inner async transport. Records the sanitized request (URL,
    model, per-message sizes, temperature and token limits; never header
    credentials or key material) and the full response body, from which
    ``finish_reason`` and ``usage`` are extracted. The returned response is
    rebuilt from the bytes read, so the caller sees identical content.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, context: DiagContext) -> None:
        self.inner = inner
        self.context = context

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.context.seq += 1
        seq = self.context.seq
        record: dict[str, Any] = {
            "type": "call",
            "seq": seq,
            "turn": self.context.turn_label,
            "attempt": self.context.attempt,
            "method": request.method,
            "url": str(request.url),
            "request_headers": _sanitized_headers(request.headers),
        }
        try:
            body = json.loads(request.content or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        record["model"] = body.get("model")
        messages = body.get("messages")
        if isinstance(messages, list):
            record["message_sizes"] = [
                {"role": message.get("role"), "chars": len(str(message.get("content") or ""))}
                for message in messages
                if isinstance(message, dict)
            ]
            record["prompt_hash"] = _prompt_hash(messages)
        else:
            record["message_sizes"] = []
            record["prompt_hash"] = None
        record["temperature"] = body.get("temperature")
        record["max_tokens"] = body.get("max_tokens")
        started = time.perf_counter()
        try:
            response = await self.inner.handle_async_request(request)
            raw = await response.aread()
        except Exception as exc:
            record["latency_s"] = round(time.perf_counter() - started, 1)
            record["error"] = f"{type(exc).__name__}: {exc}"[:300]
            record["finish_reason"] = None
            record["usage"] = None
            record["raw"] = None
            self.context.record(record)
            raise
        record["latency_s"] = round(time.perf_counter() - started, 1)
        record["status"] = response.status_code
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        finish_reason: Any = None
        usage: Any = None
        if isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
            usage = payload.get("usage")
        record["finish_reason"] = finish_reason
        record["usage"] = usage
        record["raw_chars"] = len(raw)
        record["raw"] = raw.decode("utf-8", errors="replace")
        record["error"] = None
        self.context.record(record)
        return httpx.Response(
            response.status_code, headers=response.headers, content=raw, request=request
        )


class DiagProvider(smoke.RecordingProvider):
    """The scenario's recording provider, counting structured attempts."""

    def __init__(self, *args: Any, context: DiagContext, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.diag_context = context

    async def structured(
        self, messages: list[ProviderMessage], schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.diag_context.attempt += 1
        try:
            return await super().structured(messages, schema, **kwargs)
        finally:
            self.diag_context.attempt = max(0, self.diag_context.attempt)


def _diag_provider(context: DiagContext) -> DiagProvider:
    """Build a provider from the environment, or skip.

    Mirrors ``_live_provider`` in ``test_local_smoke`` (same variables, same
    skip when unconfigured) but routes every call through the logging
    transport. Read from ``os.environ`` so a checked-in ``.env`` cannot steer
    the run.
    """
    provider_name = os.getenv("NARRATIVE_PROVIDER", "heuristic").strip()
    base_url = os.getenv("NARRATIVE_PROVIDER_BASE_URL", "").strip()
    model = os.getenv("NARRATIVE_PROVIDER_MODEL", "").strip()
    if not is_configured_provider(provider_name) or not base_url or not model:
        pytest.skip(
            "Set NARRATIVE_PROVIDER, NARRATIVE_PROVIDER_BASE_URL, and "
            "NARRATIVE_PROVIDER_MODEL to run the diagnostic scenario"
        )
    try:
        timeout = float(os.getenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", "").strip() or 600.0)
    except ValueError:
        timeout = 600.0
    inner = httpx.AsyncHTTPTransport()
    client = httpx.AsyncClient(
        transport=LoggingTransport(inner, context), timeout=timeout, base_url=base_url
    )
    return DiagProvider(
        base_url,
        model,
        os.getenv("NARRATIVE_PROVIDER_API_KEY") or None,
        timeout=timeout,
        client=client,
        context=context,
    )


def _memory_snapshot(base_url: str) -> dict[str, Any]:
    """Best-effort read-only view of what Ollama currently holds in memory.

    ``GET /api/ps`` never starts, stops, or changes a model. Anything else
    (unreachable server, non-Ollama endpoint) records as unavailable rather
    than failing the run: memory context is useful, not required.
    """
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        response = httpx.get(f"{root}/api/ps", timeout=10.0)
        payload = response.json()
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}"}
    models = []
    for entry in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        models.append(
            {
                "name": entry.get("name"),
                "size": entry.get("size"),
                "size_vram": entry.get("size_vram"),
            }
        )
    return {"available": True, "running": models}


def _generation_rows(pipeline: NarrativePipeline) -> list[dict[str, Any]]:
    rows = []
    for generation in pipeline.db.scalars(select(Generation).order_by(Generation.created_at)):
        structured = generation.structured_output or {}
        rows.append(
            {
                "type": "generation",
                "status": generation.status,
                "actor": (
                    structured.get("selected_actor") if isinstance(structured, dict) else None
                ),
                "error": (generation.error or "")[:300] or None,
                "validation": generation.validation or {},
                "structured_keys": (
                    sorted(structured.keys()) if isinstance(structured, dict) else []
                ),
                "model": generation.model_name,
            }
        )
    return rows


def _head() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=r"C:\Users\Sanskar\Documents\RP studios",
    )
    return out.stdout.strip()


def _diag_out() -> str:
    return os.getenv(DIAG_OUT_ENV, os.path.join(tempfile.gettempdir(), "narrative-diag"))


@pytest.mark.asyncio
async def test_collect_local_model_diagnostics(session, monkeypatch):
    """Drive the smoke scenario once and persist sanitized termination records.

    Opt-in and self-skipping like the smoke tests. The assertions cover the
    harness (records were written, required keys present, no secrets leaked);
    the model-dependent run itself is reported, never asserted.
    """
    context = DiagContext("diag-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    provider = _diag_provider(context)
    out_dir = _diag_out()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{context.run_id}.jsonl")
    context.record(
        {
            "type": "run",
            "head": _head(),
            "model": provider.model,
            "base_url": os.getenv("NARRATIVE_PROVIDER_BASE_URL", "").strip(),
            "context_window": os.getenv("NARRATIVE_PROVIDER_CONTEXT_WINDOW", "").strip(),
            "timeout": os.getenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", "").strip(),
            "memory_before": _memory_snapshot(
                os.getenv("NARRATIVE_PROVIDER_BASE_URL", "").strip()
            ),
        }
    )
    outcome: dict[str, Any] = {"ok": True, "failure": None}
    original_run_turn = smoke._run_turn

    async def tracked(pipeline, diag_provider, known, **kwargs):
        context.new_turn(str(kwargs.get("actor_character_id", ""))[:8])
        started = time.perf_counter()
        turn = await original_run_turn(pipeline, diag_provider, known, **kwargs)
        context.record(
            {
                "type": "turn",
                "actor": str(kwargs.get("actor_character_id", ""))[:8],
                "ok": turn["ok"],
                "error": turn["error"],
                "latency_s": round(time.perf_counter() - started, 1),
                "prompt_count": len(turn["prompts"]),
            }
        )
        return turn

    monkeypatch.setattr(smoke, "_run_turn", tracked)
    try:
        await smoke._run_scenario(session, provider)
    except BaseException as exc:
        outcome = {"ok": False, "failure": str(exc)[:300]}
    finally:
        for row in _generation_rows(_pipeline_for_rows(session, provider)):
            context.record(row)
        context.record(
            {
                "type": "run_end",
                "memory_after": _memory_snapshot(
                    os.getenv("NARRATIVE_PROVIDER_BASE_URL", "").strip()
                ),
                **outcome,
            }
        )
        await provider.close()
    with open(path, "w", encoding="utf-8") as handle:
        for event in context.events:
            handle.write(json.dumps(event, default=str) + "\n")
    print(f"\ndiagnostic records: {path} ({len(context.events)} events)")

    assert os.path.isfile(path)
    required = {"run_id", "type"}
    for event in context.events:
        assert required <= set(event), f"record missing keys: {event}"
    blob = open(path, encoding="utf-8").read()
    assert "authorization" not in blob.lower()
    api_key = (os.getenv("NARRATIVE_PROVIDER_API_KEY") or "").strip()
    if api_key:
        assert api_key not in blob


def _mock_client(handler, context: DiagContext) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=LoggingTransport(httpx.MockTransport(handler), context))


def _chat_completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "diag-test",
        "object": "chat.completion",
        "created": 0,
        "model": "mock-local",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154},
    }


@pytest.mark.asyncio
async def test_logging_transport_captures_termination_metadata():
    """finish_reason and usage come from the body the adapter discards."""
    context = DiagContext("unit")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion('{"prose": "ok"}', "length"))

    client = _mock_client(handler, context)
    response = await client.post(
        "http://localhost:11434/v1/chat/completions",
        json={"model": "mock-local", "messages": [{"role": "user", "content": "Go"}]},
    )
    assert response.json()["choices"][0]["message"]["content"] == '{"prose": "ok"}'
    (record,) = [event for event in context.events if event["type"] == "call"]
    assert record["finish_reason"] == "length"
    assert record["usage"] == {
        "prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154,
    }
    assert record["status"] == 200
    assert record["error"] is None
    assert record["prompt_hash"] is not None


@pytest.mark.asyncio
async def test_logging_transport_redacts_credentials():
    """Headers carrying credentials and any key material never reach the record."""
    context = DiagContext("unit")
    secret = "sk-diag-secret-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(200, json=_chat_completion("ok"))

    client = _mock_client(handler, context)
    response = await client.post(
        "http://localhost:11434/v1/chat/completions",
        headers={"Authorization": f"Bearer {secret}"},
        json={"model": "mock-local", "messages": []},
    )
    assert response.status_code == 200
    blob = json.dumps(context.events)
    assert "authorization" not in blob.lower()
    assert secret not in blob


@pytest.mark.asyncio
async def test_logging_transport_records_transport_failure_without_a_body():
    """A connection failure is an exception class and latency, not metadata."""
    context = DiagContext("unit")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _mock_client(handler, context)
    with pytest.raises(httpx.ConnectError):
        await client.post("http://localhost:11434/v1/chat/completions", json={})
    (record,) = [event for event in context.events if event["type"] == "call"]
    assert record["error"].startswith("ConnectError")
    assert record["finish_reason"] is None
    assert record["usage"] is None
    assert record["raw"] is None


@pytest.mark.asyncio
async def test_diagnostic_schema_end_to_end_without_network(session, monkeypatch, tmp_path):
    """The full record schema is provable without a live model."""
    from tests.test_local_smoke_scenario import STAGING, TURN

    context = DiagContext("unit-schema")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        marker = '"location"'
        if marker in body["messages"][0]["content"]:
            payload = {**STAGING, "characters_present": ["Ainsley", "Barrow", "Corvin"]}
        else:
            payload = TURN
        return httpx.Response(200, json=_chat_completion(json.dumps(payload)))

    monkeypatch.setenv(DIAG_OUT_ENV, str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROVIDER", "ollama")
    monkeypatch.setenv("NARRATIVE_PROVIDER_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("NARRATIVE_PROVIDER_MODEL", "mock-local")
    provider = DiagProvider(
        "http://localhost:11434/v1",
        "mock-local",
        None,
        client=_mock_client(handler, context),
        context=context,
    )
    try:
        await smoke._run_scenario(session, provider)
    finally:
        await provider.close()
    for row in _generation_rows(_pipeline_for_rows(session, provider)):
        context.record(row)
    out_file = tmp_path / "unit-schema.jsonl"
    with open(out_file, "w", encoding="utf-8") as handle:
        for event in context.events:
            handle.write(json.dumps(event, default=str) + "\n")

    kinds = {event["type"] for event in context.events}
    assert {"call", "generation"} <= kinds
    calls = [event for event in context.events if event["type"] == "call"]
    assert calls, "scenario made no recorded provider calls"
    for call in calls:
        assert call["finish_reason"] == "stop"
        assert call["usage"]["completion_tokens"] == 34
    for event in context.events:
        assert {"run_id", "type"} <= set(event)


def _pipeline_for_rows(session, provider: DiagProvider) -> NarrativePipeline:
    """A pipeline handle bound to the scenario session, for reading rows only."""
    from services.providers.capabilities import capabilities_from_mapping

    return NarrativePipeline(
        session,
        provider,
        capabilities=capabilities_from_mapping(
            {
                "model": provider.model,
                "adapter": "openai_compatible",
                "context_window": 0,
                "max_output_tokens": 0,
                "features": [],
            },
            model=provider.model,
            adapter="openai_compatible",
        ).to_dict(),
        director_refinement=False,
    )
