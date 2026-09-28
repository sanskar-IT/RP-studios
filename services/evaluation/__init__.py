"""Deterministic narrative evaluation.

Run the full suite from the repository root:

    python -m services.evaluation.harness --out reports/evaluation.json --print

Every number in the report is produced without a live model provider, so the
suite is reproducible on a machine with no network access.

The Director has its own suite, which asks a different question — not whether
turns validated, but whether the Director did the user's job correctly:

    python -m services.evaluation.director --out reports/director_evaluation.json --print

And the Performer has a third, which is the one that asserts on prose — whether
reactions differ between characters, whether the Performer stayed out of the
user's character's decisions, whether a beat was realised:

    python -m services.evaluation.performer --out reports/performer_evaluation.json --print

The three are kept separate on purpose. The engine harness detects broken
generations; the Director's and the Performer's characteristic failures are
silent, because each one produces a structurally valid generation that says the
wrong thing.

The harness module is intentionally not imported here: importing it would run
module-level work and cause ``python -m services.evaluation.harness`` to load
twice.
"""

from services.evaluation.metrics import EvaluationReport, ScenarioMetrics, TurnSample
from services.evaluation.scenarios import (
    ALL_SCENARIOS,
    SCENARIOS,
    Invariant,
    Scenario,
    scenario_by_key,
)

__all__ = [
    "ALL_SCENARIOS",
    "EvaluationReport",
    "Invariant",
    "SCENARIOS",
    "Scenario",
    "ScenarioMetrics",
    "TurnSample",
    "scenario_by_key",
]
