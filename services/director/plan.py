"""DirectorPlan: the proposed execution strategy.

A plan answers "how are we going to accomplish this?", and it is a separate
artifact from the Intent that answers "what does the user want?". The plan
carries a *retained copy* of the intent it was built from so an edit to the
plan can never silently rewrite the request.

Two properties matter more than the field list:

**Beats are guidance, not a screenplay queue.** Each beat has a status, and the
Performer may combine, skip, reorder, or adapt them. A beat that stops making
sense is ``skipped`` or ``invalidated``; that is normal, not a failure. The
State Engine decides what actually happened.

**Number of beats follows complexity.** A one-line action gets one beat. A
murder with a witness and a reaction gets several. Nothing forces a fixed shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import (
    AuthorityMode,
    BeatStatus,
    Horizon,
    Lane,
    PlanStatus,
)
from services.director.intent import Consistency, Intent, Specification

# Upper bound on heuristic beats. A plan longer than this is a plan the Director
# has stopped improvising and started sequencing, which is out of scope.
MAX_HEURISTIC_BEATS = 6


@dataclass
class Beat:
    """One flexible planning unit toward the plan objective."""

    description: str
    status: BeatStatus = BeatStatus.PENDING
    participants: list[str] = field(default_factory=list)
    required: bool = False
    guidance: bool = True
    requires_knowledge: str = ""
    note: str = ""
    # Consequences this *beat* must produce, as distinct from the plan's overall
    # required consequences.
    #
    # The distinction matters for pacing. A plan's required consequences describe
    # an arc that may take several turns; a beat's describe what must be true by
    # the end of *this* turn. Enforcing the arc's demands every turn would reject
    # correct scenes for not completing the whole story at once, so only
    # beat-scoped requirements are enforced per turn.
    required_consequences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "status": self.status.value,
            "participants": list(self.participants),
            "required": self.required,
            "guidance": self.guidance,
            "requires_knowledge": self.requires_knowledge,
            "note": self.note,
            "required_consequences": list(self.required_consequences),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Beat:
        try:
            status = BeatStatus(str(value.get("status", BeatStatus.PENDING.value)))
        except ValueError:
            status = BeatStatus.PENDING
        return cls(
            description=str(value.get("description", "")),
            status=status,
            participants=[str(item) for item in value.get("participants", []) or []],
            required=bool(value.get("required", False)),
            guidance=bool(value.get("guidance", True)),
            requires_knowledge=str(value.get("requires_knowledge", "")),
            note=str(value.get("note", "")),
            required_consequences=[
                str(item) for item in value.get("required_consequences", []) or []
            ],
        )


@dataclass
class DirectorPlanDraft:
    """The full plan shape, before persistence.

    Drafts live in memory and in the generation trace. Only a plan that reaches
    ``approved`` becomes a persisted narrative artifact.
    """

    objective: str = ""
    summary: str = ""
    lane: Lane = Lane.DIRECTION
    horizon: Horizon = Horizon.NEAR_TERM
    authority_mode: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED
    interpretation_source: str = "heuristic"
    participants: list[str] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    required_consequences: list[str] = field(default_factory=list)
    likely_consequences: list[str] = field(default_factory=list)
    optional_consequences: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    desired_direction: str = ""
    information_reveals: list[str] = field(default_factory=list)
    foreshadowing: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    completion_condition: str = ""
    requires_approval: bool = False
    requires_choice: bool = False
    options: list[dict[str, str]] = field(default_factory=list)
    canon_conflict: dict[str, Any] | None = None
    # The intent this plan was built from, retained verbatim.
    intent: Intent = field(default_factory=Intent)
    status: PlanStatus = PlanStatus.PROPOSED

    @property
    def current_beat(self) -> Beat | None:
        for beat in self.beats:
            if beat.status is BeatStatus.ACTIVE:
                return beat
        return self.beats[0] if self.beats else None

    def active_beat(self) -> Beat | None:
        """The beat the Performer is asked to realise next.

        Advances past completed/skipped/invalidated beats rather than stalling
        on one, so an obsolete beat never blocks the scene.
        """
        for beat in self.beats:
            if beat.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}:
                beat.status = BeatStatus.ACTIVE
                return beat
        return None

    def invalidate_beats_requiring(self, character_id: str, reason: str) -> list[Beat]:
        """Invalidate beats that depend on a character who can no longer act.

        A death or a disappearance makes dependent beats obsolete. That is the
        scene changing, not the engine failing.

        Dependence is broader than participation: a beat can also rest on a
        character's *private knowledge*, so "the General confesses" becomes
        impossible when the General dies even though the General is not listed
        as a participant in it.
        """
        invalidated: list[Beat] = []
        for beat in self.beats:
            if beat.status in {BeatStatus.COMPLETED, BeatStatus.SKIPPED, BeatStatus.INVALIDATED}:
                continue
            depends = character_id in beat.participants or (
                bool(beat.requires_knowledge)
                and beat.requires_knowledge.casefold() == character_id.casefold()
            )
            if depends:
                beat.status = BeatStatus.INVALIDATED
                beat.note = reason
                invalidated.append(beat)
        return invalidated

    def all_beats_resolved(self) -> bool:
        return all(
            beat.status
            in {BeatStatus.COMPLETED, BeatStatus.SKIPPED, BeatStatus.INVALIDATED}
            for beat in self.beats
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "summary": self.summary,
            "lane": self.lane.value,
            "horizon": self.horizon.value,
            "authority_mode": self.authority_mode.value,
            "interpretation_source": self.interpretation_source,
            "status": self.status.value,
            "participants": list(self.participants),
            "beats": [beat.to_dict() for beat in self.beats],
            "required_consequences": list(self.required_consequences),
            "likely_consequences": list(self.likely_consequences),
            "optional_consequences": list(self.optional_consequences),
            "assumptions": list(self.assumptions),
            "allowed_actions": list(self.allowed_actions),
            "desired_direction": self.desired_direction,
            "information_reveals": list(self.information_reveals),
            "foreshadowing": list(self.foreshadowing),
            "constraints": list(self.constraints),
            "exclusions": list(self.exclusions),
            "commitments": list(self.commitments),
            "completion_condition": self.completion_condition,
            "requires_approval": self.requires_approval,
            "requires_choice": self.requires_choice,
            "options": [dict(option) for option in self.options],
            "canon_conflict": self.canon_conflict,
            "intent": self.intent.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DirectorPlanDraft:
        intent_value = dict(value.get("intent", {}) or {})
        try:
            status = PlanStatus(str(value.get("status", PlanStatus.PROPOSED.value)))
        except ValueError:
            status = PlanStatus.PROPOSED
        try:
            lane = Lane(str(value.get("lane", Lane.DIRECTION.value)))
        except ValueError:
            lane = Lane.DIRECTION
        try:
            horizon = Horizon(str(value.get("horizon", Horizon.NEAR_TERM.value)))
        except ValueError:
            horizon = Horizon.NEAR_TERM
        try:
            specification = Specification(str(intent_value.get("specification", Specification.PARTIAL.value)))
        except ValueError:
            specification = Specification.PARTIAL
        try:
            consistency = Consistency(str(intent_value.get("consistency", Consistency.CONSISTENT.value)))
        except ValueError:
            consistency = Consistency.CONSISTENT
        return cls(
            objective=str(value.get("objective", "")),
            summary=str(value.get("summary", "")),
            lane=lane,
            horizon=horizon,
            authority_mode=AuthorityMode.coerce(value.get("authority_mode", "")),
            interpretation_source=str(value.get("interpretation_source", "heuristic")),
            status=status,
            participants=[str(item) for item in value.get("participants", []) or []],
            beats=[Beat.from_dict(item) for item in value.get("beats", []) or []],
            required_consequences=[str(item) for item in value.get("required_consequences", []) or []],
            likely_consequences=[str(item) for item in value.get("likely_consequences", []) or []],
            optional_consequences=[str(item) for item in value.get("optional_consequences", []) or []],
            assumptions=[str(item) for item in value.get("assumptions", []) or []],
            allowed_actions=[str(item) for item in value.get("allowed_actions", []) or []],
            desired_direction=str(value.get("desired_direction", "")),
            information_reveals=[str(item) for item in value.get("information_reveals", []) or []],
            foreshadowing=[str(item) for item in value.get("foreshadowing", []) or []],
            constraints=[str(item) for item in value.get("constraints", []) or []],
            exclusions=[str(item) for item in value.get("exclusions", []) or []],
            commitments=[str(item) for item in value.get("commitments", []) or []],
            completion_condition=str(value.get("completion_condition", "")),
            requires_approval=bool(value.get("requires_approval", False)),
            requires_choice=bool(value.get("requires_choice", False)),
            options=[dict(option) for option in value.get("options", []) or []],
            canon_conflict=value.get("canon_conflict"),
            intent=Intent(
                mode=str(intent_value.get("mode", "auto")),
                objective=str(intent_value.get("objective", "")),
                target_entities=[str(item) for item in intent_value.get("target_entities", []) or []],
                desired_outcome=str(intent_value.get("desired_outcome", "")),
                constraints=[str(item) for item in intent_value.get("constraints", []) or []],
                exclusions=[str(item) for item in intent_value.get("exclusions", []) or []],
                tone=str(intent_value.get("tone", "")),
                urgency=str(intent_value.get("urgency", "")),
                canon_preference=str(intent_value.get("canon_preference", "")),
                user_control_level=str(intent_value.get("user_control_level", "")),
                specification=specification,
                consistency=consistency,
                confidence=float(intent_value.get("confidence", 0.5)),
                lane=lane,
                horizon=str(intent_value.get("horizon", "short")),
            ),
        )


@dataclass
class DirectorPlanView:
    """A plan plus its decision and execution metadata, for API and UI."""

    plan: DirectorPlanDraft
    plan_id: str | None = None
    status: PlanStatus = PlanStatus.PROPOSED
    requires_approval: bool = False
    validation: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    intent_id: str | None = None
    commitment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = plan_to_dict(self.plan)
        payload.update(
            {
                "plan_id": self.plan_id,
                "status": self.status.value,
                "requires_approval": self.requires_approval,
                "validation": self.validation,
                "execution": self.execution,
                "intent_id": self.intent_id,
                "commitment_ids": list(self.commitment_ids),
            }
        )
        return payload


def plan_to_dict(plan: DirectorPlanDraft) -> dict[str, Any]:
    return plan.to_dict()


# --- Heuristic construction -------------------------------------------------


def _horizon_for(intent: Intent, lane: Lane) -> Horizon:
    if lane is Lane.LONG_HORIZON:
        return Horizon.LONG_TERM
    if intent.urgency == "immediate":
        return Horizon.IMMEDIATE
    if intent.specification is Specification.EXPLICIT:
        return Horizon.NEAR_TERM
    return Horizon.NEAR_TERM


def _choice_options(intent: Intent, participants: Sequence[str]) -> list[dict[str, str]]:
    """A small set of plausible directions for an underspecified request.

    Offered instead of guessing, and only under authority modes that ask.
    """
    cast = ", ".join(participants) if participants else "the participants"
    return [
        {
            "key": "A",
            "label": "A sudden event changes the situation",
            "detail": f"Something concrete happens to {cast} that cannot be ignored.",
        },
        {
            "key": "B",
            "label": "A hidden fact is revealed",
            "detail": f"Someone in the scene lets slip something {cast} did not know.",
        },
        {
            "key": "C",
            "label": "An existing tension sharpens",
            "detail": f"A relationship already in the scene turns against {cast}.",
        },
    ]


def _beats_for(
    intent: Intent,
    lane: Lane,
    participants: Sequence[str],
    requires_choice: bool,
) -> list[Beat]:
    """Build beats whose count follows the request's complexity.

    A plan that offers a choice still gets beats. The choice is a question about
    *direction*; a beat is an instruction about *execution*. Returning no beats for
    a choice-carrying plan meant every ``strict`` and ``collaborative`` plan was
    unperformable as a sequence — there was nothing for the Performer to pace
    across turns, and no place to hang a per-turn requirement.
    """
    if lane is Lane.LONG_HORIZON:
        return [
            Beat(
                description=f"Undermine or foreshadow: {intent.objective or intent.desired_outcome}".strip(),
                participants=list(participants),
                required=False,
                note="guidance only; the outcome itself must not occur yet",
            )
        ]
    beats: list[Beat] = []
    objective = intent.objective or intent.desired_outcome
    if objective:
        beats.append(
            Beat(
                description=f"Make the situation around: {objective}",
                participants=list(participants),
                required=True,
            )
        )
    if intent.desired_outcome:
        beats.append(
            Beat(
                description=f"Let the consequence arrive: {intent.desired_outcome}",
                participants=list(participants),
                required=False,
            )
        )
    if intent.tone and len(participants) > 1:
        beats.append(
            Beat(
                description=f"Each participant reacts according to their own knowledge and goal ({intent.tone})",
                participants=list(participants),
                required=False,
                note="the Director sets the situation; characters decide their own reaction",
            )
        )
    beats.append(
        Beat(
            description="Leave an unresolved thread the next turn can pick up",
            participants=[],
            required=False,
        )
    )
    return beats[:MAX_HEURISTIC_BEATS]


def _allowed_actions_for(intent: Intent, participants: Sequence[str]) -> list[str]:
    """What the Performer may do, derived from the request rather than invented.

    Only non-consequential staging actions are allowed by default; anything that
    would change state must come from the validated event path, not from a
    permission list.
    """
    actions = [
        "Speak and act in character",
        "React to the current beat",
        "Reveal a fact this character already knows",
        "Move within the scene location",
    ]
    if participants:
        actions.append("Choose an individual reaction appropriate to this character")
    if intent.tone:
        actions.append(f"Keep the tone {intent.tone}")
    return actions


def _assumptions_for(intent: Intent, lane: Lane) -> list[str]:
    assumptions: list[str] = []
    if intent.specification is not Specification.EXPLICIT:
        assumptions.append("Method, timing, and exact staging were inferred, not requested.")
    if not intent.target_entities:
        assumptions.append("No target was named; scene participants are assumed affected.")
    if lane is Lane.LONG_HORIZON:
        assumptions.append("The outcome is not executed now; only a commitment is recorded.")
    return assumptions


def build_heuristic_plan(
    intent: Intent,
    *,
    lane: Lane,
    participants: Sequence[str] = (),
    authority: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED,
) -> DirectorPlanDraft:
    """Build a plan with no provider call.

    This is the offline path and the fallback whenever refinement is unavailable
    or unusable. It is deterministic: same intent, same lane, same plan.
    """
    lane = Lane(lane) if not isinstance(lane, Lane) else lane
    needs_choice = (
        intent.specification is Specification.UNDERSPECIFIED
        and (intent.consistency is not Consistency.CONSISTENT or authority.offers_options)
    )
    options = _choice_options(intent, participants) if needs_choice else []
    objective = intent.objective or intent.desired_outcome
    horizon = _horizon_for(intent, lane)
    plan = DirectorPlanDraft(
        objective=objective,
        summary=(objective or "Continue the scene under the current direction")[:280],
        lane=lane,
        horizon=horizon,
        authority_mode=authority,
        interpretation_source="heuristic",
        participants=list(participants),
        beats=_beats_for(intent, lane, participants, needs_choice),
        required_consequences=_required_consequences_for(intent, lane),
        likely_consequences=_likely_consequences_for(intent, participants),
        optional_consequences=_optional_consequences_for(intent, participants),
        assumptions=_assumptions_for(intent, lane),
        allowed_actions=_allowed_actions_for(intent, participants),
        desired_direction=intent.desired_outcome or intent.objective,
        information_reveals=[],
        constraints=list(intent.constraints),
        exclusions=list(intent.exclusions),
        commitments=[objective] if lane is Lane.LONG_HORIZON and objective else [],
        completion_condition=_completion_for(intent, lane),
        requires_approval=authority.requires_approval,
        requires_choice=needs_choice,
        options=options,
        intent=intent,
    )
    return plan


def _required_consequences_for(intent: Intent, lane: Lane) -> list[str]:
    consequences: list[str] = []
    if intent.objective:
        consequences.append(f"The stated objective happens: {intent.objective}")
    if intent.desired_outcome:
        consequences.append(f"The desired reaction occurs: {intent.desired_outcome}")
    if lane is Lane.LONG_HORIZON:
        consequences.append(
            "The eventual outcome is NOT executed this turn; a commitment records it."
        )
    return consequences


def _likely_consequences_for(intent: Intent, participants: Sequence[str]) -> list[str]:
    if not participants:
        return []
    reactions = f"{', '.join(participants)} react according to their own knowledge and goals"
    return [reactions, "The situation carries an unresolved thread into the next turn."]


def _optional_consequences_for(intent: Intent, participants: Sequence[str]) -> list[str]:
    return [
        f"A participant notices something only they could know ({intent.target_entities or 'a private clue'})"
        if participants
        else "A hidden detail becomes available to whoever is positioned to see it."
    ]


def _completion_for(intent: Intent, lane: Lane) -> str:
    if lane is Lane.LONG_HORIZON:
        return "The recorded commitment is fulfilled, failed, or superseded."
    if intent.desired_outcome:
        return f"The desired reaction has occurred: {intent.desired_outcome}"
    if intent.objective:
        return f"The stated objective has happened: {intent.objective}"
    return "The current beat produces a concrete, committed change."


def beats_from_json(values: Iterable[dict[str, Any]]) -> list[Beat]:
    return [Beat.from_dict(value) for value in values]


def plan_from_json(raw: str) -> DirectorPlanDraft:
    return DirectorPlanDraft.from_dict(json.loads(raw))
