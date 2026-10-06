from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from services.core.events import (
    AI_ACTION,
    CANON_DIVERGENCE,
    CHARACTER_DIED,
    CHARACTER_MOVED,
    CHARACTER_PERFORMED_ACTION,
    CHARACTER_SPOKE,
    COMMITMENT_STATUS_CHANGED,
    DIRECTOR_INTENT_COMPLETED,
    GENERATION_REJECTED,
    INJURY_ADDED,
    INJURY_REMOVED,
    ITEM_ACQUIRED,
    ITEM_REMOVED,
    KNOWLEDGE_ACQUIRED,
    KNOWLEDGE_REFUTED,
    KNOWLEDGE_SUSPECTED,
    LOCATION_CHANGED,
    POSSESSION_CHANGED,
    RELATIONSHIP_CHANGED,
    SCENE_CANCELLED,
    SCENE_ENDED,
    SCENE_STAGING_EDITED,
    SCENE_STARTED,
    USER_ACTION,
    WORLD_FACT_CREATED,
    WORLD_FACT_MODIFIED,
)

# ``locations`` previously accumulated every location id any character had ever
# visited, so the projected state — and therefore every prompt — grew without
# bound. Only locations that are currently occupied are retained.
MAX_TRACKED_LOCATIONS = 24


@dataclass
class StateSnapshot:
    characters: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    world_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge: dict[str, set[str]] = field(default_factory=dict)
    suspicions: dict[str, set[str]] = field(default_factory=dict)
    items: dict[str, list[str]] = field(default_factory=dict)
    injuries: dict[str, list[str]] = field(default_factory=dict)
    control_modes: dict[str, str] = field(default_factory=dict)
    completed_intents: set[str] = field(default_factory=set)
    commitments: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected_generations: dict[str, dict[str, Any]] = field(default_factory=dict)
    removed_items: dict[str, set[str]] = field(default_factory=dict)
    canon_divergences: dict[str, dict[str, Any]] = field(default_factory=dict)
    dead: set[str] = field(default_factory=set)
    active_scene_id: str | None = None
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "characters": deepcopy(self.characters),
            "relationships": deepcopy(self.relationships),
            "locations": deepcopy(self.locations),
            "world_facts": deepcopy(self.world_facts),
            "knowledge": {key: sorted(value) for key, value in self.knowledge.items()},
            "suspicions": {key: sorted(value) for key, value in self.suspicions.items()},
            "items": deepcopy(self.items),
            "injuries": deepcopy(self.injuries),
            "control_modes": deepcopy(self.control_modes),
            "completed_intents": sorted(self.completed_intents),
            "commitments": deepcopy(self.commitments),
            "rejected_generations": deepcopy(self.rejected_generations),
            "removed_items": {key: sorted(value) for key, value in self.removed_items.items()},
            "canon_divergences": deepcopy(self.canon_divergences),
            "dead": sorted(self.dead),
            "active_scene_id": self.active_scene_id,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> StateSnapshot:
        value = value or {}
        return cls(
            characters=deepcopy(value.get("characters", {})),
            relationships=deepcopy(value.get("relationships", {})),
            locations=deepcopy(value.get("locations", {})),
            world_facts=deepcopy(value.get("world_facts", {})),
            knowledge={key: set(items) for key, items in value.get("knowledge", {}).items()},
            suspicions={key: set(items) for key, items in value.get("suspicions", {}).items()},
            items=deepcopy(value.get("items", {})),
            injuries=deepcopy(value.get("injuries", {})),
            control_modes=deepcopy(value.get("control_modes", {})),
            completed_intents=set(value.get("completed_intents", [])),
            commitments=deepcopy(value.get("commitments", {})),
            rejected_generations=deepcopy(value.get("rejected_generations", {})),
            removed_items={key: set(items) for key, items in value.get("removed_items", {}).items()},
            canon_divergences=deepcopy(value.get("canon_divergences", {})),
            dead=set(value.get("dead", [])),
            active_scene_id=value.get("active_scene_id"),
            revision=int(value.get("revision", 0)),
        )


def _character(snapshot: StateSnapshot, character_id: str) -> dict[str, Any]:
    return snapshot.characters.setdefault(character_id, {"character_id": character_id})


# Actions whose *content* is speech, and speech is not automatically shared.
#
# What each of these means: the character moved, held something, or spoke. The
# second is a thing anyone present can observe, so it is kept — continuity
# ("where did the locket go?") depends on it. The first is not, so it is reduced
# to a non-content label. Keeping the words here is what made a one-turn leak
# permanent: state is re-projected from the event log forever, and the brief
# builder puts ``last_action`` into every character's prompt, so one secret
# spoken quietly to a single person became a permanent, unknowable leak into
# every other character's context on every subsequent turn.
# Empty, not a label like "spoke".
#
# A content-free label is still a *positive* claim that the brief builders read
# as intent: ``_goal_for`` in the Director turns any non-empty ``last_action``
# into "continue: <action>", which reaches the Performer as beat direction.
# "continue: spoke" is worse than the fallback it displaces ("observe and
# respond to the current situation"), because it looks like a real instruction
# while describing nothing.
#
# The fact that a character spoke is already carried by the event log and by
# recent events within the window; what must not be carried forward is the
# content. So the field records nothing for speech, and ``last_action_type``
# still says *that* it happened.
_LAST_ACTION_SUMMARY: dict[str, str] = {
    CHARACTER_SPOKE: "",
    USER_ACTION: "",
    AI_ACTION: "",
}


def _last_action_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type in _LAST_ACTION_SUMMARY:
        return _LAST_ACTION_SUMMARY[event_type]
    return str(payload.get("text", payload.get("action", "")))


def apply_event(snapshot: StateSnapshot, event_type: str, payload: dict[str, Any]) -> StateSnapshot:
    next_snapshot = StateSnapshot.from_dict(snapshot.to_dict())
    if event_type in {CHARACTER_MOVED, LOCATION_CHANGED}:
        character_id = payload.get("character_id")
        if character_id:
            _character(next_snapshot, character_id)["location_id"] = payload.get("location_id")
        location_id = payload.get("location_id")
        if location_id:
            next_snapshot.locations.setdefault(location_id, {"location_id": location_id}).update(
                {key: value for key, value in payload.items() if key not in {"event_type"}}
            )
            # Only currently occupied locations are retained. Keeping every
            # location a character had ever visited made the projected state,
            # and therefore every prompt, grow without bound.
            occupied = {
                str(value.get("location_id"))
                for value in next_snapshot.characters.values()
                if value.get("location_id")
            }
            for stale_id in [
                key for key in next_snapshot.locations if key not in occupied
            ]:
                del next_snapshot.locations[stale_id]
    elif event_type in {CHARACTER_SPOKE, CHARACTER_PERFORMED_ACTION, USER_ACTION, AI_ACTION}:
        character_id = payload.get("character_id")
        if character_id:
            character = _character(next_snapshot, character_id)
            character["last_action"] = _last_action_summary(event_type, payload)
            character["last_action_type"] = event_type
    elif event_type == RELATIONSHIP_CHANGED:
        source_id = payload.get("source_character_id")
        target_id = payload.get("target_character_id")
        if source_id and target_id:
            key = f"{source_id}:{target_id}"
            relationship = next_snapshot.relationships.setdefault(
                key, {"source_character_id": source_id, "target_character_id": target_id}
            )
            relationship.update(
                {
                    "relationship_type": payload.get("relationship_type", relationship.get("relationship_type", "unknown")),
                    "strength": payload.get("strength", relationship.get("strength", 0.0)),
                }
            )
    elif event_type == KNOWLEDGE_ACQUIRED:
        character_id = payload.get("character_id")
        fact = payload.get("fact") or payload.get("fact_id")
        if character_id and fact:
            _character(next_snapshot, character_id)
            next_snapshot.knowledge.setdefault(character_id, set()).add(str(fact))
            next_snapshot.suspicions.get(character_id, set()).discard(str(fact))
    elif event_type == KNOWLEDGE_SUSPECTED:
        character_id = payload.get("character_id")
        fact = payload.get("fact") or payload.get("fact_id")
        if character_id and fact:
            _character(next_snapshot, character_id)
            if str(fact) not in next_snapshot.knowledge.get(character_id, set()):
                next_snapshot.suspicions.setdefault(character_id, set()).add(str(fact))
    elif event_type == KNOWLEDGE_REFUTED:
        character_id = payload.get("character_id")
        fact = payload.get("fact") or payload.get("fact_id")
        if character_id and fact:
            next_snapshot.knowledge.get(character_id, set()).discard(str(fact))
            next_snapshot.suspicions.get(character_id, set()).discard(str(fact))
    elif event_type == ITEM_ACQUIRED:
        character_id = payload.get("character_id")
        item = payload.get("item") or payload.get("item_id")
        if character_id and item:
            next_snapshot.items.setdefault(character_id, [])
            if str(item) not in next_snapshot.items[character_id]:
                next_snapshot.items[character_id].append(str(item))
            next_snapshot.removed_items.get(character_id, set()).discard(str(item))
    elif event_type == ITEM_REMOVED:
        character_id = payload.get("character_id")
        item = payload.get("item") or payload.get("item_id")
        if character_id and item:
            next_snapshot.items[character_id] = [
                existing for existing in next_snapshot.items.get(character_id, []) if existing != str(item)
            ]
            # Retained so the consistency layer can notice a later use of
            # something the character no longer holds.
            next_snapshot.removed_items.setdefault(character_id, set()).add(str(item))
    elif event_type == INJURY_ADDED:
        character_id = payload.get("character_id")
        injury = payload.get("injury") or payload.get("injury_id")
        if character_id and injury and str(injury) not in next_snapshot.injuries.setdefault(character_id, []):
            next_snapshot.injuries[character_id].append(str(injury))
    elif event_type == INJURY_REMOVED:
        character_id = payload.get("character_id")
        injury = payload.get("injury") or payload.get("injury_id")
        if character_id and injury:
            next_snapshot.injuries[character_id] = [
                existing for existing in next_snapshot.injuries.get(character_id, []) if existing != str(injury)
            ]
    elif event_type == WORLD_FACT_CREATED:
        fact_id = payload.get("fact_id") or payload.get("id")
        if fact_id:
            next_snapshot.world_facts[str(fact_id)] = {
                key: value for key, value in payload.items() if key != "event_type"
            }
    elif event_type == WORLD_FACT_MODIFIED:
        fact_id = payload.get("fact_id") or payload.get("id")
        if fact_id:
            fact = next_snapshot.world_facts.setdefault(str(fact_id), {"fact_id": str(fact_id)})
            fact.update({key: value for key, value in payload.items() if key not in {"event_type", "fact_id"}})
    elif event_type == CHARACTER_DIED:
        character_id = payload.get("character_id")
        if character_id:
            next_snapshot.dead.add(character_id)
            _character(next_snapshot, character_id)["alive"] = False
    elif event_type in {SCENE_STAGING_EDITED}:
        pass
    elif event_type == POSSESSION_CHANGED:
        character_id = payload.get("character_id")
        if character_id and payload.get("control_mode"):
            next_snapshot.control_modes[str(character_id)] = str(payload["control_mode"])
    elif event_type == SCENE_STARTED:
        next_snapshot.active_scene_id = payload.get("scene_id")
    elif event_type == SCENE_ENDED:
        if next_snapshot.active_scene_id == payload.get("scene_id"):
            next_snapshot.active_scene_id = None
    elif event_type == SCENE_CANCELLED:
        if next_snapshot.active_scene_id == payload.get("scene_id"):
            next_snapshot.active_scene_id = None
    elif event_type == DIRECTOR_INTENT_COMPLETED:
        intent_id = payload.get("intent_id")
        if intent_id:
            next_snapshot.completed_intents.add(str(intent_id))
    elif event_type == COMMITMENT_STATUS_CHANGED:
        commitment_id = payload.get("commitment_id")
        if commitment_id:
            record = next_snapshot.commitments.setdefault(
                str(commitment_id), {"commitment_id": str(commitment_id)}
            )
            record["status"] = str(payload.get("to_status", ""))
            if payload.get("from_status"):
                record["previous_status"] = str(payload["from_status"])
            if payload.get("progress") is not None:
                record["progress"] = payload["progress"]
    elif event_type == GENERATION_REJECTED:
        generation_id = payload.get("generation_id")
        if generation_id:
            next_snapshot.rejected_generations[str(generation_id)] = {
                "reason": str(payload.get("reason", "")),
                "scene_id": payload.get("scene_id"),
            }
    elif event_type == CANON_DIVERGENCE:
        divergence_id = str(payload.get("id", len(next_snapshot.canon_divergences)))
        next_snapshot.canon_divergences[divergence_id] = {
            key: value for key, value in payload.items() if key != "event_type"
        }
    next_snapshot.revision += 1
    return next_snapshot


def project_state(base: StateSnapshot | dict[str, Any] | None, events: Iterable[dict[str, Any]]) -> StateSnapshot:
    snapshot = base if isinstance(base, StateSnapshot) else StateSnapshot.from_dict(base)
    for event in events:
        snapshot = apply_event(snapshot, str(event["event_type"]), dict(event.get("payload", {})))
    return snapshot


def reconstruct_state(checkpoint: dict[str, Any] | None, events: Iterable[dict[str, Any]]) -> StateSnapshot:
    return project_state(StateSnapshot.from_dict(checkpoint), events)


replay_events = project_state
