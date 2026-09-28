from __future__ import annotations

from services.context.consistency import ContradictionDetector, detect, errors_only
from services.context.knowledge import build_knowledge_view
from services.core.state import StateSnapshot


def state_with(**overrides) -> StateSnapshot:
    base = StateSnapshot(
        characters={"a": {"character_id": "a", "location_id": "London"}},
        knowledge={"a": {"the butler is the murderer"}},
        items={"a": ["lantern"]},
        removed_items={"a": {"lantern"}},
        locations={"London": {"location_id": "London"}},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_dead_character_action_is_an_error():
    state = state_with(dead={"a"})
    warnings = detect(state, "character_spoke", {"character_id": "a", "text": "I am back."})
    assert [warning.code for warning in warnings] == ["dead_character_acts"]
    assert errors_only(warnings)


def test_living_character_passes():
    assert detect(state_with(), "character_spoke", {"character_id": "a", "text": "I wait."}) == []


def test_location_conflict_needs_the_scene_location():
    state = state_with()
    warnings = detect(
        state,
        "character_moved",
        {"character_id": "a", "location_id": "Tokyo"},
        scene_location="London",
    )
    assert [warning.code for warning in warnings] == ["location_conflict"]
    assert warnings[0].actions == ("accept", "regenerate", "correct_state")


def test_matching_location_is_fine():
    state = state_with()
    assert (
        detect(state, "character_moved", {"character_id": "a", "location_id": "London"}, scene_location="London")
        == []
    )


def test_removed_item_reuse_is_an_error():
    state = state_with(items={"a": []})
    warnings = detect(
        state,
        "character_performed_action",
        {"character_id": "a", "text": "I raise my lantern against the dark."},
    )
    assert [warning.code for warning in warnings] == ["removed_item_reused"]


def test_held_item_is_fine():
    state = StateSnapshot(characters={"a": {"character_id": "a"}}, items={"a": ["lantern"]})
    assert (
        detect(
            state,
            "character_performed_action",
            {"character_id": "a", "text": "I raise my lantern."},
        )
        == []
    )


def test_unknown_location_is_an_error_when_locations_exist():
    warnings = detect(
        state_with(),
        "character_moved",
        {"character_id": "a", "location_id": "Atlantis"},
        known_locations={"London", "Paris"},
    )
    assert [warning.code for warning in warnings] == ["unknown_location"]


def test_no_locations_defined_means_no_location_error():
    assert (
        detect(
            state_with(),
            "character_moved",
            {"character_id": "a", "location_id": "Anywhere"},
            known_locations=set(),
        )
        == []
    )


def test_certain_knowledge_reference_is_an_error():
    state = StateSnapshot(
        characters={"a": {"character_id": "a"}, "b": {"character_id": "b"}},
        knowledge={"b": {"the butler is the murderer and planned it all"}},
    )
    view = build_knowledge_view(state, viewer_character_id="a")
    warnings = detect(
        state,
        "character_spoke",
        {
            "character_id": "a",
            "text": "The butler is the murderer and planned it all, I am certain of it.",
        },
        knowledge=view,
    )
    codes = [warning.code for warning in warnings]
    assert "knowledge_leak" in codes


def test_suspicion_stated_as_suspicion_is_not_an_error():
    state = StateSnapshot(
        characters={"a": {"character_id": "a"}, "b": {"character_id": "b"}},
        knowledge={"b": {"the vault code is seven seven nine"}},
        suspicions={"a": {"the vault code is seven seven nine"}},
    )
    view = build_knowledge_view(state, viewer_character_id="a")
    warnings = detect(
        state,
        "character_spoke",
        {"character_id": "a", "text": "I only suspect the vault code is seven seven nine."},
        knowledge=view,
    )
    assert errors_only(warnings) == []


def test_prose_drift_is_a_warning_not_an_error():
    state = state_with()
    detector = ContradictionDetector(state, scene_location="London")
    warnings = detector.for_prose("The detective enters the Kyoto station and looks around.")
    assert [warning.code for warning in warnings] == ["prose_location_drift"]
    assert errors_only(warnings) == []


def test_warnings_render_with_resolution_actions():
    state = state_with(dead={"a"})
    warning = detect(state, "character_spoke", {"character_id": "a", "text": "Hello."})[0]
    rendered = warning.render()
    assert "dead" in rendered
    assert "accept" in rendered
    assert warning.to_dict()["subject_character_id"] == "a"
