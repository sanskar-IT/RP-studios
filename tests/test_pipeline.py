from __future__ import annotations

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.enums import CommitmentStatus, ControlMode, IntentStatus
from services.core.models import DirectorIntent, Event, SceneParticipant, Source, StoryCommitment
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import HeuristicProvider


@pytest.mark.asyncio
async def test_high_level_director_intent_becomes_pending_commitment(session):
    project = repository.create_project(session, "Intent")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Council",
        participant_ids=[character.id],
    )
    session.commit()
    result = await NarrativePipeline(session, HeuristicProvider()).direct(
        project_id=project.id,
        scene_id=scene.id,
        text="General betrays Emperor eventually",
    )
    intent = session.get(DirectorIntent, result.intent_id)
    commitment = session.get(StoryCommitment, result.commitment_id)
    events = list(session.scalars(select(Event).where(Event.timeline_id == project.active_timeline_id)))
    assert intent.status == IntentStatus.PENDING.value
    assert commitment.status == CommitmentStatus.CREATED.value
    assert [event.event_type for event in events] == ["director_intent_created"]
    assert events[0].payload["literal_action"] is False
    assert session.scalar(select(StoryCommitment).where(StoryCommitment.project_id == project.id)).description


@pytest.mark.asyncio
async def test_possession_and_generation_update_state_and_memories(session):
    project = repository.create_project(session, "Possession")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Interview",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session, HeuristicProvider())
    await pipeline.stage(project_id=project.id, premise="A witness decides what to reveal at night", scene_id=scene.id, character_ids=[character.id])
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    possession = pipeline.possess(project_id=project.id, scene_id=scene.id, character_id=character.id)
    assert possession.state is not None
    participant = session.scalar(select(SceneParticipant).where(SceneParticipant.scene_id == scene.id))
    assert participant.control_mode == ControlMode.USER.value
    result = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="I remain silent.",
    )
    assert result.output_text
    assert result.state["active_scene_id"] == scene.id
    assert result.state["control_modes"][character.id] == ControlMode.USER.value
    assert len(result.structured_output["committed_event_ids"]) >= 1
    committed = repository.list_events(session, project.active_timeline_id)
    assert any(event.event_type == "user_action" and event.source == "user" for event in committed)
    assert any(event.event_type == "possession_changed" for event in committed)
    pipeline.release_possession(project_id=project.id, scene_id=scene.id, character_id=character.id)
    released = await pipeline.continue_scene(project_id=project.id, scene_id=scene.id, user_input="The witness waits.")
    assert released.structured_output["selected_actor"] == character.id
    assert any(event.event_type == "ai_action" and event.source == "ai" for event in repository.list_events(session, project.active_timeline_id))


@pytest.mark.asyncio
async def test_stage_returns_assumptions_instead_of_executing_premise(session):
    project = repository.create_project(session, "Staging")
    result = await NarrativePipeline(session).stage(
        project_id=project.id,
        premise="I want to assassinate the target in a library",
    )
    proposal = result.structured_output
    assert proposal["status"] == "proposed"
    assert proposal["objective"].startswith("I want to assassinate")
    assert proposal["assumptions"]
    events = repository.list_events(session, project.active_timeline_id)
    assert [event.event_type for event in events] == ["scene_staged"]


@pytest.mark.asyncio
async def test_structured_provider_events_are_validated_and_committed(session):
    class MockProvider:
        async def structured(self, messages, schema):
            return {
                "selected_actor": "",
                "reason": "test",
                "prose": "The witness changes the subject.",
                "actions": [],
                "new_events": [
                    {
                        "event_type": "knowledge_acquired",
                        "fact": "the eastern door is locked",
                    },
                ],
                "state_changes": [],
                "open_commitments": [],
            }

    project = repository.create_project(session, "Structured")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Question",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session, MockProvider())
    await pipeline.stage(project_id=project.id, premise="A witness is questioned", scene_id=scene.id, character_ids=[character.id])
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    result = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="What do you know?"
    )
    events = repository.list_events(session, project.active_timeline_id)
    assert any(event.event_type == "knowledge_acquired" for event in events)
    assert all(event.event_type != "not_allowed" for event in events)
    assert result.state["knowledge"][character.id] == ["the eastern door is locked"]


@pytest.mark.asyncio
async def test_canon_conflict_is_surfaced_and_override_is_recorded(session):
    project = repository.create_project(session, "Canon")
    source = Source(
        project_id=project.id,
        title="Oath",
        content="The General is loyal and must never betray the Emperor.",
    )
    session.add(source)
    session.commit()
    pipeline = NarrativePipeline(session)
    staged = await pipeline.stage(project_id=project.id, premise="The General betrays the Emperor")
    assert staged.structured_output["canon_conflicts"]
    result = pipeline.create_canon_divergence(
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        text="The General chooses betrayal.",
        canon_reference="Oath",
    )
    assert result.state is not None
    assert any(event.event_type == "canon_divergence" for event in repository.list_events(session, project.active_timeline_id))


@pytest.mark.asyncio
async def test_regenerate_creates_a_new_timeline_branch(session):
    project = repository.create_project(session, "Regenerate")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    await pipeline.stage(project_id=project.id, premise="A witness waits in a library", scene_id=scene.id, character_ids=[character.id])
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    original = await pipeline.continue_scene(project_id=project.id, scene_id=scene.id, user_input="I wait.")
    regenerated = await pipeline.regenerate_scene(project_id=project.id, scene_id=scene.id, user_input="I speak.")
    assert regenerated.timeline_id != original.timeline_id
    assert regenerated.scene_id != scene.id
    assert repository.get_project(session, project.id).active_timeline_id == regenerated.timeline_id
    assert any(event.event_type == "timeline_forked" for event in repository.list_events(session, regenerated.timeline_id))
