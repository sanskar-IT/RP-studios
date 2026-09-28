from __future__ import annotations

from typing import Any

CHARACTER_MOVED = "character_moved"
CHARACTER_SPOKE = "character_spoke"
CHARACTER_PERFORMED_ACTION = "character_performed_action"
RELATIONSHIP_CHANGED = "relationship_changed"
KNOWLEDGE_ACQUIRED = "knowledge_acquired"
KNOWLEDGE_SUSPECTED = "knowledge_suspected"
KNOWLEDGE_REFUTED = "knowledge_refuted"
ITEM_ACQUIRED = "item_acquired"
ITEM_REMOVED = "item_removed"
INJURY_ADDED = "injury_added"
INJURY_REMOVED = "injury_removed"
LOCATION_CHANGED = "location_changed"
WORLD_FACT_CREATED = "world_fact_created"
WORLD_FACT_MODIFIED = "world_fact_modified"
CHARACTER_DIED = "character_died"
SCENE_STAGED = "scene_staged"
SCENE_STAGING_EDITED = "scene_staging_edited"
SCENE_STARTED = "scene_started"
SCENE_ENDED = "scene_ended"
SCENE_CANCELLED = "scene_cancelled"
POSSESSION_CHANGED = "possession_changed"
DIRECTOR_INTENT_CREATED = "director_intent_created"
DIRECTOR_INTENT_COMPLETED = "director_intent_completed"
COMMITMENT_STATUS_CHANGED = "commitment_status_changed"
GENERATION_REJECTED = "generation_rejected"
CANON_OVERRIDE_CREATED = "canon_override_created"
CANON_DIVERGENCE = "canon_divergence"
TIMELINE_FORKED = "timeline_forked"
USER_ACTION = "user_action"
AI_ACTION = "ai_action"

KNOWN_EVENT_TYPES = frozenset(
    {
        CHARACTER_MOVED,
        CHARACTER_SPOKE,
        CHARACTER_PERFORMED_ACTION,
        RELATIONSHIP_CHANGED,
        KNOWLEDGE_ACQUIRED,
        KNOWLEDGE_SUSPECTED,
        KNOWLEDGE_REFUTED,
        ITEM_ACQUIRED,
        ITEM_REMOVED,
        INJURY_ADDED,
        INJURY_REMOVED,
        LOCATION_CHANGED,
        WORLD_FACT_CREATED,
        WORLD_FACT_MODIFIED,
        CHARACTER_DIED,
        SCENE_STAGED,
        SCENE_STAGING_EDITED,
        SCENE_STARTED,
        SCENE_ENDED,
        SCENE_CANCELLED,
        POSSESSION_CHANGED,
        DIRECTOR_INTENT_CREATED,
        DIRECTOR_INTENT_COMPLETED,
        COMMITMENT_STATUS_CHANGED,
        GENERATION_REJECTED,
        CANON_OVERRIDE_CREATED,
        CANON_DIVERGENCE,
        TIMELINE_FORKED,
        USER_ACTION,
        AI_ACTION,
    }
)


_REQUIRED_EVENT_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    CHARACTER_MOVED: (("character_id",), ("location_id",)),
    LOCATION_CHANGED: (("location_id",),),
    CHARACTER_SPOKE: (("character_id",), ("text", "action")),
    CHARACTER_PERFORMED_ACTION: (("character_id",), ("text", "action")),
    USER_ACTION: (("text", "action"),),
    AI_ACTION: (("text", "action", "prose"),),
    RELATIONSHIP_CHANGED: (("source_character_id",), ("target_character_id",)),
    KNOWLEDGE_ACQUIRED: (("character_id",), ("fact", "fact_id")),
    KNOWLEDGE_SUSPECTED: (("character_id",), ("fact", "fact_id")),
    KNOWLEDGE_REFUTED: (("character_id",), ("fact", "fact_id")),
    ITEM_ACQUIRED: (("character_id",), ("item", "item_id")),
    ITEM_REMOVED: (("character_id",), ("item", "item_id")),
    INJURY_ADDED: (("character_id",), ("injury", "injury_id")),
    INJURY_REMOVED: (("character_id",), ("injury", "injury_id")),
    WORLD_FACT_CREATED: (("fact_id", "id"),),
    WORLD_FACT_MODIFIED: (("fact_id", "id"),),
    CHARACTER_DIED: (("character_id",),),
    SCENE_STAGED: (("scene_id",), ("proposal",)),
    SCENE_STAGING_EDITED: (("scene_id",), ("proposal",)),
    SCENE_STARTED: (("scene_id",),),
    SCENE_ENDED: (("scene_id",),),
    SCENE_CANCELLED: (("scene_id",),),
    POSSESSION_CHANGED: (("scene_id",), ("character_id",), ("control_mode",)),
    DIRECTOR_INTENT_CREATED: (("intent_id",), ("commitment_id",), ("goal",)),
    DIRECTOR_INTENT_COMPLETED: (("intent_id",),),
    COMMITMENT_STATUS_CHANGED: (("commitment_id",), ("from_status",), ("to_status",)),
    GENERATION_REJECTED: (("generation_id",), ("reason",)),
    CANON_OVERRIDE_CREATED: (("text",),),
    CANON_DIVERGENCE: (("text",),),
    TIMELINE_FORKED: (("source_timeline_id",), ("source_node_id",)),
}


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type not in KNOWN_EVENT_TYPES:
        raise ValueError(f"Unsupported narrative event type: {event_type}")
    for alternatives in _REQUIRED_EVENT_FIELDS.get(event_type, ()):
        if not any(payload.get(field) not in (None, "") for field in alternatives):
            raise ValueError(f"Event {event_type} is missing one of: {', '.join(alternatives)}")
    return payload


def event_payload(event_type: str, **values: Any) -> dict[str, Any]:
    payload = {"event_type": event_type, **values}
    return validate_event_payload(event_type, payload)
