"""The Performer evaluation suite.

The engine harness asks "did the turn validate". The Director suite asks "did the
Director do the user's job correctly". This suite asks a third question: **did the
scene actually play** — and a fourth, harder one: *was it good*.

The fourth question is why this file exists separately. A Performer can emit
perfectly valid events and write prose that is flat, uniform, or a takeover of the
user's character, and none of that raises a validation error. Those are the
failures that matter most to a roleplay engine and the ones structural checks are
blindest to, so several scenarios here assert on prose directly.

Every scenario uses a scripted provider, so the assertions are about the
Performer's *behaviour* given an output — not about a particular model's writing.
Where a scenario claims something about prose quality, it asserts against a
fixture the suite itself defines, and says so.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app import repository
from services.core.enums import AuthorityMode, PlanStatus, SceneStatus
from services.core.events import KNOWLEDGE_ACQUIRED
from services.core.models import (
    Base,
    Event,
    Generation,
)
from services.core.state import StateSnapshot
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ProviderMessage, ScriptedProvider

FIXTURES = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "provider"

SECRET = "the ledger in the archive is forged"
# A fact the *acting* character legitimately holds, so scenario I's isolation
# check has a positive control rather than only an absence.
OWN_FACT = "the eastern door sticks unless lifted"
PERFORMER_HEADER = "PERFORM THIS SCENE SEGMENT"


@dataclass
class Check:
    key: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    key: str
    title: str
    rule: str
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "rule": self.rule,
            "passed": self.passed,
            "checks": [
                {"key": c.key, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "notes": list(self.notes),
        }


@dataclass
class PerformerReport:
    generated_at: str
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    @property
    def failure_count(self) -> int:
        return sum(1 for s in self.scenarios for c in s.checks if not c.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "passed": self.passed,
            "scenario_count": len(self.scenarios),
            "failure_count": self.failure_count,
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def render(self) -> str:
        lines = ["Performer evaluation", "=" * 20]
        for scenario in self.scenarios:
            mark = "PASS" if scenario.passed else "FAIL"
            lines.append(f"[{mark}] {scenario.key} {scenario.title}")
            for check in scenario.checks:
                if not check.passed:
                    lines.append(f"    x {check.key}: {check.detail}")
        lines.append("")
        failed = sum(1 for s in self.scenarios if not s.passed)
        lines.append(
            f"{len(self.scenarios) - failed}/{len(self.scenarios)} scenarios passed, "
            f"{self.failure_count} failed checks"
        )
        return "\n".join(lines)


# --- Providers --------------------------------------------------------------


class RecordingProvider(ScriptedProvider):
    """Records the prompt it was actually given.

    A check on what the Performer was *told* has to read the prompt. The trace
    records what we meant to say, not what the model received.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.prompts: list[str] = []

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Any:
        self.prompts.append(messages[-1].content)
        return await super().structured(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )


def provider(*responses: dict[str, Any], record: bool = False) -> Any:
    cls = RecordingProvider if record else ScriptedProvider
    return cls([dict(response) for response in responses])


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def turn(
    *,
    prose: str,
    dialogue: Sequence[dict[str, str]] = (),
    actions: Sequence[dict[str, str]] = (),
    events: Sequence[dict[str, Any]] = (),
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Performer-shaped provider response."""
    return {
        "prose": prose,
        "dialogue": list(dialogue),
        "actor_actions": list(actions),
        "proposed_events": list(events),
        "completion_signals": dict(signals or {}),
    }


# --- World ------------------------------------------------------------------


class World:
    """A seeded project whose cast and scene the scenarios share."""

    def __init__(self, db: Session, *, authority: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED) -> None:
        self.project = repository.create_project(db, "Performer Evaluation")
        self.project.authority_mode = authority.value
        db.add(self.project)
        self.timeline_id = str(self.project.active_timeline_id or "")
        self.detective = self._character(db, "Detective")
        self.witness = self._character(db, "Witness")
        self.suspect = self._character(db, "Suspect")
        self.offstage = self._character(db, "Librarian")
        self.scene = repository.create_scene(
            db,
            project_id=self.project.id,
            timeline_id=self.timeline_id,
            title="Archive Room",
            participant_ids=[self.detective.id, self.witness.id, self.suspect.id],
        )
        db.commit()

    def _character(self, db: Session, name: str) -> Any:
        return repository.add_character(db, project_id=self.project.id, name=name)

    def activate(self, db: Session) -> Any:
        self.scene.status = SceneStatus.STAGED.value
        db.add(self.scene)
        db.commit()
        self.pipeline(db).approve_scene(project_id=self.project.id, scene_id=self.scene.id)
        return self.scene

    def pipeline(self, db: Session, *responses: dict[str, Any], record: bool = False) -> NarrativePipeline:
        return NarrativePipeline(db, provider(*responses, record=record))

    def state(self, db: Session) -> StateSnapshot:
        return repository.current_state(db, self.timeline_id)

    def events(self, db: Session) -> list[Event]:
        return repository.list_events(db, self.timeline_id)


def seed_secret(db: Session, world: World) -> None:
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": world.suspect.id, "fact": SECRET},
        source="user",
    )
    db.commit()


def kill(db: Session, world: World, character: Any) -> None:
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type="character_died",
        payload={"character_id": character.id, "cause": "the scene's doing"},
        source="ai",
    )
    db.commit()


def performer_trace(db: Session, generation_id: str | None) -> dict[str, Any]:
    if not generation_id:
        return {}
    row = db.get(Generation, generation_id)
    return dict((row.trace or {}).get("performer") or {}) if row else {}


# --- A. Single-character scene ---------------------------------------------


def scenario_a_single_actor(db: Session) -> ScenarioResult:
    """One character, one action. Prose and state must agree.

    This is the floor. If the Performer cannot keep a single character's action
    in agreement with the event it proposes, nothing else is worth checking.
    """
    result = ScenarioResult(
        "A", "Single-character scene", "prose and committed state agree"
    )
    world = World(db)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(
            prose="The detective crosses to the window and studies the courtyard below.",
            actions=[{"character_id": world.detective.id, "action": "crosses to the window", "kind": "decision"}],
            events=[
                {
                    "event_type": "character_moved",
                    "character_id": world.detective.id,
                    "location_id": "window",
                }
            ],
        ),
    )
    turn_result = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id, scene_id=world.scene.id, user_input="I study the courtyard."
        )
    )
    committed = world.events(db)
    moved = [event for event in committed if event.event_type == "character_moved"]
    result.checks.append(Check("turn_committed", turn_result.generation_id is not None))
    result.checks.append(Check("one_event", len(moved) == 1, f"{len(moved)} movement events"))
    result.checks.append(
        Check(
            "state_matches_prose",
            bool(moved)
            and world.state(db).characters.get(world.detective.id, {}).get("location_id")
            == moved[-1].payload.get("location_id"),
            "the projection does not match the event the prose described",
        )
    )
    result.checks.append(
        Check(
            "event_attributed_to_the_actor",
            bool(moved) and moved[-1].actor_character_id == world.detective.id,
            "the event was not attributed to the character who moved",
        )
    )
    trace = performer_trace(db, turn_result.generation_id)
    result.checks.append(
        Check("trace_present", bool(trace), "no performer trace block was recorded")
    )
    result.checks.append(
        Check(
            "trace_has_actor_set",
            bool(trace.get("actor_set")),
            "the trace does not record which actors were in the scene",
        )
    )
    return result


# --- B. Multi-character dialogue -------------------------------------------


def scenario_b_multi_actor_dialogue(db: Session) -> ScenarioResult:
    """Three characters speaking, each constrained by their own knowledge.

    The point of the check is asymmetry: three characters in one room must not
    end up with the same information, and the Performer must be able to write all
    three without merging them.
    """
    result = ScenarioResult(
        "B", "Multi-character dialogue", "each actor speaks within their own knowledge"
    )
    world = World(db)
    seed_secret(db, world)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(
            prose=(
                "The detective turns on the witness.\n\n"
                '"You said the ledger was never signed."\n\n'
                'The witness swallows. "I never saw it."\n\n'
                "The suspect says nothing at all, and watches the door instead."
            ),
            dialogue=[
                {"character_id": world.detective.id, "line": "You said the ledger was never signed."},
                {"character_id": world.witness.id, "line": "I never saw it."},
                {"character_id": world.suspect.id, "line": "Ask him. I was never here."},
            ],
            events=[
                {
                    "event_type": "character_spoke",
                    "character_id": world.detective.id,
                    "text": "You said the ledger was never signed.",
                },
                {
                    "event_type": "character_spoke",
                    "character_id": world.witness.id,
                    "text": "I never saw it.",
                },
                {
                    "event_type": "character_spoke",
                    "character_id": world.suspect.id,
                    "text": "Ask him. I was never here.",
                },
            ],
        ),
    )
    turn_result = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Press the witness about the ledger.",
        )
    )
    committed = world.events(db)
    spoken = [event for event in committed if event.event_type == "character_spoke"]
    result.checks.append(Check("turn_committed", turn_result.generation_id is not None))
    result.checks.append(Check("all_three_spoke", len(spoken) >= 3, f"{len(spoken)} speech events"))

    speakers = {event.payload.get("character_id") for event in spoken}
    expected = {world.detective.id, world.witness.id, world.suspect.id}
    result.checks.append(
        Check(
            "per_speaker_attribution",
            expected <= speakers,
            f"missing speakers: {sorted(expected - speakers)}",
        )
    )
    # The indexed actor column must agree with the payload. When it did not, a
    # witness's line was recorded as the detective's.
    result.checks.append(
        Check(
            "indexed_actor_matches_speaker",
            all(event.actor_character_id == event.payload.get("character_id") for event in spoken),
            "an event's indexed actor disagrees with the character who spoke",
        )
    )
    result.checks.append(
        Check(
            "secret_not_spoken",
            all(SECRET not in str(event.payload.get("text", "")) for event in spoken),
            "a character voiced a fact only one of them knew",
        )
    )
    result.checks.append(
        Check(
            "suspicion_not_promoted",
            world.state(db).knowledge.get(world.detective.id, set()) != {SECRET},
            "hearing the suspect's denial taught the detective the secret",
        )
    )
    trace = performer_trace(db, turn_result.generation_id)
    result.checks.append(
        Check(
            "knowledge_scope_recorded",
            all(
                entry.get("knowledge_scope") in {"viewer", "observational_only"}
                for entry in trace.get("actor_set", [])
            ),
            "the trace does not record each actor's knowledge scope",
        )
    )
    return result


# --- C. User possession -----------------------------------------------------


def scenario_c_user_possession(db: Session) -> ScenarioResult:
    """The Performer must not invent major decisions for a possessed character.

    "I open the drawer" may become the drawer opening, dust, and a witness
    reacting. It may not become the detective also drawing a weapon and accusing
    someone.
    """
    result = ScenarioResult(
        "C", "User possession", "no major decision is invented for the user's character"
    )
    world = World(db)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(
            prose=(
                "The drawer slides open on a dry rasp. Ledger dust lifts in the lamplight, "
                "and the witness inhales sharply but says nothing."
            ),
            actions=[
                {"character_id": world.detective.id, "action": "opens the drawer", "kind": "observation"},
                {"character_id": world.witness.id, "action": "inhales sharply", "kind": "reaction"},
            ],
            events=[
                {
                    "event_type": "character_spoke",
                    "character_id": world.witness.id,
                    "text": "…",
                }
            ],
        ),
    )
    pipeline.possess(
        project_id=world.project.id, scene_id=world.scene.id, character_id=world.detective.id
    )
    turn_result = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            possessed_character_id=world.detective.id,
            user_input="I open the drawer.",
        )
    )
    committed = world.events(db)
    detective_events = [
        event
        for event in committed
        if event.payload.get("character_id") == world.detective.id
    ]
    result.checks.append(Check("turn_committed", turn_result.generation_id is not None))
    result.checks.append(
        Check(
            "witness_speech_not_attributed_to_the_detective",
            all(
                event.actor_character_id == world.witness.id
                for event in committed
                if event.payload.get("character_id") == world.witness.id
            ),
            "the witness's line was recorded under the detective",
        )
    )
    result.checks.append(
        Check(
            "user_action_is_authoritative",
            any(event.event_type == "user_action" and event.source == "user" for event in detective_events)
            or not detective_events,
            "the possessed character's own action was not recorded as a user action",
        )
    )
    result.checks.append(
        Check(
            "world_reacts",
            any(event.payload.get("character_id") == world.witness.id for event in committed),
            "nobody else reacted; a possessed scene must not go silent",
        )
    )
    result.checks.append(
        Check(
            "no_invented_accusation",
            "accus" not in turn_result.output_text.casefold()
            and "gun" not in turn_result.output_text.casefold()
            and "weapon" not in turn_result.output_text.casefold(),
            "the Performer invented a major decision for the user",
        )
    )
    trace = performer_trace(db, turn_result.generation_id)
    result.checks.append(
        Check(
            "agency_withheld_recorded",
            any(entry.get("agency_withheld") for entry in trace.get("actor_set", [])),
            "the trace does not record that the character's agency was withheld",
        )
    )
    return result


# --- D. Non-user character agency -------------------------------------------


def scenario_d_character_agency(db: Session) -> ScenarioResult:
    """Characters must not respond identically.

    Uniform reaction is the single most artificial thing a multi-character scene
    can do, and nothing in the validator catches it. This scenario asserts on the
    content of the reactions themselves.
    """
    result = ScenarioResult(
        "D", "Character agency", "reactions differ per character, derived from their own state"
    )
    world = World(db)
    seed_secret(db, world)
    world.activate(db)
    # Give the detective a suspicion and the witness nothing, so their grounds for
    # reacting are genuinely different.
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type="knowledge_suspected",
        payload={"character_id": world.detective.id, "fact": "the witness is hiding something"},
        source="user",
    )
    db.commit()
    pipeline = world.pipeline(
        db,
        turn(
            prose=(
                "The detective leans in, unhurried.\n\n"
                '"You hesitated."\n\n'
                "The witness takes a step back, hands open. \"I didn't. You imagined it.\"\n\n"
                "The suspect does not deny anything, because the suspect was never asked."
            ),
            dialogue=[
                {"character_id": world.detective.id, "line": "You hesitated."},
                {"character_id": world.witness.id, "line": "I didn't. You imagined it."},
                {"character_id": world.suspect.id, "line": "Nobody asked me anything."},
            ],
            actions=[
                {"character_id": world.detective.id, "action": "presses the witness", "kind": "decision"},
                {"character_id": world.witness.id, "action": "steps back, hands open", "kind": "reaction"},
                {"character_id": world.suspect.id, "action": "says nothing about the hesitation", "kind": "reaction"},
            ],
        ),
    )
    asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="A suspicious corpse is discovered in the archive.",
        )
    )
    result.checks.append(
        Check(
            "all_three_reacted",
            len(performer_actions(db)) >= 3,
            f"{len(performer_actions(db))} characters reacted",
        )
    )
    result.checks.append(
        Check(
            "reactions_are_distinct",
            len({action["action"] for action in performer_actions(db)}) == len(performer_actions(db)),
            "two characters were given the same reaction",
        )
    )
    result.checks.append(
        Check(
            "suspicion_informed_the_detective",
            any("press" in action["action"] for action in performer_actions(db)),
            "the character with a suspicion did not act on it",
        )
    )
    result.checks.append(
        Check(
            "uninformed_character_did_not_confess",
            not any(
                SECRET in action["action"] for action in performer_actions(db)
            ),
            "an uninformed character acted on knowledge they do not have",
        )
    )
    return result


def performer_actions(db: Session) -> list[dict[str, Any]]:
    """Every ``character_performed_action`` currently on the timeline."""
    return [
        {"character_id": event.payload.get("character_id"), "action": str(event.payload.get("action", ""))}
        for event in repository.list_events(db, _current_timeline(db))
        if event.event_type == "character_performed_action"
    ]


def _current_timeline(db: Session) -> str:
    from services.core.models import Project

    project = db.query(Project).first()
    return str(project.active_timeline_id) if project is not None else ""


# --- E. Interrupted plan ----------------------------------------------------


def scenario_e_interrupted_plan(db: Session) -> ScenarioResult:
    """A user action during a running plan skips the beat, not the plan.

    Ending the whole arc because the player did something unexpected is the
    failure mode. Skipping the beat and continuing is the correct behaviour.
    """
    result = ScenarioResult(
        "E", "Interrupted plan", "user action skips the active beat, plan continues"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(prose="Ready.", actions=[]),
        turn(prose="The detective studies the ledger's spine.", actions=[]),
        turn(prose="The witness turns for the door.", actions=[]),
        turn(prose="The detective steps between them.", actions=[]),
    )
    plan_turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Build toward the confrontation slowly, keeping the tension high",
        )
    )
    plan_id = plan_turn.plan_id
    if not plan_id:
        raise AssertionError("expected a plan for a direction request")
    pipeline.decide_plan(project_id=world.project.id, plan_id=plan_id, decision="approve")
    asyncio.run(pipeline.execute_plan(project_id=world.project.id, plan_id=plan_id))
    mid = repository.get_director_plan(db, plan_id)
    result.checks.append(
        Check("plan_running", mid.status == PlanStatus.EXECUTING.value, mid.status)
    )
    result.checks.append(
        Check("plan_is_multi_beat", len(_beats(mid)) >= 2, f"{len(_beats(mid))} beats")
    )
    beats_before = _beats(mid)

    # The user takes the wheel and acts. Possession is what makes an input an
    # action rather than a new direction: without it, "I put my hand on the
    # suspect's arm" is the user directing the scene, and superseding is correct.
    pipeline.possess(
        project_id=world.project.id, scene_id=world.scene.id, character_id=world.detective.id
    )
    asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            possessed_character_id=world.detective.id,
            user_input="I put my hand on the suspect's arm.",
        )
    )
    after = repository.get_director_plan(db, plan_id)
    result.checks.append(
        Check(
            "plan_survived",
            after.status == PlanStatus.EXECUTING.value,
            f"the plan was {after.status} after the user intervened",
        )
    )
    result.checks.append(
        Check(
            "a_beat_was_skipped",
            any(beat["status"] == "skipped" for beat in _beats(after)),
            "no beat was marked skipped",
        )
    )
    result.checks.append(
        Check(
            "not_everything_invalidated",
            any(
                beat["status"] in {"pending", "active", "completed"}
                for beat in _beats(after)
            ),
            "the interruption discarded the whole plan",
        )
    )
    result.checks.append(
        Check(
            "interruption_recorded",
            "interrupted_by" in (after.decision_json or {}),
            "the interruption was not recorded on the plan",
        )
    )
    result.notes.append(f"beats before: {beats_before}")
    return result


def _beats(row: Any) -> list[dict[str, Any]]:
    payload = row.plan_json or {}
    return list(payload.get("beats") or [])


# --- F/G. Required and optional consequences -------------------------------


def scenario_f_required_consequence(db: Session) -> ScenarioResult:
    """A beat-scoped required consequence must be realised or the turn is rejected.

    The guarantee has to be negative as well as positive: a demand that silently
    failed would let the user believe the plan is progressing when it is not.
    """
    result = ScenarioResult(
        "F", "Required consequence", "a beat requirement is met, or the turn is rejected"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(prose="The witness finally admits the ledger was never signed.", actions=[]),
    )
    plan_turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Press the witness about the ledger, keeping the pressure on",
        )
    )
    plan_id = plan_turn.plan_id
    if not plan_id:
        raise AssertionError("expected a plan for a direction request")
    pipeline.decide_plan(project_id=world.project.id, plan_id=plan_id, decision="approve")
    _set_beat_requirement(db, plan_id, "The witness admits the ledger was never signed")
    result.checks.append(
        Check(
            "requirement_persisted",
            "The witness admits the ledger was never signed"
            in _plan_beat_requirements(db, plan_id),
            "the beat's required consequence was not persisted",
        )
    )
    performed = asyncio.run(pipeline.execute_plan(project_id=world.project.id, plan_id=plan_id))
    result.checks.append(Check("requirement_met", performed.generation_id is not None))
    trace = performer_trace(db, performed.generation_id)
    result.checks.append(
        Check(
            "satisfaction_recorded",
            bool((trace.get("required_consequences") or {}).get("satisfied")),
            "the trace does not record the requirement being satisfied",
        )
    )
    return result


def _plan_beat_requirements(db: Session, plan_id: str) -> list[str]:
    """Every beat-scoped requirement currently on a plan."""
    row = repository.get_director_plan(db, plan_id)
    return [
        requirement
        for beat in (row.plan_json or {}).get("beats", [])
        for requirement in beat.get("required_consequences", [])
    ]


def _set_beat_requirement(db: Session, plan_id: str, requirement: str) -> None:
    """Attach a requirement to a beat through the public plan API.

    Not by writing the JSON column: an in-place mutation of a nested key is not
    always tracked by the ORM, so the edit appears to succeed and silently does
    not. Going through ``decide_plan`` is also the honest test, because that is
    the path a user's plan edit takes.
    """
    pipeline = _editing_pipeline(db)
    row = repository.get_director_plan(db, plan_id)
    beats = [dict(beat) for beat in (row.plan_json or {}).get("beats", [])]
    for beat in beats:
        if beat.get("status") in {"pending", "active"}:
            beat["required_consequences"] = [requirement]
            break
    pipeline.decide_plan(
        project_id=row.project_id,
        plan_id=plan_id,
        decision="edit",
        edits={"beats": beats},
    )
    pipeline.decide_plan(project_id=row.project_id, plan_id=plan_id, decision="approve")


def _editing_pipeline(db: Session) -> NarrativePipeline:
    """A provider-free pipeline, for decisions that never call a model."""
    return NarrativePipeline(db)


def _recorded_prompts(pipeline: NarrativePipeline) -> list[str]:
    """Prompts a recording provider kept, or none if it was not recording."""
    recorder = getattr(pipeline.provider, "prompts", None)
    return list(recorder) if isinstance(recorder, list) else []


def scenario_g_optional_consequence(db: Session) -> ScenarioResult:
    """An optional consequence must never become a mandatory commitment.

    The Performer is offered a possibility. If it realises one, that is a
    legitimate choice; if it does not, nothing has been promised. What must never
    happen is an unchosen option being recorded as if it were agreed.
    """
    result = ScenarioResult(
        "G", "Optional consequence", "unchosen options are not recorded as commitments"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(prose="Ready.", actions=[]),
        turn(prose="The detective says nothing, and watches the witness instead.", actions=[]),
    )
    plan_turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Stage a tense scene, but do not alert anyone",
        )
    )
    result.checks.append(Check("plan_proposed", bool(plan_turn.plan_id)))
    if not plan_turn.plan_id:
        raise AssertionError("expected a plan for a direction request")
    pipeline.decide_plan(
        project_id=world.project.id, plan_id=plan_turn.plan_id, decision="approve"
    )
    _set_optional_consequence(db, plan_turn.plan_id, "The suspect flees the building")
    performed = asyncio.run(
        pipeline.execute_plan(project_id=world.project.id, plan_id=plan_turn.plan_id)
    )
    committed = world.events(db)
    result.checks.append(Check("turn_committed", performed.generation_id is not None))
    result.checks.append(
        Check(
            "no_unchosen_commitment",
            not any("flee" in str(event.payload).casefold() for event in committed),
            "an unchosen optional consequence became a committed event",
        )
    )
    result.checks.append(
        Check(
            "exclusion_respected",
            not any(
                event.event_type == "knowledge_acquired"
                and "alarm" in str(event.payload.get("fact", "")).casefold()
                for event in committed
            ),
            "the user's exclusion was violated by the committed state",
        )
    )
    return result


def _set_optional_consequence(db: Session, plan_id: str, option: str) -> None:
    row = repository.get_director_plan(db, plan_id)
    payload = dict(row.plan_json or {})
    payload["optional_consequences"] = [option]
    row.plan_json = payload
    db.add(row)
    db.commit()


# --- H. Dead and off-stage characters --------------------------------------


def scenario_h_unavailable_characters(db: Session) -> ScenarioResult:
    """Nobody dead or off-stage may act or speak.

    Two different failures, both silent: a dead character acting, and a character
    who is not in the scene being written into it. The second is only prevented by
    scoping the Performer's cast to participants.
    """
    result = ScenarioResult(
        "H", "Dead and off-stage characters", "only available scene characters may act"
    )
    world = World(db)
    world.activate(db)
    # Possession removes the Director from the turn, which is what makes this a
    # test of the *Performer's* guard rather than the plan validator's: a
    # direction request naming a dead character is blocked upstream by
    # ``validate_plan``, and a blocked turn never exercises the Performer at all.
    world.pipeline(db, turn(prose="Ready.")).possess(
        project_id=world.project.id, scene_id=world.scene.id, character_id=world.detective.id
    )
    kill(db, world, world.suspect)
    # Refuse a turn in which the Performer writes the dead suspect and the
    # off-stage librarian.
    pipeline = world.pipeline(
        db,
        turn(
            prose="The suspect rises and walks out. The librarian watches from the doorway.",
            dialogue=[{"character_id": world.suspect.id, "line": "I have somewhere to be."}],
            actions=[
                {"character_id": world.suspect.id, "action": "stands up", "kind": "decision"},
                {"character_id": world.offstage.id, "action": "watches", "kind": "reaction"},
            ],
        ),
        turn(
            prose="The witness turns to the door and puts her shoulder to it.",
            actions=[{"character_id": world.witness.id, "action": "bars the door", "kind": "decision"}],
        ),
        record=True,
    )
    failed = False
    try:
        asyncio.run(
            pipeline.continue_scene(
                project_id=world.project.id,
                scene_id=world.scene.id,
                possessed_character_id=world.detective.id,
                user_input="I watch the suspect leave.",
            )
        )
    except ValueError:
        failed = True
    result.checks.append(
        Check("unavailable_actor_turn_refused", failed, "the turn was accepted")
    )
    result.checks.append(
        Check(
            "nothing_committed",
            not any(
                event.payload.get("character_id") in {world.suspect.id, world.offstage.id}
                # Only events the Performer produced count. The seeded
                # ``character_died`` event legitimately names the suspect, and
                # counting it would make the check fail on its own setup.
                and event.payload.get("generation_id")
                for event in world.events(db)
            ),
            "a dead or off-stage character reached the timeline",
        )
    )
    # And the positive control: an available character does work.
    ok = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            possessed_character_id=world.detective.id,
            user_input="I bar the door behind them.",
        )
    )
    result.checks.append(
        Check("available_actor_permitted", ok.generation_id is not None)
    )
    result.checks.append(
        Check(
            "offstage_never_in_prompt",
            all(
                world.offstage.name not in prompt
                for prompt in _recorded_prompts(pipeline)
            ),
            "the off-stage character was given to the Performer",
        )
    )
    return result


# --- I. Knowledge isolation -------------------------------------------------


def scenario_i_knowledge_isolation(db: Session) -> ScenarioResult:
    """The Phase B leak scenario, repeated at the Performer level.

    The Director's brief was already viewer-scoped. The Performer brief is
    per-actor, so it has to be checked separately: a second surface is a second
    opportunity to leak.
    """
    result = ScenarioResult(
        "I", "Knowledge isolation", "no character's private fact reaches another's prompt"
    )
    world = World(db)
    seed_secret(db, world)
    # The acting character holds a fact of its own. Without this the positive
    # control is vacuous: a viewer who knows nothing would "pass" an isolation
    # check that had in fact removed everything.
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": world.detective.id, "fact": OWN_FACT},
        source="user",
    )
    db.commit()
    world.activate(db)
    pipeline = world.pipeline(
        db,
        turn(prose="The detective turns to the witness.", actions=[]),
        record=True,
    )
    asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            # Pin the viewer. The instruction names the Witness, so the shared
            # actor selector would pick them, and the positive control below is
            # about the *detective's* own fact.
            actor_character_id=world.detective.id,
            user_input="Ask the witness about the ledger.",
        )
    )
    prompts = _recorded_prompts(pipeline)
    result.checks.append(Check("prompt_recorded", bool(prompts), "the provider was never called"))
    result.checks.append(Check("secret_absent", all(SECRET not in prompt for prompt in prompts)))
    result.checks.append(
        Check(
            "performer_prompt_present",
            any(PERFORMER_HEADER in prompt for prompt in prompts),
            "the Performer prompt never reached the model",
        )
    )
    # Positive control. Checking only for absence would pass if the Performer were
    # handed nothing at all, which is blindness rather than isolation.
    result.checks.append(
        Check(
            "viewer_gets_own_knowledge",
            any(OWN_FACT in prompt for prompt in prompts),
            "the acting character was not given the fact it legitimately knows",
        )
    )
    result.checks.append(
        Check(
            "other_knowledge_redacted",
            all(
                # The suspect's fact is not in the prompt even though the suspect
                # is a participant and therefore appears in the cast.
                SECRET not in prompt for prompt in prompts
            ),
            "a non-viewer's private fact reached the prompt",
        )
    )
    result.checks.append(
        Check(
            "cast_is_limited_to_participants",
            all("Librarian" not in prompt for prompt in prompts),
            "a non-participant character was given to the Performer",
        )
    )
    return result


# --- J. Branch execution ----------------------------------------------------


def scenario_j_branch_execution(db: Session) -> ScenarioResult:
    """The same scene on two branches must stay isolated.

    A Performer that leaks across branches is leaking the timeline, which is the
    same defect as leaking character knowledge but with worse consequences.
    """
    result = ScenarioResult(
        "J", "Branch execution", "divergent state stays isolated between branches"
    )
    world = World(db)
    world.activate(db)
    parent = world.pipeline(
        db,
        turn(
            prose="The witness leaves the archive alone.",
            actions=[{"character_id": world.witness.id, "action": "leaves the archive", "kind": "decision"}],
        ),
    )
    asyncio.run(
        parent.continue_scene(
            project_id=world.project.id, scene_id=world.scene.id, user_input="The witness leaves."
        )
    )
    parent_timeline = world.timeline_id

    branch = repository.fork_timeline(
        db,
        source_timeline_id=parent_timeline,
        source_node_id=repository.latest_node(db, parent_timeline).id,
        name="The witness stays",
    )
    db.commit()
    branch_scene = repository.create_scene(
        db,
        project_id=world.project.id,
        timeline_id=branch.id,
        title="Archive Room (witness stays)",
        participant_ids=[world.detective.id, world.witness.id],
    )
    branch_scene.status = SceneStatus.STAGED.value
    db.add(branch_scene)
    db.commit()
    branch_pipeline = NarrativePipeline(
        db,
        provider(
            turn(
                prose="The witness stays, and the archive closes around them both.",
                actions=[
                    {
                        "character_id": world.witness.id,
                        "action": "stays in the archive",
                        "kind": "decision",
                    }
                ],
            )
        ),
    )
    branch_pipeline.approve_scene(
        project_id=world.project.id, scene_id=branch_scene.id
    )
    performed = asyncio.run(
        branch_pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=branch_scene.id,
            user_input="The witness stays.",
        )
    )
    result.checks.append(Check("branch_turn_committed", performed.generation_id is not None))

    parent_events = repository.list_events(db, parent_timeline)
    branch_events = repository.list_events(db, branch.id)
    result.checks.append(
        Check(
            "parent_has_its_own_action",
            any("leaves" in str(event.payload.get("action", "")) for event in parent_events),
            "the parent timeline did not record its own action",
        )
    )
    result.checks.append(
        Check(
            "branch_has_its_own_action",
            any("stays" in str(event.payload.get("action", "")) for event in branch_events),
            "the branch did not record its own action",
        )
    )
    result.checks.append(
        Check(
            "no_cross_timeline_leak",
            not any("stays" in str(event.payload.get("action", "")) for event in parent_events)
            and not any("leaves" in str(event.payload.get("action", "")) for event in branch_events),
            "an action leaked between branches",
        )
    )
    result.checks.append(
        Check(
            "branch_does_not_inherit_the_later_beat",
            not any(
                event.event_type == "knowledge_acquired" for event in branch_events
            ),
            "the branch inherited state it was never given",
        )
    )
    return result


SCENARIOS: dict[str, Any] = {
    "A": scenario_a_single_actor,
    "B": scenario_b_multi_actor_dialogue,
    "C": scenario_c_user_possession,
    "D": scenario_d_character_agency,
    "E": scenario_e_interrupted_plan,
    "F": scenario_f_required_consequence,
    "G": scenario_g_optional_consequence,
    "H": scenario_h_unavailable_characters,
    "I": scenario_i_knowledge_isolation,
    "J": scenario_j_branch_execution,
}


def _run_isolated(scenario: Any) -> ScenarioResult:
    """Each scenario gets its own database.

    Sharing one would let a commitment or a branch created in an earlier scenario
    mask a missing behaviour in a later one, which is exactly the
    cross-contamination a regression report must not contain.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            Base.metadata.create_all(engine)
            return scenario(db)
    finally:
        engine.dispose()


def run(keys: Sequence[str] | None = None) -> PerformerReport:
    wanted = [key.strip().upper() for key in keys] if keys else list(SCENARIOS)
    unknown = [key for key in wanted if key not in SCENARIOS]
    if unknown:
        raise ValueError(f"Unknown Performer scenarios: {unknown}")
    return PerformerReport(
        generated_at=datetime.now(UTC).isoformat(),
        scenarios=[_run_isolated(SCENARIOS[key]) for key in wanted],
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the deterministic Performer evaluation.")
    parser.add_argument("--scenario", action="append", dest="scenarios", default=None)
    parser.add_argument("--out", default="reports/performer_evaluation.json")
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args(argv)
    report = run(args.scenarios)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.show:
        print(report.render())
    print(f"wrote {target}")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
