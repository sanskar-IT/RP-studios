"""Evaluation metrics.

The numbers do not have to be perfect. They have to exist, be defined, and be
comparable between runs, so that "the engine got worse" is a statement anyone
can check.

Every counter here is a definition someone else could reimplement from the prose
below. That is the whole point: a metric nobody can reproduce is a mood, not a
measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnSample:
    """What one turn produced, before anything is aggregated."""

    turn: int
    actor_character_id: str | None
    total_tokens: int
    max_input_tokens: int
    memories_retrieved: int
    memories_considered: int
    lore_activated: int
    lore_candidates: int
    lore_missed: int
    events_committed: int
    validation_errors: int
    contradictions: int
    status: str
    duration_ms: float


@dataclass
class ScenarioMetrics:
    """Aggregated reliability numbers for one scenario at one turn count."""

    scenario: str
    title: str
    turns: int
    state_contradictions: int = 0
    knowledge_leaks: int = 0
    broken_commitments: int = 0
    invalid_events: int = 0
    branch_leaks: int = 0
    memory_retrieval_failures: int = 0
    context_overflows: int = 0
    context_tokens: list[int] = field(default_factory=list)
    lore_false_activations: int = 0
    lore_missed_activations: int = 0
    rejected_generations: int = 0
    memory_rows_written: int = 0
    transcript_turns: int = 0
    contradiction_warnings: int = 0
    lore_activated_total: int = 0
    invariants: dict[str, bool] = field(default_factory=dict)
    invariant_failures: list[str] = field(default_factory=list)
    samples: list[TurnSample] = field(default_factory=list)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    @property
    def average_context(self) -> float:
        return round(sum(self.context_tokens) / len(self.context_tokens), 1) if self.context_tokens else 0.0

    @property
    def peak_context(self) -> int:
        return max(self.context_tokens) if self.context_tokens else 0

    @property
    def memory_growth_ratio(self) -> float:
        """Memory rows per turn.

        The previous write policy produced five rows per turn, three of them
        verbatim copies of the same prose. A ratio near or below one is the
        signal that the transcript is no longer the memory store.
        """
        return round(self.memory_rows_written / self.turns, 3) if self.turns else 0.0

    @property
    def passed(self) -> bool:
        return not self.invariant_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "title": self.title,
            "turns": self.turns,
            "passed": self.passed,
            "state_contradictions": self.state_contradictions,
            "knowledge_leaks": self.knowledge_leaks,
            "broken_commitments": self.broken_commitments,
            "invalid_events": self.invalid_events,
            "branch_leaks": self.branch_leaks,
            "memory_retrieval_failures": self.memory_retrieval_failures,
            "context_overflows": self.context_overflows,
            "rejected_generations": self.rejected_generations,
            "lore_false_activations": self.lore_false_activations,
            "lore_missed_activations": self.lore_missed_activations,
            "lore_entries_activated_total": self.lore_activated_total,
            "contradiction_warnings": self.contradiction_warnings,
            "average_context": self.average_context,
            "peak_context": self.peak_context,
            "memory_rows_written": self.memory_rows_written,
            "memory_rows_per_turn": self.memory_growth_ratio,
            "invariants": dict(sorted(self.invariants.items())),
            "invariant_failures": sorted(self.invariant_failures),
            "findings": list(self.findings),
            "stage_timings_ms": {
                key: round(value, 3) for key, value in sorted(self.stage_timings_ms.items())
            },
            "samples": [
                {
                    "turn": sample.turn,
                    "total_tokens": sample.total_tokens,
                    "max_input_tokens": sample.max_input_tokens,
                    "memories_retrieved": sample.memories_retrieved,
                    "memories_considered": sample.memories_considered,
                    "lore_activated": sample.lore_activated,
                    "lore_missed": sample.lore_missed,
                    "events_committed": sample.events_committed,
                    "validation_errors": sample.validation_errors,
                    "contradictions": sample.contradictions,
                    "status": sample.status,
                }
                for sample in self.samples
            ],
        }


@dataclass
class EvaluationReport:
    """A complete, machine-readable evaluation run."""

    turns_requested: list[int]
    generated_at: str = ""
    estimator: str = ""
    scenarios: list[ScenarioMetrics] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(metrics.passed for metrics in self.scenarios)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "narrative-evaluation/1",
            "generated_at": self.generated_at,
            "turns_requested": list(self.turns_requested),
            "token_estimator": self.estimator,
            "passed": self.passed,
            "totals": {
                "runs": len(self.scenarios),
                "runs_passed": sum(1 for metrics in self.scenarios if metrics.passed),
                "state_contradictions": sum(m.state_contradictions for m in self.scenarios),
                "knowledge_leaks": sum(m.knowledge_leaks for m in self.scenarios),
                "broken_commitments": sum(m.broken_commitments for m in self.scenarios),
                "invalid_events": sum(m.invalid_events for m in self.scenarios),
                "branch_leaks": sum(m.branch_leaks for m in self.scenarios),
                "memory_retrieval_failures": sum(m.memory_retrieval_failures for m in self.scenarios),
                "context_overflows": sum(m.context_overflows for m in self.scenarios),
                "rejected_generations": sum(m.rejected_generations for m in self.scenarios),
            },
            "runs": [metrics.to_dict() for metrics in self.scenarios],
        }

    def render(self) -> str:
        """The human-readable form of the same numbers."""
        lines: list[str] = []
        for metrics in self.scenarios:
            status = "PASS" if metrics.passed else "FAIL"
            lines.append(
                f"Scenario: {metrics.title}\n"
                f"Turns: {metrics.turns}\n"
                f"Result: {status}"
            )
            if metrics.invariant_failures:
                lines.append("Failed invariants: " + ", ".join(sorted(metrics.invariant_failures)))
            lines.append(
                "\n".join(
                    [
                        f"State contradictions: {metrics.state_contradictions}",
                        f"Knowledge leaks: {metrics.knowledge_leaks}",
                        f"Broken commitments: {metrics.broken_commitments}",
                        f"Invalid events: {metrics.invalid_events}",
                        f"Branch leaks: {metrics.branch_leaks}",
                        f"Memory retrieval failures: {metrics.memory_retrieval_failures}",
                        f"Rejected generations: {metrics.rejected_generations}",
                        f"Context overflows: {metrics.context_overflows}",
                        f"Average context: {metrics.average_context:,.0f} tokens",
                        f"Peak context: {metrics.peak_context:,} tokens",
                        f"Memory rows per turn: {metrics.memory_growth_ratio}",
                    ]
                )
            )
            lines.append("")
        return "\n".join(lines)
