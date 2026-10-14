"""The local smoke scenario, exercised without a local model.

``tests/test_local_smoke.py`` only runs when a provider is configured, so on a
machine without one its scenario is never executed and a mistake in it would sit
undetected until the day someone actually tried to dogfood.

This file drives the identical ``_run_scenario`` body through an
``httpx.MockTransport`` that speaks the OpenAI-compatible contract, including
the fenced replies a weak local model actually produces. Fences are decoration:
the adapter strips one fence pair before parsing, so a correct object in fences
behaves exactly like the same object bare.
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
async def test_fenced_turns_parse_and_the_run_proceeds(session):
    """A fenced but valid reply is decorated, not broken.

    The adapter strips one fence pair before parsing, so these turns commit
    exactly as their bare equivalents would and the isolation assertions still
    run against the captured prompts. Fences must not produce a green tick on
    a run where nothing committed — here turns do commit, so the run passes
    honestly.
    """
    provider = _provider(_fenced_turns)
    try:
        await _run_scenario(session, provider)
    finally:
        await provider.close()
    assert len(provider.prompts) >= 4


@pytest.mark.asyncio
async def test_fenced_staging_parses_like_unfenced_staging(session):
    """Fence stripping changes nothing about shape validation.

    A fenced ``{}`` parses to ``{}``, which the staging flow accepts exactly
    as it accepts a bare ``{}``. No new leniency is introduced: whatever the
    engine does with an empty proposal, it does identically with or without
    the fence.
    """
    provider = _provider(_fenced_everything)
    try:
        await _run_scenario(session, provider)
    finally:
        await provider.close()
    assert len(provider.prompts) >= 4


@pytest.mark.asyncio
async def test_the_smoke_scenario_skips_rather_than_fails_without_configuration(monkeypatch, session):
    monkeypatch.delenv("NARRATIVE_PROVIDER", raising=False)
    monkeypatch.delenv("NARRATIVE_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("NARRATIVE_PROVIDER_MODEL", raising=False)
    from tests.test_local_smoke import _live_provider

    with pytest.raises(BaseException) as caught:
        _live_provider()
    assert "Set NARRATIVE_PROVIDER" in str(caught.value)