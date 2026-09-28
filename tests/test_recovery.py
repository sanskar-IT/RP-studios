from __future__ import annotations

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.models import Generation
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import HeuristicProvider, ProviderError, ScriptedProvider


def active_scene(session):
    project = repository.create_project(session, "Recovery")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    return project, character, scene


def staged_pipeline(session, provider):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, provider)
    return pipeline, project, character, scene


@pytest.mark.asyncio
async def test_unknown_character_reference_rejects_without_committing(session):
    from tests.test_failure_paths import provider as failure_provider

    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, failure_provider("staging.json"))
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    pipeline.provider = ScriptedProvider(
        [
            {
                "selected_actor": character.id,
                "reason": "scripted",
                "prose": "A stranger appears.",
                "actions": [],
                "new_events": [
                    {"event_type": "character_moved", "character_id": "ghost", "location_id": "hall"}
                ],
                "state_changes": [],
                "open_commitments": [],
            }
        ]
    )
    events_before = [event.id for event in repository.list_events(session, project.active_timeline_id)]
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    session.expire_all()
    assert [event.id for event in repository.list_events(session, project.active_timeline_id)] == events_before
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "rejected"


@pytest.mark.asyncio
async def test_malformed_structured_envelope_fails_the_generation(session):
    from tests.test_failure_paths import provider as failure_provider

    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, failure_provider("staging.json"))
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    pipeline.provider = ScriptedProvider([{"prose": [], "selected_actor": 5, "reason": 1}])
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "failed"
    assert generation.trace["status"] == "traced"


@pytest.mark.asyncio
async def test_rejected_generation_can_be_retried_regenerated_or_rejected(session):
    project, character, scene = active_scene(session)
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
                    "reason": "scripted",
                    "prose": "An impossible claim.",
                    "actions": [],
                    "new_events": [{"event_type": "teleport_to_mars"}],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    with pytest.raises(ValueError, match="rejected before commit"):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    rejected = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert rejected.status == "rejected"
    assert rejected.validation["recovery_actions"]

    # Retry with a valid provider succeeds on the same scene.
    pipeline.provider = ScriptedProvider(
        [
            {
                "selected_actor": character.id,
                "reason": "retry",
                "prose": "The witness waits quietly.",
                "actions": [],
                "new_events": [],
                "state_changes": [],
                "open_commitments": [],
            }
        ]
    )
    retried = await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    assert retried.structured_output["selected_actor"] == character.id

    # The operator can also disown a committed generation after the fact.
    rejection = pipeline.reject_generation(
        project_id=project.id, generation_id=retried.generation_id, reason="wrong tone"
    )
    assert rejection.state is not None
    assert session.get(Generation, retried.generation_id).status == "rejected"
    assert any(
        event.event_type == "generation_rejected"
        for event in repository.list_events(session, project.active_timeline_id)
    )


@pytest.mark.asyncio
async def test_context_overflow_is_reported_not_truncated(session):
    project, character, scene = active_scene(session)
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
        capabilities={"context_window": 600, "max_output_tokens": 100, "features": []},
    )
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene " * 200, scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    pipeline.provider = HeuristicProvider()
    with pytest.raises(ValueError, match="(?i)budget"):
        await pipeline.continue_scene(
            project_id=project.id, scene_id=scene.id, user_input="Describe the library. " * 2000
        )
    session.expire_all()
    assert not any(
        event.event_type in {"ai_action", "user_action"}
        for event in repository.list_events(session, project.active_timeline_id)
    )


@pytest.mark.asyncio
async def test_unknown_location_rejects_without_committing(session):
    from services.core.models import Location

    project, character, scene = active_scene(session)
    session.add(Location(project_id=project.id, name="Library"))
    session.commit()
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "Library",
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
                    "reason": "scripted",
                    "prose": "The witness leaves for somewhere uncharted.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "character_moved",
                            "character_id": character.id,
                            "location_id": "Atlantis",
                        }
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    events_before = [event.id for event in repository.list_events(session, project.active_timeline_id)]
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    session.expire_all()
    assert [event.id for event in repository.list_events(session, project.active_timeline_id)] == events_before
    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "rejected"
    assert generation.validation["status"] == "rejected"


@pytest.mark.asyncio
async def test_missing_actor_override_is_rejected_before_any_work(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, HeuristicProvider())
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    events_before = [event.id for event in repository.list_events(session, project.active_timeline_id)]
    with pytest.raises(ValueError, match="scene participant"):
        await pipeline.continue_scene(
            project_id=project.id, scene_id=scene.id, actor_character_id="ghost"
        )
    session.expire_all()
    assert [event.id for event in repository.list_events(session, project.active_timeline_id)] == events_before
    assert session.scalar(select(Generation).where(Generation.scene_id == scene.id)) is None


@pytest.mark.asyncio
async def test_provider_outage_leaves_no_partial_state(session):
    project, character, scene = active_scene(session)
    pipeline = NarrativePipeline(session, HeuristicProvider())
    staged = await pipeline.stage(
        project_id=project.id, premise="A staged scene", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(
        project_id=project.id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"]
    )
    pipeline.provider = ScriptedProvider([], error=ProviderError("simulated outage"))
    events_before = [event.id for event in repository.list_events(session, project.active_timeline_id)]
    memories_before = len(
        repository.list_memories(session, project_id=project.id, timeline_id=project.active_timeline_id, limit=5000)
    )
    with pytest.raises(ProviderError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    session.expire_all()
    assert [event.id for event in repository.list_events(session, project.active_timeline_id)] == events_before
    assert (
        len(
            repository.list_memories(
                session, project_id=project.id, timeline_id=project.active_timeline_id, limit=5000
            )
        )
        == memories_before
    )
