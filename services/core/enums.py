from __future__ import annotations

from enum import StrEnum
from typing import Any


class SceneStatus(StrEnum):
    DRAFT = "draft"
    STAGED = "staged"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ControlMode(StrEnum):
    AI = "ai"
    USER = "user"


class MemoryClass(StrEnum):
    PERMANENT = "permanent"
    PERSISTENT = "persistent"
    SCENE = "scene"
    WORKING = "working"
    ARCHIVE = "archive"
    TRANSIENT = "transient"


class MemoryScope(StrEnum):
    """Who a memory is valid for.

    Scope answers "which eyes may see this", which is a different question from
    :class:`MemoryClass`, which answers "how long should it live".
    """

    TRANSIENT = "transient"
    SCENE = "scene"
    CHARACTER = "character"
    WORLD = "world"


class KnowledgeKind(StrEnum):
    """The four kinds of "knowing" the engine refuses to conflate.

    ``WORLD`` is true for everyone, ``CHARACTER`` is a belief held by one
    character, ``PLAYER`` is out-of-band knowledge the operator supplied and no
    character has, and ``SOURCE`` exists only in imported material and is
    advisory rather than true.
    """

    WORLD = "world"
    CHARACTER = "character"
    PLAYER = "player"
    SOURCE = "source"


class IntentType(StrEnum):
    STORY_COMMITMENT = "story_commitment"
    DIRECTION = "direction"
    NARRATION = "narration"
    WORLD = "world"
    RETCON = "retcon"
    IMMEDIATE_ACTION = "immediate_action"


class Lane(StrEnum):
    """Execution lane assigned by the Intent Router.

    The router is the deterministic authority for lane selection. Lanes 1 and 2
    execute directly; lanes 3 and 4 go through the Director.
    """

    DIRECT_ACTOR = "direct_actor"
    SIMPLE_WORLD = "simple_world"
    DIRECTION = "direction"
    LONG_HORIZON = "long_horizon"


class AuthorityMode(StrEnum):
    """Who may fill the gaps. Narrative configuration, not a user property.

    Authority is freedom *inside* the user's constraint envelope — never
    permission to reinterpret explicit instructions. No mode, however
    permissive, may introduce an outcome the user explicitly excluded.
    """

    STRICT = "strict"
    COLLABORATIVE = "collaborative"
    DIRECTOR_ASSISTED = "director_assisted"
    AI_DIRECTED = "ai_directed"

    @classmethod
    def coerce(cls, value: Any) -> AuthorityMode:
        try:
            return cls(str(value).strip().casefold())
        except ValueError:
            return cls.DIRECTOR_ASSISTED

    @property
    def requires_approval(self) -> bool:
        """Whether a DirectorPlan may auto-proceed under this authority.

        ``strict`` and ``collaborative`` keep the human in the loop. The two
        permissive modes fill gaps and execute, which is what keeps an ordinary
        turn from stopping for approval.
        """
        return self in {AuthorityMode.STRICT, AuthorityMode.COLLABORATIVE}

    @property
    def infers_aggressively(self) -> bool:
        return self is AuthorityMode.AI_DIRECTED

    @property
    def offers_options(self) -> bool:
        """Whether an underspecified intent should surface concrete choices."""
        return self in {AuthorityMode.STRICT, AuthorityMode.COLLABORATIVE}


class Horizon(StrEnum):
    """How far ahead the Director is allowed to plan.

    Forward planning is commitment-driven and stays lightweight: the Director
    never plans further than a commitment's horizon, and never autonomously.
    """

    IMMEDIATE = "immediate"
    NEXT_FEW_TURNS = "next_few_turns"
    NEAR_TERM = "near_term"
    LONG_TERM = "long_term"
    END_STATE = "end_state"


class PlanStatus(StrEnum):
    """DirectorPlan lifecycle.

    ``proposed`` rows are proposals awaiting a decision, not narrative state:
    they emit no events, affect no projection, and can be superseded or
    cancelled. Only ``approved`` onward is a committed artifact.
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class BeatStatus(StrEnum):
    """Beat status.

    A beat is guidance toward a narrative objective, not an authoritative
    event. The Performer may combine, skip, reorder, or adapt beats; a beat
    that becomes obsolete is ``skipped`` or ``invalidated``, never an error.
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"


class IntentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class CommitmentStatus(StrEnum):
    """The story commitment lifecycle.

    ``pending`` is a legacy stored value that is read as :attr:`CREATED`, so rows
    written by earlier milestones keep resolving to a valid state.
    """

    CREATED = "created"
    ACTIVE = "active"
    PROGRESSING = "progressing"
    FULFILLED = "fulfilled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    @classmethod
    def coerce(cls, value: str) -> CommitmentStatus:
        try:
            return cls(str(value))
        except ValueError:
            return CommitmentStatus.CREATED if str(value) == "pending" else cls.FAILED

    @property
    def is_open(self) -> bool:
        return self in {CommitmentStatus.CREATED, CommitmentStatus.ACTIVE, CommitmentStatus.PROGRESSING}


OPEN_COMMITMENT_STATUSES: frozenset[str] = frozenset(
    {
        CommitmentStatus.CREATED.value,
        CommitmentStatus.ACTIVE.value,
        CommitmentStatus.PROGRESSING.value,
        IntentStatus.PENDING.value,
    }
)

TERMINAL_COMMITMENT_STATUSES: frozenset[str] = frozenset(
    {
        CommitmentStatus.FULFILLED.value,
        CommitmentStatus.FAILED.value,
        CommitmentStatus.CANCELLED.value,
        CommitmentStatus.SUPERSEDED.value,
        IntentStatus.COMPLETED.value,
        IntentStatus.CANCELLED.value,
    }
)

COMMITMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    CommitmentStatus.CREATED.value: frozenset(
        {
            CommitmentStatus.ACTIVE.value,
            CommitmentStatus.PROGRESSING.value,
            CommitmentStatus.FULFILLED.value,
            CommitmentStatus.FAILED.value,
            CommitmentStatus.CANCELLED.value,
            CommitmentStatus.SUPERSEDED.value,
        }
    ),
    CommitmentStatus.ACTIVE.value: frozenset(
        {
            CommitmentStatus.PROGRESSING.value,
            CommitmentStatus.FULFILLED.value,
            CommitmentStatus.FAILED.value,
            CommitmentStatus.CANCELLED.value,
            CommitmentStatus.SUPERSEDED.value,
        }
    ),
    CommitmentStatus.PROGRESSING.value: frozenset(
        {
            CommitmentStatus.FULFILLED.value,
            CommitmentStatus.FAILED.value,
            CommitmentStatus.CANCELLED.value,
            CommitmentStatus.SUPERSEDED.value,
        }
    ),
    CommitmentStatus.FULFILLED.value: frozenset(),
    CommitmentStatus.FAILED.value: frozenset({CommitmentStatus.SUPERSEDED.value}),
    CommitmentStatus.CANCELLED.value: frozenset(),
    CommitmentStatus.SUPERSEDED.value: frozenset(),
}


class GenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class EventSource(StrEnum):
    USER = "user"
    AI = "ai"
    SYSTEM = "system"
    IMPORT = "import"


class LorebookScope(StrEnum):
    PROJECT = "project"
    WORLD = "world"
    CHARACTER = "character"
