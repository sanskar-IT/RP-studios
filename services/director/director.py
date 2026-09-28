"""Director orchestration: build, validate, decide, persist, and execute plans.

This is the layer the narrative pipeline calls. It owns the ordering that the
milestone's pipeline diagram specifies, and it owns the transactional boundary:

```text
User Input
    -> DirectorIntent          (transient interpretation; recorded once)
    -> DirectorPlan            (draft; transient until approved)
    -> Performance             (the existing generation path)
    -> Proposed Events
    -> Validation
    -> Committed State
```

The Director runs *before* the generation call, so a plan that cannot be
performed, or that violates the user's constraint envelope, stops the turn with
nothing committed. That ordering is what makes "regenerate / edit / return to
user" a real recovery rather than a repair of half-applied work.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from apps.api.app import repository
from services.core.enums import (
    AuthorityMode,
    BeatStatus,
    Horizon,
    Lane,
    PlanStatus,
)
from services.core.models import Character, DirectorPlan
from services.core.state import StateSnapshot
from services.director.canon import CanonConflict, detect_canon_conflict
from services.director.envelope import (
    PlanValidation,
    validate_plan,
)
from services.director.envelope import (
    requires_approval as approval_required,
)
from services.director.plan import DirectorPlanDraft, DirectorPlanView
from services.director.refine import RefinementResult, refine_intent
from services.director.router import route_intent_for_lane


def horizon_for(lane: Lane, intent: Any) -> str:
    """The narrative horizon the Director plans against.

    Long-horizon intent is always long-term. Everything else follows the
    request's own urgency, defaulting to near-term. Forward planning stays
    commitment-driven: the Director never plans a chapter ahead of what the
    commitment says it is working toward.
    """
    if lane is Lane.LONG_HORIZON:
        return Horizon.LONG_TERM.value
    if getattr(intent, "urgency", "") == "immediate":
        return Horizon.IMMEDIATE.value
    return Horizon.NEAR_TERM.value



@dataclass
class DirectorDecision:
    """The outcome of running the Director for one turn."""

    view: DirectorPlanView
    intent_payload: dict[str, Any] = field(default_factory=dict)
    validation: PlanValidation | None = None
    refinement: RefinementResult | None = None
    canon_conflict: CanonConflict | None = None
    plan_row: DirectorPlan | None = None
    requires_approval: bool = False
    blocked: bool = False
    block_reason: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    performer_block: str = ""
    # Long-horizon intent produces commitments, never an executed outcome.
    commitments_created: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def committed_summary(self) -> str:
        """A one-line, user-facing account of what a long-horizon request did."""
        if not self.commitments_created:
            return ""
        goals = ", ".join(row["description"] for _id, row in self.commitments_created)
        return (
            f"Recorded as a long-term commitment rather than performed now: {goals}. "
            "It will surface as foreshadowing until it happens or is cancelled."
        )

    @property
    def executable(self) -> bool:
        """Whether the turn may proceed to performance."""
        return not self.blocked and not self.requires_approval

    def trace(self) -> dict[str, Any]:
        """The trace block. Structured decisions only; no chain of thought."""
        current_beat = self.view.plan.active_beat()
        payload: dict[str, Any] = dict(self.intent_payload)
        payload.update(
            {
                "interpretation_source": (
                    self.refinement.source if self.refinement else "heuristic"
                ),
                "plan_id": self.view.plan_id,
                "plan_status": self.view.status.value,
                "plan_generation_ms": round(self.timings.get("plan_ms", 0.0), 3),
                "plan_validation": self.validation.to_dict() if self.validation else {},
                "requires_approval": self.requires_approval,
                "blocked": self.blocked,
                "block_reason": self.block_reason,
                "authority_mode": self.view.plan.authority_mode.value,
                "horizon": self.view.plan.horizon.value,
                "canon_conflict": self.canon_conflict.to_dict() if self.canon_conflict else None,
                "beats": [beat.to_dict() for beat in self.view.plan.beats],
                "current_beat": current_beat.to_dict() if current_beat is not None else None,
                "required_consequences": list(self.view.plan.required_consequences),
                "likely_consequences": list(self.view.plan.likely_consequences),
                "optional_consequences": list(self.view.plan.optional_consequences),
                "assumptions": list(self.view.plan.assumptions),
                "refinement": self.refinement.to_dict() if self.refinement else {},
            }
        )
        return payload


def resolve_authority(db: Session, *, project_id: str, scene_id: str | None) -> AuthorityMode:
    return repository.resolve_authority(db, project_id=project_id, scene_id=scene_id)


def _sources(db: Session, project_id: str) -> list[tuple[str, str]]:
    from sqlalchemy import select

    from services.core.models import Source

    rows = db.execute(
        select(Source.title, Source.content).where(Source.project_id == project_id)
    ).all()
    return [(str(title or "Source material"), str(content or "")) for title, content in rows]


PRIVATE_MARKER = "private to them"


def _character_briefs(
    characters: Sequence[Character],
    state: StateSnapshot,
    scene_location: str,
    viewer_character_id: str | None = None,
) -> list[dict[str, Any]]:
    """Per-character agency briefs, derived from state and scoped to a viewer.

    The Director establishes the situation; each character decides its own
    reaction from what it knows, wants, and currently holds. Nothing here is
    stored — every brief is recomputed from the projection each turn, so a brief
    can never go stale.

    **Knowledge is viewer-scoped.** The acting character sees its own certainties
    and suspicions; every other character's beliefs are redacted. Showing a
    character's private knowledge to another is the exact leak the knowledge
    isolation layer exists to prevent, and a brief is still a leak.
    """
    briefs: list[dict[str, Any]] = []
    for character in characters:
        character_id = character.id
        record = state.characters.get(character_id, {})
        is_viewer = character_id == viewer_character_id
        certain = sorted(state.knowledge.get(character_id, set())) if is_viewer else []
        suspected = sorted(state.suspicions.get(character_id, set())) if is_viewer else []
        if is_viewer:
            goal = _goal_for(record, certain, suspected)
        else:
            goal = _observable_goal_for(record, character_id in state.dead)
        relationships = [
            {
                "with": value.get("target_character_id")
                if value.get("source_character_id") == character_id
                else value.get("source_character_id"),
                "type": value.get("relationship_type", "unknown"),
                "strength": value.get("strength", 0.0),
            }
            for value in state.relationships.values()
            if value.get("source_character_id") == character_id
            or value.get("target_character_id") == character_id
        ]
        briefs.append(
            {
                "character_id": character_id,
                "name": character.name,
                "is_viewer": is_viewer,
                "alive": character_id not in state.dead,
                "location_id": record.get("location_id", ""),
                "in_scene_location": str(record.get("location_id", "")) == scene_location,
                "goal": goal,
                "knows": certain,
                "suspects": suspected,
                "injuries": sorted(state.injuries.get(character_id, set())),
                "carries": sorted(state.items.get(character_id, set())),
                "relationships": relationships,
                "last_action": str(record.get("last_action", ""))[:160],
            }
        )
    return briefs


def _observable_goal_for(record: dict[str, Any], dead: bool) -> str:
    """A goal readable from what an observer can see.

    Deliberately weaker than :func:`_goal_for`: without the character's own
    knowledge, the only defensible read of their intent is what they just did
    and what they are visibly carrying or suffering.
    """
    if dead:
        return "dead; take no action"
    if record.get("last_action"):
        return f"continue: {str(record['last_action'])[:80]}"
    return "respond to what happens next, from their own perspective"


# Public alias: the pipeline builds the same viewer-scoped briefs the Director
# does when it re-checks an approved plan.
character_briefs = _character_briefs


def _goal_for(record: dict[str, Any], certain: list[str], suspected: list[str]) -> str:
    """A short, derived read of what this character is plausibly after.

    Deliberately weak: it reads the projection, not a stored motive. A character
    with a recent action is pursuing it; a character holding something has a
    reason to keep holding it; a character who suspects something is chasing it.
    """
    if record.get("alive") is False:
        return "dead; take no action"
    if suspected:
        return f"confirm or drop the suspicion: {suspected[0]}"
    if certain:
        return f"act on what is known: {certain[-1]}"
    if record.get("last_action"):
        return f"continue: {str(record['last_action'])[:80]}"
    return "observe and respond to the current situation"


def render_performer_contract(
    plan: DirectorPlanDraft,
    *,
    briefs: Sequence[dict[str, Any]],
    scene_objective: str = "",
) -> str:
    """The Director-to-Performer handoff, rendered for the prompt.

    Everything here is guidance. The Performer decides how the beat is realised;
    the State Engine decides what became true.
    """
    beat = plan.active_beat()
    lines = [
        "DIRECTOR DIRECTION (guidance, not instruction to obey literally)",
        f"Scene objective: {scene_objective or plan.objective or 'continue the scene'}",
        f"Current beat: {beat.description if beat else 'improvise from the situation'}",
        f"Desired narrative direction: {plan.desired_direction or plan.objective}",
        f"Required consequences: {'; '.join(plan.required_consequences) or 'none stated'}",
        f"Likely consequences: {'; '.join(plan.likely_consequences) or 'none stated'}",
        f"Optional consequences (do not force): {'; '.join(plan.optional_consequences) or 'none'}",
        f"Constraints that must hold: {'; '.join(plan.constraints) or 'none stated'}",
        f"Excluded by the user: {'; '.join(plan.exclusions) or 'none stated'}",
        "Characters decide their own reactions. Do not make everyone respond identically.",
    ]
    for brief in briefs:
        if brief["is_viewer"]:
            private = "knows={knows}; suspects={suspects}".format(
                knows=", ".join(brief["knows"]) or "nothing established",
                suspects=", ".join(brief["suspects"]) or "nothing",
            )
        else:
            private = f"{PRIVATE_MARKER} ({brief['name']} reasons from their own knowledge)"
        lines.append(
            "  - {name}: goal={goal}; {private}; injuries={injuries}; carries={carries}".format(
                name=brief["name"],
                goal=brief["goal"],
                private=private,
                injuries=", ".join(brief["injuries"]) or "none",
                carries=", ".join(brief["carries"]) or "nothing",
            )
        )
    lines.append(
        "Combine, skip, or adapt the beat if the scene has moved on. "
        "Only assert state you can support; the engine validates everything."
    )
    return "\n".join(lines)


async def direct_turn(
    db: Session,
    *,
    project_id: str,
    scene_id: str,
    scene_objective: str = "",
    timeline_id: str = "",
    text: str,
    mode: str = "auto",
    horizon: str = "short",
    possessed_character_id: str | None = None,
    provider: Any,
    state: StateSnapshot,
    characters: Sequence[Character],
    participant_ids: set[str],
    known_locations: Sequence[str] = (),
    scene_location: str = "",
    viewer_character_id: str | None = None,
    refine: bool = True,
) -> DirectorDecision:
    """Run the Director for one turn and decide whether performance may proceed."""
    from time import perf_counter

    started = perf_counter()
    director_commitments: list[tuple[str, dict[str, Any]]] = []
    intent = route_intent_for_lane(
        text,
        mode=mode,
        participant_names=[character.name for character in characters],
        possessed_character_id=possessed_character_id,
        horizon=horizon,
    )
    lane = Lane(intent.lane) if intent.lane else Lane.DIRECTION
    authority = resolve_authority(db, project_id=project_id, scene_id=scene_id)
    intent.horizon = horizon

    if lane in {Lane.DIRECT_ACTOR, Lane.SIMPLE_WORLD}:
        # Lanes 1 and 2 need no plan; the intent is still recorded in the trace.
        view = DirectorPlanView(
            plan=_no_plan(lane, intent, authority), requires_approval=False
        )
        return DirectorDecision(view=view, intent_payload=intent.to_dict())

    plan_started = perf_counter()
    if refine:
        refinement = await refine_intent(
            provider,
            intent=intent,
            lane=lane,
            participants=[character.id for character in characters],
            authority=authority,
        )
        plan = refinement.plan
    else:
        from services.director.plan import build_heuristic_plan

        refinement = None
        plan = build_heuristic_plan(
            intent,
            lane=lane,
            participants=[character.id for character in characters],
            authority=authority,
        )
    plan_ms = (perf_counter() - plan_started) * 1000.0

    conflict = detect_canon_conflict(
        requested_text=text or intent.objective,
        sources=_sources(db, project_id),
        state=state,
        canon_preference=intent.canon_preference,
    )
    if conflict is not None:
        plan.canon_conflict = conflict.to_dict()

    validation = validate_plan(
        plan,
        state=state,
        participant_ids=participant_ids,
        known_locations=known_locations,
        known_character_names=[character.name for character in characters],
        intent_constraints=intent.constraints,
        intent_exclusions=intent.exclusions,
    )
    needs_approval = approval_required(plan, authority=authority)
    if lane is Lane.LONG_HORIZON:
        # A long-horizon request is not performed, so there is nothing for a human
        # to watch and approve before it happens. Gating it would ask the user to
        # approve a promise rather than an action, and would leave the request
        # unrecorded until they did. Only an unresolved canon conflict still needs
        # a decision, because that changes what the commitment would mean.
        needs_approval = bool(
            conflict is not None and conflict.resolution in {None, "", "ask"}
        )
    plan.requires_approval = needs_approval

    blocked = not validation.valid
    block_reason = "; ".join(validation.errors) if blocked else ""
    if lane is Lane.LONG_HORIZON and not blocked:
        # Long-horizon intent: record the outcome as a commitment and stop. The
        # turn must not perform the betrayal, the deterioration, or the discovery
        # that the user asked for eventually.
        #
        # One DirectorIntent backs every commitment from this request, so the
        # retained intent is a single record of what the user actually asked for
        # rather than one row per restatement of it.
        intent_row = _record_intent_row(
            db,
            project_id=project_id,
            scene_id=scene_id,
            intent=intent,
            plan=plan,
            source=refinement.source if refinement else "heuristic",
        )
        view_intent_id = intent_row.id
        resolved_timeline = (
            timeline_id or repository.get_project(db, project_id).active_timeline_id or ""
        )
        for description in plan.commitments or ([plan.objective] if plan.objective else []):
            director_commitments.append(
                _create_commitment(
                    db,
                    project_id=project_id,
                    timeline_id=resolved_timeline,
                    intent_id=view_intent_id,
                    description=description,
                    horizon=plan.horizon,
                )
            )
    else:
        view_intent_id = None
    view = DirectorPlanView(
        plan=plan,
        status=PlanStatus.PROPOSED,
        requires_approval=needs_approval,
        validation=validation.to_dict(),
    )
    view.intent_id = view_intent_id
    decision = DirectorDecision(
        view=view,
        intent_payload=intent.to_dict(),
        validation=validation,
        refinement=refinement,
        canon_conflict=conflict,
        requires_approval=needs_approval,
        blocked=blocked,
        block_reason=block_reason,
        commitments_created=director_commitments,
        timings={"plan_ms": plan_ms, "total_ms": (perf_counter() - started) * 1000.0},
    )
    if not blocked and not director_commitments:
        decision.performer_block = render_performer_contract(
            plan,
            briefs=_character_briefs(
                characters, state, scene_location, viewer_character_id
            ),
            scene_objective=scene_objective,
        )
    return decision


def _record_intent_row(
    db: Session,
    *,
    project_id: str,
    scene_id: str | None,
    intent: Any,
    plan: DirectorPlanDraft,
    source: str,
) -> Any:
    """Persist the retained intent once, for a request that creates commitments."""
    from services.core.enums import IntentType
    from services.core.models import DirectorIntent

    payload = intent.to_dict()
    row = DirectorIntent(
        project_id=project_id,
        scene_id=scene_id,
        intent_type=IntentType.STORY_COMMITMENT.value,
        text=plan.objective or intent.objective,
        goal=plan.objective or intent.objective,
        horizon=plan.horizon.value,
        lane=plan.lane.value,
        specification=str(payload.get("specification", "")),
        consistency=str(payload.get("consistency", "")),
        confidence=float(payload.get("confidence", 0.0)),
        objective=str(payload.get("objective", "")),
        desired_outcome=str(payload.get("desired_outcome", "")),
        constraints_json=list(payload.get("constraints", [])),
        exclusions_json=list(payload.get("exclusions", [])),
        tone=str(payload.get("tone", "")),
        urgency=str(payload.get("urgency", "")),
        canon_preference=str(payload.get("canon_preference", "")),
        user_control_level=str(payload.get("user_control_level", "")),
        interpretation_json=payload,
        interpretation_source=source,
    )
    db.add(row)
    db.flush()
    return row


def _create_commitment(
    db: Session,
    *,
    project_id: str,
    timeline_id: str,
    intent_id: str,
    description: str,
    horizon: Horizon,
) -> tuple[str, dict[str, Any]]:
    """Reuse the existing commitment lifecycle for long-horizon intent.

    Long-horizon intent is a commitment, not a new concept: the same rows, the
    same statuses, and the same branch scoping that every other commitment uses.
    Forward planning stays commitment-driven rather than becoming its own system.
    """
    from services.core.enums import CommitmentStatus
    from services.core.models import StoryCommitment

    commitment = StoryCommitment(
        project_id=project_id,
        intent_id=intent_id,
        timeline_id=timeline_id,
        description=description,
        status=CommitmentStatus.CREATED.value,
        priority=1,
        created_sequence=repository.latest_node(db, timeline_id).sequence,
    )
    db.add(commitment)
    db.flush()
    return intent_id, {
        "id": commitment.id,
        "description": commitment.description,
        "status": commitment.status,
        "horizon": horizon.value,
        "intent_id": intent_id,
    }


def _no_plan(lane: Lane, intent: Any, authority: AuthorityMode) -> DirectorPlanDraft:
    from services.director.plan import DirectorPlanDraft

    return DirectorPlanDraft(
        objective=intent.objective,
        summary="Direct execution; no Director plan required.",
        lane=lane,
        horizon=Horizon.IMMEDIATE,
        authority_mode=authority,
        interpretation_source="not_required",
        intent=intent,
    )


def persist_plan(
    db: Session,
    decision: DirectorDecision,
    *,
    project_id: str,
    timeline_id: str,
    scene_id: str,
    intent_id: str | None = None,
    generation_id: str | None = None,
    checkpoint_node_id: str | None = None,
) -> DirectorPlan:
    """Persist a plan artifact.

    ``proposed`` rows are proposals awaiting a decision: no events, no
    projection. Only ``approved`` onward is a committed narrative artifact.
    """
    plan = decision.view.plan
    status = PlanStatus.PROPOSED if decision.requires_approval or decision.blocked else PlanStatus.APPROVED
    row = repository.add_director_plan(
        db,
        project_id=project_id,
        timeline_id=timeline_id,
        scene_id=scene_id,
        intent_id=intent_id,
        generation_id=generation_id,
        checkpoint_node_id=checkpoint_node_id,
        status=status.value,
        lane=plan.lane.value,
        horizon=plan.horizon.value,
        authority_mode=plan.authority_mode.value,
        objective=plan.objective,
        summary=plan.summary,
        required_approval=decision.requires_approval,
        current_beat_index=0,
        created_sequence=repository.latest_node(db, timeline_id).sequence,
        intent_json=plan.intent.to_dict(),
        plan_json=plan.to_dict(),
        validation_json=decision.validation.to_dict() if decision.validation else {},
        canon_conflict_json=plan.canon_conflict,
        decision_json={},
    )
    decision.view.plan_id = row.id
    decision.view.status = status
    return row


def advance_beats(
    plan: DirectorPlanDraft, *, upto: int = 1, status: BeatStatus = BeatStatus.COMPLETED
) -> list[str]:
    """Resolve the beats that were performed, and return their descriptions.

    Resolution is keyed on the beat that was *active*, not on position. A
    positional slice advances the first N beats regardless of which one the turn
    realised, so a plan could never get past its second beat: the first was
    already terminal and the slice skipped over the one actually performed.

    ``status`` records what happened to it. A beat the Performer never realised is
    ``skipped``, not an error — the scene moved on, which is improvisation working
    rather than the engine breaking.
    """
    resolved: list[str] = []
    for beat in plan.beats:
        if len(resolved) >= upto:
            break
        if beat.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}:
            beat.status = status
            resolved.append(beat.description)
    return resolved


def plan_from_row(row: DirectorPlan) -> DirectorPlanDraft:
    payload = dict(row.plan_json or {})
    if not payload:
        payload = {"objective": row.objective, "summary": row.summary, "intent": dict(row.intent_json or {})}
    return DirectorPlanDraft.from_dict(payload)


# A plan may only move forward through the lifecycle, or back to ``proposed``
# when the user edits it. Approving a completed plan, or reviving a cancelled
# one, is a bug rather than a workflow.
PLAN_TRANSITIONS: dict[PlanStatus, set[PlanStatus]] = {
    PlanStatus.PROPOSED: {PlanStatus.APPROVED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED},
    PlanStatus.APPROVED: {PlanStatus.EXECUTING, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED},
    PlanStatus.EXECUTING: {PlanStatus.COMPLETED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED},
    PlanStatus.COMPLETED: set(),
    PlanStatus.CANCELLED: set(),
    PlanStatus.SUPERSEDED: set(),
}


def set_plan_status(db: Session, row: DirectorPlan, status: PlanStatus) -> DirectorPlan:
    current = PlanStatus(row.status)
    if status is not current and status not in PLAN_TRANSITIONS[current]:
        raise ValueError(f"Plan cannot move from {current.value} to {status.value}")
    row.status = status.value
    db.add(row)
    db.flush()
    return row
