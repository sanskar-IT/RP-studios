from __future__ import annotations

import pytest

from services.director.intent import (
    AuthorityMode,
    Consistency,
    Intent,
    Specification,
    interpret,
)


def test_explicit_consistent_detective_shoots():
    intent = interpret("Detective shoots the suspect.")
    assert intent.specification == Specification.EXPLICIT
    assert intent.consistency == Consistency.CONSISTENT
    assert intent.confidence >= 0.6


def test_partial_consistent_kill_mysteriously():
    intent = interpret("Kill the suspect mysteriously.")
    assert intent.specification == Specification.PARTIAL
    assert intent.consistency == Consistency.CONSISTENT


def test_underspecified_consistent_something_interesting():
    intent = interpret("Make something interesting happen.")
    assert intent.specification == Specification.UNDERSPECIFIED
    assert intent.consistency == Consistency.CONSISTENT


def test_explicit_contradictory_alice_leave():
    intent = interpret(
        "Have Alice leave while keeping her in the room.",
        participant_names=["Alice", "Detective"],
    )
    assert intent.specification == Specification.EXPLICIT
    assert intent.consistency == Consistency.CONTRADICTORY


def test_contradiction_outranks_ambiguity():
    intent = interpret(
        "Have Alice leave while keeping her in the room.",
        participant_names=["Alice", "Detective", "Witness"],
    )
    assert intent.consistency == Consistency.CONTRADICTORY


def test_ambiguous_pronoun_with_multiple_participants():
    intent = interpret(
        "Tell him the truth.",
        participant_names=["Alice", "Detective"],
    )
    assert intent.consistency == Consistency.AMBIGUOUS


def test_same_pronoun_with_single_participant_is_consistent():
    intent = interpret("Tell him the truth.", participant_names=["Detective"])
    assert intent.consistency == Consistency.CONSISTENT


@pytest.mark.parametrize("authority", list(AuthorityMode))
def test_constraint_envelope_survives_every_authority(authority):
    intent = interpret(
        "Kill the guard, but do not alert anyone.",
        participant_names=["Guard", "Detective"],
        authority=authority,
    )
    assert intent.constraints == ["do not alert anyone"]
    assert intent.objective == "Kill the guard"
    assert intent.consistency == Consistency.CONSISTENT


def test_exclusions_extracted_without_dropping_constraints():
    intent = interpret(
        "I want Makima dead, but I don't want to become Denji.",
        participant_names=["Makima", "Denji"],
    )
    assert intent.constraints == ["I don't want to become Denji"]
    assert any("Denji" in exclusion for exclusion in intent.exclusions)
    assert intent.target_entities == ["Makima", "Denji"]


def test_desired_outcome_split_from_objective():
    intent = interpret(
        "Kill Makima in a suspicious way that makes everyone panic.",
        participant_names=["Makima"],
    )
    assert intent.objective == "Kill Makima in a suspicious way"
    assert intent.desired_outcome == "everyone panic"
    assert intent.target_entities == ["Makima"]


def test_empty_input_is_underspecified_with_floor_confidence():
    intent = interpret("   ")
    assert intent.specification == Specification.UNDERSPECIFIED
    assert intent.consistency == Consistency.CONSISTENT
    assert intent.confidence == 0.05
    assert intent.objective == ""


def test_confidence_stays_in_bounds():
    texts = [
        "",
        "Run!",
        "Detective shoots the suspect.",
        "Make something interesting happen.",
        "Have Alice leave while keeping her in the room.",
        "Tell him the truth.",
        "Kill the guard, but do not alert anyone.",
        "Three hours pass.",
        "Eventually the General betrays the Emperor.",
    ]
    for text in texts:
        confidence = interpret(text, participant_names=["Alice", "Detective"]).confidence
        assert 0.05 <= confidence <= 0.95, text


def test_no_plan_state_on_intent():
    intent = interpret("Detective shoots the suspect.")
    assert not hasattr(intent, "beats")
    assert not hasattr(intent, "plan")
    assert not hasattr(intent, "steps")
    assert "beats" not in intent.to_dict()
    assert "plan" not in intent.to_dict()


def test_urgency_and_tone_and_canon_preference():
    immediate = interpret("Shoot him right now.")
    assert immediate.urgency == "immediate"
    eventual = interpret("Slowly deteriorate their relationship.")
    assert eventual.urgency == "eventual"
    assert interpret("Kill him mysteriously.").tone == "suspenseful"
    assert interpret("Branch from canon here.").canon_preference == "branch"
    assert interpret("Ignore canon and open the gate.").canon_preference == "override"


def test_user_control_level_follows_mode_and_possession():
    assert interpret("Go.", mode="actor").user_control_level == "actor"
    assert interpret("Go.", mode="director").user_control_level == "director"
    assert interpret("Go.", mode="retcon").user_control_level == "author"
    assert (
        interpret("Go.", mode="auto", possessed_character_id="c1").user_control_level == "actor"
    )


def test_unknown_authority_coerces_to_default():
    intent = interpret("Go.", authority="not-a-mode")
    assert isinstance(intent, Intent)


def test_total_on_hostile_input():
    nasty = "￾" + "x" * 10_000 + " \U0001f600 do NOT!!! ...but,,,"
    intent = interpret(nasty, participant_names=["X"])
    assert 0.05 <= intent.confidence <= 0.95
