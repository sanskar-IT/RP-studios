"""The local smoke scenario, exercised without a local model.

``tests/test_local_smoke.py`` only runs when a provider is configured, so on a
machine without one its scenario is never executed and a mistake in it would sit
undetected until the day someone actually tried to dogfood.

This file drives the identical ``_run_scenario`` body through an
``httpx.MockTransport`` that speaks the OpenAI-compatible contract, including the
two shapes a weak local model actually produces: a markdown-fenced reply, and a
reply that never parses at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.test_local_smoke import RecordingProvider, _run_scenario

FIXTURES = Path(__file__).parent / "fixtures" / "provider"
STAGING = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
TURN = json.loads((FIXTURES / "normal_turn.json").read_text(encoding="utf-8"))

STAGING_MARKER = '"location"'


def _reply(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def _is_staging(body: dict) -> bool:
    return STAGING_MARKER in body["messages"][0]["content"]


def _well_formed(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if _is_staging(body):
        return _reply({**STAGING, "characters_present": ["Ainsley", "Barrow", "Corvin"]})
    return _reply(TURN)


def _fenced_turns(request: httpx.Request) -> httpx.Response:
    """Stages cleanly, then wraps every turn in a markdown fence."""
    body = json.loads(request.content)
    if _is_staging(body):
        return _reply({**STAGING, "characters_present": ["Ainsley", "Barrow", "Corvin"]})
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": '```json\n{"prose": "It is quiet."}\n```'}}]},
    )


def _fenced_everything(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "```json\n{}\n```"}}]}
    )


def _provider(handler) -> RecordingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RecordingProvider("http://localhost:11434/v1", "mock-local:7b", None, client=client)


@pytest.mark.asyncio
async def test_scenario_runs_every_section_against_a_conforming_endpoint(session):
    provider = _provider(_well_formed)
    try:
        await _run_scenario(session, provider)
    finally:
        await provider.close()
    assert len(provider.prompts) >= 4


@pytest.mark.asyncio
async def test_fenced_turns_fail_the_run_instead_of_passing_quietly(session):
    """A model that cannot hold the contract must not produce a green tick.

    The scenario still inspects the prompts it captured before the parse failed,
    so the isolation guarantee is genuinely checked here; what it must not do is
    report success on a run where nothing committed.
    """
    provider = _provider(_fenced_turns)
    try:
        with pytest.raises(BaseException) as caught:
            await _run_scenario(session, provider)
    finally:
        await provider.close()
    assert "no turn completed" in str(caught.value)


@pytest.mark.asyncio
async def test_fenced_staging_fails_before_any_turn_is_attempted(session):
    provider = _provider(_fenced_everything)
    try:
        with pytest.raises(BaseException) as caught:
            await _run_scenario(session, provider)
    finally:
        await provider.close()
    assert "staging failed" in str(caught.value)


@pytest.mark.asyncio
async def test_the_smoke_scenario_skips_rather_than_fails_without_configuration(monkeypatch, session):
    monkeypatch.delenv("NARRATIVE_PROVIDER", raising=False)
    monkeypatch.delenv("NARRATIVE_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("NARRATIVE_PROVIDER_MODEL", raising=False)
    from tests.test_local_smoke import _live_provider

    with pytest.raises(BaseException) as caught:
        _live_provider()
    assert "Set NARRATIVE_PROVIDER" in str(caught.value)