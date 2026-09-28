from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.models import Generation
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import HeuristicProvider, ProviderError, ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"


def provider(name: str) -> ScriptedProvider:
    return ScriptedProvider([json.loads((FIXTURES / name).read_text(encoding="utf-8"))])


def active_scene(session):
    project = repository.create_project(session, "Failure Paths")
    character = repository.add_character(session, project_id=project.id, name="Detective")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    return project, character, scene


@pytest.mark.asyncio
async def test_invalid_provider_output_fails_generation_without_committing_events(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, provider("staging.json"))
    staged = await pipeline.stage(
        project_id=project.id,
        premise="A staged scene",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"])
    pipeline.provider = provider("invalid_output.json")
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "failed"
    assert not any(
        event.event_type in {"ai_action", "user_action"}
        for event in repository.list_events(session, project.active_timeline_id)
    )


@pytest.mark.asyncio
async def test_malformed_event_is_rejected_before_persistence(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, provider("staging.json"))
    staged = await pipeline.stage(
        project_id=project.id,
        premise="A staged scene",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"])
    pipeline.provider = provider("malformed_event.json")
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    assert not any(
        event.event_type == "character_moved"
        for event in repository.list_events(session, project.active_timeline_id)
    )


@pytest.mark.asyncio
async def test_provider_failure_does_not_fall_back_to_a_narrative_event(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, HeuristicProvider())
    staged = await pipeline.stage(
        project_id=project.id,
        premise="A staged scene",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"])
    pipeline.provider = ScriptedProvider([], error=ProviderError("simulated provider outage"))
    with pytest.raises(ProviderError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    events = repository.list_events(session, project.active_timeline_id)
    assert [event.event_type for event in events] == ["scene_staged", "scene_started"]
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "failed"
    assert generation.error == "Provider generation failed"


@pytest.mark.asyncio
async def test_failed_regenerate_does_not_commit_a_fork(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, HeuristicProvider())
    staged = await pipeline.stage(
        project_id=project.id,
        premise="A staged scene",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"])
    original_timeline_id = project.active_timeline_id
    pipeline.provider = ScriptedProvider([], error=ProviderError("simulated outage"))
    with pytest.raises(ProviderError):
        await pipeline.regenerate_scene(project_id=project.id, scene_id=scene.id, user_input="Try another outcome.")
    session.expire_all()
    assert repository.get_project(session, project.id).active_timeline_id == original_timeline_id
    assert len(repository.list_timelines(session, project.id)) == 1
