from __future__ import annotations

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.context.trace import FORBIDDEN_TRACE_KEYS, GenerationTrace
from services.core.models import Generation
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider


def test_trace_never_carries_credentials():
    trace = GenerationTrace(
        project_id="p",
        provider="openai_compatible",
        model="x",
        capabilities={"api_key": "sk-live", "base_url": "https://x", "context_window": 1},
        context={"api_key": "hunter2", "note": "a story about a password"},
    ).to_dict()
    assert "sk-live" not in str(trace)
    assert "hunter2" not in str(trace)

    def keys(value):
        found = set()
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(str(key).casefold())
                found |= keys(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                found |= keys(item)
        return found

    assert not (set(keys(trace)) & FORBIDDEN_TRACE_KEYS), "credential-shaped keys must be dropped"
    assert trace["capabilities"]["context_window"] == 1
    # Free prose is the caller's responsibility and is never mangled.
    assert trace["context"]["note"] == "a story about a password"


@pytest.mark.asyncio
async def test_completed_turn_records_budget_validation_and_timings(session):
    project = repository.create_project(session, "Trace")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "library",
                    "time": "night",
                    "characters_present": ["Witness"],
                    "objective": "Wait.",
                    "initial_conditions": [],
                    "environmental_assumptions": [],
                    "potential_consequences": [],
                    "canon_conflicts": [],
                },
                {
                    "selected_actor": character.id,
                    "reason": "only participant",
                    "prose": "The witness waits.",
                    "actions": [],
                    "new_events": [],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id, premise="A witness waits", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    result = await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)

    generation = session.get(Generation, result.generation_id)
    assert generation.status == "completed"
    assert generation.context_debug["total_tokens"] > 0
    assert generation.context_debug["max_input_tokens"] > 0
    assert generation.validation["status"] in {"valid", "accepted_with_warnings"}
    stages = {stage["name"] for stage in generation.trace["performance"]["stages"]}
    assert {"llm_request", "claim_validation", "context_assembly", "memory_lifecycle"} <= stages
    assert generation.trace["performance"]["total_ms"] >= 0
    assert generation.trace["retrieval"]["pool_limit"] > 0

    stored = pipeline.generation_trace(project_id=project.id, generation_id=generation.id)
    assert stored["context"]["total_tokens"] == generation.context_debug["total_tokens"]
    assert stored["validation"]["status"] == generation.validation["status"]
    assert stored["provider_name"] == generation.provider_name
    assert "api_key" not in str(stored).casefold()

    listing = pipeline.list_generation_traces(project_id=project.id, timeline_id=project.active_timeline_id)
    assert listing[0]["generation_id"] == generation.id
    assert listing[0]["total_tokens"] == generation.context_debug["total_tokens"]
    assert generation.trace["actor_selection"]["reason"]


@pytest.mark.asyncio
async def test_actor_override_reason_is_recorded(session):
    project = repository.create_project(session, "Trace Actor")
    first = repository.add_character(session, project_id=project.id, name="First")
    second = repository.add_character(session, project_id=project.id, name="Second")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[first.id, second.id],
    )
    session.commit()
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "library",
                    "time": "night",
                    "characters_present": ["First", "Second"],
                    "objective": "Wait.",
                    "initial_conditions": [],
                    "environmental_assumptions": [],
                    "potential_consequences": [],
                    "canon_conflicts": [],
                },
                {
                    "selected_actor": "",
                    "reason": "scripted",
                    "prose": "The scene holds.",
                    "actions": [],
                    "new_events": [],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id,
        premise="Two wait",
        scene_id=scene.id,
        character_ids=[first.id, second.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    result = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=second.id
    )
    generation = session.get(Generation, result.generation_id)
    assert result.structured_output["selected_actor"] == second.id
    assert generation.trace["actor_selection"] == {
        "actor_character_id": second.id,
        "reason": "explicit actor override",
    }


@pytest.mark.asyncio
async def test_failed_turn_still_records_a_trace(session):
    from services.providers.base import ProviderError

    project = repository.create_project(session, "Trace Failure")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "library",
                    "time": "night",
                    "characters_present": ["Witness"],
                    "objective": "Wait.",
                    "initial_conditions": [],
                    "environmental_assumptions": [],
                    "potential_consequences": [],
                    "canon_conflicts": [],
                }
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id, premise="A witness waits", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    pipeline.provider = ScriptedProvider([], error=ProviderError("simulated provider outage"))
    with pytest.raises(ProviderError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "failed"
    assert generation.trace["status"] == "traced"
    assert generation.trace["context"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_memory_inspection_reports_scope_and_active_rows(session):
    project = repository.create_project(session, "Trace Memory")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    first = pipeline.memory_inspection(project_id=project.id, timeline_id=project.active_timeline_id)
    assert first["total"] == 0
    assert first["active"] == 0
    assert first["timeline_lineage"] == [project.active_timeline_id]
    assert first["knowledge"] == {}
    assert first["retrieval"]["pool_limit"] > 0
