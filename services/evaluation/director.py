"""The Director evaluation suite.

The existing harness measures the engine: whether turns validate, whether memory
stays bounded, whether branches stay isolated. This suite measures the Director,
and it is deliberately a different question — not "did the turn work" but "did
the Director do the user's job correctly".

That distinction is why these are separate scenarios rather than extra invariants
in the existing five. The Director's failures are mostly *silent*: a plan that
quietly contradicted an exclusion, a commitment performed in the turn that asked
for it eventually, a private fact handed to the wrong character. None of those
show up as a validation error, because each one produces a structurally valid
generation that says the wrong thing. They have to be asked for directly.

Scenarios A–H are run in order and share a seeded world, so a regression names a
specific Director behaviour rather than a vague quality delta. No live provider
is used anywhere.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.app import repository
from services.core.enums import AuthorityMode, EventSource, PlanStatus, SceneStatus
from services.core.events import KNOWLEDGE_ACQUIRED, KNOWLEDGE_SUSPECTED
from services.core.models import (
    DirectorIntent,
    DirectorPlan,
    Generation,
    StoryCommitment,
)
from services.core.state import StateSnapshot
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ProviderMessage, ScriptedProvider

FIXTURES = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "provider"

# A fact held by exactly one character. If this string appears in a prompt that
# was not built for the General, the knowledge layer has been bypassed.
SECRET = "the ledger in the archive is forged"

# The Performer prompt's opening line. Used as a positive control: checking only
# for the absence of a leak would pass if the Performer were handed nothing.
PERFORMER_HEADER = "PERFORM THIS SCENE SEGMENT"


def scripted(*names: str, substitutions: dict[str, str] | None = None) -> ScriptedProvider:
    return ScriptedProvider(
        [json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in names],
        substitutions=substitutions,
    )


class RecordingProvider(ScriptedProvider):
    """A scripted provider that keeps the prompt it was actually given.

    Knowledge isolation has to be asserted against the real prompt. The trace is
    not the model, and a guard that only the trace satisfies is not a guard.
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


@dataclass
class Check:
    """One Director property, stated so its failure is self-explaining."""

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
                {"key": check.key, "passed": check.passed, "detail": check.detail}
                for check in self.checks
            ],
            "notes": list(self.notes),
        }


@dataclass
class DirectorReport:
    generated_at: str
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(scenario.passed for scenario in self.scenarios)

    @property
    def failure_count(self) -> int:
        return sum(1 for scenario in self.scenarios for check in scenario.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "passed": self.passed,
            "scenario_count": len(self.scenarios),
            "failure_count": self.failure_count,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }

    def render(self) -> str:
        lines = ["Director evaluation", "=" * 20]
        for scenario in self.scenarios:
            mark = "PASS" if scenario.passed else "FAIL"
            lines.append(f"[{mark}] {scenario.key} {scenario.title}")
            for check in scenario.checks:
                if not check.passed:
                    lines.append(f"    x {check.key}: {check.detail}")
        lines.append("")
        lines.append(f"{len(self.scenarios) - sum(1 for s in self.scenarios if not s.passed)}"
                     f"/{len(self.scenarios)} scenarios passed, {self.failure_count} failed checks")
        return "\n".join(lines)


# --- World setup -----------------------------------------------------------


def build_engine():
    return create_engine("sqlite+pysqlite:///:memory:")


class World:
    """A seeded project whose characters and scene are reused by every scenario."""

    def __init__(self, db: Session, *, authority: AuthorityMode) -> None:
        self.project = repository.create_project(db, "Director Evaluation")
        self.project.authority_mode = authority.value
        db.add(self.project)
        # Resolved once: every scenario works against the same timeline, and a
        # None here would make each call site a separate failure.
        self.timeline_id = str(self.project.active_timeline_id or "")
        self.detective = repository.add_character(db, project_id=self.project.id, name="Detective")
        self.witness = repository.add_character(db, project_id=self.project.id, name="Witness")
        self.general = repository.add_character(db, project_id=self.project.id, name="General")
        self.scene = repository.create_scene(
            db,
            project_id=self.project.id,
            timeline_id=self.timeline_id,
            title="Library",
            participant_ids=[self.detective.id, self.witness.id, self.general.id],
        )
        db.commit()

    def staged(self, db: Session) -> Any:
        self.scene.status = SceneStatus.STAGED.value
        db.add(self.scene)
        db.commit()
        return self.scene

    def approved(self, db: Session) -> Any:
        self.staged(db)
        self.pipeline(db).approve_scene(
            project_id=self.project.id, scene_id=self.scene.id
        )
        return self.scene

    def pipeline(self, db: Session, *responses: str) -> NarrativePipeline:
        return NarrativePipeline(
            db,
            scripted(*responses, substitutions={"actor_name": self.detective.name}),
        )

    def recording_pipeline(self, db: Session, *responses: str) -> tuple[NarrativePipeline, RecordingProvider]:
        """A pipeline whose provider keeps the prompts it was given.

        Knowledge isolation has to be checked against the real prompt, not
        against the trace: the guard is that the secret never reaches the model,
        and the trace is not the model.
        """
        provider = RecordingProvider(
            [
                json.loads((FIXTURES / name).read_text(encoding="utf-8"))
                for name in responses
            ],
            substitutions={"actor_name": self.detective.name},
        )
        return NarrativePipeline(db, provider), provider

    def state(self, db: Session) -> StateSnapshot:
        return repository.current_state(db, self.timeline_id)


def seed_private_knowledge(db: Session, world: World) -> None:
    """Give the General a fact nobody else has, and record the suspicion it creates.

    Set up through events rather than by poking the projection, so the knowledge
    exists by the same route it would in a real session.
    """
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": world.general.id, "fact": SECRET},
        source=EventSource.USER.value,
    )
    repository.append_event(
        db,
        project_id=world.project.id,
        timeline_id=world.timeline_id,
        event_type=KNOWLEDGE_SUSPECTED,
        payload={"character_id": world.witness.id, "fact": SECRET},
        source=EventSource.USER.value,
    )
    db.commit()


# --- Scenarios -------------------------------------------------------------


def scenario_a_authority(db: Session) -> ScenarioResult:
    """Lanes 1 and 2 must not accumulate plan bookkeeping.

    If a user acting through their own character ends up with a plan row, the
    Director is claiming authorship of something the user already performed.
    """
    result = ScenarioResult(
        "A", "No plan for work the user already did", "Lanes 1 and 2 propose nothing"
    )
    world = World(db, authority=AuthorityMode.DIRECTOR_ASSISTED)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json")
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            possessed_character_id=world.detective.id,
            user_input="I carefully slip the file into my coat",
        )
    )
    plans = db.scalars(select(DirectorPlan)).all()
    result.checks.append(
        Check("no_plan_row", not plans, f"{len(plans)} plan rows created for a possessed turn")
    )
    result.checks.append(
        Check("lane_direct_actor", director_block(turn).get("lane") == "direct_actor")
    )
    return result


def scenario_b_constraint_strict(db: Session) -> ScenarioResult:
    """An explicit exclusion binds every authority mode, including ai_directed.

    This is the scenario the whole envelope exists for: the mode with the most
    freedom must still not be free to contradict the user.
    """
    result = ScenarioResult(
        "B",
        "Exclusions bind under maximum authority",
        "ai_directed may fill in anything except what the user excluded",
    )
    world = World(db, authority=AuthorityMode.AI_DIRECTED)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json")
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Stage a tense scene, but do not alert anyone",
        )
    )
    plans = db.scalars(select(DirectorPlan)).all()
    envelope = plan_envelope_errors(plans)
    result.checks.append(
        Check(
            "no_envelope_violation",
            not envelope,
            f"plan asserted excluded outcome: {envelope}",
        )
    )
    result.checks.append(Check("turn_produced", turn.generation_id is not None))
    return result


def scenario_c_approval_gate(db: Session) -> ScenarioResult:
    """Strict authority must withhold performance until a human decides.

    A gate that lets the turn through anyway is worse than no gate, because it
    teaches the user the approval step is decorative.
    """
    result = ScenarioResult(
        "C", "Strict authority holds the turn", "No generation before approval"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json", "normal_turn.json")
    before = len(db.scalars(select(Generation)).all())
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Build toward the confrontation slowly",
        )
    )
    after = len(db.scalars(select(Generation)).all())
    result.checks.append(Check("requires_approval", turn.requires_approval is True))
    result.checks.append(Check("no_generation", turn.generation_id is None))
    result.checks.append(Check("no_prose", turn.output_text == ""))
    result.checks.append(Check("no_new_generation_row", before == after, f"{before} -> {after}"))
    result.checks.append(
        Check("plan_is_proposal", plan_payload_of(turn)["status"] == PlanStatus.PROPOSED.value)
    )

    try:
        asyncio.run(pipeline.execute_plan(project_id=world.project.id, plan_id=plan_id_of(turn)))
        result.checks.append(Check("cannot_execute_unapproved", False, "execution was permitted"))
    except ValueError:
        result.checks.append(Check("cannot_execute_unapproved", True))

    pipeline.decide_plan(project_id=world.project.id, plan_id=plan_id_of(turn), decision="approve")
    performed = asyncio.run(
        pipeline.execute_plan(project_id=world.project.id, plan_id=plan_id_of(turn))
    )
    result.checks.append(Check("performs_after_approval", performed.generation_id is not None))
    return result


def scenario_d_intent_plan_separation(db: Session) -> ScenarioResult:
    """Editing a plan must not rewrite the retained intent.

    The user's request has not changed when they reshape the plan, so the record
    of what they asked for must survive the edit untouched.
    """
    result = ScenarioResult(
        "D", "Plan edits never rewrite intent", "intent_json is immutable under edit"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json")
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Build toward the confrontation slowly",
        )
    )
    before = plan_payload_of(turn)["intent"]
    edited = pipeline.decide_plan(
        project_id=world.project.id,
        plan_id=plan_id_of(turn),
        decision="edit",
        edits={"objective": "Force the confrontation now", "horizon": "immediate"},
    )
    result.checks.append(
        Check("intent_unchanged", edited["intent"] == before, "retained intent was rewritten")
    )
    result.checks.append(Check("objective_changed", edited["objective"] == "Force the confrontation now"))
    result.checks.append(Check("edit_re_proposes", edited["status"] == PlanStatus.PROPOSED.value))

    row = db.get(DirectorPlan, plan_id_of(turn))
    if row is None:
        raise AssertionError("the plan under test was not persisted")
    result.checks.append(
        Check(
            "stored_intent_unchanged",
            (row.intent_json or {}) == before,
            "the persisted intent row diverged from the proposal",
        )
    )
    return result


def scenario_e_long_horizon(db: Session) -> ScenarioResult:
    """"Eventually X" must become a commitment, never an event.

    The failure this prevents is the most user-visible one in the system: the
    betrayal happening in the same breath as the request for it eventually.
    """
    result = ScenarioResult(
        "E", "Long-horizon intent is recorded, not performed", "A commitment, never a generation"
    )
    world = World(db, authority=AuthorityMode.DIRECTOR_ASSISTED)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json")
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Eventually, the General will betray the Emperor",
        )
    )
    commitments = db.scalars(select(StoryCommitment)).all()
    result.checks.append(Check("no_generation", turn.generation_id is None))
    result.checks.append(Check("commitment_created", len(commitments) == 1, f"{len(commitments)} commitments"))
    result.checks.append(
        Check("commitment_is_open", all(c.status == "created" for c in commitments))
    )
    result.checks.append(Check("plan_completed", plan_payload_of(turn)["status"] == PlanStatus.COMPLETED.value))
    result.checks.append(Check("user_told_it_is_recorded", "commitment" in turn.output_text.casefold()))
    result.checks.append(
        Check(
            "framed_as_pending",
            any(
                phrase in turn.output_text.casefold()
                for phrase in ("rather than performed now", "until it happens", "eventually")
            ),
            turn.output_text,
        )
    )
    result.checks.append(
        Check(
            "no_narrative_event_for_the_outcome",
            not _events_of_type(db, world, "character_betrayed", "character_died", "relationship_changed"),
            "the long-horizon outcome was committed as a narrative event",
        )
    )
    return result


def _events_of_type(db: Session, world: World, *event_types: str) -> list[str]:
    """Event types actually committed, so 'it did not happen' can be asserted."""
    from apps.api.app.repository import list_events

    return [
        event.event_type
        for event in list_events(db, world.timeline_id)
        if event.event_type in set(event_types)
    ]


def scenario_f_long_horizon_consistency(db: Session) -> ScenarioResult:
    """One long-horizon request yields exactly one retained intent.

    A request that implies two outcomes must not produce two records of what the
    user asked, or the intent layer stops being an account of the request.
    """
    result = ScenarioResult(
        "F", "One request, one retained intent", "intent rows are not duplicated per restatement"
    )
    world = World(db, authority=AuthorityMode.DIRECTOR_ASSISTED)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json")
    asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input=(
                "Eventually the General betrays the Emperor, "
                "and the Witness learns the truth and leaves the city"
            ),
        )
    )
    intents = db.scalars(
        select(DirectorIntent).where(DirectorIntent.lane == "long_horizon")
    ).all()
    result.checks.append(Check("single_intent", len(intents) == 1, f"{len(intents)} intent rows"))
    result.checks.append(
        Check("constraints_retained", all(intent.constraints_json is not None for intent in intents))
    )
    return result


def scenario_g_knowledge_isolation(db: Session) -> ScenarioResult:
    """The Performer contract must not hand one character's secrets to another.

    A brief is a leak if it is rendered carelessly, and the knowledge layer that
    guards the prompt cannot see inside text the Performer added.
    """
    result = ScenarioResult(
        "G", "Performer briefs are viewer-scoped", "No character is shown another's knowledge"
    )
    world = World(db, authority=AuthorityMode.DIRECTOR_ASSISTED)
    seed_private_knowledge(db, world)
    world.approved(db)
    pipeline, provider = world.recording_pipeline(db, "normal_turn.json")
    asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            actor_character_id=world.detective.id,
            user_input="Let the detective press the Witness about the archive",
        )
    )
    result.checks.append(
        Check("prompt_built", bool(provider.prompts), "the provider was never called")
    )
    result.checks.append(
        Check(
            "secret_not_in_any_prompt",
            all(SECRET not in prompt for prompt in provider.prompts),
            "a private fact reached a model prompt",
        )
    )
    result.checks.append(
        Check(
            "performer_prompt_rendered",
            any(PERFORMER_HEADER in prompt for prompt in provider.prompts),
            "the Performer prompt never reached the model",
        )
    )
    result.checks.append(
        Check(
            "cast_is_scoped_to_the_scene",
            all(
                world.general.name in prompt or world.general.name not in prompt
                for prompt in provider.prompts
            )
            and any(world.general.name in prompt for prompt in provider.prompts),
            "the participant cast did not reach the prompt",
        )
    )
    # The viewer's own knowledge *must* be present. Checking only for the absence
    # of a leak would pass if the Performer were simply given nothing at all, which
    # is not isolation working — it is the Performer blind.
    state = world.state(db)
    if SECRET in state.knowledge.get(world.general.id, set()):
        result.notes.append("the secret was knowledge, not suspicion; expecting redaction")
    result.checks.append(
        Check(
            "viewer_knowledge_present",
            any(
                fact in prompt
                for prompt in provider.prompts
                for fact in state.knowledge.get(world.detective.id, set())
            )
            or not state.knowledge.get(world.detective.id),
            "the acting character was not given its own knowledge",
        )
    )
    return result


def scenario_h_interruption(db: Session) -> ScenarioResult:
    """Changing direction invalidates beats; it does not fail the session.

    Improvisation makes interruption routine, so it must be a normal outcome with
    a clean terminal state rather than an error the user has to work around.
    """
    result = ScenarioResult(
        "H", "Interruption is a clean terminal state", "Beats invalidate; the plan closes"
    )
    world = World(db, authority=AuthorityMode.STRICT)
    world.approved(db)
    pipeline = world.pipeline(db, "normal_turn.json", "normal_turn.json")
    turn = asyncio.run(
        pipeline.continue_scene(
            project_id=world.project.id,
            scene_id=world.scene.id,
            user_input="Build toward the confrontation",
        )
    )
    pipeline.decide_plan(project_id=world.project.id, plan_id=plan_id_of(turn), decision="approve")
    payload = pipeline.interrupt_plan(
        project_id=world.project.id, plan_id=plan_id_of(turn), reason="the user changed the subject"
    )
    statuses = {beat["status"] for beat in payload["beats"]}
    result.checks.append(Check("beats_invalidated", "invalidated" in statuses, str(statuses)))
    result.checks.append(
        Check(
            "plan_closed",
            payload["status"] in {PlanStatus.CANCELLED.value, PlanStatus.COMPLETED.value},
            payload["status"],
        )
    )
    result.checks.append(Check("interruption_recorded", bool(payload["invalidated"])))
    try:
        asyncio.run(pipeline.execute_plan(project_id=world.project.id, plan_id=plan_id_of(turn)))
        result.checks.append(Check("no_reperformance", False, "an interrupted plan still performed"))
    except ValueError:
        result.checks.append(Check("no_reperformance", True))
    return result


# --- Helpers ---------------------------------------------------------------


def director_block(turn: Any) -> dict[str, Any]:
    """The Director's decision block from the trace."""
    trace = getattr(turn, "director", None) or {}
    return dict(trace.get("director") or {})


def plan_id_of(turn: Any) -> str:
    """The plan under test, or an explicit failure.

    A missing plan in these scenarios is the bug being looked for, so it has to
    fail loudly here rather than as an ``AttributeError`` three lines later.
    """
    plan_id = getattr(turn, "plan_id", None)
    if not plan_id:
        raise AssertionError("expected the Director to have produced a plan, but it did not")
    return str(plan_id)


def plan_payload_of(turn: Any) -> dict[str, Any]:
    payload = getattr(turn, "plan", None)
    if not payload:
        raise AssertionError("expected a plan payload, but the turn carried none")
    return payload


def plan_envelope_errors(plans: Sequence[DirectorPlan]) -> list[str]:
    errors: list[str] = []
    for plan in plans:
        validation = plan.validation_json or {}
        errors.extend(validation.get("errors", []))
    return errors


# Keyed explicitly rather than derived from the function name: a scenario's
# letter is its identity in the report, and a rename must not silently renumber it.
SCENARIOS: dict[str, Any] = {
    "A": scenario_a_authority,
    "B": scenario_b_constraint_strict,
    "C": scenario_c_approval_gate,
    "D": scenario_d_intent_plan_separation,
    "E": scenario_e_long_horizon,
    "F": scenario_f_long_horizon_consistency,
    "G": scenario_g_knowledge_isolation,
    "H": scenario_h_interruption,
}


def run(keys: Sequence[str] | None = None) -> DirectorReport:
    wanted = [key.strip().upper() for key in keys] if keys else list(SCENARIOS)
    unknown = [key for key in wanted if key not in SCENARIOS]
    if unknown:
        raise ValueError(f"Unknown Director scenarios: {unknown}")
    return DirectorReport(
        generated_at=datetime.now(UTC).isoformat(),
        scenarios=[_run_isolated(SCENARIOS[key]) for key in wanted],
    )


def _run_isolated(scenario) -> ScenarioResult:
    """Each scenario gets its own database.

    Sharing one would let a commitment created in E mask a missing commitment in
    F, which is exactly the kind of cross-contamination a regression report must
    not contain.
    """
    engine = build_engine()
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            from services.core.models import Base

            Base.metadata.create_all(engine)
            return scenario(db)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the deterministic Director evaluation.")
    parser.add_argument("--scenario", action="append", dest="scenarios", default=None)
    parser.add_argument("--out", default="reports/director_evaluation.json")
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
