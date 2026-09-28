# Architecture

## Shape

The repository is a modular monolith:

```text
apps/api       FastAPI transport and dependency wiring
apps/web       React/Vite desktop-first studio
services/core  Domain entities, event vocabulary, state projection
services/narrative  Pipeline orchestration
services/importers  Character card and lorebook normalization
services/lorebook  Structured retrieval and activation debug data
services/memory    Memory ranking and boundary rules
services/providers Provider protocol and OpenAI-compatible adapter
packages/schemas  Shared API contracts
```

PostgreSQL with pgvector is the production database. SQLAlchemy 2.x is used synchronously for the MVP API transaction boundary; the domain services are independent of FastAPI and can be moved behind an async boundary later without changing event semantics. Alembic is the schema source of truth: the baseline is static, later revisions are explicit, and normal tests use SQLite while `pytest -m postgres` validates the production database behavior when configured.

## Runtime flow

```text
request
  -> intent classification
  -> context retrieval
  -> scene staging or direction
  -> approval boundary
  -> actor selection
  -> provider generation
  -> structured validation
  -> append event
  -> project state
  -> update memory
  -> checkpoint
  -> render response
```

The current vertical slice uses `HeuristicProvider` for offline development and `ScriptedProvider` for deterministic tests. Both implement the same provider protocol as the OpenAI-compatible adapter.

## State ownership

- SQL rows hold definitions, configuration, and durable metadata.
- Events are the source of narrative change.
- A timeline node checkpoint stores a complete projected state for efficient loading.
- Character state rows are derived projections for query convenience; they can be rebuilt from checkpoints and events.
- `Scene.staging_revision` and `approved_staging_revision` make approval reviewable.
- `Generation.checkpoint_node_id` associates a completed or failed generation attempt with a node when one exists.
- Memories are separate records classified by lifecycle, visibility, and retrieval role.

## Transaction rules

A generation or explicit user operation appends events in order, updates the latest checkpoint, and commits once. Provider failure and invalid structured output create a failed `Generation` record but no narrative event. Unknown or malformed event types are rejected before persistence.

## Security boundary

Provider credentials are read from environment-backed settings. They are not fields on projects, characters, prompts, generation records, or exports. Provider errors expose status categories rather than request bodies or credentials. Imported card and lorebook files are untrusted data: uploads are size, type, depth, and entry bounded, and imported text is labeled as reference material before it reaches a provider prompt.

## Extension points

The provider protocol is the only model integration boundary. Additional importers and retrieval indexes can be added without changing event projection. The nullable pgvector column on `Memory` is reserved for semantic retrieval; the MVP uses deterministic lexical ranking and preserves the same class, visibility, character, and scene filters.
