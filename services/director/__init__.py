"""Narrative Director and Intent Layer: plans, interpretation, execution.

Phase B adds the DirectorPlan artifact, provider refinement, constraint-envelope
enforcement, authority modes, and lane 3/4 execution.

Invariants held by this package:

- ``Intent`` is what the user wants; ``DirectorPlan`` is how it will be done.
  Neither acquires the other's fields.
- The deterministic Intent Router is the authority for lane selection.
- Authority is freedom inside the user's constraint envelope, never permission
  to reinterpret or contradict explicit instructions.
- Beats are guidance, not a screenplay queue.
- The Director never mutates authoritative state; the State Engine does.
"""

from services.core.enums import (
    AuthorityMode,
    BeatStatus,
    Horizon,
    Lane,
    PlanStatus,
)
from services.director.canon import CanonConflict, detect_canon_conflict
from services.director.envelope import (
    EnvelopeViolation,
    PlanValidation,
    check_envelope,
    validate_plan,
)
from services.director.intent import (
    Consistency,
    Intent,
    Specification,
    interpret,
)
from services.director.plan import (
    Beat,
    DirectorPlanDraft,
    DirectorPlanView,
    build_heuristic_plan,
    plan_to_dict,
)
from services.director.refine import (
    REFINEMENT_SCHEMA,
    RefinementResult,
    refine_intent,
)
from services.director.router import classify_lane, route

__all__ = [
    "AuthorityMode",
    "Beat",
    "BeatStatus",
    "CanonConflict",
    "Consistency",
    "DirectorPlanDraft",
    "DirectorPlanView",
    "EnvelopeViolation",
    "Horizon",
    "Intent",
    "Lane",
    "PlanStatus",
    "PlanValidation",
    "REFINEMENT_SCHEMA",
    "RefinementResult",
    "Specification",
    "build_heuristic_plan",
    "check_envelope",
    "classify_lane",
    "detect_canon_conflict",
    "interpret",
    "plan_to_dict",
    "refine_intent",
    "route",
    "validate_plan",
]
