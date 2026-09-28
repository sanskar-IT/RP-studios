from __future__ import annotations

from services.core.state import StateSnapshot, apply_event, reconstruct_state


def test_state_projection_preserves_definition_state_separation():
    initial = StateSnapshot()
    state = apply_event(initial, "character_moved", {"character_id": "c1", "location_id": "library"})
    state = apply_event(state, "knowledge_acquired", {"character_id": "c1", "fact": "the eastern door is locked"})
    state = apply_event(state, "relationship_changed", {"source_character_id": "c1", "target_character_id": "c2", "relationship_type": "ally", "strength": 0.8})
    state = apply_event(state, "world_fact_created", {"fact_id": "f1", "value": "The archive is awake"})
    assert state.characters["c1"]["location_id"] == "library"
    assert "the eastern door is locked" in state.knowledge["c1"]
    assert state.relationships["c1:c2"]["relationship_type"] == "ally"
    assert state.world_facts["f1"]["value"] == "The archive is awake"
    assert initial.characters == {}


def test_reconstruction_from_checkpoint_and_events():
    checkpoint = StateSnapshot().to_dict()
    events = [
        {"event_type": "item_acquired", "payload": {"character_id": "c1", "item": "key"}},
        {"event_type": "injury_added", "payload": {"character_id": "c1", "injury": "cut"}},
        {"event_type": "character_died", "payload": {"character_id": "c2"}},
    ]
    state = reconstruct_state(checkpoint, events)
    assert state.items["c1"] == ["key"]
    assert state.injuries["c1"] == ["cut"]
    assert state.dead == {"c2"}
    assert state.revision == 3
