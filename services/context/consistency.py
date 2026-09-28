"""Lightweight consistency checking.

This is deliberately not a theorem prover. It looks for the handful of
contradictions that visibly break a narrative — a corpse that keeps talking, a
character in two places, a character citing a fact they were never told, an item
that was destroyed turning up in someone's hand — and reports them as
structured warnings.

Two rules shape the design:

- **The engine never silently rewrites state to hide a contradiction.** A
  warning is returned to the caller with the resolution options, and the state
  the user already committed is left alone.
- **Severity is explicit.** ``error`` means the claim is impossible and must
  not be committed; ``warning`` means the claim is improbable and is committed
  but surfaced.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.context.knowledge import KnowledgeView, knowledge_leaks, shares_words
from services.core.events import (
    CHARACTER_DIED,
    CHARACTER_MOVED,
    CHARACTER_PERFORMED_ACTION,
    CHARACTER_SPOKE,
    ITEM_ACQUIRED,
    ITEM_REMOVED,
    LOCATION_CHANGED,
)
from services.core.state import StateSnapshot

ERROR = "error"
WARNING = "warning"

ACTION_ACCEPT = "accept"
ACTION_REGENERATE = "regenerate"
ACTION_CORRECT_STATE = "correct_state"
DEFAULT_ACTIONS = (ACTION_ACCEPT, ACTION_REGENERATE, ACTION_CORRECT_STATE)

# Action-like events that assert a character is present and capable.
ACTION_EVENTS = frozenset(
    {
        CHARACTER_SPOKE,
        CHARACTER_PERFORMED_ACTION,
        CHARACTER_MOVED,
        LOCATION_CHANGED,
        ITEM_ACQUIRED,
        ITEM_REMOVED,
        CHARACTER_DIED,
    }
)

# Phrases that assert certainty. Used only to sharpen a knowledge warning, never
# to create one on its own.
CERTAINTY_MARKERS = ("is the murderer", "did it", "was the killer", "killed", "guilty", "confessed")

_LOCATION_TOKENS = re.compile(
    r"\b(?:in|at|inside|enters?|reaches?|arrives at)\s+(?:the\s+)?"
    r"([A-Z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2})"
)


@dataclass
class ContradictionWarning:
    """One detected inconsistency, with the evidence and the ways out."""

    code: str
    severity: str
    summary: str
    expected: str = ""
    observed: str = ""
    subject_character_id: str | None = None
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    actions: tuple[str, ...] = DEFAULT_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "expected": self.expected,
            "observed": self.observed,
            "subject_character_id": self.subject_character_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "actions": list(self.actions),
        }

    def render(self) -> str:
        lines = [f"Potential state contradiction: {self.summary}"]
        if self.expected:
            lines.append(f"Expected: {self.expected}")
        if self.observed:
            lines.append(f"Generated: {self.observed}")
        if self.actions:
            lines.append("[" + "][".join(self.actions) + "]")
        return "\n".join(lines)


def _text_of(event_type: str, payload: dict[str, Any]) -> str:
    for key in ("text", "action", "prose", "fact", "item", "injury", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _dead_character_acts(
    state: StateSnapshot, event_type: str, payload: dict[str, Any]
) -> ContradictionWarning | None:
    character_id = payload.get("character_id")
    if not character_id or character_id not in state.dead:
        return None
    if event_type == CHARACTER_DIED:
        return None
    return ContradictionWarning(
        code="dead_character_acts",
        severity=ERROR,
        summary=f"Character {character_id} is dead but performs a new action",
        expected="character is dead",
        observed=_text_of(event_type, payload) or event_type,
        subject_character_id=character_id,
        event_type=event_type,
        payload={"character_id": character_id},
    )


def _location_conflict(
    state: StateSnapshot, event_type: str, payload: dict[str, Any], scene_location: str
) -> ContradictionWarning | None:
    if event_type not in {CHARACTER_MOVED, LOCATION_CHANGED}:
        return None
    character_id = payload.get("character_id")
    destination = str(payload.get("location_id") or "")
    if not character_id or not destination or not scene_location:
        return None
    if scene_location.casefold() == destination.casefold():
        return None
    current = str(state.characters.get(character_id, {}).get("location_id") or "")
    if current.casefold() != scene_location.casefold():
        return None
    return ContradictionWarning(
        code="location_conflict",
        severity=ERROR,
        summary=f"Character {character_id} is in {scene_location} but moves to {destination}",
        expected=f"located in {scene_location}",
        observed=f"moves to {destination}",
        subject_character_id=character_id,
        event_type=event_type,
        payload={"character_id": character_id, "location_id": destination},
    )


def _removed_item_reused(
    state: StateSnapshot, event_type: str, payload: dict[str, Any]
) -> ContradictionWarning | None:
    if event_type not in {CHARACTER_SPOKE, CHARACTER_PERFORMED_ACTION}:
        return None
    character_id = payload.get("character_id")
    if not character_id:
        return None
    held = set(state.items.get(character_id, []))
    lost = set(state.removed_items.get(character_id, set())) - held
    if not lost:
        return None
    text = _text_of(event_type, payload)
    reused = sorted(item for item in lost if item and item.casefold() in text.casefold())
    if not reused:
        return None
    return ContradictionWarning(
        code="removed_item_reused",
        severity=ERROR,
        summary=f"Character {character_id} uses {reused[0]} after it was removed",
        expected=f"no longer holds {reused[0]}",
        observed=text,
        subject_character_id=character_id,
        event_type=event_type,
        payload={"character_id": character_id, "item": reused[0]},
    )


def _unknown_location(
    event_type: str, payload: dict[str, Any], known_locations: set[str]
) -> ContradictionWarning | None:
    if event_type not in {CHARACTER_MOVED, LOCATION_CHANGED}:
        return None
    if not known_locations:
        return None
    location_id = str(payload.get("location_id") or "")
    if not location_id or location_id in known_locations:
        return None
    return ContradictionWarning(
        code="unknown_location",
        severity=ERROR,
        summary=f"Event references location {location_id}, which the project does not define",
        expected=f"one of {sorted(known_locations)[:5]}",
        observed=location_id,
        event_type=event_type,
        payload={"location_id": location_id},
    )


def _knowledge_leak(
    event_type: str,
    payload: dict[str, Any],
    view: KnowledgeView,
) -> list[ContradictionWarning]:
    if event_type not in {CHARACTER_SPOKE, CHARACTER_PERFORMED_ACTION, ITEM_ACQUIRED}:
        return []
    character_id = payload.get("character_id")
    if not character_id or character_id != view.viewer_character_id:
        return []
    text = _text_of(event_type, payload)
    if not text:
        return []
    leaks = knowledge_leaks(view, [text])
    if not leaks:
        return []
    certain = any(marker in text.casefold() for marker in CERTAINTY_MARKERS)
    leaked = leaks[0]
    return [
        ContradictionWarning(
            code="knowledge_leak",
            severity=ERROR if certain else WARNING,
            summary=(
                f"Character {character_id} references a fact held by another character: "
                f"{leaked.fact}"
            ),
            expected="character has not learned this fact",
            observed=text,
            subject_character_id=character_id,
            event_type=event_type,
            payload={"character_id": character_id, "fact": leaked.fact, "held_by": leaked.character_id},
        )
    ]


def _prose_location_drift(
    prose: str, scene_location: str, state: StateSnapshot
) -> ContradictionWarning | None:
    if not prose or not scene_location:
        return None
    occupied = {
        str(value.get("location_id"))
        for value in state.characters.values()
        if value.get("location_id")
    }
    if not occupied:
        return None
    known = occupied | {scene_location}
    for match in _LOCATION_TOKENS.finditer(prose):
        mentioned = match.group(1).strip().rstrip(".,;:!?")
        if not mentioned:
            continue
        lowered = mentioned.casefold()
        if any(
            lowered == value.casefold() or lowered.startswith(value.casefold() + " ")
            for value in known
        ):
            continue
        if len(mentioned.split()) > 3:
            continue
        return ContradictionWarning(
            code="prose_location_drift",
            severity=WARNING,
            summary=f"Prose places the scene in {mentioned}, which no character occupies",
            expected=f"scene in {scene_location}",
            observed=mentioned,
            event_type="prose",
            payload={"mentioned": mentioned},
        )
    return None


def detect(
    state: StateSnapshot,
    event_type: str,
    payload: dict[str, Any],
    *,
    scene_location: str = "",
    known_locations: Iterable[str] = (),
    knowledge: KnowledgeView | None = None,
    prose: str = "",
) -> list[ContradictionWarning]:
    """Run every detector over one claimed event."""
    warnings: list[ContradictionWarning] = []
    for detector in (
        lambda: _dead_character_acts(state, event_type, payload),
        lambda: _location_conflict(state, event_type, payload, scene_location),
        lambda: _removed_item_reused(state, event_type, payload),
        lambda: _unknown_location(event_type, payload, set(known_locations)),
    ):
        warning = detector()
        if warning is not None:
            warnings.append(warning)
    if knowledge is not None:
        warnings.extend(_knowledge_leak(event_type, payload, knowledge))
    if event_type not in ACTION_EVENTS:
        drift = _prose_location_drift(prose, scene_location, state)
        if drift is not None:
            warnings.append(drift)
    return warnings


class ContradictionDetector:
    """Stateless detector bound to one turn's context.

    Holding the scene location, the known location set, and the acting
    character's knowledge view once per turn keeps every claim checkable against
    the same reference state, which is what makes the result reproducible.
    """

    def __init__(
        self,
        state: StateSnapshot,
        *,
        scene_location: str = "",
        known_locations: Sequence[str] = (),
        knowledge: KnowledgeView | None = None,
    ) -> None:
        self.state = state
        self.scene_location = scene_location
        self.known_locations = set(known_locations)
        self.knowledge = knowledge

    def for_event(
        self, event_type: str, payload: dict[str, Any], *, prose: str = ""
    ) -> list[ContradictionWarning]:
        return detect(
            self.state,
            event_type,
            payload,
            scene_location=self.scene_location,
            known_locations=self.known_locations,
            knowledge=self.knowledge,
            prose=prose,
        )

    def for_prose(self, prose: str) -> list[ContradictionWarning]:
        warnings: list[ContradictionWarning] = []
        drift = _prose_location_drift(prose, self.scene_location, self.state)
        if drift is not None:
            warnings.append(drift)
        if self.knowledge is not None and self.knowledge.viewer_character_id:
            known = set(self.knowledge.visible_texts())
            for fact in self.knowledge.withheld:
                if shares_words(prose, fact.fact) and fact.fact.casefold() not in {
                    value.casefold() for value in known
                }:
                    warnings.append(
                        ContradictionWarning(
                            code="knowledge_leak_prose",
                            severity=WARNING,
                            summary=(
                                f"Prose states a fact that the acting character has not learned: "
                                f"{fact.fact}"
                            ),
                            expected="actor has not learned this fact",
                            observed=prose[:240],
                            subject_character_id=self.knowledge.viewer_character_id,
                            event_type="prose",
                            payload={"fact": fact.fact},
                        )
                    )
                    break
        return warnings


def errors_only(warnings: Sequence[ContradictionWarning]) -> list[ContradictionWarning]:
    return [warning for warning in warnings if warning.severity == ERROR]


def summarise(warnings: Sequence[ContradictionWarning]) -> str:
    if not warnings:
        return ""
    return " | ".join(f"{warning.severity}:{warning.code}" for warning in warnings)
