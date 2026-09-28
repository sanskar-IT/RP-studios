from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.app import repository
from services.core.enums import MemoryClass, MemoryScope
from services.memory.retrieval import MemoryCandidate, retrieve_with_report


def candidate(index: int, **overrides) -> MemoryCandidate:
    values = {
        "id": f"m{index}",
        "content": f"memory content {index} about the library",
        "memory_class": MemoryClass.PERSISTENT,
        "importance": 0.5,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "metadata": {"timeline_id": "t-main", "visibility": "world"},
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def test_inherited_copy_collapses_to_one_entry():
    original = candidate(1, id="original", valid_from_sequence=3, metadata={"timeline_id": "t-main"})
    copy = candidate(
        2,
        id="copy",
        valid_from_sequence=9,
        metadata={"timeline_id": "t-branch"},
        inherited_from_id="original",
    )
    selected, report = retrieve_with_report([copy, original], "library")
    assert [memory.id for memory in selected] == ["original"]
    assert report.dropped_deduplicated == 1


def test_sibling_branch_memory_is_out_of_scope():
    main_fact = candidate(1, content="the harbour gate stands open", valid_from_sequence=12)
    branch_fact = candidate(
        2,
        content="the harbour gate was never opened here",
        valid_from_sequence=40,
        metadata={"timeline_id": "t-branch"},
    )
    selected, report = retrieve_with_report(
        [main_fact, branch_fact],
        "harbour gate",
        timeline_ids={"t-main"},
        lineage_sequences={"t-branch": 9},
    )
    assert [memory.id for memory in selected] == ["m1"]
    assert report.dropped_scope == 1


def test_inactive_memories_are_excluded_by_default():
    live = candidate(1)
    retired = candidate(2, is_active=False, importance=0.99)
    selected, report = retrieve_with_report([live, retired], "library")
    assert [memory.id for memory in selected] == ["m1"]
    assert report.dropped_inactive == 1
    selected_all, _report = retrieve_with_report([live, retired], "library", include_inactive=True)
    assert {memory.id for memory in selected_all} == {"m1", "m2"}


def test_character_boundary_holds_for_character_scoped_rows():
    owned = candidate(
        1,
        character_id="c1",
        scope=MemoryScope.CHARACTER,
        metadata={"timeline_id": "t-main", "visibility": "actor"},
    )
    selected, report = retrieve_with_report([owned], "library", character_ids={"c2"})
    assert selected == []
    assert report.dropped_character == 1


def test_scene_filter_keeps_other_scenes_out():
    here = candidate(1, scene_id="s1")
    there = candidate(2, scene_id="s2")
    selected, _report = retrieve_with_report([here, there], "library", scene_id="s1")
    assert [memory.id for memory in selected] == ["m1"]


def test_retrieval_report_explains_every_drop():
    rows = [
        candidate(1),
        candidate(2, is_active=False),
        candidate(3, character_id="other"),
    ]
    _selected, report = retrieve_with_report(rows, "library", character_ids={"mine"})
    assert report.considered == 3
    assert report.dropped_inactive == 1
    assert report.dropped_character == 1
    assert report.pool_limit > 0


@pytest.mark.asyncio
async def test_branch_memories_do_not_leak_across_timelines(session):
    project = repository.create_project(session, "Branch Memory")
    repository.add_character(session, project_id=project.id, name="Alice")
    session.commit()
    node = repository.latest_node(session, project.active_timeline_id)
    branch_b = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="B: Alice dies",
    )
    branch_c = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="C: Alice survives",
    )
    session.commit()

    repository.add_memory(
        session,
        project_id=project.id,
        content="Alice is dead.",
        memory_class=MemoryClass.PERMANENT,
        scope=MemoryScope.WORLD,
        timeline_id=branch_b.id,
        importance=0.95,
    )
    repository.add_memory(
        session,
        project_id=project.id,
        content="Alice survived the night.",
        memory_class=MemoryClass.PERMANENT,
        scope=MemoryScope.WORLD,
        timeline_id=branch_c.id,
        importance=0.95,
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    pipeline = NarrativePipeline(session)
    parent_view = pipeline.memory_inspection(
        project_id=project.id, timeline_id=project.active_timeline_id, limit=50
    )
    branch_b_view = pipeline.memory_inspection(
        project_id=project.id, timeline_id=branch_b.id, limit=50
    )
    branch_c_view = pipeline.memory_inspection(
        project_id=project.id, timeline_id=branch_c.id, limit=50
    )

    def surfaced(view) -> str:
        return " ".join(item["content"] for item in view["selected"]).casefold()

    assert "alice is dead" not in surfaced(parent_view)
    assert "alice survived" not in surfaced(parent_view)
    assert "alice is dead" in surfaced(branch_b_view)
    assert "alice survived" not in surfaced(branch_b_view)
    assert "alice survived" in surfaced(branch_c_view)
    assert "alice is dead" not in surfaced(branch_c_view)
