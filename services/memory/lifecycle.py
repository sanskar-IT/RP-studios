"""Memory lifecycle.

The previous write policy stored the whole transcript three times per turn. The
lifecycle implemented here is explicit and each stage can be inspected:

.. code-block:: text

    Event
      -> MemorySeed              what the event asserts
      -> importance evaluation  is this worth keeping at all
      -> MemoryProposal         class + scope + retention decision
      -> persistence            only proposals that survive are written
      -> retrieval              ancestry-scoped, budgeted
      -> context injection      P7, never privileged

The important consequence is that **not every event becomes memory**. A turn
produces at most one narrative-beat record plus one record per durable fact it
established. Everything else is a transient that is scored, rejected, and
discarded, and the rejection is reported in the generation trace so the write
policy is auditable rather than mysterious.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import EventSource, MemoryClass, MemoryScope

# A turn's prose becomes a scene memory only if it reads like a durable beat.
# Filler beats are still recorded, but as transient rows that expire with the
# scene and never enter a prompt as narrative context.
DURABLE_SIGNALS = (
    "reveals",
    "confesses",
    "discover",
    "learns",
    "promis",
    "oath",
    "vow",
    "betrays",
    "allies",
    "steals",
    "murder",
    "dies",
    "dead",
    "married",
    "betroth",
    "signed",
    "seal",
    "key to",
    "letter",
    "blood",
    "wound",
    "arrest",
)
TRANSIENT_SIGNALS = (
    "glances",
    "pauses",
    "shifts weight",
    "clears throat",
    "smiles faintly",
    "the rain continues",
    "a moment later",
)

# Facts extracted from event payloads rather than from prose.
FACT_EVENT_TYPES = frozenset(
    {
        "knowledge_acquired",
        "knowledge_suspected",
        "world_fact_created",
        "world_fact_modified",
        "item_acquired",
        "item_removed",
        "injury_added",
        "injury_removed",
        "character_died",
        "character_moved",
        "relationship_changed",
    }
)

PROSE_EVENT_TYPES = frozenset({"character_spoke", "character_performed_action", "user_action", "ai_action"})

# Importance bands. Kept explicit so a report can say why something was kept.
IMPORTANCE_TRANSIENT = 0.2
IMPORTANCE_SCENE = 0.45
IMPORTANCE_PERSISTENT = 0.7
IMPORTANCE_WORLD = 0.85
IMPORTANCE_CEILING = 0.95

_WORD = re.compile(r"[\w'-]+", re.UNICODE)


@dataclass
class MemorySeed:
    """What an event asserts, before any decision is made about it."""

    content: str
    event_type: str
    character_id: str | None = None
    scene_id: str | None = None
    source: str = "event"
    source_event_id: str | None = None
    facts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryProposal:
    """A candidate together with the decision made about it."""

    seed: MemorySeed
    memory_class: MemoryClass
    scope: MemoryScope
    importance: float
    persist: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.seed.content,
            "event_type": self.seed.event_type,
            "memory_class": self.memory_class.value,
            "scope": self.scope.value,
            "importance": round(self.importance, 3),
            "persist": self.persist,
            "reason": self.reason,
            "character_id": self.seed.character_id,
            "source": self.seed.source,
        }


def _lowered(value: str) -> str:
    return value.casefold()


def score_importance(content: str, *, event_type: str, has_facts: bool) -> float:
    """Deterministic importance in ``[0, 1]``.

    Deliberately not a model call: the milestone is about measurable reliability,
    and a learned scorer would make the memory layer untestable and
    non-reproducible.
    """
    text = _lowered(content)
    if not text.strip():
        return 0.0
    score = 0.3
    if event_type in FACT_EVENT_TYPES:
        score += 0.25
    if any(signal in text for signal in DURABLE_SIGNALS):
        score += 0.25
    if any(signal in text for signal in TRANSIENT_SIGNALS):
        score -= 0.15
    if has_facts:
        score += 0.1
    if len(_WORD.findall(content)) > 60:
        score -= 0.1
    return max(0.0, min(IMPORTANCE_CEILING, score))


def classify(seed: MemorySeed) -> tuple[MemoryClass, MemoryScope, str]:
    """Choose the class and scope for a seed.

    Class answers "how long does this live"; scope answers "whose eyes may see
    it". They are separate decisions, and conflating them is how a character's
    private knowledge ends up in another character's prompt.
    """
    character_id = seed.character_id
    if seed.event_type == "knowledge_acquired" and character_id:
        return MemoryClass.PERMANENT, MemoryScope.CHARACTER, "learned fact about a character"
    if seed.event_type == "knowledge_suspected" and character_id:
        return MemoryClass.PERMANENT, MemoryScope.CHARACTER, "unproven suspicion held by a character"
    if seed.event_type in {"world_fact_created", "world_fact_modified"}:
        return MemoryClass.PERMANENT, MemoryScope.WORLD, "committed world fact"
    if seed.event_type in {"character_died", "injury_added", "injury_removed"}:
        return MemoryClass.PERMANENT, MemoryScope.WORLD, "durable condition change"
    if seed.event_type in {"item_acquired", "item_removed", "relationship_changed"}:
        if character_id:
            return MemoryClass.PERMANENT, MemoryScope.CHARACTER, "durable character condition"
        return MemoryClass.PERSISTENT, MemoryScope.WORLD, "durable world condition"
    if seed.event_type == "character_moved":
        return MemoryClass.TRANSIENT, MemoryScope.SCENE, "position is state, not memory"
    if seed.facts and character_id:
        return MemoryClass.PERSISTENT, MemoryScope.CHARACTER, "fact asserted by a character event"
    return MemoryClass.SCENE, MemoryScope.TRANSIENT, "narrative beat for the active scene"


def proposals_for(
    seeds: Iterable[MemorySeed],
    *,
    minimum_importance: float = IMPORTANCE_SCENE,
) -> list[MemoryProposal]:
    """Turn seeds into scored, classified proposals.

    Proposals below the threshold are returned with ``persist=False`` rather than
    dropped, so the caller can report the decision.
    """
    proposals: list[MemoryProposal] = []
    for seed in seeds:
        if not seed.content.strip():
            continue
        memory_class, scope, reason = classify(seed)
        importance = score_importance(
            seed.content, event_type=seed.event_type, has_facts=bool(seed.facts)
        )
        if memory_class is MemoryClass.TRANSIENT:
            persist = False
            reason = "transient: recorded in state, not in memory"
        elif importance < minimum_importance:
            persist = False
            reason = f"importance {importance:.2f} below threshold {minimum_importance:.2f}"
        else:
            persist = True
        proposals.append(
            MemoryProposal(
                seed=seed,
                memory_class=memory_class,
                scope=scope,
                importance=importance,
                persist=persist,
                reason=reason,
            )
        )
    return proposals


def describe_event(event: Any) -> str:
    """A one-line, factual restatement of an event.

    Memory holds facts, not rhetoric. Restating the event rather than copying the
    prose is what keeps memory from becoming a transcript.
    """
    payload = dict(getattr(event, "payload", {}) or {})
    event_type = str(getattr(event, "event_type", ""))
    character_id = payload.get("character_id") or getattr(event, "actor_character_id", None)
    for key in ("fact", "item", "injury", "goal", "text", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            subject = f"{character_id}: " if character_id else ""
            return f"[{event_type}] {subject}{value.strip()}"
    return f"[{event_type}] {json.dumps(payload, sort_keys=True, default=str)}"


def beat_seed(prose: str, *, scene_id: str, actor_id: str | None, sequence: int) -> MemorySeed:
    """The narrative-beat record for a turn.

    One per generation, not three. The class it receives is decided by importance,
    so a throwaway line becomes a transient scene record and a turning point
    becomes a scene record worth injecting.
    """
    trimmed = " ".join(prose.split())
    return MemorySeed(
        content=trimmed,
        event_type="ai_action",
        character_id=actor_id,
        scene_id=scene_id,
        source="generation",
        facts=[],
        metadata={"sequence": sequence},
    )


def fact_seed(event: Any) -> MemorySeed | None:
    """Turn a committed event into a fact seed, or ``None`` if it is not one."""
    payload = dict(getattr(event, "payload", {}) or {})
    event_type = str(getattr(event, "event_type", ""))
    if event_type not in FACT_EVENT_TYPES:
        return None
    content = describe_event(event)
    facts: list[str] = []
    for key in ("fact", "item", "injury", "location_id", "relationship_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            facts.append(value.strip())
    raw_source = str(getattr(event, "source", "system"))
    return MemorySeed(
        content=content,
        event_type=event_type,
        character_id=str(payload["character_id"]) if payload.get("character_id") else None,
        scene_id=None,
        source=raw_source if raw_source in set(EventSource) else "system",
        source_event_id=str(getattr(event, "id", "")) or None,
        facts=facts,
    )


def retention_report(proposals: Sequence[MemoryProposal]) -> dict[str, Any]:
    """What the write policy decided, for the generation trace."""
    kept = [proposal for proposal in proposals if proposal.persist]
    dropped = [proposal for proposal in proposals if not proposal.persist]
    by_class: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for proposal in kept:
        by_class[proposal.memory_class.value] = by_class.get(proposal.memory_class.value, 0) + 1
        by_scope[proposal.scope.value] = by_scope.get(proposal.scope.value, 0) + 1
    return {
        "proposed": len(proposals),
        "persisted": len(kept),
        "dropped": len(dropped),
        "by_class": by_class,
        "by_scope": by_scope,
        "drop_reasons": sorted({proposal.reason.split(":")[0] for proposal in dropped}),
        "proposals": [proposal.to_dict() for proposal in proposals],
    }
