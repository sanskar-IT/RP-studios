from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.enums import SceneStatus
from services.core.models import SceneParticipant
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"


def provider(*names: str) -> ScriptedProvider:
    return ScriptedProvider([json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in names])


@pytest.mark.asyncio
async def test_staging_can_be_edited_approved_and_cancelled(session):
    project = repository.create_project(session, "Staging Lifecycle")
    first = repository.add_character(session, project_id=project.id, name="Detective")
    second = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Draft",
        participant_ids=[first.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session, provider("staging.json"))
    staged = await pipeline.stage(
        project_id=project.id,
        premise="A suspicious murder in a library",
        scene_id=scene.id,
        character_ids=[first.id],
    )
    assert staged.structured_output["scene_id"] == scene.id
    assert scene.staging["scene_id"] == scene.id
    edited = pipeline.edit_staging(
        project_id=project.id,
        scene_id=scene.id,
        expected_revision=1,
        updates={
            "location": "archive",
            "character_ids": [first.id, second.id],
            "unresolved_assumptions": ["The archive key is still hidden."],
        },
    )
    assert edited.structured_output["staging_revision"] == 2
    assert edited.structured_output["location"] == "archive"
    participants = session.scalars(select(SceneParticipant).where(SceneParticipant.scene_id == scene.id))
    assert {participant.character_id for participant in participants} == {first.id, second.id}
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=2)
    assert scene.status == SceneStatus.ACTIVE.value

    cancellable = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Cancelled",
        participant_ids=[first.id],
    )
    session.commit()
    pipeline.cancel_scene(project_id=project.id, scene_id=cancellable.id)
    assert cancellable.status == SceneStatus.CANCELLED.value
    assert any(event.event_type == "scene_cancelled" for event in repository.list_events(session, project.active_timeline_id))


@pytest.mark.asyncio
async def test_staging_regeneration_does_not_execute_the_scene(session):
    project = repository.create_project(session, "Staging Regeneration")
    character = repository.add_character(session, project_id=project.id, name="Detective")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Draft",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session, provider("staging.json", "staging.json"))
    await pipeline.stage(
        project_id=project.id,
        premise="A first staging",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    regenerated = await pipeline.regenerate_staging(
        project_id=project.id,
        scene_id=scene.id,
        premise="A regenerated staging",
    )
    assert regenerated.structured_output["staging_revision"] == 2
    assert scene.status == SceneStatus.STAGED.value
    assert not any(event.event_type in {"scene_started", "ai_action"} for event in repository.list_events(session, project.active_timeline_id))
