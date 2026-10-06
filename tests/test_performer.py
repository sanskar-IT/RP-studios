"""Performer behaviour through the real pipeline.

The evaluation suite asserts the Performer's *policy*. These tests pin the
mechanics that policy depends on — the fixes, the guards, and the shape of the
contract — so a regression names a specific behaviour rather than a vague
failure somewhere downstream.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.app import repository
from services.core.enums import AuthorityMode, ControlMode, PlanStatus, SceneStatus
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ProviderMessage, ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"


class Recorder(ScriptedProvider):
    """A scripted provider that keeps the prompt it was given."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prompts: list[str] = []

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, object],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ):
        self.prompts.append(messages[-1].content)
        return await super().structured(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )


def performer_turn(**fields) -> dict:
    payload = {
        "prose": "Something happens.",
        "dialogue": [],
        "actor_actions": [],
        "proposed_events": [],
    }
    payload.update(fields)
    return payload


@pytest.fixture
def cast(session):
    """Project, three participants, one off-stage character, and an active scene."""

    def build(*, authority: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED, control: str | None = None):
        project = repository.create_project(session, "Performer")
        project.authority_mode = authority.value
        session.add(project)
        detective = repository.add_character(session, project_id=project.id, name="Detective")
        witness = repository.add_character(session, project_id=project.id, name="Witness")
        suspect = repository.add_character(session, project_id=project.id, name="Suspect")
        repository.add_character(session, project_id=project.id, name="Librarian")
        scene = repository.create_scene(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            title="Archive",
            participant_ids=[detective.id, witness.id, suspect.id],
        )
        scene.status = SceneStatus.STAGED.value
        session.add(scene)
        session.commit()
        NarrativePipeline(session).approve_scene(
            project_id=project.id, scene_id=scene.id
        )
        if control is not None:
            target = {"detective": detective, "witness": witness, "suspect": suspect}[control]
            NarrativePipeline(session).possess(
                project_id=project.id, scene_id=scene.id, character_id=target.id
            )
        return project, {"Detective": detective, "Witness": witness, "Suspect": suspect}, scene

    return build


# --- The multi-claim corruption fix ---------------------------------------


@pytest.mark.asyncio
async def test_each_actors_speech_is_attributed_to_that_actor(session, cast):
    """A witness's line must not be recorded as the detective's.

    The previous implementation rewrote every ``character_spoke`` in a turn to
    ``user_action`` whenever the selected actor was user-controlled, regardless of
    which character the payload named. The event type, the source, and the
    indexed actor all said "detective" while the payload said "witness".
    """
    project, people, scene = cast()
    detective, witness = people["Detective"], people["Witness"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective asks; the witness answers.",
                    dialogue=[
                        {"character_id": detective.id, "line": "Where were you?"},
                        {"character_id": witness.id, "line": "By the door."},
                    ],
                )
            ]
        ),
    )
    await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Question the witness."
    )
    events = repository.list_events(session, project.active_timeline_id)
    spoken = {event.payload["character_id"]: event for event in events if event.event_type == "character_spoke"}
    assert set(spoken) == {detective.id, witness.id}
    for character_id, event in spoken.items():
        assert event.actor_character_id == character_id, (
            "the indexed actor disagrees with the character who spoke"
        )


@pytest.mark.asyncio
async def test_a_witness_speaking_during_a_possessed_turn_is_not_made_authoritative(session, cast):
    """Possession must not launder another character's line into a user action."""
    project, people, scene = cast(control="detective")
    detective, witness = people["Detective"], people["Witness"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective says nothing. The witness fills the silence.",
                    dialogue=[{"character_id": witness.id, "line": "I was by the door."}],
                )
            ]
        ),
    )
    await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=detective.id,
        user_input="I say nothing.",
    )
    events = repository.list_events(session, project.active_timeline_id)
    witness_speech = [
        event
        for event in events
        if event.event_type == "character_spoke"
        and event.payload.get("character_id") == witness.id
    ]
    assert witness_speech, "the witness's line was not recorded as speech"
    for event in witness_speech:
        assert event.event_type == "character_spoke"
        assert event.source == "ai"
        assert event.actor_character_id == witness.id


# --- Possession as execution authority -------------------------------------


@pytest.mark.asyncio
async def test_per_call_possession_makes_the_actor_user_authoritative(session, cast):
    """A per-call possession changes event authority, not only the actor pick.

    Possession and ``control_mode`` used to be independent: sending possession
    changed routing and the actor but left the event AI-sourced. There is now one
    resolved control-mode view that both read.
    """
    project, people, scene = cast()
    detective = people["Detective"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective crosses to the door.",
                    actor_actions=[
                        {
                            "character_id": detective.id,
                            # Observational, not a decision: the user owns the
                            # intention, the Performer supplies the description.
                            "action": "crosses to the door",
                            "kind": "observation",
                        }
                    ],
                )
            ]
        ),
    )
    await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=detective.id,
        user_input="I cross to the door.",
    )
    events = repository.list_events(session, project.active_timeline_id)
    assert any(
        event.event_type == "user_action" and event.source == "user"
        for event in events
        if event.payload.get("character_id") == detective.id
    )


@pytest.mark.asyncio
async def test_possession_does_not_mutate_the_character_or_its_knowledge(session, cast):
    """Possession is an execution authority, not a state change."""
    project, people, scene = cast(control="witness")
    witness = people["Witness"]
    pipeline = NarrativePipeline(
        session, ScriptedProvider([performer_turn(prose="The witness waits.")])
    )
    await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Continue.",
    )
    state = repository.current_state(session, project.active_timeline_id)
    assert witness.id in state.control_modes
    assert state.control_modes[witness.id] == ControlMode.USER.value
    assert state.knowledge.get(witness.id, set()) == set()
    record = state.characters.get(witness.id, {})
    assert "identity" not in record, "possession rewrote something about the character itself"


# --- User agency -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_performer_may_not_invent_a_decision_for_a_possessed_character(session, cast):
    project, people, scene = cast(control="detective")
    detective = people["Detective"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose=(
                        "The detective opens the drawer, gasps, draws a weapon, and accuses "
                        "the witness of everything."
                    ),
                    actor_actions=[
                        {
                            "character_id": detective.id,
                            "action": "draws a weapon and accuses the witness",
                            "kind": "decision",
                        }
                    ],
                )
            ]
        ),
    )
    with pytest.raises(ValueError, match="user-controlled"):
        await pipeline.continue_scene(
            project_id=project.id,
            scene_id=scene.id,
            possessed_character_id=detective.id,
            user_input="I open the drawer.",
        )
    assert not any(
        event.event_type == "character_performed_action"
        for event in repository.list_events(session, project.active_timeline_id)
    ), "a rejected turn must commit nothing"


@pytest.mark.asyncio
async def test_the_performer_may_supply_connective_description_for_a_possessed_character(
    session, cast
):
    """Blocking every action would make possession produce a silent scene."""
    project, people, scene = cast(control="detective")
    detective, witness = people["Detective"], people["Witness"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The drawer slides open. The witness inhales sharply.",
                    actor_actions=[
                        {
                            "character_id": detective.id,
                            "action": "opens the drawer",
                            "kind": "observation",
                        },
                        {
                            "character_id": witness.id,
                            "action": "inhales sharply",
                            "kind": "reaction",
                        },
                    ],
                )
            ]
        ),
    )
    result = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=detective.id,
        user_input="I open the drawer.",
    )
    assert result.generation_id
    events = repository.list_events(session, project.active_timeline_id)
    assert any(event.payload.get("character_id") == witness.id for event in events)


@pytest.mark.asyncio
async def test_the_performer_may_not_speak_for_a_possessed_character(session, cast):
    project, people, scene = cast(control="detective")
    detective = people["Detective"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective speaks without being asked.",
                    dialogue=[{"character_id": detective.id, "line": "I know everything."}],
                )
            ]
        ),
    )
    with pytest.raises(ValueError, match="spoke for"):
        await pipeline.continue_scene(
            project_id=project.id,
            scene_id=scene.id,
            possessed_character_id=detective.id,
            user_input="I open the drawer.",
        )


# --- Scope -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_performer_is_never_given_a_non_participant(session, cast):
    """A character who is not in the room is not context."""
    project, people, scene = cast()
    pipeline = NarrativePipeline(
        session, Recorder([performer_turn(prose="The detective waits.")])
    )
    await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Wait."
    )
    assert pipeline.provider.prompts
    for prompt in pipeline.provider.prompts:
        assert "Librarian" not in prompt
    for prompt in pipeline.provider.prompts:
        assert "PERFORM THIS SCENE SEGMENT" in prompt


@pytest.mark.asyncio
async def test_a_claim_naming_a_stranger_is_rejected_rather_than_silently_dropped(session, cast):
    """Dropping it would look identical to the model never having made it.

    Silently deleting a bad claim is how the user never learns the engine tried
    to write someone who is not in the scene.
    """
    project, people, scene = cast()
    librarian = next(
        character
        for character in repository.list_characters(session, project.id)
        if character.name == "Librarian"
    )
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective turns.",
                    proposed_events=[
                        {
                            "event_type": "character_spoke",
                            "character_id": librarian.id,
                            "text": "I heard that.",
                        }
                    ],
                )
            ]
        ),
    )
    with pytest.raises(ValueError):
        await pipeline.continue_scene(
            project_id=project.id, scene_id=scene.id, user_input="Listen."
        )
    assert not any(
        event.payload.get("character_id") == librarian.id
        for event in repository.list_events(session, project.active_timeline_id)
    )


# --- Pacing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_turn_realises_one_beat(session, cast):
    """A plan spans turns; a turn does not finish the arc."""
    project, people, scene = cast(authority=AuthorityMode.STRICT)
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider([performer_turn(prose="The detective watches.")] * 6),
    )
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="Build toward the confrontation slowly, keeping the tension high",
    )
    assert turn.plan_id
    pipeline.decide_plan(project_id=project.id, plan_id=turn.plan_id, decision="approve")
    await pipeline.execute_plan(project_id=project.id, plan_id=turn.plan_id)
    row = repository.get_director_plan(session, turn.plan_id)
    assert row.status == PlanStatus.EXECUTING.value
    payload = row.plan_json or {}
    assert any(
        beat["status"] in {"pending", "active"} for beat in payload.get("beats", [])
    ), "the whole plan was consumed in one turn"


# --- Presentation style ----------------------------------------------------


def test_presentation_style_is_coerced_rather_than_rejected():
    """A typo in a UI field must not fail a turn."""
    from services.performer import normalize_style

    assert normalize_style("literary") == "literary"
    assert normalize_style("dialogue-focused") == "dialogue_focused"
    assert normalize_style("nonsense") == "hybrid"
    assert normalize_style(None) == "hybrid"


@pytest.mark.asyncio
async def test_the_selected_style_reaches_the_prompt(session, cast):
    project, people, scene = cast()
    pipeline = NarrativePipeline(
        session,
        Recorder([performer_turn(prose="The detective waits.")]),
        presentation_style="literary",
    )
    await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Wait."
    )
    assert pipeline.provider.prompts
    assert any("sensory detail" in prompt for prompt in pipeline.provider.prompts)


# --- Trace -----------------------------------------------------------------


# --- Possession: the model does not get to grant itself authority -----------


@pytest.mark.asyncio
async def test_a_major_decision_relabelled_as_a_reaction_is_still_refused(session, cast):
    """Relabelling must not defeat the agency check.

    ``check_user_agency`` read ``turn.kind == "decision"`` to decide whether the
    Performer had taken over the user's character. ``kind`` is a field the model
    fills in, so the check was a request rather than a guarantee: the same action
    that is refused as ``decision`` is accepted as ``reaction`` and then committed
    as an authoritative ``user_action`` for a character the user owns.

    The user's own input is the only trustworthy statement of intent, so
    grounding in that is what the check now tests.
    """
    project, people, scene = cast(control="detective")
    detective = people["Detective"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective looks around the archive.",
                    actor_actions=[
                        {
                            "character_id": detective.id,
                            "action": "signs away the estate to the suspect",
                            "kind": "reaction",
                        }
                    ],
                )
            ]
        ),
    )
    # A contract violation is the refusal mechanism: the proposal is discarded
    # and the turn is not committed.
    with pytest.raises(ValueError) as raised:
        await pipeline.continue_scene(
            project_id=project.id,
            scene_id=scene.id,
            possessed_character_id=detective.id,
            user_input="I look around the archive.",
        )
    assert "major decision" in str(raised.value)
    committed = [
        event
        for event in repository.list_events(session, project.active_timeline_id)
        if "estate" in json.dumps(event.payload)
    ]
    assert not committed, "the invented decision was committed to the timeline"


@pytest.mark.parametrize(
    "user_input,action,expected",
    [
        # The user's own action, restated.
        ("I open the drawer.", "draws the drawer open", True),
        # Paraphrase: the input says "him", the action names him.
        ("I tell him the truth.", "tells the detective the truth", True),
        # Connective texture, the case possession exists for.
        ("I open the drawer.", "steadies the drawer with one hand", True),
        ("I confront the butler.", "hesitates", True),
        # Irreversible acts the user never asked for, appended to a legitimate
        # clause so the action still contains a grounded word.
        ("I open the drawer.", "opens the drawer, retrieves the ledger, burns it", False),
        ("I sign the contract.", "signs the contract and hands over the deed", False),
        # Unrelated takeover.
        ("I nod.", "picks up the knife and stabs the butler", False),
        ("I open the drawer.", "signs away the estate to the suspect", False),
        # No user input at all: nothing is grounded.
        ("", "hesitates", False),
        # Nothing substantive claimed.
        ("I wait.", "", True),
    ],
)
def test_grounding_is_measured_by_coverage_not_overlap(user_input, action, expected):
    """One matching word must not license arbitrary extra content.

    The first implementation required only that a single action word appeared in
    the input. That is satisfied for free by prefixing a takeover with something
    the user did ask for — "opens the drawer, retrieves the ledger, burns it"
    matches "drawer" and commits two irreversible acts. The budget is on words
    the input does *not* explain, so adding an unasked act costs budget whether
    or not the action also contains a grounded verb.
    """
    from services.performer.performer import _grounded_in_user_input

    assert _grounded_in_user_input(action, user_input) is expected


@pytest.mark.asyncio
async def test_a_grounded_action_for_the_users_character_is_allowed(session, cast):
    """The check must not refuse the user the actions they actually chose.

    Grounding is a permissive test, not a blunt one: the action has to overlap
    the user's own input. This is the connective case possession exists for.
    """
    project, people, scene = cast(control="detective")
    detective = people["Detective"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The detective draws the drawer open.",
                    actor_actions=[
                        {
                            "character_id": detective.id,
                            "action": "draws the drawer open",
                            "kind": "decision",
                        }
                    ],
                )
            ]
        ),
    )
    result = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=detective.id,
        user_input="I open the drawer.",
    )
    trace = ((result.director or {}).get("performer")) or {}
    codes = {violation["code"] for violation in trace.get("violations") or []}
    assert "user_agency_violation" not in codes


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["proposed_events", "state_claims"])
async def test_a_possessed_id_in_a_claim_is_refused(session, cast, field):
    """Possession must be enforced on claims, not only on described action.

    ``check_user_agency`` looked at ``result.actor_actions`` and nothing else, so
    a turn with no actor actions for the possessed character passed — while
    ``proposed_events`` and ``state_claims`` named them directly. The engine
    would then apply state on the user's character's behalf with no check
    performed at all.
    """
    from services.performer.performer import (
        PerformerRequest,
        PerformerResult,
        check_user_agency,
    )

    project, people, scene = cast(control="detective")
    detective = people["Detective"]

    class _Brief:
        character_id = detective.id
        name = "Detective"
        agency_withheld = True

    request = PerformerRequest(
        scene_id=scene.id,
        user_input="I look around.",
        actors=[_Brief()],
    )
    claims = {
        "proposed_events": [
            {
                "event_type": "character_performed_action",
                "character_id": detective.id,
                "action": "signs away the estate",
            }
        ],
        "state_claims": [
            {
                "entity_id": detective.id,
                "field": "location_id",
                "value": "attic",
            }
        ],
    }
    result = PerformerResult(
        prose="Nothing happens.",
        proposed_events=claims["proposed_events"] if field == "proposed_events" else [],
        state_claims=claims["state_claims"] if field == "state_claims" else [],
    )
    violations = check_user_agency(result, request=request)
    assert any(v.code == "user_agency_violation" for v in violations), (
        f"a possessed character_id in {field} was not checked"
    )


@pytest.mark.parametrize(
    "claim",
    [
        {
            "event_type": "relationship_changed",
            "source_character_id": "u1",
            "target_character_id": "w1",
        },
        {
            "event_type": "relationship_changed",
            "source_character_id": "w1",
            "target_character_id": "u1",
        },
    ],
    ids=["as_source", "as_target"],
)
def test_a_possessed_id_in_any_character_field_is_refused(claim):
    """A claim can name a character through more than one field.

    ``claims.CHARACTER_FIELDS`` is what the engine validates against, and it
    includes ``source_character_id`` and ``target_character_id`` because
    ``relationship_changed`` names two characters that way. The possession check
    read only ``character_id``/``entity_id``, so the model could rewrite the
    user's relationships — an assertion about the user's own character — without
    tripping it at all.
    """
    from services.performer.performer import (
        PerformerRequest,
        PerformerResult,
        check_user_agency,
    )

    class _Brief:
        character_id = "u1"
        name = "Detective"
        agency_withheld = True

    violations = check_user_agency(
        PerformerResult(prose="", proposed_events=[claim]),
        request=PerformerRequest(scene_id="s", user_input="I look around.", actors=[_Brief()]),
    )
    assert any(v.code == "user_agency_violation" for v in violations)


@pytest.mark.asyncio
async def test_the_trace_records_the_performers_decisions(session, cast):
    project, people, scene = cast(control="detective")
    detective, witness = people["Detective"], people["Witness"]
    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                performer_turn(
                    prose="The witness hesitates.",
                    dialogue=[{"character_id": witness.id, "line": "I was nowhere."}],
                    actor_actions=[
                        {
                            "character_id": witness.id,
                            "action": "hesitates",
                            "kind": "reaction",
                        }
                    ],
                )
            ]
        ),
    )
    result = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        possessed_character_id=detective.id,
        user_input="I press the witness.",
    )
    trace = ((result.director or {}).get("performer")) or {}
    assert trace, "no performer trace block"
    for key in (
        "actor_set",
        "possession",
        "beat",
        "input_context",
        "proposed_events",
        "completion_result",
        "validation",
    ):
        assert key in trace, key
    entries = {entry["character_id"]: entry for entry in trace["actor_set"]}
    assert entries[detective.id]["user_controlled"] is True
    assert entries[detective.id]["agency_withheld"] is True
    assert entries[detective.id]["knowledge_scope"] == "viewer"
    assert entries[witness.id]["knowledge_scope"] == "observational_only"
    assert entries[witness.id]["user_control_level"] == "performer"


@pytest.mark.asyncio
async def test_the_trace_holds_no_deliberation(session, cast):
    """A trace carrying scratch work becomes the explanation users read."""
    project, people, scene = cast()
    pipeline = NarrativePipeline(
        session, ScriptedProvider([performer_turn(prose="The detective waits.")])
    )
    result = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Wait."
    )
    trace = json.dumps(result.director or {}).casefold()
    for banned in ("chain_of_thought", "reasoning", "thought:", "scratchpad"):
        assert banned not in trace


# --- Backward compatibility ------------------------------------------------


@pytest.mark.asyncio
async def test_a_legacy_shaped_provider_response_still_works(session, cast):
    """Providers that have not moved to the Performer's shape keep working."""
    project, _people, scene = cast()
    legacy = json.loads((FIXTURES / "normal_turn.json").read_text(encoding="utf-8"))
    pipeline = NarrativePipeline(session, ScriptedProvider([legacy]))
    result = await pipeline.continue_scene(
        project_id=project.id, scene_id=scene.id, user_input="Look around."
    )
    assert result.generation_id
    assert result.structured_output["committed_event_ids"]
