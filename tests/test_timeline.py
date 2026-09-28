from __future__ import annotations

from sqlalchemy import select

from apps.api.app import repository
from services.core.events import KNOWLEDGE_ACQUIRED
from services.core.models import CharacterState, Event


def test_fork_preserves_original_and_uses_checkpoint(session):
    project = repository.create_project(session, "Branching")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    event, node, state = repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type="character_moved",
        payload={"character_id": character.id, "location_id": "library"},
    )
    session.commit()
    original_events = list(session.scalars(select(Event).where(Event.timeline_id == project.active_timeline_id)))
    branch = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="Alternate",
    )
    session.commit()
    original_after = list(session.scalars(select(Event).where(Event.timeline_id == project.active_timeline_id)))
    branch_events = list(session.scalars(select(Event).where(Event.timeline_id == branch.id)))
    assert [item.id for item in original_events] == [item.id for item in original_after]
    assert any(item.event_type == "timeline_forked" for item in branch_events)
    assert branch_events[0].payload["source_node_id"] == node.id
    assert repository.current_state(session, branch.id).characters[character.id]["location_id"] == "library"


def test_character_state_projection_is_written(session):
    project = repository.create_project(session, "State")
    character = repository.add_character(session, project_id=project.id, name="General")
    repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": character.id, "fact": "the decree is forged"},
    )
    session.commit()
    row = session.scalar(select(CharacterState).where(CharacterState.character_id == character.id))
    assert row is not None
    assert row.state["character_id"] == character.id
