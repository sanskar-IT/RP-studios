from __future__ import annotations

import pytest

from apps.api.app import repository
from services.core.enums import MemoryClass, MemoryScope
from services.memory import lifecycle
from services.memory.lifecycle import (
    IMPORTANCE_SCENE,
    MemoryProposal,
    MemorySeed,
    beat_seed,
    classify,
    fact_seed,
    proposals_for,
    retention_report,
    score_importance,
)


def test_seed_classification_separates_class_from_scope():
    memory_class, scope, _reason = classify(
        MemorySeed(content="x", event_type="knowledge_acquired", character_id="c1")
    )
    assert memory_class == MemoryClass.PERMANENT
    assert scope == MemoryScope.CHARACTER

    memory_class, scope, _reason = classify(
        MemorySeed(content="x", event_type="world_fact_created")
    )
    assert memory_class == MemoryClass.PERMANENT
    assert scope == MemoryScope.WORLD

    memory_class, scope, _reason = classify(
        MemorySeed(content="x", event_type="character_moved", character_id="c1")
    )
    assert memory_class == MemoryClass.TRANSIENT
    assert scope == MemoryScope.SCENE


def test_character_moves_are_always_transient():
    proposal = proposals_for(
        [MemorySeed(content="the witness left", event_type="character_moved", character_id="c1")]
    )[0]
    assert proposal.persist is False
    assert "transient" in proposal.reason


def test_unimportant_prose_is_dropped_but_decisions_are_reported():
    seeds = [
        MemorySeed(content="The witness glances aside.", event_type="ai_action"),
        MemorySeed(
            content="The butler confesses the murder was his idea.",
            event_type="knowledge_acquired",
            character_id="c1",
            facts=["the butler planned the murder"],
        ),
    ]
    proposals = proposals_for(seeds)
    report = retention_report(proposals)
    kept = [proposal for proposal in proposals if proposal.persist]
    assert report["persisted"] == 1
    assert kept[0].scope == MemoryScope.CHARACTER
    assert report["proposals"][0]["persist"] is False


def test_importance_is_deterministic_and_bounded():
    first = score_importance("The butler confesses the murder.", event_type="ai_action", has_facts=True)
    second = score_importance("The butler confesses the murder.", event_type="ai_action", has_facts=True)
    assert first == second
    assert 0.0 <= first <= 0.95
    slow = score_importance("The witness glances aside and shifts weight.", event_type="ai_action", has_facts=False)
    assert slow < first


def test_beat_seed_carries_no_facts_and_fact_seed_needs_a_fact_event():
    class FakeEvent:
        event_type = "character_spoke"
        payload = {"text": "hello"}
        source = "ai"
        id = "e1"

    assert beat_seed("Some prose.", scene_id="s1", actor_id="a1", sequence=4).facts == []
    assert fact_seed(FakeEvent()) is None


def test_fact_seed_describes_durable_events():
    class Death:
        event_type = "character_died"
        payload = {"character_id": "c1"}
        source = "ai"
        id = "e2"

    seed = fact_seed(Death())
    assert seed is not None
    assert seed.character_id == "c1"
    assert "[character_died]" in seed.content


def test_memory_proposal_round_trip():
    proposal = MemoryProposal(
        seed=MemorySeed(content="x", event_type="ai_action"),
        memory_class=MemoryClass.SCENE,
        scope=MemoryScope.TRANSIENT,
        importance=0.2,
        persist=False,
        reason="transient",
    )
    assert proposal.to_dict()["scope"] == "transient"


def test_indirect_recall_irrelevant_rejection_and_contradiction():
    from datetime import UTC, datetime

    from services.memory.retrieval import MemoryCandidate, retrieve_memories

    rows = [
        MemoryCandidate(
            "door",
            "the eastern door is locked",
            MemoryClass.PERMANENT,
            0.8,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"timeline_id": "t", "visibility": "world"},
        ),
        MemoryCandidate(
            "harbour",
            "the harbour gate stands open",
            MemoryClass.PERMANENT,
            0.8,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
            metadata={"timeline_id": "t", "visibility": "world"},
        ),
        MemoryCandidate(
            "rumor",
            "a rumor that the eastern door stands open",
            MemoryClass.PERSISTENT,
            0.4,
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
            is_active=False,
            metadata={"timeline_id": "t", "visibility": "world"},
        ),
    ]
    # Indirect recall: "eastern entrance" shares the distinctive word, not the sentence.
    indirect = retrieve_memories(rows, "eastern entrance", limit=3)
    assert [memory.id for memory in indirect][0] == "door"
    # Irrelevant rejection: a harbour query must not surface the door first.
    assert retrieve_memories(rows, "harbour gate manifest", limit=1)[0].id == "harbour"
    # Contradiction: the superseded rumor never surfaces, however it scores.
    assert "rumor" not in [memory.id for memory in retrieve_memories(rows, "eastern door", limit=3)]


@pytest.mark.asyncio
async def test_a_turn_writes_at_most_one_beat_plus_facts(session):
    from services.narrative.pipeline import NarrativePipeline
    from services.providers.base import ScriptedProvider

    project = repository.create_project(session, "Lifecycle")
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
                    "prose": "The witness glances aside and says nothing of consequence.",
                    "actions": [],
                    "new_events": [
                        {"event_type": "character_moved", "character_id": character.id, "location_id": "hall"}
                    ],
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
    rows_before = len(
        repository.list_memories(session, project_id=project.id, timeline_id=project.active_timeline_id, limit=5000)
    )
    await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    rows_after = repository.list_memories(
        session, project_id=project.id, timeline_id=project.active_timeline_id, limit=5000
    )
    assert len(rows_after) - rows_before <= 1
    assert all("lifecycle_reason" in (row.metadata_json or {}) for row in rows_after)


@pytest.mark.asyncio
async def test_durable_facts_are_persisted_with_scope(session):
    from services.narrative.pipeline import NarrativePipeline
    from services.providers.base import ScriptedProvider

    project = repository.create_project(session, "Lifecycle Facts")
    character = repository.add_character(session, project_id=project.id, name="Detective")
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
                    "characters_present": ["Detective"],
                    "objective": "Learn.",
                    "initial_conditions": [],
                    "environmental_assumptions": [],
                    "potential_consequences": [],
                    "canon_conflicts": [],
                },
                {
                    "selected_actor": character.id,
                    "reason": "only participant",
                    "prose": "The detective learns that the eastern door is locked.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "knowledge_acquired",
                            "character_id": character.id,
                            "fact": "the eastern door is locked",
                        }
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id, premise="A detective learns", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    await pipeline.continue_scene(project_id=project.id, scene_id=scene.id, actor_character_id=character.id)
    rows = repository.list_memories(
        session, project_id=project.id, timeline_id=project.active_timeline_id, limit=5000
    )
    fact_rows = [
        row
        for row in rows
        if "eastern door" in row.content
        and (row.metadata_json or {}).get("origin_event_type") == "knowledge_acquired"
    ]
    assert fact_rows, "the learned fact must be persisted as its own record"
    assert all(row.scope == MemoryScope.CHARACTER.value for row in fact_rows)
    assert all(row.character_id == character.id for row in fact_rows)
    assert all(row.is_active for row in fact_rows)
    assert lifecycle.IMPORTANCE_PERSISTENT >= IMPORTANCE_SCENE
