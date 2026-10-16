"""Opt-in dogfooding scenario against a live local model.

Run with ``pytest -m smoke`` once ``NARRATIVE_PROVIDER``,
``NARRATIVE_PROVIDER_BASE_URL``, and ``NARRATIVE_PROVIDER_MODEL`` are set. See
``docs/PROVIDERS.md`` for the Ollama block. Nothing here needs a paid account,
and nothing here needs PostgreSQL.

What this asserts and what it does not
--------------------------------------

The assertions cover the engine. Seeding is deterministic, possession is
deterministic, and the prompt assembler is deterministic, so those are claimed
rather than observed.

Everything that depends on the model's behaviour is reported and never
asserted. Whether the prose is good, whether the model kept a character's
private knowledge to itself, and how many turns it got through are all printed
as findings. A run in which the model failed every call is a run that told us
almost nothing, so that case fails loudly instead of passing quietly.

Private knowledge is seeded through the event log, never asked of the model. A
model asked to invent three secrets and then tested on whether it kept them
apart measures its own setup as much as its discretion.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.context.knowledge import build_knowledge_view, knowledge_leaks, knowledge_table
from services.core.enums import ControlMode, EventSource
from services.core.events import KNOWLEDGE_ACQUIRED
from services.core.models import Character, Generation, SceneParticipant
from services.narrative.pipeline import NarrativePipeline
from services.providers import is_configured_provider
from services.providers.base import ProviderMessage
from services.providers.capabilities import capabilities_from_mapping
from services.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.smoke

PREMIERE = "the gardener was dismissed last winter"
LEDGER = "the silver key is missing from the library ledger"
SERVICE_DOOR = "the eastern service door stays unbolted"

CAST = ("Ainsley", "Barrow", "Corvin")

# The possessed character is Corvin. Possession decides who is allowed to act
# and whose claims are recorded as the user's; it grants no knowledge, which is
# why Corvin's own secret is seeded exactly like the other two.
POSSESSED_NAME = "Corvin"


class RecordingProvider(OpenAICompatibleProvider):
    """The real adapter, keeping the prompt it was handed.

    The prompt is the only place prompt-assembly guarantees are visible, so a
    scenario that cannot show it cannot check it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.prompts: list[str] = []

    async def structured(
        self, messages: list[ProviderMessage], schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1].content)
        return await super().structured(messages, schema, **kwargs)


def _int_env(name: str) -> int:
    try:
        return int(os.getenv(name, "").strip() or 0)
    except ValueError:
        return 0


def _float_env(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or fallback)
    except ValueError:
        return fallback


def _live_provider() -> RecordingProvider:
    """Build a provider from the environment, or skip.

    Read from ``os.environ`` rather than through ``get_settings`` so a checked-in
    ``.env`` cannot steer the test and no cached settings object leaks between
    runs. A provider name outside the known set is treated as unconfigured,
    matching what the API does with it.
    """
    provider_name = os.getenv("NARRATIVE_PROVIDER", "heuristic").strip()
    base_url = os.getenv("NARRATIVE_PROVIDER_BASE_URL", "").strip()
    model = os.getenv("NARRATIVE_PROVIDER_MODEL", "").strip()
    if not is_configured_provider(provider_name) or not base_url or not model:
        pytest.skip(
            "Set NARRATIVE_PROVIDER, NARRATIVE_PROVIDER_BASE_URL, and "
            "NARRATIVE_PROVIDER_MODEL to run the local smoke scenario"
        )
    return RecordingProvider(
        base_url,
        model,
        os.getenv("NARRATIVE_PROVIDER_API_KEY") or None,
        timeout=_float_env("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", 600.0),
    )


def _live_capabilities(model: str) -> dict[str, Any]:
    features = [
        value.strip().casefold()
        for value in os.getenv("NARRATIVE_PROVIDER_FEATURES", "").split(",")
        if value.strip()
    ]
    return capabilities_from_mapping(
        {
            "model": model,
            "adapter": "openai_compatible",
            "context_window": _int_env("NARRATIVE_PROVIDER_CONTEXT_WINDOW"),
            "max_output_tokens": _int_env("NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS"),
            "features": features,
        },
        model=model,
        adapter="openai_compatible",
    ).to_dict()


def _seed_private_knowledge(session, project, characters: list[Character], facts: dict[str, str]) -> None:
    """Give each character one fact, through the event log.

    Seeded rather than generated so the isolation question is about the engine
    and the model separately. Each fact's long words are unshared with the other
    two, because the leak detector keys on a fact's full distinctive payload.
    """
    for character in characters:
        repository.append_event(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            event_type=KNOWLEDGE_ACQUIRED,
            payload={"character_id": character.id, "fact": facts[character.name]},
            source=EventSource.USER,
            scene_id=None,
        )
    session.commit()


def _generation_ids(pipeline: NarrativePipeline) -> set[str]:
    return set(pipeline.db.scalars(select(Generation.id)))


def _new_generation(pipeline: NarrativePipeline, known: set[str]) -> Generation | None:
    """The generation this turn added, if it added one.

    Selected by set difference rather than by timestamp: two generations
    committed close together can share ``created_at``, and picking the wrong one
    would report another turn's error as this one's.
    """
    fresh = [
        generation_id
        for generation_id in pipeline.db.scalars(select(Generation.id))
        if generation_id not in known
    ]
    return pipeline.db.get(Generation, fresh[0]) if fresh else None


async def _run_turn(
    pipeline: NarrativePipeline, provider: RecordingProvider, known: set[str], **kwargs: Any
) -> dict[str, Any]:
    """One turn, recording whether it committed and what the prompt held.

    A turn that raises still leaves a ``Generation`` row behind, which is the
    point: the engine records the failure rather than dropping it, so the smoke
    run can tell the operator which failure they are looking at.
    """
    before = len(provider.prompts)
    try:
        result = await pipeline.continue_scene(**kwargs)
    except Exception as exc:
        generation = _new_generation(pipeline, known)
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "recorded_status": getattr(generation, "status", None),
            "recorded_error": getattr(generation, "error", None),
            "prompts": provider.prompts[before:],
            "prose": "",
        }
    generation = pipeline.db.get(Generation, result.generation_id) if result.generation_id else None
    return {
        "ok": True,
        "error": "",
        "recorded_status": getattr(generation, "status", None),
        "recorded_error": None,
        "prompts": provider.prompts[before:],
        "prose": getattr(generation, "output_text", "") or "",
    }


def _report(lines: list[str]) -> None:
    print("\n" + "\n".join(lines))


async def _run_scenario(session, provider: RecordingProvider) -> None:
    """The scenario body, independent of where the provider came from.

    Split out so ``tests/test_local_smoke_scenario.py`` can drive the same
    sequence through a mock transport in the deterministic suite. A smoke test
    that is only ever exercised by hand is a smoke test that rots silently.
    """
    model = provider.model
    report: list[str] = [f"local smoke scenario against {model}"]

    project = repository.create_project(session, "Local Smoke")
    characters = [
        repository.add_character(session, project_id=project.id, name=name) for name in CAST
    ]
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Drawing room",
        participant_ids=[character.id for character in characters],
    )
    session.commit()

    facts = {
        "Ainsley": PREMIERE,
        "Barrow": LEDGER,
        "Corvin": SERVICE_DOOR,
    }
    assert set(facts) == set(CAST)
    _seed_private_knowledge(session, project, characters, facts)

    state = repository.current_state(session, project.active_timeline_id)
    table = knowledge_table(state)
    for character in characters:
        assert table[character.id]["certain"] == [facts[character.name]]
    report.append(f"seeded three private facts, one per character: {facts}")

    pipeline = NarrativePipeline(
        session,
        provider,
        capabilities=_live_capabilities(model),
        director_refinement=False,
    )

    possessed = next(character for character in characters if character.name == POSSESSED_NAME)
    pipeline.possess(project_id=project.id, scene_id=scene.id, character_id=possessed.id)
    participant = session.scalar(
        select(SceneParticipant).where(
            SceneParticipant.scene_id == scene.id,
            SceneParticipant.character_id == possessed.id,
        )
    )
    assert participant is not None and participant.control_mode == ControlMode.USER.value
    assert any(
        event.event_type == "possession_changed" and event.actor_character_id == possessed.id
        for event in repository.list_events(session, project.active_timeline_id)
    )
    report.append(f"possessed {possessed.name}; control_mode is user")

    try:
        await pipeline.stage(
            project_id=project.id,
            premise="Three people wait out a storm in a drawing room and avoid the subject.",
            scene_id=scene.id,
            character_ids=[character.id for character in characters],
        )
    except Exception as exc:
        _report(report + [f"stage: FAILED, {type(exc).__name__}: {exc}"[:300]])
        pytest.fail(
            "staging failed, so no turn could be attempted and this run verified nothing. "
            "The staging call is a structured provider call, so this is the first place a "
            "model that cannot hold the JSON contract shows up. See the troubleshooting "
            "section in README.md: check the base URL for the /v1 suffix and the model id "
            "against `ollama list` before concluding the model is too weak."
        )
    report.append("stage: ok")
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    turns: dict[str, dict[str, Any]] = {}
    for character in characters:
        known = _generation_ids(pipeline)
        turns[character.name] = await _run_turn(
            pipeline,
            provider,
            known,
            project_id=project.id,
            scene_id=scene.id,
            actor_character_id=character.id,
            user_input=f"{character.name} watches the others and says nothing about the storm.",
        )
        outcome = "ok" if turns[character.name]["ok"] else f"FAILED, {turns[character.name]['error']}"
        report.append(f"turn {character.name}: {outcome}")

    leaked_in_prose: list[str] = []
    withheld_from_prompt: list[str] = []
    never_told_own_fact: list[str] = []
    for character in characters:
        view = build_knowledge_view(
            repository.current_state(session, project.active_timeline_id),
            viewer_character_id=character.id,
        )
        outcome = turns[character.name]
        for prompt in outcome["prompts"]:
            if facts[character.name] not in prompt:
                never_told_own_fact.append(character.name)
            for other in characters:
                if other.id == character.id:
                    continue
                if facts[other.name] in prompt:
                    withheld_from_prompt.append(f"{character.name} saw {facts[other.name]!r}")
        for leak in knowledge_leaks(view, [outcome["prose"]]):
            leaked_in_prose.append(f"{character.name} leaked {leak.fact!r}")

    completed = [name for name, outcome in turns.items() if outcome["ok"]]
    report.append(f"turns completed: {len(completed)} of {len(CAST)}")
    report.append(
        "prompts inspected for isolation: "
        + (", ".join(name for name, outcome in turns.items() if outcome["prompts"]) or "none")
    )
    report.append(f"private facts absent from other characters' prompts: {len(withheld_from_prompt) == 0}")
    report.append(f"private facts echoed in prose (reported, not asserted): {leaked_in_prose or 'none'}")

    _report(report)

    # Without this, the isolation assertion below would also pass on a build
    # that told every character nothing at all, which is a different failure
    # wearing the same green tick.
    assert not never_told_own_fact, (
        "these characters never received their own private fact in any prompt, so the "
        "isolation check below proves nothing: "
        + ", ".join(sorted(set(never_told_own_fact)))
    )

    assert withheld_from_prompt == [], (
        "the prompt assembler sent a character another character's private fact: "
        + "; ".join(withheld_from_prompt)
    )

    if not completed:
        pytest.fail(
            "no turn completed against the configured provider, so this run verified nothing. "
            f"First turn error: {turns[CAST[0]]['error'] or 'none recorded'}. "
            "See the troubleshooting section in README.md before changing configuration."
        )


@pytest.mark.asyncio
async def test_local_model_keeps_three_secrets_apart_with_one_character_possessed(session):
    provider = _live_provider()
    try:
        await _run_scenario(session, provider)
    finally:
        await provider.close()