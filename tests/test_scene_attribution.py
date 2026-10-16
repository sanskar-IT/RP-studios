from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.app import repository
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"

SCENE_LINE = "The loose stone hides a file naming the captain as the traitor."
CANON_TEXT = "the captain was never on the ship that night"


def _two_scenes(session):
    project = repository.create_project(session, "Attribution")
    one = repository.add_character(session, project_id=project.id, name="One")
    stranger = repository.add_character(session, project_id=project.id, name="Stranger")
    first = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Cellar",
        participant_ids=[one.id],
    )
    second = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Attic",
        participant_ids=[stranger.id],
    )
    session.commit()
    return project, one, stranger, first, second


def test_append_event_requires_explicit_scene_attribution(session):
    """Attribution is a decision, not a default.

    ``scene_id`` has no default, so an omitted argument raises instead of
    quietly producing a legacy row that renders in every scene. This is the
    guard against the accidental-omission leak the review found.
    """
    project = repository.create_project(session, "Explicit")
    character = repository.add_character(session, project_id=project.id, name="One")
    with pytest.raises(TypeError):
        repository.append_event(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            event_type="character_moved",
            payload={"character_id": character.id, "location_id": "library"},
        )


def test_operator_events_are_visible_in_every_scene(session):
    """Timeline/operator events carry no scene and reach every scene by design.

    A canon divergence changes what is true for the whole timeline, so unlike
    scene-bound speech it must appear in any scene's prompt. This pins the
    operator/scene distinction so a future change to scene scoping neither hides
    canon nor promotes scene-local content.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, one, stranger, first, second = _two_scenes(session)
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    pipeline.create_canon_divergence(
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        text=CANON_TEXT,
    )
    repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type="character_spoke",
        payload={"character_id": one.id, "text": SCENE_LINE},
        source="user",
        scene_id=first.id,
    )
    session.commit()

    rendered = "\n".join(
        pipeline._recent_events(project.active_timeline_id, second.id, actor_id=stranger.id)
    )
    assert CANON_TEXT in rendered, "operator/timeline canon must reach every scene"
    assert SCENE_LINE not in rendered, "scene-bound speech must not cross into another scene"


def test_history_digest_is_scoped_to_the_active_scene(session):
    """The digest obeys the same boundary as the recent-events block.

    A scene sees only its own events (plus legacy rows with no scene), so one
    scene's event composition cannot describe itself into another scene's prompt.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, one, _stranger, first, second = _two_scenes(session)
    for index in range(30):
        repository.append_event(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            event_type="character_moved",
            payload={"character_id": one.id, "location_id": f"room-{index}"},
            scene_id=first.id,
        )
    session.commit()

    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    first_digest = pipeline._history_digest(project.active_timeline_id, first.id)
    second_digest = pipeline._history_digest(project.active_timeline_id, second.id)
    assert first_digest, "the scene with 30 events must produce a digest"
    assert second_digest == "", "a scene must not count another scene's events"


@pytest.mark.asyncio
async def test_scene_bound_turn_attributes_every_event_to_the_scene(session):
    """Every event a completed scene turn writes names that scene.

    The inheritance guard from ``append_event`` proves omission is loud; this
    pins that the pipeline actually stamps the scene at each commit site for a
    real turn, so no turn event silently becomes legacy.
    """
    from services.narrative.pipeline import NarrativePipeline

    project = repository.create_project(session, "Stamped turn")
    one = repository.add_character(session, project_id=project.id, name="One")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Hall",
        participant_ids=[one.id],
    )
    session.commit()

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    await NarrativePipeline(session, ScriptedProvider([staging])).stage(
        project_id=project.id,
        premise="One waits in the hall",
        scene_id=scene.id,
        character_ids=[one.id],
    )
    NarrativePipeline(session, ScriptedProvider([])).approve_scene(
        project_id=project.id, scene_id=scene.id
    )
    await NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "selected_actor": one.id,
                    "reason": "scripted",
                    "prose": "One crosses the hall.",
                    "actions": [{"character_id": one.id, "action": "crosses the hall", "kind": "decision"}],
                    "new_events": [],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        ),
    ).continue_scene(project_id=project.id, scene_id=scene.id, actor_character_id=one.id)

    events = repository.list_events(session, project.active_timeline_id)
    assert events
    assert all(event.scene_id == scene.id for event in events), (
        "a scene-bound turn left an event without scene attribution"
    )
