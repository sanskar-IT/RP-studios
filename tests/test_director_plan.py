"""The Director's plan layer: beats as guidance, the envelope as authority.

The tests here are deliberately about the properties the architecture promises,
not about implementation shape. Each one states a rule that, if broken, would
silently change what the user asked for.
"""

from __future__ import annotations

import pytest

from services.core.enums import AuthorityMode, BeatStatus, Horizon, Lane
from services.director.envelope import (
    check_envelope,
    prohibited_terms,
    requires_approval,
    validate_plan,
)
from services.director.intent import Consistency, Intent, Specification
from services.director.plan import Beat, DirectorPlanDraft, build_heuristic_plan

# --- Fixtures --------------------------------------------------------------


def make_intent(
    text: str = "make the witness quietly pocket the file",
    *,
    lane: Lane = Lane.DIRECTION,
    constraints: tuple[str, ...] = (),
    exclusions: tuple[str, ...] = (),
    consistency: Consistency = Consistency.CONSISTENT,
) -> Intent:
    return Intent(
        objective=text,
        lane=lane,
        specification=Specification.EXPLICIT,
        consistency=consistency,
        confidence=0.9,
        constraints=list(constraints),
        exclusions=list(exclusions),
    )


def make_plan(
    *,
    intent: Intent | None = None,
    beats: list[Beat] | None = None,
    **overrides,
) -> DirectorPlanDraft:
    base = {
        "objective": "move the witness toward the archive",
        "summary": "s",
        "lane": Lane.DIRECTION,
        "horizon": Horizon.NEAR_TERM,
        "authority_mode": AuthorityMode.DIRECTOR_ASSISTED,
        "interpretation_source": "test",
        "intent": intent or make_intent(),
    }
    base.update(overrides)
    plan = DirectorPlanDraft(**base)
    if beats is not None:
        plan.beats = beats
    return plan


@pytest.fixture
def state():
    from services.core.state import StateSnapshot

    snapshot = StateSnapshot()
    snapshot.characters = {"c1": {"character_id": "c1", "location_id": "library"}}
    return snapshot


# --- The envelope ----------------------------------------------------------


def test_exclusion_binds_ai_directed_authority():
    """Authority decides how much the Director may fill in, never whether it may contradict.

    This is the single most important rule in the layer: an ``ai_directed`` plan
    may invent anything, *except* the thing the user forbade.
    """
    plan = make_plan(
        intent=make_intent(exclusions=("do not alert anyone",)),
        authority_mode=AuthorityMode.AI_DIRECTED,
        likely_consequences=["A guard shouts a warning to the whole hall"],
    )
    violations = check_envelope(
        plan, intent_exclusions=plan.intent.exclusions
    )
    assert violations, "ai_directed authority must not permit a stated exclusion"
    assert "alert" in violations[0].summary.casefold()


def test_exclusion_matches_synonym_family_not_only_the_exact_word():
    """Users forbid outcomes, not the word that names them.

    "do not alert anyone" has to catch "warns", "shouts", and "announces", or the
    exclusion is trivially bypassed by paraphrase.
    """
    for paraphrase in (
        "The guard warns the room",
        "The General announces the intrusion",
        "She yells across the hall",
    ):
        plan = make_plan(
            intent=make_intent(exclusions=("do not alert anyone",)),
            authority_mode=AuthorityMode.AI_DIRECTED,
            likely_consequences=[paraphrase],
        )
        assert check_envelope(plan, intent_exclusions=plan.intent.exclusions), paraphrase


def test_negated_mention_of_an_excluded_outcome_is_not_a_violation():
    """A plan that *avoids* the forbidden outcome must not be blocked.

    Symmetry with the rule above: matching the word is not enough, the plan has
    to actually assert the forbidden thing.
    """
    plan = make_plan(
        intent=make_intent(exclusions=("do not alert anyone",)),
        likely_consequences=["Nobody is alerted; the theft goes unnoticed"],
    )
    assert check_envelope(plan, intent_exclusions=plan.intent.exclusions) == []


def test_optional_consequences_are_not_policed_but_beats_are():
    """Advisory possibilities are not instructions; a beat is.

    A plan may list a consequence the user forbade as an *option* it does not
    intend to take, but a beat describing the forbidden act is a proposal to do
    it, whether or not the Performer is obliged to.
    """
    plan = make_plan(
        intent=make_intent(exclusions=("no violence",)),
        optional_consequences=["The scene could escalate to violence if it stalls"],
        beats=[Beat(description="The witness slips out", required=False, guidance=True)],
    )
    assert check_envelope(plan, intent_exclusions=plan.intent.exclusions) == []

    plan.beats.append(Beat(description="A guard raises a weapon", required=False, guidance=True))
    assert check_envelope(plan, intent_exclusions=plan.intent.exclusions)


def test_envelope_still_catches_a_required_beat_that_breaks_an_exclusion():
    """Required beats are commitments, so the envelope applies to them."""
    plan = make_plan(
        intent=make_intent(exclusions=("no violence",)),
        beats=[Beat(description="The General attacks the witness", required=True)],
    )
    assert check_envelope(plan, intent_exclusions=plan.intent.exclusions)


def test_prohibited_terms_derives_a_family_from_the_exclusion_itself():
    terms = prohibited_terms("do not alert anyone", "keep it quiet")
    assert terms
    assert any("alert" in term for family in terms.values() for term in family)


# --- Plan validation -------------------------------------------------------


def test_beat_requiring_a_dead_character_is_rejected_before_performance(state):
    state.dead = {"c2"}
    plan = make_plan(beats=[Beat(description="The witness acts", participants=["c2"])])
    result = validate_plan(plan, state=state, participant_ids={"c1", "c2"})
    assert not result.valid
    assert "dead" in " ".join(result.errors)


def test_rejected_plan_offers_recovery_rather_than_partial_work(state):
    """A rejected plan must leave the user a way forward, not a half-applied scene."""
    state.dead = {"c2"}
    plan = make_plan(beats=[Beat(description="The witness acts", participants=["c2"])])
    result = validate_plan(plan, state=state, participant_ids={"c1", "c2"})
    assert set(result.recovery_actions) >= {"regenerate", "edit", "return_to_user"}


def test_choice_plan_without_options_is_invalid(state):
    plan = make_plan(requires_choice=True, options=[])
    result = validate_plan(plan, state=state, participant_ids={"c1"})
    assert not result.valid
    assert "choice" in " ".join(result.errors).casefold()


def test_empty_plan_is_invalid_rather_than_silently_doing_nothing(state):
    plan = make_plan(objective="", desired_direction="", beats=[])
    plan.intent = make_intent()
    result = validate_plan(plan, state=state, participant_ids={"c1"})
    assert not result.valid


# --- Approval gates --------------------------------------------------------


def test_strict_authority_requires_a_human_decision(state):
    plan = make_plan(authority_mode=AuthorityMode.STRICT)
    assert requires_approval(plan, authority=AuthorityMode.STRICT)


def test_director_assisted_does_not_gate_an_ordinary_well_formed_turn(state):
    """Approval that fires on every turn trains the user to approve reflexively.

    That is the failure mode an approval gate is supposed to avoid, so the
    ordinary path must stay uninterrupted.
    """
    plan = make_plan(authority_mode=AuthorityMode.DIRECTOR_ASSISTED)
    assert not requires_approval(plan, authority=AuthorityMode.DIRECTOR_ASSISTED)


def test_inconsistent_intent_always_requires_approval_regardless_of_authority(state):
    """A contradictory request needs a human even when the Director could guess.

    Guessing which half of a contradiction the user meant is exactly the kind of
    silent reinterpretation the Director exists to prevent.
    """
    plan = make_plan(authority_mode=AuthorityMode.AI_DIRECTED)
    plan.intent = make_intent(consistency=Consistency.CONTRADICTORY)
    assert requires_approval(plan, authority=AuthorityMode.AI_DIRECTED)


def test_unresolved_canon_conflict_requires_approval(state):
    plan = make_plan(canon_conflict={"summary": "contradicts source", "resolution": None})
    assert requires_approval(plan, authority=AuthorityMode.DIRECTOR_ASSISTED)


def test_resolved_canon_conflict_does_not_block_the_turn(state):
    plan = make_plan(canon_conflict={"summary": "contradicts source", "resolution": "branch"})
    assert not requires_approval(plan, authority=AuthorityMode.DIRECTOR_ASSISTED)


# --- Beats as guidance -----------------------------------------------------


def test_beat_defaults_to_optional_guidance():
    beat = Beat(description="a witness hesitates")
    assert beat.guidance is True
    assert beat.required is False


def test_beats_are_independently_skippable_and_invalidatable():
    plan = build_heuristic_plan(
        make_intent(),
        lane=Lane.DIRECTION,
        participants=["c1"],
        authority=AuthorityMode.DIRECTOR_ASSISTED,
    )
    plan.beats[0].requires_knowledge = "The General"
    invalidated = plan.invalidate_beats_requiring("The General", "the General died")
    assert invalidated and invalidated[0].status is BeatStatus.INVALIDATED
    assert plan.all_beats_resolved() is False


def test_invalidation_targets_only_the_beats_that_depended_on_the_entity(state):
    """Losing one character must not discard the whole plan.

    A scene's beats are mostly independent; invalidating all of them on any loss
    would make a death reset the Director's plan to nothing.
    """
    plan = make_plan(
        beats=[
            Beat(description="The General confesses", requires_knowledge="The General"),
            Beat(description="The witness leaves the hall"),
        ]
    )
    invalidated = plan.invalidate_beats_requiring("The General", "the General died")
    assert len(invalidated) == 1
    assert plan.beats[1].status is BeatStatus.PENDING


def test_all_beats_resolved_only_once_every_beat_has_a_terminal_status():
    plan = make_plan(
        beats=[
            Beat(description="one", status=BeatStatus.COMPLETED),
            Beat(description="two", status=BeatStatus.SKIPPED),
            Beat(description="three", status=BeatStatus.INVALIDATED),
        ]
    )
    assert plan.all_beats_resolved()


def test_plan_round_trips_through_its_serialized_form():
    plan = build_heuristic_plan(
        make_intent(exclusions=("keep it quiet",)),
        lane=Lane.DIRECTION,
        participants=["c1"],
        authority=AuthorityMode.COLLABORATIVE,
    )
    restored = DirectorPlanDraft.from_dict(plan.to_dict())
    assert restored.to_dict() == plan.to_dict()
    assert restored.intent.exclusions == plan.intent.exclusions
    assert restored.authority_mode is AuthorityMode.COLLABORATIVE
