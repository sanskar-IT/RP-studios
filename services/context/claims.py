"""Separating generated prose from claimed state changes.

A model returns one object containing both. The engine treats it as two
different things:

- **prose**, which is presentation, and is stored but never treated as truth;
- **claims**, which are assertions about state, and are validated against the
  current projection before anything is committed.

Nothing is committed until validation passes. A generation whose claims are
impossible is rejected with structured reasons, and the caller is told which
recovery to use rather than being handed a half-applied turn.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.context.consistency import (
    ERROR,
    WARNING,
    ContradictionWarning,
)
from services.core.events import KNOWN_EVENT_TYPES, validate_event_payload
from services.core.state import StateSnapshot

# Event fields that name a character. A claim may not reference a character that
# is not in the scene; that is a validation error, not a warning.
CHARACTER_FIELDS = ("character_id", "source_character_id", "target_character_id")

# Keys in a claim object that select the event type, in precedence order.
EVENT_TYPE_KEYS = ("event_type", "type", "kind")

# Arrays in a provider response that carry claims, in the order they are read.
CLAIM_ARRAYS = ("new_events", "state_changes", "knowledge_changes", "relationship_changes")

VALID = "valid"
REJECTED = "rejected"
ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"

RECOVERY_ACTIONS = ("retry", "regenerate", "edit", "reject")


@dataclass
class ClaimedEvent:
    event_type: str
    payload: dict[str, Any]
    origin: str = "new_events"
    warnings: list[ContradictionWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "origin": self.origin,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass
class GenerationClaims:
    """Everything a single generation asserted."""

    prose: str = ""
    selected_actor: str = ""
    reason: str = ""
    events: list[ClaimedEvent] = field(default_factory=list)
    knowledge_changes: list[dict[str, Any]] = field(default_factory=list)
    relationship_changes: list[dict[str, Any]] = field(default_factory=list)
    open_commitments: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def knowledge_events(self) -> list[ClaimedEvent]:
        return [event for event in self.events if event.event_type.startswith("knowledge_")]

    @property
    def relationship_events(self) -> list[ClaimedEvent]:
        return [event for event in self.events if event.event_type == "relationship_changed"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prose": self.prose,
            "selected_actor": self.selected_actor,
            "reason": self.reason,
            "events": [event.to_dict() for event in self.events],
            "knowledge_changes": self.knowledge_changes,
            "relationship_changes": self.relationship_changes,
            "open_commitments": self.open_commitments,
            "errors": self.errors,
        }


@dataclass
class ClaimValidation:
    """The verdict on a set of claims, with the reason for every rejection."""

    status: str
    accepted: list[ClaimedEvent] = field(default_factory=list)
    rejected: list[ClaimedEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[ContradictionWarning] = field(default_factory=list)
    recovery_actions: tuple[str, ...] = RECOVERY_ACTIONS

    @property
    def valid(self) -> bool:
        return self.status in {VALID, ACCEPTED_WITH_WARNINGS}

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid": self.valid,
            "accepted_event_count": len(self.accepted),
            "rejected_event_count": len(self.rejected),
            "errors": self.errors,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "rejected": [event.to_dict() for event in self.rejected],
            "recovery_actions": list(self.recovery_actions),
        }


def _event_type_of(value: dict[str, Any]) -> str:
    nested = value.get("payload")
    nested_payload = nested if isinstance(nested, dict) else value
    for key in EVENT_TYPE_KEYS:
        candidate = value.get(key) or nested_payload.get(key)
        if candidate:
            return str(candidate)
    return ""


def _payload_of(value: dict[str, Any]) -> dict[str, Any]:
    nested = value.get("payload")
    nested_payload = nested if isinstance(nested, dict) else value
    return {
        key: item
        for key, item in nested_payload.items()
        if key not in {"event_type", "type", "kind", "payload"}
    }


def extract_claims(
    structured: dict[str, Any],
    *,
    actor_id: str = "",
    participant_ids: set[str] | None = None,
) -> GenerationClaims:
    """Pull claims out of a provider response without judging them.

    Extraction is total: it never raises on a malformed claim, so validation can
    report every problem in one pass instead of failing on the first.
    """
    allowed = participant_ids if participant_ids is not None else set()
    claims = GenerationClaims(
        prose=str(structured.get("prose") or ""),
        selected_actor=str(structured.get("selected_actor") or ""),
        reason=str(structured.get("reason") or ""),
    )
    knowledge_changes = structured.get("knowledge_changes")
    relationship_changes = structured.get("relationship_changes")
    claims.knowledge_changes = [item for item in knowledge_changes if isinstance(item, dict)] if isinstance(knowledge_changes, list) else []
    claims.relationship_changes = [item for item in relationship_changes if isinstance(item, dict)] if isinstance(relationship_changes, list) else []
    commitments = structured.get("open_commitments")
    claims.open_commitments = [item for item in commitments if isinstance(item, dict)] if isinstance(commitments, list) else []
    for origin in CLAIM_ARRAYS:
        values = structured.get(origin)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                claims.errors.append(f"{origin} contains a non-object entry")
                continue
            event_type = _event_type_of(value)
            payload = _payload_of(value)
            if not payload.get("character_id") and actor_id:
                payload["character_id"] = actor_id
            claimed = ClaimedEvent(event_type=event_type, payload=payload, origin=origin)
            for field_name in CHARACTER_FIELDS:
                referenced = payload.get(field_name)
                if referenced and allowed and str(referenced) not in allowed:
                    claimed.warnings.append(
                        _reference_warning(str(referenced), origin, field_name)
                    )
            claims.events.append(claimed)
    return claims


def _reference_warning(character_id: str, origin: str, field_name: str) -> ContradictionWarning:
    from services.context.consistency import ERROR as SEVERITY_ERROR

    return ContradictionWarning(
        code="non_participant_reference",
        severity=SEVERITY_ERROR,
        summary=f"Claim references {character_id} via {field_name}, who is not a scene participant",
        expected="a scene participant",
        observed=character_id,
        subject_character_id=character_id,
        payload={"origin": origin, "field": field_name},
    )


def validate_claims(
    claims: GenerationClaims,
    state: StateSnapshot,
    *,
    detector: Any | None = None,
) -> ClaimValidation:
    """Judge claims against the current projection.

    A claim is rejected when the event type is unknown, the payload is
    structurally invalid, it names a character outside the scene, or a
    contradiction detector reports it as impossible. Warnings are recorded and
    the claim is accepted, because refusing to commit a merely improbable turn
    would make the studio unusable while the operator is improvising.
    """
    accepted: list[ClaimedEvent] = []
    rejected: list[ClaimedEvent] = []
    errors: list[str] = list(claims.errors)
    warnings: list[ContradictionWarning] = []
    for claim in claims.events:
        claim_errors: list[str] = []
        if not claim.event_type:
            claim_errors.append("Claim does not name an event type")
        elif claim.event_type not in KNOWN_EVENT_TYPES:
            claim_errors.append(f"Unsupported event type: {claim.event_type}")
        if not claim_errors:
            try:
                validate_event_payload(claim.event_type, claim.payload)
            except ValueError as exc:
                claim_errors.append(str(exc))
        found: list[ContradictionWarning] = list(claim.warnings)
        if detector is not None and not claim_errors:
            found.extend(detector.for_event(claim.event_type, claim.payload))
        blocking = [warning for warning in found if warning.severity == ERROR]
        if blocking:
            claim_errors.extend(warning.summary for warning in blocking)
        if claim_errors:
            errors.extend(f"{claim.origin}: {message}" for message in claim_errors)
            claim.warnings = [warning for warning in found if warning.severity != ERROR]
            rejected.append(claim)
            continue
        claim.warnings = [warning for warning in found if warning.severity != ERROR]
        warnings.extend(claim.warnings)
        accepted.append(claim)
    if rejected or errors:
        return ClaimValidation(
            status=REJECTED,
            accepted=accepted,
            rejected=rejected,
            errors=errors,
            warnings=warnings,
        )
    status = ACCEPTED_WITH_WARNINGS if warnings else VALID
    return ClaimValidation(
        status=status,
        accepted=accepted,
        rejected=rejected,
        errors=errors,
        warnings=warnings,
        recovery_actions=RECOVERY_ACTIONS,
    )


def warnings_with_severity(
    warnings: Iterable[ContradictionWarning], severity: str = WARNING
) -> list[ContradictionWarning]:
    return [warning for warning in warnings if warning.severity == severity]


def claim_summary(claims: GenerationClaims) -> list[dict[str, Any]]:
    return [
        {
            "event_type": claim.event_type,
            "origin": claim.origin,
            "character_id": claim.payload.get("character_id"),
            "summary": claim.payload.get("text")
            or claim.payload.get("fact")
            or claim.payload.get("item")
            or claim.payload.get("injury")
            or claim.payload.get("goal")
            or "",
        }
        for claim in claims.events
    ]


def event_tuples(claims: Sequence[ClaimedEvent]) -> list[tuple[str, dict[str, Any]]]:
    return [(claim.event_type, dict(claim.payload)) for claim in claims]
