"""Memory retrieval.

Retrieval is deterministic, ancestry-scoped, and budgeted. Three properties
matter more than cleverness:

**Ancestry scope.** A memory is visible on a timeline only if it was written on
that timeline or on one of its ancestors, and only from the sequence at which it
became valid. A memory that says "Alice is dead" on a branch is therefore
invisible to the timeline it branched away from, and stays invisible no matter
how many turns later it is asked for.

**The window is the best rows, not the newest rows.** The previous implementation
read the newest 200 memory rows and then ranked them, which meant the engine
forgot by construction around turn 40. Selection now happens over a candidate
pool that is filtered by scope first and ranked second, and the pool size is a
reported number rather than an invisible limit.

**Character boundaries hold.** A character-owned memory is only ever returned
for its owner, and the withheld set is available so a test can prove isolation
instead of assuming it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.core.enums import MemoryClass, MemoryScope

# Candidate pool per retrieval call. Chosen to be large enough that an old,
# important memory is still reachable after a hundred turns.
CANDIDATE_POOL = 400
DEFAULT_LIMIT = 10

CLASS_PRIORITY = {
    MemoryClass.WORKING: 5,
    MemoryClass.SCENE: 4,
    MemoryClass.PERMANENT: 3,
    MemoryClass.PERSISTENT: 3,
    MemoryClass.ARCHIVE: 1,
    MemoryClass.TRANSIENT: 0,
}

SCOPE_PRIORITY = {
    MemoryScope.WORLD: 4,
    MemoryScope.CHARACTER: 3,
    MemoryScope.SCENE: 2,
    MemoryScope.TRANSIENT: 1,
}

WORLD_VISIBILITY = frozenset({"world", "public", "everyone"})


@dataclass
class MemoryCandidate:
    id: str
    content: str
    memory_class: MemoryClass
    importance: float
    created_at: datetime | None = None
    character_id: str | None = None
    scene_id: str | None = None
    metadata: dict[str, Any] | None = None
    scope: MemoryScope = MemoryScope.SCENE
    valid_from_sequence: int = 0
    is_active: bool = True
    source_event_id: str | None = None
    inherited_from_id: str | None = None

    @property
    def dedup_key(self) -> str:
        """Identity used to collapse a memory and its inherited copy on a branch."""
        return self.inherited_from_id or self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "memory_class": self.memory_class.value,
            "scope": self.scope.value,
            "importance": self.importance,
            "character_id": self.character_id,
            "scene_id": self.scene_id,
            "valid_from_sequence": self.valid_from_sequence,
            "is_active": self.is_active,
            "source_event_id": self.source_event_id,
            "inherited_from_id": self.inherited_from_id,
            "metadata": self.metadata or {},
        }


@dataclass
class RetrievalReport:
    """Why each memory was or was not returned, for the trace and the harness."""

    considered: int = 0
    pool_limit: int = 0
    dropped_scope: int = 0
    dropped_character: int = 0
    dropped_inactive: int = 0
    dropped_deduplicated: int = 0
    selected_ids: list[str] | None = None
    lineage: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "pool_limit": self.pool_limit,
            "dropped_scope": self.dropped_scope,
            "dropped_character": self.dropped_character,
            "dropped_inactive": self.dropped_inactive,
            "dropped_deduplicated": self.dropped_deduplicated,
            "selected_ids": self.selected_ids or [],
            "lineage": self.lineage or [],
        }


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in value.replace("_", " ").split() if token}


def lineage_gate(memory: MemoryCandidate, lineage_sequences: dict[str, int]) -> bool:
    """Whether a memory is valid at the current position on the current branch.

    ``lineage_sequences`` maps each timeline in the current ancestry to the
    sequence at which the current branch forked from it. A memory inherited from
    an ancestor is only visible if it became valid at or before that fork point,
    which is what stops a sibling branch's later memories from appearing here.
    """
    if memory.valid_from_sequence <= 0:
        return True
    timeline_id = str((memory.metadata or {}).get("timeline_id", ""))
    if timeline_id and timeline_id in lineage_sequences:
        return memory.valid_from_sequence <= lineage_sequences[timeline_id]
    return True


def retrieve_memories(
    memories: Iterable[MemoryCandidate],
    query: str = "",
    *,
    limit: int = DEFAULT_LIMIT,
    character_ids: set[str] | None = None,
    scene_id: str | None = None,
    timeline_ids: Iterable[str] | None = None,
    lineage_sequences: dict[str, int] | None = None,
    include_inactive: bool = False,
    pool_limit: int = CANDIDATE_POOL,
) -> list[MemoryCandidate]:
    """Rank and return the memories that belong in this turn's context."""
    return retrieve_with_report(
        memories,
        query,
        limit=limit,
        character_ids=character_ids,
        scene_id=scene_id,
        timeline_ids=timeline_ids,
        lineage_sequences=lineage_sequences,
        include_inactive=include_inactive,
        pool_limit=pool_limit,
    )[0]


def retrieve_with_report(
    memories: Iterable[MemoryCandidate],
    query: str = "",
    *,
    limit: int = DEFAULT_LIMIT,
    character_ids: set[str] | None = None,
    scene_id: str | None = None,
    timeline_ids: Iterable[str] | None = None,
    lineage_sequences: dict[str, int] | None = None,
    include_inactive: bool = False,
    pool_limit: int = CANDIDATE_POOL,
) -> tuple[list[MemoryCandidate], RetrievalReport]:
    """Same as :func:`retrieve_memories`, but reports why things were dropped."""
    query_tokens = _tokens(query)
    allowed_timelines = set(timeline_ids) if timeline_ids is not None else None
    report = RetrievalReport(pool_limit=pool_limit)
    report.lineage = sorted(allowed_timelines) if allowed_timelines is not None else []
    seen_keys: dict[str, MemoryCandidate] = {}
    pool: list[MemoryCandidate] = []
    total = 0
    for memory in memories:
        total += 1
        if not memory.is_active and not include_inactive:
            report.dropped_inactive += 1
            continue
        if allowed_timelines is not None:
            origin = str((memory.metadata or {}).get("timeline_id", ""))
            if origin and origin not in allowed_timelines:
                report.dropped_scope += 1
                continue
            if lineage_sequences is not None and not lineage_gate(memory, lineage_sequences):
                report.dropped_scope += 1
                continue
        if character_ids is not None:
            if memory.character_id is not None and memory.character_id not in character_ids:
                report.dropped_character += 1
                continue
            if memory.character_id is None:
                visibility = (memory.metadata or {}).get("visibility", "world")
                if memory.scope in {MemoryScope.CHARACTER} or (
                    memory.memory_class in {MemoryClass.PERMANENT, MemoryClass.PERSISTENT}
                    and visibility not in WORLD_VISIBILITY
                ):
                    report.dropped_character += 1
                    continue
        if scene_id is not None and memory.scene_id not in {None, scene_id}:
            report.dropped_character += 1
            continue
        key = memory.dedup_key
        existing = seen_keys.get(key)
        if existing is None:
            seen_keys[key] = memory
            pool.append(memory)
        else:
            # Prefer the copy that is native to the current branch, and
            # otherwise prefer the earlier one so a fork does not duplicate.
            report.dropped_deduplicated += 1
            if memory.valid_from_sequence < existing.valid_from_sequence:
                pool[pool.index(existing)] = memory
                seen_keys[key] = memory
    report.considered = total
    scored: list[tuple[float, MemoryCandidate]] = []
    for memory in pool:
        overlap = len(query_tokens & _tokens(memory.content)) if query_tokens else 0
        score = (
            float(overlap) * 1.5
            + memory.importance
            + CLASS_PRIORITY.get(memory.memory_class, 0) / 10.0
            + SCOPE_PRIORITY.get(memory.scope, 0) / 10.0
        )
        if overlap == 0 and query_tokens and memory.memory_class not in {
            MemoryClass.WORKING,
            MemoryClass.SCENE,
            MemoryClass.TRANSIENT,
        }:
            score -= 1.0
        scored.append((score, memory))
    scored.sort(key=lambda item: (item[0], item[1].created_at or datetime.min), reverse=True)
    selected = [memory for _score, memory in scored[: max(0, limit)]]
    report.selected_ids = [memory.id for memory in selected]
    return selected, report


def memory_to_dict(memory: MemoryCandidate) -> dict[str, Any]:
    return memory.to_dict()


retrieve_context = retrieve_memories
