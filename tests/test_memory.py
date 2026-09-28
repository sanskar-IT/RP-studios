from __future__ import annotations

from services.core.enums import MemoryClass
from services.memory.retrieval import MemoryCandidate, retrieve_memories


def test_memory_retrieval_ranks_relevant_and_respects_character_boundary():
    memories = [
        MemoryCandidate("a", "The eastern door is locked", MemoryClass.PERMANENT, 0.8, character_id="c1"),
        MemoryCandidate("b", "The witness knows the route", MemoryClass.SCENE, 0.7, character_id="c2", scene_id="s1"),
        MemoryCandidate("c", "An older market rumor", MemoryClass.ARCHIVE, 0.2, character_id="c1"),
    ]
    result = retrieve_memories(memories, "eastern door", character_ids={"c1"}, limit=2)
    assert [memory.id for memory in result] == ["a", "c"]


def test_scene_memory_can_be_working_context():
    memories = [
        MemoryCandidate("working", "The current scene objective", MemoryClass.WORKING, 0.5, scene_id="s1"),
        MemoryCandidate("old", "The old scene objective", MemoryClass.SCENE, 0.9, scene_id="s0"),
    ]
    result = retrieve_memories(memories, "objective", scene_id="s1", limit=1)
    assert result[0].id == "working"
