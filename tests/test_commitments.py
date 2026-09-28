from __future__ import annotations

import pytest

from apps.api.app import repository
from services.core.enums import COMMITMENT_TRANSITIONS, CommitmentStatus
from services.core.models import StoryCommitment
from services.narrative.pipeline import NarrativePipeline


def test_lifecycle_statuses_coerce_legacy_pending():
    assert CommitmentStatus.coerce("pending") == CommitmentStatus.CREATED
    assert CommitmentStatus.coerce("created") == CommitmentStatus.CREATED
    assert CommitmentStatus.coerce("nonsense").is_open is False


def test_terminal_states_have_no_outgoing_transitions():
    assert COMMITMENT_TRANSITIONS[CommitmentStatus.FULFILLED.value] == frozenset()
    assert COMMITMENT_TRANSITIONS[CommitmentStatus.CANCELLED.value] == frozenset()
    assert CommitmentStatus.PROGRESSING.value in COMMITMENT_TRANSITIONS[CommitmentStatus.ACTIVE.value]


@pytest.mark.asyncio
async def test_direct_creates_an_open_commitment_scoped_to_the_timeline(session):
    project = repository.create_project(session, "Commit Cycle")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Court",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    result = await pipeline.direct(
        project_id=project.id, scene_id=scene.id, text="The General eventually betrays the Emperor."
    )
    commitment = session.get(StoryCommitment, result.commitment_id)
    assert commitment.status == CommitmentStatus.CREATED.value
    assert commitment.timeline_id == project.active_timeline_id
    assert commitment.created_sequence >= 0
    open_rows = pipeline.pending_commitments(project.id, project.active_timeline_id)
    assert [row["id"] for row in open_rows] == [commitment.id]


@pytest.mark.asyncio
async def test_commitment_moves_through_its_lifecycle(session):
    project = repository.create_project(session, "Commit Lifecycle")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Court",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    created = await pipeline.direct(
        project_id=project.id, scene_id=scene.id, text="The General eventually betrays the Emperor."
    )
    for status in (
        CommitmentStatus.ACTIVE.value,
        CommitmentStatus.PROGRESSING.value,
        CommitmentStatus.FULFILLED.value,
    ):
        result = pipeline.update_commitment(
            project_id=project.id, commitment_id=created.commitment_id, status=status
        )
        assert session.get(StoryCommitment, created.commitment_id).status == status
        assert result.state is not None
    events = repository.list_events(session, project.active_timeline_id)
    transitions = [event for event in events if event.event_type == "commitment_status_changed"]
    assert len(transitions) == 3
    assert session.get(StoryCommitment, created.commitment_id).status == CommitmentStatus.FULFILLED.value
    assert pipeline.pending_commitments(project.id, project.active_timeline_id) == []


@pytest.mark.asyncio
async def test_illegal_transitions_are_refused(session):
    project = repository.create_project(session, "Commit Illegal")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Court",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    created = await pipeline.direct(project_id=project.id, scene_id=scene.id, text="The General betrays the Emperor.")
    pipeline.update_commitment(
        project_id=project.id, commitment_id=created.commitment_id, status=CommitmentStatus.FULFILLED.value
    )
    with pytest.raises(ValueError):
        pipeline.update_commitment(
            project_id=project.id, commitment_id=created.commitment_id, status=CommitmentStatus.ACTIVE.value
        )


@pytest.mark.asyncio
async def test_cancelled_and_superseded_commitments_leave_the_planner(session):
    project = repository.create_project(session, "Commit End")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Court",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    first = await pipeline.direct(project_id=project.id, scene_id=scene.id, text="First plan.")
    _second = await pipeline.direct(project_id=project.id, scene_id=scene.id, text="Second plan.")
    pipeline.update_commitment(
        project_id=project.id, commitment_id=first.commitment_id, status=CommitmentStatus.CANCELLED.value
    )
    remaining = pipeline.pending_commitments(project.id, project.active_timeline_id)
    assert [row["description"] for row in remaining] == ["Second plan."]
    rows = pipeline.all_commitments(project.id, project.active_timeline_id)
    assert {row["status"] for row in rows} == {"created", "cancelled"}


@pytest.mark.asyncio
async def test_commitments_fork_with_the_timeline(session):
    project = repository.create_project(session, "Commit Fork")
    character = repository.add_character(session, project_id=project.id, name="General")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Court",
        participant_ids=[character.id],
    )
    session.commit()
    pipeline = NarrativePipeline(session)
    created = await pipeline.direct(project_id=project.id, scene_id=scene.id, text="The long plan.")
    node = repository.latest_node(session, project.active_timeline_id)
    branch = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="Alternate",
    )
    session.commit()
    branch_rows = pipeline.all_commitments(project.id, branch.id)
    assert len(branch_rows) == 2
    copies = [row for row in branch_rows if row["timeline_id"] == branch.id]
    assert len(copies) == 1
    assert copies[0]["forked_from_commitment_id"] == created.commitment_id
    # The planner sees the branch copy once, not the ancestor and the copy.
    assert [row["id"] for row in pipeline.pending_commitments(project.id, branch.id)] == [copies[0]["id"]]
    pipeline.update_commitment(
        project_id=project.id, commitment_id=copies[0]["id"], status=CommitmentStatus.FULFILLED.value
    )
    session.expire_all()
    assert session.get(StoryCommitment, created.commitment_id).status == CommitmentStatus.CREATED.value
    assert pipeline.pending_commitments(project.id, project.active_timeline_id)
    assert pipeline.pending_commitments(project.id, branch.id) == []
