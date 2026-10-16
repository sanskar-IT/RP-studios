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

    A scene sees only its own events (plus timeline-scoped rows with no scene),
    so one scene's event composition cannot describe itself into another scene's
    prompt.
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


@pytest.mark.asyncio
async def test_retcon_intent_is_visible_in_every_scene(session):
    """A retcon changes the whole timeline, so it crosses scene boundaries.

    Retcon is the one Director intent that is not scene-local: "ignore canon"
    changes what is true everywhere. It is committed timeline-scoped, while a
    line spoken in the issuing scene stays inside that scene. This pins the
    operator-versus-scene split for retcon so a future change to the intent
    commit site cannot quietly make a retcon scene-local.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, one, stranger, first, second = _two_scenes(session)
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    await pipeline.direct(
        project_id=project.id,
        scene_id=first.id,
        text=CANON_TEXT,
        intent_type="retcon",
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
    assert CANON_TEXT in rendered, "a retcon intent must reach every scene"
    assert SCENE_LINE not in rendered, "scene-bound speech must not cross into another scene"


@pytest.mark.asyncio
async def test_direction_intent_stays_scene_bound(session):
    """Direction directs the room, so it does not leak into another scene.

    Only retcon is timeline-scoped. A direction or story commitment issued in
    one scene must not appear in another scene's prompt, so this pins the
    default branch of the retcon special-case.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, one, stranger, first, second = _two_scenes(session)
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    await pipeline.direct(
        project_id=project.id,
        scene_id=first.id,
        text="Build toward the betrayal slowly.",
        intent_type="direction",
    )
    await pipeline.direct(
        project_id=project.id,
        scene_id=first.id,
        text="The hall is cold.",
        intent_type="narration",
    )
    session.commit()

    other = "\n".join(
        pipeline._recent_events(project.active_timeline_id, second.id, actor_id=stranger.id)
    )
    assert "Build toward the betrayal slowly." not in other
    assert "The hall is cold." not in other


def test_possession_events_record_the_actor(session):
    """Take and release are symmetric: both name the character they act on.

    The release path once omitted ``actor_character_id`` while the take path set
    it, so half the possession history was unattributable. Both directions pass
    the same explicit attribution now.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, one, _stranger, first, _second = _two_scenes(session)
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    pipeline.possess(project_id=project.id, scene_id=first.id, character_id=one.id)
    pipeline.release_possession(project_id=project.id, scene_id=first.id, character_id=one.id)

    events = [
        event
        for event in repository.list_events(session, project.active_timeline_id)
        if event.event_type == "possession_changed"
    ]
    assert len(events) == 2
    assert all(event.actor_character_id == one.id for event in events), (
        "both possession events must name the character they act on"
    )


def test_generation_rejection_is_scoped_to_the_generations_scene(session):
    """A rejection comments on a scene-bound beat, so it shares that boundary.

    The rejection must not render in a different scene, and it must not become a
    timeline-scoped broadcast. It inherits the rejected generation's scene.
    """
    from services.narrative.pipeline import NarrativePipeline

    project, _one, _stranger, first, _second = _two_scenes(session)
    generation = repository.add_generation(
        session,
        project_id=project.id,
        scene_id=first.id,
        timeline_id=project.active_timeline_id,
        status="completed",
        input_text="",
        provider_name="ScriptedProvider",
        model_name="scripted",
    )
    session.commit()

    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    pipeline.reject_generation(project_id=project.id, generation_id=generation.id, reason="operator disowned it")

    rejected = [
        event
        for event in repository.list_events(session, project.active_timeline_id)
        if event.event_type == "generation_rejected"
    ]
    assert rejected and rejected[-1].scene_id == first.id

