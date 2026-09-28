"""Per-generation trace.

Every generation answers the same question: what exactly happened, and why.
The trace is the durable answer. It is written in the same transaction as the
generation row, so a failed turn is as inspectable as a successful one.

What is deliberately **not** stored: base URLs, API keys, headers, or any other
provider credential. Only the provider's *name*, the model identifier, and its
declared capabilities are recorded, because those are needed to explain a
result and are not secret. Credentials are configuration and stay in the
environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Keys that must never reach a persisted trace. Asserted on write so a future
# change cannot start leaking them by accident.
FORBIDDEN_TRACE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "base_url",
        "credential",
        "credential_env",
        "headers",
        "password",
        "secret",
        "token",
    }
)


def _scrub(value: Any) -> Any:
    """Drop credential-shaped keys recursively, at any depth.

    The keys are removed rather than redacted: a trace that says "a credential
    was here" carries no debugging value and still advertises the shape of the
    secret it held.
    """
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).casefold() not in FORBIDDEN_TRACE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


@dataclass
class StageTiming:
    """Wall-clock cost of one pipeline stage, in milliseconds."""

    name: str
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "duration_ms": round(self.duration_ms, 3)}


class StageTimer:
    """Collects stage timings for a single generation.

    Used as a context manager so a stage that raises still reports the time it
    spent before failing, which is exactly when the number is interesting.
    """

    def __init__(self) -> None:
        self.timings: list[StageTiming] = []

    def record(self, name: str, duration_ms: float) -> None:
        self.timings.append(StageTiming(name=name, duration_ms=duration_ms))

    def measure(self, name: str) -> _StageContext:
        return _StageContext(self, name)

    @property
    def total_ms(self) -> float:
        return round(sum(timing.duration_ms for timing in self.timings), 3)

    def to_list(self) -> list[dict[str, Any]]:
        return [timing.to_dict() for timing in self.timings]

    def by_name(self) -> dict[str, float]:
        return {timing.name: round(timing.duration_ms, 3) for timing in self.timings}


class _StageContext:
    def __init__(self, timer: StageTimer, name: str) -> None:
        self.timer = timer
        self.name = name
        self._start = 0.0

    def __enter__(self) -> _StageContext:
        from time import perf_counter

        self._start = perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        from time import perf_counter

        self.timer.record(self.name, (perf_counter() - self._start) * 1000.0)


@dataclass
class GenerationTrace:
    """The complete, inspectable record of one generation attempt."""

    project_id: str = ""
    timeline_id: str = ""
    scene_id: str | None = None
    actor_character_id: str | None = None
    input_mode: str = "auto"
    provider: str = ""
    model: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    actor_selection: dict[str, Any] = field(default_factory=dict)
    director: dict[str, Any] = field(default_factory=dict)
    performer: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    activated_lore: list[dict[str, Any]] = field(default_factory=list)
    lore_debug: dict[str, Any] = field(default_factory=dict)
    active_commitments: list[dict[str, Any]] = field(default_factory=list)
    generated_events: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    memory_writes: list[dict[str, Any]] = field(default_factory=list)
    timings: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    error: str = ""
    generation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _scrub(
            {
                "generation_id": self.generation_id,
                "project_id": self.project_id,
                "timeline_id": self.timeline_id,
                "scene_id": self.scene_id,
                "actor_character_id": self.actor_character_id,
                "input_mode": self.input_mode,
                "provider": self.provider,
                "model": self.model,
                "capabilities": self.capabilities,
                "actor_selection": self.actor_selection,
                "director": self.director,
                "performer": self.performer,
                "context": self.context,
                "retrieval": self.retrieval,
                "retrieved_memories": self.retrieved_memories,
                "activated_lore": self.activated_lore,
                "lore_debug": self.lore_debug,
                "active_commitments": self.active_commitments,
                "generated_events": self.generated_events,
                "validation": self.validation,
                "contradictions": self.contradictions,
                "state_changes": self.state_changes,
                "memory_writes": self.memory_writes,
                "timings": self.timings,
                "status": self.status,
                "error": self.error,
            }
        )


def summarise_timings(timings: list[dict[str, Any]]) -> dict[str, Any]:
    """A report-ready timing block: per stage plus a total."""
    return {
        "stages": timings,
        "total_ms": round(sum(float(item.get("duration_ms", 0.0)) for item in timings), 3),
    }
