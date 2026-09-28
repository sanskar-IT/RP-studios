# Data Model

## Aggregate boundaries

A project owns a world, source records, characters, locations, factions, relationships, lorebooks, scenes, memories, generations, and timelines. A timeline owns an append-only sequence of nodes and events. A scene belongs to one timeline and records staging metadata separately from events.

## Required entities

| Entity | Responsibility |
| --- | --- |
| Project | User-owned narrative workspace and active timeline |
| World | Project-level world identity and metadata |
| Source | Imported or authored advisory material, including recoverable raw import text and filename metadata |
| Character | Stable definition and identity |
| CharacterCard | Preserved external card payload and extensions |
| CharacterState | Timeline-scoped derived runtime projection |
| Lorebook / LorebookEntry | Structured retrieval rules and content |
| Location / Faction | World reference entities |
| Relationship | Directed relationship projection |
| Scene | Performance boundary, staging revision, approval revision, and staging proposal |
| SceneParticipant | Presence and temporary control mode |
| Timeline / TimelineNode | Branch identity and checkpoint sequence |
| Event | Immutable narrative change with payload and provenance |
| DirectorIntent | User's high-level direction |
| StoryCommitment | Open desired outcome tracked separately from immediate action |
| Memory | Classified, retrievable narrative information with visibility metadata |
| Generation | Provider input/output, failure metadata, and checkpoint association |
| ModelProvider / ModelConfig | Provider endpoint configuration without credentials |

## Staging and checkpoints

`Scene.staging` stores the proposal separately from the `scene_staged` and `scene_staging_edited` events. `staging_revision` increments on each edit or regeneration. `approved_staging_revision` records the exact proposal revision accepted by the user. A scene cannot continue until it is active through that approval boundary.

Each committed event creates a `TimelineNode` checkpoint. A completed `Generation` points to its final checkpoint through `checkpoint_node_id`. The checkpoint API exposes node IDs for explicit fork selection. `timeline_id + sequence` is unique for both nodes and events, and one scene per timeline may be marked current.

## Schema constraints

`timeline_nodes` and `events` are unique on `(timeline_id, sequence)`, so an append-only history cannot contain two entries at the same position on a timeline. A partial unique index on `scenes(timeline_id) where current` allows at most one current scene per timeline, and creating a scene demotes the previous one. These constraints are declared in the model metadata and applied by `0003_production_constraints`.

`memories.embedding` is a nullable pgvector column. It has no dimension and no vector index because no code writes embeddings yet; a NULL embedding is expected, not a defect.

## Event payload rules

Every event has a type, timeline, sequence, node, source, actor, timestamp, and JSON payload. Payloads contain enough information to apply the event without consulting transcript text. Examples:

```json
{"character_id":"c-1","location_id":"loc-library"}
```

```json
{"source_character_id":"c-1","target_character_id":"c-2","relationship_type":"ally","strength":0.8}
```

Prose may be carried in an action event for rendering, but it is not used as the only representation of state.

Character-specific events must reference a scene participant when they are produced by a scene generation. Event validation rejects unknown event types, missing required fields, and character references outside the active participant set.

## Branching

A branch references `parent_timeline_id` and `forked_from_node_id`. It starts with a root node whose checkpoint equals the selected source checkpoint. The source timeline and its events are never updated. Derived character state and timeline-scoped memories are copied into the branch before new events are appended.

## JSON and extensions

SQLAlchemy JSON columns retain provider output, lorebook extension fields, card extensions, and event payloads. Unknown imported card fields are copied into extension storage rather than discarded. Lorebook entry `extra_data` keeps source IDs, positions, priorities, and unknown entry metadata; the normalized `Lorebook` row keeps book-level scan depth, token budget, recursive scanning, warnings, and source association.
