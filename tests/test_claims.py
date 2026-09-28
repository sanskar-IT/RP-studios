from __future__ import annotations

import pytest

from apps.api.app import repository
from services.context.claims import (
    REJECTED,
    VALID,
    event_tuples,
    extract_claims,
    validate_claims,
)
from services.context.consistency import ContradictionDetector
from services.core.state import StateSnapshot


def test_clean_claims_validate():
    claims = extract_claims(
        {
            "selected_actor": "a",
            "reason": "x",
            "prose": "The witness waits.",
            "actions": [],
            "new_events": [{"event_type": "character_moved", "location_id": "hall"}],
            "state_changes": [],
            "open_commitments": [],
        },
        actor_id="a",
        participant_ids={"a"},
    )
    validation = validate_claims(claims, StateSnapshot())
    assert validation.status == VALID
    assert validation.valid
    assert event_tuples(validation.accepted) == [("character_moved", {"location_id": "hall", "character_id": "a"})]


def test_unknown_event_type_rejects_the_claim():
    claims = extract_claims(
        {
            "selected_actor": "a",
            "reason": "x",
            "prose": "p",
            "actions": [],
            "new_events": [{"event_type": "teleport_to_mars"}],
            "state_changes": [],
            "open_commitments": [],
        },
        actor_id="a",
        participant_ids={"a"},
    )
    validation = validate_claims(claims, StateSnapshot())
    assert validation.status == REJECTED
    assert not validation.valid
    assert not validation.accepted
    assert validation.rejected
    assert validation.recovery_actions


def test_non_participant_reference_rejects_the_claim():
    claims = extract_claims(
        {
            "selected_actor": "a",
            "reason": "x",
            "prose": "p",
            "actions": [],
            "new_events": [{"event_type": "character_moved", "character_id": "ghost", "location_id": "hall"}],
            "state_changes": [],
            "open_commitments": [],
        },
        actor_id="a",
        participant_ids={"a"},
    )
    validation = validate_claims(claims, StateSnapshot())
    assert validation.status == REJECTED
    assert any("non-participant" in error or "not a scene participant" in error for error in validation.errors)


def test_malformed_payload_rejects_the_claim():
    claims = extract_claims(
        {
            "selected_actor": "a",
            "reason": "x",
            "prose": "p",
            "actions": [],
            "new_events": [{"event_type": "character_moved"}],
            "state_changes": [],
            "open_commitments": [],
        },
        actor_id="a",
        participant_ids={"a"},
    )
    validation = validate_claims(claims, StateSnapshot())
    assert validation.status == REJECTED


def test_dead_character_claim_rejects_but_others_survive_where_valid():
    state = StateSnapshot(dead={"a"})
    claims = extract_claims(
        {
            "selected_actor": "b",
            "reason": "x",
            "prose": "p",
            "actions": [],
            "new_events": [
                {"event_type": "character_spoke", "character_id": "a", "text": "I rise from the grave."},
                {"event_type": "character_spoke", "character_id": "b", "text": "Impossible."},
            ],
            "state_changes": [],
            "open_commitments": [],
        },
        actor_id="b",
        participant_ids={"a", "b"},
    )
    detector = ContradictionDetector(state)
    validation = validate_claims(claims, state, detector=detector)
    assert validation.status == REJECTED
    assert validation.errors


def test_prose_and_claims_stay_separate():
    claims = extract_claims(
        {
            "selected_actor": "a",
            "reason": "because",
            "prose": "Long prose here.",
            "actions": [],
            "new_events": [],
            "state_changes": [],
            "open_commitments": [{"description": "x"}],
        },
        actor_id="a",
        participant_ids={"a"},
    )
    assert claims.prose == "Long prose here."
    assert claims.events == []
    assert claims.open_commitments == [{"description": "x"}]
    assert "Long prose here." not in str(claims.to_dict()["events"])


@pytest.mark.asyncio
async def test_invalid_claims_reject_before_commit(session):
    from services.narrative.pipeline import NarrativePipeline
    from services.providers.base import ScriptedProvider

    project = repository.create_project(session, "Claim Gate")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Gate",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "gate",
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
                    "prose": "The witness claims an impossible thing.",
                    "actions": [],
                    "new_events": [{"event_type": "teleport_to_mars"}],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id, premise="A gate waits", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    events_before = len(repository.list_events(session, project.active_timeline_id))
    with pytest.raises(ValueError):
        await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    events_after = repository.list_events(session, project.active_timeline_id)
    assert len(events_after) == events_before
    assert not any(event.event_type == "teleport_to_mars" for event in events_after)
    from sqlalchemy import select

    from services.core.models import Generation

    generation = session.scalar(select(Generation).where(Generation.scene_id == scene.id))
    assert generation.status == "rejected"
    assert generation.validation["status"] == "rejected"
    assert generation.trace["validation"]["status"] == "rejected"
