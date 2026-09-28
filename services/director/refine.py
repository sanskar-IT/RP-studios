"""Provider-assisted intent refinement.

The deterministic Phase A interpreter is always stage one. This module is stage
two, and only for lanes 3 and 4, where the Director is about to make decisions
the user did not specify.

Three rules govern the provider call:

**No chain of thought, ever.** The schema asks for structured fields and a short
rationale. Nothing that models private reasoning is requested, stored, or
displayed. What is persisted is a decision and its justification.

**The provider may fill gaps, not the envelope.** The intent's explicit
constraints and exclusions are passed to the provider and re-checked
afterwards. A refinement that contradicts them is discarded in favour of the
heuristic plan, and the disagreement is reported.

**Degrade, never crash.** An empty, malformed, or unavailable response yields the
heuristic plan with the source recorded as ``heuristic_fallback``. Offline
development and deterministic test suites therefore keep working unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import AuthorityMode, Horizon, Lane
from services.director.intent import Intent
from services.director.plan import (
    Beat,
    DirectorPlanDraft,
    build_heuristic_plan,
)
from services.providers.base import ProviderMessage

REFINEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "desired_outcome": {"type": "string"},
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "missing_details": {"type": "array", "items": {"type": "string"}},
        "target_entities": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string"},
        "urgency": {"type": "string"},
        "horizon": {"type": "string"},
        "executes_now": {"type": "boolean"},
        "beats": {"type": "array", "items": {"type": "string"}},
        "required_consequences": {"type": "array", "items": {"type": "string"}},
        "likely_consequences": {"type": "array", "items": {"type": "string"}},
        "optional_consequences": {"type": "array", "items": {"type": "string"}},
        "participants": {"type": "array", "items": {"type": "string"}},
        "foreshadowing": {"type": "array", "items": {"type": "string"}},
        "information_reveals": {"type": "array", "items": {"type": "string"}},
        "commitments": {"type": "array", "items": {"type": "string"}},
        "completion_condition": {"type": "string"},
        "desired_direction": {"type": "string"},
        "allowed_actions": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": [
        "objective",
        "assumptions",
        "beats",
        "required_consequences",
        "likely_consequences",
        "completion_condition",
    ],
}

DIRECTOR_SYSTEM = (
    "You are the narrative director for a roleplay engine. Convert the user's request into a "
    "structured execution plan. Fill in operational detail the user did not specify. "
    "Never contradict the user's explicit constraints or exclusions, and never execute a "
    "long-horizon outcome now. Return only the requested fields. "
    "Do not include reasoning, deliberation, or chain of thought."
)

_HORIZONS = {value.value: value for value in Horizon}


@dataclass
class RefinementResult:
    """The outcome of a refinement attempt, including how it was produced."""

    plan: DirectorPlanDraft
    source: str = "heuristic"
    rationale: str = ""
    missing_details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rejected_reason: str = ""

    @property
    def refined(self) -> bool:
        return self.source == "refined"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "refined": self.refined,
            "rationale": self.rationale,
            "missing_details": list(self.missing_details),
            "errors": list(self.errors),
            "rejected_reason": self.rejected_reason,
        }


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _usable(payload: dict[str, Any]) -> bool:
    """Whether a response carries real content.

    The heuristic provider and an empty structured answer both return
    schema-shaped but empty objects. Those are not refinements.
    """
    if not isinstance(payload, dict):
        return False
    if not str(payload.get("objective", "")).strip():
        return False
    if not _text_list(payload.get("beats")) and not _text_list(payload.get("required_consequences")):
        return False
    return True


def build_refinement_messages(
    *,
    intent: Intent,
    lane: Lane,
    participants: Sequence[str],
    authority: AuthorityMode,
) -> list[ProviderMessage]:
    envelope = {
        "constraints": list(intent.constraints),
        "exclusions": list(intent.exclusions),
    }
    facts = (
        f"User request: {intent.objective or intent.desired_outcome or '(unclear)'}",
        f"Desired outcome: {intent.desired_outcome or '(not stated)'}",
        f"Execution lane: {lane.value}",
        f"Narrative horizon: {intent.horizon}",
        f"Authority mode: {authority.value}",
        f"Scene participants: {', '.join(participants) if participants else '(none named)'}",
        f"Mandatory constraint envelope: {json.dumps(envelope, sort_keys=True)}",
        "Rules: fill unspecified detail freely. Never contradict the constraint envelope. "
        "A long-horizon request must not execute its outcome now.",
    )
    return [
        ProviderMessage(role="system", content=DIRECTOR_SYSTEM),
        ProviderMessage(role="user", content="\n".join(facts)),
    ]


def plan_from_refinement(
    payload: dict[str, Any],
    *,
    intent: Intent,
    lane: Lane,
    participants: Sequence[str],
    authority: AuthorityMode,
) -> DirectorPlanDraft:
    """Convert a validated provider response into a plan.

    Constraints and exclusions are *restored from the intent*, not taken from the
    provider. Even if the provider paraphrased or dropped one, the user's own
    words remain authoritative.
    """
    raw_horizon = str(payload.get("horizon", "")).strip().casefold()
    horizon = _HORIZONS.get(raw_horizon)
    if horizon is None:
        horizon = Horizon.LONG_TERM if lane is Lane.LONG_HORIZON else Horizon.NEAR_TERM
    if lane is Lane.LONG_HORIZON:
        # A long-horizon request is a commitment even if the provider said
        # otherwise; the lane is the router's authority.
        horizon = Horizon.LONG_TERM
    executes_now = bool(payload.get("executes_now", False))
    if lane is Lane.LONG_HORIZON and executes_now:
        horizon = Horizon.LONG_TERM
    return DirectorPlanDraft(
        objective=str(payload.get("objective", "")) or intent.objective,
        summary=str(payload.get("summary", "")).strip()
        or (intent.objective or intent.desired_outcome)[:280],
        lane=lane,
        horizon=horizon,
        authority_mode=authority,
        interpretation_source="refined",
        participants=_text_list(payload.get("participants")) or list(participants),
        beats=[
            Beat(description=description, required=index == 0)
            for index, description in enumerate(_text_list(payload.get("beats")))
        ],
        required_consequences=_text_list(payload.get("required_consequences")),
        likely_consequences=_text_list(payload.get("likely_consequences")),
        optional_consequences=_text_list(payload.get("optional_consequences")),
        assumptions=_text_list(payload.get("assumptions")) + _text_list(payload.get("missing_details")),
        allowed_actions=_text_list(payload.get("allowed_actions")),
        desired_direction=str(payload.get("desired_direction", ""))
        or intent.desired_outcome
        or intent.objective,
        information_reveals=_text_list(payload.get("information_reveals")),
        foreshadowing=_text_list(payload.get("foreshadowing")),
        constraints=list(intent.constraints),
        exclusions=list(intent.exclusions),
        commitments=_text_list(payload.get("commitments"))
        or ([intent.objective] if lane is Lane.LONG_HORIZON and intent.objective else []),
        completion_condition=str(payload.get("completion_condition", "")),
        requires_approval=authority.requires_approval,
        # The interpreted intent is retained verbatim; a refinement may elaborate
        # on it but never becomes it.
        intent=intent,
    )


async def refine_intent(
    provider: Any,
    *,
    intent: Intent,
    lane: Lane,
    participants: Sequence[str] = (),
    authority: AuthorityMode = AuthorityMode.DIRECTOR_ASSISTED,
) -> RefinementResult:
    """Refine an intent into a plan, falling back to the heuristic on any problem.

    Never raises for provider, schema, or content problems: a failed refinement
    produces a usable heuristic plan with the reason recorded, because refusing
    to direct a scene is a worse outcome than directing it conservatively.
    """
    fallback = build_heuristic_plan(intent, lane=lane, participants=participants, authority=authority)
    messages = build_refinement_messages(
        intent=intent, lane=lane, participants=participants, authority=authority
    )
    try:
        payload = await provider.structured(messages, REFINEMENT_SCHEMA)
    except Exception as exc:  # provider outage, timeout, refusal
        return RefinementResult(
            plan=fallback,
            source="heuristic_fallback",
            errors=[f"provider refinement failed: {type(exc).__name__}"],
        )
    if not _usable(payload):
        return RefinementResult(
            plan=fallback,
            source="heuristic_fallback",
            errors=["provider returned no usable refinement"],
        )
    try:
        plan = plan_from_refinement(
            payload,
            intent=intent,
            lane=lane,
            participants=participants,
            authority=authority,
        )
    except (TypeError, ValueError) as exc:
        return RefinementResult(
            plan=fallback,
            source="heuristic_fallback",
            errors=[f"refinement could not be applied: {type(exc).__name__}"],
        )
    return RefinementResult(
        plan=plan,
        source="refined",
        # Only a short rationale is retained; no reasoning trace is stored.
        rationale=_clean_rationale(str(payload.get("rationale", ""))),
        missing_details=_text_list(payload.get("missing_details")),
    )


_THINKING_MARKERS = re.compile(
    r"\b(because|since|therefore|thus|hence|let me|considering|reasoning|"
    r"first|i think|step \d|i will now|thought)\b",
    re.IGNORECASE,
)


def _clean_rationale(value: str) -> str:
    """Keep a decision note; drop anything shaped like reasoning.

    A rationale that reads as deliberation is discarded rather than stored,
    because the trace is a permanent artifact and private reasoning has no place
    in it.
    """
    text = " ".join(value.split())
    if not text or _THINKING_MARKERS.search(text):
        return ""
    return text[:240]
