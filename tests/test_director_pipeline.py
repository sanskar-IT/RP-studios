"""End-to-end Director behaviour through the pipeline.

These tests drive the real ``NarrativePipeline`` with a scripted provider. The
provider is scripted rather than live so the assertions are about the Director's
decisions — authority, gating, persistence, and the separation of intent from
plan — and not about prose quality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.enums import AuthorityMode, BeatStatus, PlanStatus, SceneStatus
from services.core.models import DirectorPlan, StoryCommitment
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"


def scripted(*names: str, substitutions: dict[str, str] | None = None) -> ScriptedProvider:
    return ScriptedProvider(
        [json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in names],
        substitutions=substitutions,
    )


@pytest.fixture
def staged(session):
    """A project, three characters, and an approved scene, per authority mode."""

    def build(
        *,
        authority: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED,
        responses: tuple[str, ...] = ("normal_turn.json",),
    ):
        project = repository.create_project(session, "Director")
        project.authority_mode = authority.value
        session.add(project)
        characters = [
            repository.add_character(session, project_id=project.id, name=name)
            for name in ["Detective", "Witness", "General"]
        ]
        scene = repository.create_scene(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            title="Library",
            participant_ids=[character.id for character in characters],
        )
        scene.status = SceneStatus.STAGED.value
        session.add(scene)
        session.commit()
        pipeline = NarrativePipeline(
            session,
            scripted(*responses, substitutions={"actor_name": characters[0].name}),
        )
        pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
        return project, characters, scene, pipeline

    return build


# --- Lane 3: direction -----------------------------------------------------


@pytest.mark.asyncio
async def test_direction_turn_produces_a_plan_linked_to_its_generation(staged, session):
    """Auto-approved direction still leaves a durable record of what directed it.

    Without the plan row, "why did this happen" has no answer once the turn is
    over, and the Director becomes invisible exactly when it is doing work.
    """
    project, _characters, scene, pipeline = staged()
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly, keeping the tension high",
    )
    assert turn.generation_id
    assert turn.plan_id
    assert turn.plan["generation_id"] == turn.generation_id
    assert turn.plan["lane"] == "direction"


@pytest.mark.asyncio
async def test_a_choice_carrying_plan_still_has_beats_to_perform(staged, session):
    """A plan that offers a choice is still a sequence, not a single instruction.

    Regression: ``_beats_for`` used to return nothing whenever a plan required a
    choice, which under ``strict`` and ``collaborative`` left every such plan
    unperformable — there was nothing to pace across turns, and nowhere to hang a
    per-turn requirement. A choice is a question about *direction*; a beat is an
    instruction about *execution*. The two are independent.

    The input must actually produce ``requires_choice``, otherwise the test
    passes for the wrong reason and the bug can return unnoticed.
    """
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=tuple(["normal_turn.json"] * 8)
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        # Underspecified, so the Director offers a choice under strict authority.
        user_input="Let something dramatic occur",
    )
    assert turn.plan_id
    row = repository.get_director_plan(session, turn.plan_id)
    payload = row.plan_json or {}
    assert payload.get("requires_choice") is True, (
        "the fixture no longer triggers the choice path; this test would pass "
        "without exercising the regression"
    )
    assert payload.get("options"), "a choice-carrying plan must offer options"
    assert len(payload.get("beats") or []) >= 1, (
        "a plan with no beats cannot be performed as a sequence"
    )
    # And it can actually be driven turn by turn.
    pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    performed = await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    assert performed.generation_id, "a choice-carrying plan could not be performed"


@pytest.mark.asyncio
async def test_a_multi_beat_plan_stays_executing_until_its_beats_are_done(staged, session):
    """One turn realises one beat. A plan is not complete until it has been performed.

    Marking the plan complete after the first turn would claim the whole arc was
    delivered when only one beat was, and the user would lose the plan they were
    still relying on.
    """
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT,
        responses=tuple(["normal_turn.json"] * 12),
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly, keeping the tension high",
    )
    assert turn.plan["status"] == PlanStatus.PROPOSED.value
    pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    first = await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    row = repository.get_director_plan(session, turn.plan_id)
    assert row.status == PlanStatus.EXECUTING.value, "a partly performed plan is still running"
    assert first.director["remaining_beats"], "there is more plan left to realise"

    # Drive the remaining beats out through the public API, not by poking rows.
    for _ in range(6):
        current = repository.get_director_plan(session, turn.plan_id)
        if current.status != PlanStatus.EXECUTING.value:
            break
        await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    final = repository.get_director_plan(session, turn.plan_id)
    assert final.status == PlanStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_strict_authority_holds_the_turn_until_a_human_decides(staged, session):
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT,
        responses=("normal_turn.json", "normal_turn.json"),
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly",
    )
    assert turn.requires_approval is True
    assert turn.generation_id is None, "no performance may happen before approval"
    assert turn.output_text == ""
    assert turn.plan["status"] == PlanStatus.PROPOSED.value
    assert not repository.list_events(session, scene.timeline_id) or all(
        event.event_type != "character_moved" for event in repository.list_events(session, scene.timeline_id)
    ), "a proposal must not commit events"

    approved = pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    assert approved["status"] == PlanStatus.APPROVED.value

    performed = await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    assert performed.generation_id
    row = repository.get_director_plan(session, turn.plan_id)
    assert row.generation_id == performed.generation_id
    assert row.status in {PlanStatus.EXECUTING.value, PlanStatus.COMPLETED.value}


@pytest.mark.asyncio
async def test_proposed_plan_cannot_be_executed_before_approval(staged, session):
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=("normal_turn.json",)
    )
    turn = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Build toward the confrontation slowly"
    )
    with pytest.raises(ValueError, match="approved"):
        await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)


@pytest.mark.asyncio
async def test_cancelling_a_plan_prevents_performance_entirely(staged, session):
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=("normal_turn.json",)
    )
    turn = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Build toward the confrontation slowly"
    )
    cancelled = pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="cancel")
    assert cancelled["status"] == PlanStatus.CANCELLED.value
    with pytest.raises(ValueError, match="cancelled"):
        await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)


# --- Intent and plan are separate -------------------------------------------


@pytest.mark.asyncio
async def test_editing_a_plan_never_rewrites_the_retained_intent(staged):
    """The user's request did not change, so the record of it must not change.

    This is the distinction the whole layer rests on: the plan is *how*, and a
    user reshaping the how has not changed the what.
    """
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=("normal_turn.json",)
    )
    turn = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Build toward the confrontation slowly"
    )
    before = turn.plan["intent"]
    edited = pipeline.decide_plan(
        project_id=project.id,
        plan_id=turn.plan_id,
        decision="edit",
        edits={"objective": "Force the confrontation now"},
    )
    assert edited["objective"] == "Force the confrontation now"
    assert edited["intent"] == before
    assert edited["intent"]["objective"] == "Build toward the confrontation slowly"
    assert edited["status"] == PlanStatus.PROPOSED.value, "an edit must be re-approved"


@pytest.mark.asyncio
async def test_an_edit_cannot_be_used_to_escape_the_constraint_envelope(staged, session):
    """Approving a corrected plan is not permission to run an impermissible one."""
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=("normal_turn.json",)
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Make it tense, but do not alert anyone",
    )
    assert turn.plan_id
    # The escape is attempted in a *required consequence*, not the objective:
    # consequences are what a plan commits to causing.
    pipeline.decide_plan(
        project_id=project.id,
        plan_id=turn.plan_id,
        decision="edit",
        edits={
            "objective": "Force the confrontation now",
            "required_consequences": ["The guard announces the intrusion to the hall"],
        },
    )
    with pytest.raises(ValueError, match="envelope"):
        pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")


# --- Lane 4: long horizon --------------------------------------------------


@pytest.mark.asyncio
async def test_long_horizon_intent_becomes_a_commitment_and_is_not_performed(staged, session):
    """A betrayal asked for "eventually" cannot happen in the turn that asked.

    The request becomes a commitment and nothing else: no generation, no events
    beyond the audit record, and a plan that is already complete.
    """
    project, _characters, scene, pipeline = staged()
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Eventually, the General will betray the Emperor",
    )
    assert turn.generation_id is None
    assert turn.requires_approval is False
    assert "commitment" in turn.output_text.casefold()

    commitments = session.scalars(select(StoryCommitment)).all()
    assert len(commitments) == 1
    assert commitments[0].status == "created"

    plan_row = repository.get_director_plan(session, turn.plan_id)
    assert plan_row.status == PlanStatus.COMPLETED.value
    assert plan_row.lane == "long_horizon"


@pytest.mark.asyncio
async def test_long_horizon_intent_is_recorded_once_regardless_of_commitment_count(staged, session):
    """One request, one retained intent — however many commitments it implies."""
    project, _characters, scene, pipeline = staged()
    await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Eventually the General betrays the Emperor, and the Witness learns of it",
    )
    from services.core.models import DirectorIntent

    intents = session.scalars(
        select(DirectorIntent).where(DirectorIntent.lane == "long_horizon")
    ).all()
    assert len(intents) == 1


# --- Interruption ----------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupting_a_plan_invalidates_its_open_beats(staged, session):
    """A beat that can no longer happen is invalidated, not failed.

    Improvisation makes this routine, so treating it as an error would punish
    the user for changing their mind.
    """
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT, responses=("normal_turn.json", "normal_turn.json")
    )
    turn = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Build toward the confrontation"
    )
    pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    result = pipeline.interrupt_plan(
        project_id=project.id, plan_id=turn.plan_id, reason="the user changed the subject"
    )
    assert result["status"] in {PlanStatus.CANCELLED.value, PlanStatus.SUPERSEDED.value}
    row = repository.get_director_plan(session, turn.plan_id)
    plan = json.loads(row.plan_json) if isinstance(row.plan_json, str) else row.plan_json
    statuses = {beat["status"] for beat in plan["beats"]}
    assert BeatStatus.INVALIDATED.value in statuses


# --- Plan inspection --------------------------------------------------------


@pytest.mark.asyncio
async def test_plans_are_listable_and_scoped_to_their_project(staged, session):
    project, _characters, scene, pipeline = staged()
    await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Let the scene breathe"
    )
    listed = pipeline.list_plans(project_id=project.id)
    assert listed and listed[0]["plan_id"]
    assert all(entry["project_id"] == project.id for entry in listed)

    other = repository.create_project(session, "Other")
    session.commit()
    with pytest.raises(ValueError, match="does not belong"):
        pipeline.get_plan(project_id=other.id, plan_id=listed[0]["plan_id"])


@pytest.mark.asyncio
async def test_completed_plan_cannot_be_decided_afterwards(staged, session):
    """The lifecycle is one-way: a finished plan cannot be reopened.

    Reopening a completed plan would let an old decision rewrite a scene that has
    already been performed and committed.
    """
    project, _characters, scene, pipeline = staged(
        authority=AuthorityMode.STRICT,
        responses=tuple(["normal_turn.json"] * 12),
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly"
    )
    pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    for _ in range(6):
        current = repository.get_director_plan(session, turn.plan_id)
        if current.status == PlanStatus.COMPLETED.value:
            break
        await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    assert repository.get_director_plan(session, turn.plan_id).status == PlanStatus.COMPLETED.value
    for decision in ("edit", "approve", "cancel"):
        with pytest.raises(ValueError):
            pipeline.decide_plan(
                project_id=project.id, plan_id=turn.plan_id, decision=decision
            )


# --- Trace ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_records_the_directors_structured_decisions_without_reasoning(staged):
    """The trace must be auditable without becoming a transcript of the model.

    A user asking "why did the scene go that way" needs the decision, the lane,
    the constraints, and the validation verdict — not the model's scratch work.
    """
    project, _characters, scene, pipeline = staged()
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly",
    )
    director = turn.director["director"]
    for key in ("lane", "plan_status", "plan_validation", "authority_mode", "beats", "constraints"):
        assert key in director, key
    # The trace is an audit record of decisions, not a transcript of reasoning.
    # Free-text deliberation in here would become the de facto explanation the
    # user reads instead of the structured verdict.
    assert "chain_of_thought" not in json.dumps(director).casefold()
    assert director["authority_mode"] == AuthorityMode.DIRECTOR_ASSISTED.value


@pytest.mark.asyncio
async def test_no_director_plan_row_exists_for_a_possessed_actor_turn(staged, session):
    """A user acting through their own character needs no plan at all.

    Lane 1 is the user already performing. The Director has nothing to decide, so
    it must not leave bookkeeping behind that implies it did.
    """
    project, characters, scene, pipeline = staged()
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=characters[0].id,
        user_input="I carefully slip the file into my coat",
    )
    assert turn.plan_id is None
    assert turn.director["director"]["lane"] == "direct_actor"
    assert session.scalars(select(DirectorPlan)).all() == []


@pytest.mark.asyncio
async def test_no_director_plan_row_exists_for_a_simple_world_turn(staged, session):
    """Ambience and time passage are the world's business, not a plan's."""
    project, _characters, scene, pipeline = staged()
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Rain begins against the tall windows",
    )
    assert turn.director["director"]["lane"] == "simple_world"
    assert session.scalars(select(DirectorPlan)).all() == []
