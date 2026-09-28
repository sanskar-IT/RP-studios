from __future__ import annotations

import pytest

from apps.api.app import repository
from services.core.models import Generation
from services.director.intent import Lane
from services.director.router import classify_lane, route
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider


def test_possessed_input_routes_direct_actor():
    assert (
        classify_lane("I open the door.", possessed_character_id="c1")
        == Lane.DIRECT_ACTOR
    )


def test_simple_world_actions():
    assert classify_lane("Three hours pass.") == Lane.SIMPLE_WORLD
    assert classify_lane("It starts raining.") == Lane.SIMPLE_WORLD
    assert classify_lane("The lights go out.") == Lane.SIMPLE_WORLD
    assert classify_lane("Anything.", mode="world") == Lane.SIMPLE_WORLD


def test_narrative_direction():
    assert classify_lane("Make the detective increasingly suspicious.") == Lane.DIRECTION
    assert classify_lane("Have the villain manipulate the group.") == Lane.DIRECTION
    assert classify_lane("Escalate this confrontation.") == Lane.DIRECTION
    assert classify_lane("I want to open the door.") == Lane.DIRECTION
    assert classify_lane("Betray him right now.") == Lane.DIRECTION


def test_long_horizon_intent():
    assert classify_lane("Eventually the General betrays the Emperor.") == Lane.LONG_HORIZON
    assert classify_lane("The General will betray the Emperor.") == Lane.LONG_HORIZON
    assert classify_lane("Slowly deteriorate their relationship.") == Lane.LONG_HORIZON


def test_explicit_horizon_wins_over_content():
    assert classify_lane("Open the door.", horizon="long-term") == Lane.LONG_HORIZON
    assert classify_lane("Open the door.", horizon="short") == Lane.DIRECTION


def test_empty_input_defaults_to_direction():
    assert classify_lane("") == Lane.DIRECTION
    assert classify_lane("   ") == Lane.DIRECTION


def test_possession_beats_horizon():
    assert (
        classify_lane("I betray him.", possessed_character_id="c1", horizon="long-term")
        == Lane.DIRECT_ACTOR
    )


def test_route_returns_interpreted_intent_with_lane():
    intent = route(
        "Kill the guard, but do not alert anyone.",
        participant_names=["Guard", "Detective"],
    )
    assert intent.lane == Lane.DIRECTION
    assert intent.constraints == ["do not alert anyone"]
    assert intent.objective == "Kill the guard"


def test_route_never_needs_a_provider():
    # route() takes no provider argument; a ScriptedProvider with zero
    # responses would raise on any call, so the absence of a parameter is the
    # guarantee. This test pins the signature.
    import inspect

    assert "provider" not in inspect.signature(route).parameters
    assert "provider" not in inspect.signature(classify_lane).parameters


@pytest.mark.asyncio
async def test_continue_scene_annotates_lane_in_trace(session):
    project = repository.create_project(session, "Router Trace")
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
    result = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="The witness waits."
    )
    generation = session.get(Generation, result.generation_id)
    assert generation.trace["director"]["lane"] == Lane.DIRECTION.value
    assert generation.trace["director"]["specification"]
    assert generation.trace["director"]["consistency"] == "consistent"
    assert result.output_text == "The witness waits."
