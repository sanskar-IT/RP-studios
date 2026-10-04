# AI Narrative Studio

AI Narrative Studio is a self-hosted, single-user narrative simulation studio for actors, directors, narrators, writers, and world authorities. It is designed as a simulation desk rather than a chat client: the user controls narrative intent and fictional authority, while structured events, checkpoints, memories, and state projections control what is true.

The current repository contains the MVP foundation and first complete narrative workflow:

- Project, world, timeline, scene, character, source, and state persistence
- Append-only event history with checkpoint snapshots
- Non-destructive timeline branching and regeneration on a branch
- Character Card V1 and V2 import
- JSON, PNG, and APNG embedded card import
- Character books and standalone lorebooks
- Structured lorebook activation with an inspectable debug view
- Five memory classes with bounded retrieval
- Context assembly with priorities, budgets, and per-generation traces
- Character knowledge isolation with suspicion kept apart from certainty
- Story commitment lifecycle with branch-scoped forks
- Deterministic long-running evaluation across five canonical scenarios
- Local heuristic provider for offline development
- OpenAI-compatible provider adapter for local and generic endpoints
- Scene staging with explicit assumptions and approval
- Director intent stored as a pending story commitment
- User possession of any scene participant
- Canon divergence records
- Desktop-first React studio with cast, performance, state, timeline, and director controls

## Verification Status

Last full run on the `fix/p0-dogfood-blockers` branch. Every number below was
produced by the commands in [Testing and Quality Checks](#testing-and-quality-checks).

| Check | Command | Result |
| --- | --- | --- |
| Python suite | `pytest` | 263 passed, 12 skipped |
| Migrations | `pytest tests/test_migrations.py` | 8 passed |
| Engine evaluation | `python -m services.evaluation.harness` | pass, 0 invalid events, 0 knowledge leaks, 0 branch leaks, all six invariants true |
| Director evaluation | `python -m services.evaluation.director` | 8/8 scenarios |
| Performer evaluation | `python -m services.evaluation.performer` | 10/10 scenarios |
| Lint | `ruff check apps services packages tests` | clean |
| Types | `mypy apps services packages` | clean, 56 files |
| Web types and build | `npm run web:lint`, `npm run web:build` | clean |

These suites assert behaviour against scripted providers. **Passing them is not
a claim that the prose is good** — that still needs a human reading the output.

There is no JavaScript test runner. Frontend behaviour is covered by server
contract tests plus type checking and a successful build, so UI regressions are
verified manually rather than automatically.

### Known limitations

| Limitation | Status |
| --- | --- |
| `_recent_events` renders speech to every character within the context window, and `_overhear` grants no suspicion for a paraphrase | **Open.** `test_recent_events_still_expose_speech_within_the_window` asserts the leak still exists, so the gap stays visible until it is closed deliberately |
| No authentication; Compose binds all interfaces and publishes PostgreSQL on `5432` | Open. Bind to loopback before any shared deployment — see [Security Before Publishing](#security-before-publishing) |
| Unbounded request body size; uploads spool to disk before the 413 | Open |
| `creator_notes` and card text reach prompts without a trust boundary; over-long card descriptions are rejected rather than truncated | Open. Needs a trust-boundary design, not a patch |
| Context budget can starve character state, and trims the knowledge guardrail it still describes as exhaustive | Open |
| Performer output is never validated against the Director constraint envelope; exclusions are prompt-only | Open |
| `active_director_plan` has no timeline predicate after a fork | Open |
| No CI workflow | Open |

## Product Model

The application keeps these distinctions separate:

| Concept | Meaning |
| --- | --- |
| Character definition | Stable imported or authored identity and prompt material |
| Character state | Facts currently true for a character on a timeline |
| Source material | Advisory canon that informs generation |
| World state | Facts created or changed by committed events |
| Director intent | A desired future direction, not an immediate literal action |
| Story commitment | An open outcome the planner must continue to pursue |
| Timeline | An ordered event history with checkpoints |
| Transcript | Presentation text, not the source of truth |
| Memory | Classified information selected for bounded context |
| Lorebook | Structured retrieval rules, not a flat text appendix |

The normal flow is:

```text
Premise
  -> intent classification
  -> context retrieval
  -> scene staging
  -> user approval
  -> actor selection
  -> provider generation
  -> structured validation
  -> state extraction
  -> memory updates
  -> immutable event commit
  -> checkpoint
  -> render
```

## Technology

- Python 3.12 or newer
- FastAPI
- SQLAlchemy 2.x
- Alembic
- PostgreSQL 16 with pgvector
- React, TypeScript, and Vite
- pytest, Ruff, and mypy
- Docker Compose for the self-hosted stack

The application is a modular monolith. It does not require Redis, Kafka, Kubernetes, or a distributed worker for the MVP.

## Repository Layout

```text
apps/
  api/                 FastAPI application and database wiring
  web/                 React/Vite studio
packages/
  schemas/             Shared API contracts
  core/                Reserved shared package boundary
services/
  core/                SQLAlchemy entities, events, and state projection
  narrative/           Narrative pipeline orchestration
  importers/           Character card and lorebook normalization
  lorebook/            Structured activation and debug output
  memory/              Memory classification and retrieval
  providers/           Provider protocol and HTTP adapter
alembic/               Database migrations
tests/                 Deterministic SQLite tests and the opt-in PostgreSQL suite
docs/                  Product, API, and architecture documentation
```

## Requirements

Choose one of the following setups:

### Docker setup

- Docker Desktop or Docker Engine
- Docker Compose v2

### Local setup

- Python 3.12 or newer
- Node.js 20 or newer
- PostgreSQL 16 with pgvector, or Docker for PostgreSQL
- npm

## Quick Start with Docker

Docker Compose starts PostgreSQL with pgvector, applies migrations, starts the API, and serves the built studio through nginx.

1. Copy the environment template:

   ```text
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Review `.env`. The default provider is the deterministic heuristic provider, so no model account is required for the first run. Docker uses the internal `postgres` hostname for its database; the database URL in `.env` is for a locally running API. Provider settings from `.env` are passed into the Compose API service.

3. Build and start the stack:

   ```text
   docker compose up --build
   ```

4. Open the studio:

   ```text
   http://localhost:5173
   ```

5. Open API documentation:

   ```text
   http://localhost:8000/docs
   ```

6. Stop the services when finished:

   ```text
   docker compose down
   ```

To remove the local PostgreSQL volume as well, use `docker compose down -v`. This deletes local narrative data.

## Local Development

### 1. Create a Python environment

```text
python -m venv .venv
```

Activate it on macOS or Linux:

```text
source .venv/bin/activate
```

Activate it in Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 2. Install Python dependencies

```text
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 3. Configure the environment

```text
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Until a provider key is configured, the studio displays a banner stating that
responses are templated rather than written. This is expected on a fresh
installation.

The API reads environment variables with the `NARRATIVE_` prefix. The important settings are:

| Variable | Purpose | Default |
| --- | --- | --- |
| `NARRATIVE_DATABASE_URL` | SQLAlchemy PostgreSQL URL | Local `narrative` database |
| `NARRATIVE_CORS_ORIGINS` | Comma-separated allowed web origins | `http://localhost:5173` |
| `NARRATIVE_PROVIDER` | `heuristic` or `openai_compatible` | `heuristic` |
| `NARRATIVE_PROVIDER_BASE_URL` | OpenAI-compatible API base URL | Empty |
| `NARRATIVE_PROVIDER_MODEL` | Model identifier sent to the provider | Empty |
| `NARRATIVE_PROVIDER_API_KEY` | Provider credential read from the environment | Empty |
| `NARRATIVE_PROVIDER_CONTEXT_WINDOW` | Declared context window in tokens; 0 means the conservative default | `0` |
| `NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS` | Reserved output budget in tokens; 0 means the default | `0` |
| `NARRATIVE_PROVIDER_FEATURES` | Comma-separated declared features (`structured_output`, `streaming`, `tools`, `json_mode`, `embeddings`) | Empty |
| `NARRATIVE_MEMORY_RETRIEVAL_LIMIT` | Memories injected per turn | `10` |
| `NARRATIVE_MEMORY_CANDIDATE_POOL` | Candidate pool ranked per retrieval | `400` |
| `NARRATIVE_LORE_TOKEN_BUDGET` | Legacy lore budget hint; lore is additionally capped at a fifth of the input budget | `2048` |
| `NARRATIVE_AUTO_CREATE_SCHEMA` | Create tables on API startup | `false` |

Keep `NARRATIVE_AUTO_CREATE_SCHEMA=false` for normal development and use Alembic for schema changes.

### 4. Start PostgreSQL

If PostgreSQL is available locally, create a database matching the URL in `.env`. Otherwise start only the Compose database:

```text
docker compose up -d postgres
```

### 5. Apply migrations

```text
alembic upgrade head
```

### 6. Start the API

```text
uvicorn apps.api.app.main:app --reload --port 8000
```

The health endpoint is available at `http://localhost:8000/health`.

### 7. Start the web studio

In a second terminal:

```text
npm install
npm run web:dev
```

Open `http://localhost:5173`.

## Provider Configuration

The heuristic provider is the default because it works without a network connection and makes the core workflow deterministic. It returns a small structured response so the event and state pipeline can be exercised locally.

To use an OpenAI-compatible endpoint, set these values in `.env`:

```text
NARRATIVE_PROVIDER=openai_compatible
NARRATIVE_PROVIDER_BASE_URL=https://your-compatible-endpoint.example/v1
NARRATIVE_PROVIDER_MODEL=your-model-name
NARRATIVE_PROVIDER_API_KEY=your-key-from-a-secret-store
```

The adapter expects an endpoint that provides an OpenAI-compatible `/chat/completions` contract. It can represent local inference servers and compatible gateways without changing narrative code.

Provider credentials are configuration only. They must not be placed in:

- Project or character JSON
- Scene content or prompts
- Generation records
- Memory records
- Exports
- Client-side environment variables
- Logs or error messages

Do not commit a real API key. `.env` and common local secret formats are ignored. `.env.example` is intentionally tracked and contains empty configuration values only.

## Using the Studio

1. Create a project.
2. Add characters or import a Character Card JSON, PNG, or APNG file.
3. Import a lorebook if the project needs structured retrieval rules.
4. Enter an underspecified premise in the staging surface.
5. Review the inferred location, time, participants, objective, assumptions, consequences, and canon conflicts.
6. Accept the staging proposal. A staged scene cannot continue until this approval boundary is recorded.
7. Use the staging controls to edit, regenerate, or cancel the proposal before approval.
8. Continue the scene in `Auto`, `Actor`, or `Narrator` mode. The engine chooses a participant or honors the explicit actor override. Submitting with an empty input in these modes is valid and advances the scene without authoring a line.
9. Use `Director` mode for a high-level commitment. It does not immediately force the requested outcome.
10. Use `World` or `Retcon` mode for explicit world changes or accepted canon divergence.
11. Use `Possess` to temporarily take control of any scene participant. The next turn is user-authored even without resending a possession ID.
12. Inspect state, memories, event history, activated lore, planner context, and checkpoint IDs from the right panel.
13. Open the Context panel for the same timeline to see the assembled context composition, validation verdicts, contradiction warnings, commitment lifecycles, memory scope, and per-generation traces.
14. Use `Regenerate` or `Fork` when exploring an alternate history. The previous timeline is preserved.

## API Overview

Narrative endpoints are under `/api`; health and interactive documentation are served at the application root.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `GET` | `/docs` | Interactive OpenAPI documentation |
| `POST` | `/api/projects` | Create a project and initial timeline |
| `GET` | `/api/projects` | List projects |
| `GET` | `/api/projects/{project_id}/characters` | List character definitions |
| `POST` | `/api/projects/{project_id}/characters` | Add a character |
| `POST` | `/api/projects/{project_id}/sources` | Store source material |
| `GET` | `/api/projects/{project_id}/scenes` | List scenes |
| `POST` | `/api/projects/{project_id}/stage` | Create or revise a staging proposal |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/approve` | Approve the reviewed staging revision and start the scene |
| `PATCH` | `/api/projects/{project_id}/scenes/{scene_id}/staging` | Edit a staged proposal |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/staging/regenerate` | Regenerate a staged proposal |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/cancel` | Cancel a staged or active scene |
| `GET` | `/api/projects/{project_id}/scenes/{scene_id}/actors` | List participants and control modes |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/continue` | Run the generation pipeline for an approved scene |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/direct` | Record director intent or an explicit world direction |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/possess` | Give the user control of a participant |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/release` | Return control to the AI |
| `POST` | `/api/projects/{project_id}/scenes/{scene_id}/regenerate` | Generate an alternate result on a new branch |
| `POST` | `/api/projects/{project_id}/timelines/{timeline_id}/fork` | Fork from a timeline checkpoint |
| `GET` | `/api/projects/{project_id}/timelines/{timeline_id}/checkpoints` | List checkpoint IDs for an explicit fork |
| `GET` | `/api/projects/{project_id}/timelines/{timeline_id}/inspect` | Inspect state, memories, lore, events, commitments, and checkpoints |
| `GET` | `/api/projects/{project_id}/timelines/{timeline_id}/generations` | List per-generation context and validation summaries |
| `GET` | `/api/projects/{project_id}/generations/{generation_id}` | Read the full generation trace |
| `POST` | `/api/projects/{project_id}/generations/{generation_id}/reject` | Mark a committed generation as rejected |
| `POST` | `/api/projects/{project_id}/timelines/{timeline_id}/memories` | Inspect memory scope, activity, and retrieval |
| `POST` | `/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}` | Move a commitment through its lifecycle |
| `POST` | `/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}/supersede` | Retire a commitment, optionally replacing it |
| `POST` | `/api/projects/{project_id}/timelines/{timeline_id}/correct-state` | Apply an operator correction to a world fact |
| `GET` | `/api/providers/capabilities` | Read the configured provider's `adapter`, context window, and features. `adapter: "heuristic"` means no model is configured |
| `GET` | `/api/projects/{project_id}/characters/{character_id}` | Retrieve a character definition and preserved card metadata |
| `GET` | `/api/projects/{project_id}/lorebooks` | List imported books and entry counts |
| `POST` | `/api/projects/{project_id}/lorebooks/{lorebook_id}/associate` | Associate a project book with a character |
| `POST` | `/api/projects/{project_id}/imports/character-card/preview` | Parse and validate a card without persisting it |
| `POST` | `/api/projects/{project_id}/imports/lorebook/preview` | Parse and validate a book without persisting it |
| `POST` | `/api/projects/{project_id}/imports/character-card` | Import JSON, PNG, or APNG cards |
| `POST` | `/api/projects/{project_id}/imports/lorebook` | Import a standalone lorebook |
| `POST` | `/api/projects/{project_id}/lorebooks/{lorebook_id}/preview` | Preview deterministic lore activation |

The API does not return provider credentials. The frontend only receives narrative data and non-secret configuration.

## State and Timeline Guarantees

Narrative changes are append-only events. A timeline node stores a complete checkpoint so loading the latest state does not require replaying the entire history.

Every event type a model is asked to propose must be one the engine accepts. The
schemas derive their enum from `MODEL_WRITABLE_EVENT_TYPES` rather than carrying
hand-written lists, because a disagreement between the two is silent until a turn
is rejected wholesale. Events outside that set — scene lifecycle, possession,
forks, `world_fact_created` — are engine-originated and are not advertised to the
model. See `tests/test_event_vocabulary.py`.

Branching never edits the source timeline:

1. Select an immutable source node.
2. Create a new timeline that references the source timeline and node.
3. Copy the source checkpoint, derived character state, and timeline-scoped memories into the branch root.
4. Append new events only to the branch.

Character state rows are derived projections. They can be rebuilt from checkpoints and events. Generated prose is retained for presentation and debugging, but it is not the only representation of state.

## Character Card Compatibility

The importer supports:

- Character Card V1 JSON
- Character Card V2 JSON
- PNG and APNG files with embedded card text in `tEXt`, `iTXt`, or `zTXt` metadata
- Embedded character books
- Standalone lorebooks, Tavern-style arrays, and UID-keyed books
- Alternate greetings
- Optional and legacy field variations
- Arbitrary extension data

Unknown fields and unsupported V2 entry metadata are preserved and reported as warnings. Imports are bounded on upload size, entry count, and nesting depth.

Compressed PNG metadata is additionally bounded on the *decompressed* size, because
`zTXt` and `iTXt` chunks expand by a ratio the uploaded byte count cannot express. A
card whose text chunk expands beyond 4 MiB is rejected rather than inflated, and a
truncated compressed chunk is refused instead of being decoded to whatever survived.

The import UI previews a validated file before confirmation.

Fixtures live in `tests/fixtures/`. Compatibility tests cover V1, V2, alternate greetings, embedded books, arbitrary extensions, PNG/APNG metadata, Tavern-style book shapes, bounded imports, round-trip normalization, and lore activation.

The import UI follows this flow: select a file, call the preview endpoint, review the name, version, entry count, token budget, and warnings, then confirm persistence. The same parsed result is used by the import endpoint, so preview and confirmed import share one normalizer.

## Memory Classes

| Class | Use |
| --- | --- |
| `permanent` | Stable character or world information |
| `persistent` | Facts currently true on a timeline |
| `scene` | Information relevant to the active scene |
| `working` | Context assembled for the next generation |
| `archive` | Older material available for retrieval |
| `transient` | Scored and discarded; state only, never persisted |

Memory retrieval is budgeted and scoped by project, timeline ancestry, scene,
and character. Scope (`world`, `character`, `scene`, `transient`) is stored
separately from class and decides whose prompt a memory may enter. A character
does not automatically know a fact merely because the source material or model
contains it, and suspicion is stored apart from certainty.

Derived character state does not retain speech content. `last_action` records
*that* a character spoke without keeping the words, because state is re-projected
from the event log indefinitely: storing the text there made a single quiet
utterance reappear in other characters' prompts on every later turn. Observable
performed actions are kept verbatim, since anything in the room can witness one.

This closes the permanent half of the leak. In-window exposure remains — see
[Known limitations](#known-limitations).

## Database and Migrations

The schema is defined with SQLAlchemy 2.x and managed with Alembic.

Create a migration after changing a model:

```text
alembic revision --autogenerate -m "describe the schema change"
```

Review the generated migration before applying it. Then run:

```text
alembic upgrade head
```

The chain is explicit and reviewable, and the baseline no longer derives the deployed schema from the models:

| Revision | Content |
| --- | --- |
| `0001_initial` | Static baseline: 22 tables, their indexes, foreign keys, and unique constraints, plus `CREATE EXTENSION IF NOT EXISTS vector` on PostgreSQL |
| `0002_vertical_slice` | Adds `scenes.staging_revision`, `scenes.approved_staging_revision`, and `generations.checkpoint_node_id`, each only when the column is absent |
| `0003_production_constraints` | Adds `(timeline_id, sequence)` uniqueness for `timeline_nodes` and `events`, and a partial unique index that allows one current scene per timeline |
| `0004_context_reliability` | Adds memory `scope`, `is_active`, `superseded_by_id`, and `source_event_id`; commitment `timeline_id`, `forked_from_commitment_id`, and `created_sequence`; generation `context_debug`, `validation`, and `trace`; backfills legacy memory scope from class |
| `0005_narrative_director` | Creates `director_plans`; adds `authority_mode` to `projects` and `scenes`; extends `director_intents` with the interpreted-intent and Director columns |

`0001_initial` does not call `Base.metadata.create_all`, so an applied database keeps the schema it was migrated to even as the models move. The column and constraint guards in the later revisions exist for databases that were created by the previous dynamic baseline.

Rows without an explicit `authority_mode` resolve to `director_assisted`, so an upgraded run does not begin gating every direction turn on approval.

`memories.embedding` stays a nullable pgvector column without a dimension and without a vector index, because no code writes embeddings yet.

Tables are not created on API startup. `NARRATIVE_AUTO_CREATE_SCHEMA` defaults to `false`; `apps.api.app.db.init_db` remains available for a throwaway local database when the flag is set explicitly.

## Testing and Quality Checks

Run the complete Python test suite:

```text
pytest
```

Run Python linting and type checks:

```text
ruff check apps services packages tests alembic
mypy apps services packages
```

Run the web checks:

```text
npm run web:lint
npm run web:build
```

The deterministic suite uses fixture files under `tests/fixtures/provider/` and mocked structured provider output. It also migrates a temporary SQLite database from the baseline to `head` and compares the result with the model metadata, so a migration that drifts from the models fails the normal suite. Prose may vary between models, but state projection, importer normalization, lore activation, intent handling, possession, memory boundaries, provider failures, malformed output, and branch preservation are tested as deterministic behavior.

Run the deterministic long-running evaluation (no live provider required):

```text
python -m services.evaluation.harness --out reports/evaluation.json --print
```

It drives the real pipeline with a scripted provider through five canonical
scenarios at 10, 25, 50, and 100 turns and writes a machine-readable report of
state contradictions, knowledge leaks, broken commitments, invalid events,
branch leaks, memory retrieval failures, and context sizes. See
`docs/NARRATIVE_EVALUATION.md`.

The Director has its own suite, because it fails differently:

```text
python -m services.evaluation.director --out reports/director_evaluation.json --print
```

Eight scenarios (A–H) assert the properties the Director promises: that exclusions
bind under `ai_directed`, that strict authority withholds the turn, that editing a
plan never rewrites the retained intent, that long-horizon intent becomes a
commitment rather than an event, and that no character's private knowledge reaches
another character's prompt. The engine harness detects broken generations; these
ask whether the Director did the user's job correctly. See `docs/INTENT_MODEL.md`.

The Performer has a third suite, and it is the one that asserts on prose:

```text
python -m services.evaluation.performer --out reports/performer_evaluation.json --print
```

Ten scenarios (A–J) cover a single-actor scene, three-character dialogue with
asymmetric knowledge, user possession, character agency, an interrupted plan,
required and optional consequences, dead and off-stage characters, knowledge
isolation, and branch isolation. Several checks are deliberately semantic — whether
reactions differ, whether the Performer invented a decision for the user's
character — because those failures are invisible to structural validation.
See `docs/PERFORMER_ARCHITECTURE.md`.

**Passing the structural tests does not mean the prose is good.** These suites
verify behaviour against scripted providers, not writing quality. Narrative quality
is a separate question and needs a human reading the output.

Run the opt-in live provider test only when a compatible endpoint is configured:

```text
pytest -m smoke
```

The deterministic suite runs on SQLite only. Persistence, JSON round-trip, foreign key enforcement, transaction rollback, timeline fork, memory persistence, and the production constraints are also covered against a real PostgreSQL database by an opt-in suite. Point `NARRATIVE_TEST_DATABASE_URL` at a disposable database with pgvector available, then run:

```text
pytest -m postgres
```

The opt-in suite requires `NARRATIVE_TEST_DATABASE_URL` and skips otherwise:

| Test | Covers |
| --- | --- |
| `test_migration_upgrade_is_idempotent` | Alembic upgrade can be re-applied |
| `test_migration_chain_matches_models_on_postgresql` | Chain matches models, pgvector present |
| `test_core_entities_persist_and_reload` | Core entities round-trip |
| `test_json_columns_round_trip_without_loss` | JSON columns keep types and weights |
| `test_foreign_keys_are_enforced` | Foreign keys reject orphans |
| `test_transaction_rollback_discards_committed_work` | Rollback discards work |
| `test_timeline_fork_preserves_source_and_branch_state` | Fork preserves checkpoints and copies memories |
| `test_production_constraints_reject_duplicate_order_and_second_current_scene` | Ordering and current-scene constraints |
| `test_memory_persistence_keeps_class_scope_and_null_embedding` | Memory class scope with null embeddings |
| `test_reliability_columns_round_trip_on_postgresql` | Memory scope/activity, commitment lifecycle columns, generation trace JSON |
| `test_timeline_fork_copies_memory_scope_and_commitment_linkage` | Fork copies memory scope with ancestry links and links branch commitments |

The suite skips when the variable is unset or the database is unreachable, and it leaves no test data behind. See `docs/DEPLOYMENT.md` for the full procedure.

## Recent Changes

### P0 dogfood blockers

Seven defects found by external validation, each reproduced with a failing
regression test before being fixed.

**Event vocabulary.** The Performer schema advertised `item_obtained`,
`item_destroyed`, `character_injured`, and `scene_ended` — none of which the
engine accepts. Because claim validation is all-or-nothing, a schema-compliant
model producing one of those names lost the entire turn, including the prose:
picking up a locket could not be committed. Both schemas now derive their enum
from a single `MODEL_WRITABLE_EVENT_TYPES` allowlist, which also stops
advertising `world_fact_created` (a private belief promoted to canon for every
viewer). The three names already in the wild are normalised on read, so a model
carrying a cached prompt degrades instead of returning a 422.

**Approval gate.** `requires_approval` gated on any consistency other than
`consistent`, so an ordinary pronoun — `_REFERENT` marks "I follow her down the
hall" ambiguous — returned an empty turn and an approval prompt at *every*
authority level, including the default `director_assisted`. A scene with two
characters could not be played with ordinary sentences. Only
`CONTRADICTORY` now gates; ambiguity still discounts plan confidence. This is
what `docs/INTENT_MODEL.md` already promised.

**Unbounded zlib.** PNG text chunks were inflated with `zlib.decompress`, which
has no size ceiling: roughly 16 KiB of upload reached 52 MiB of memory before
the importer's own limit, and that limit only bounds the *uploaded* file. Replaced
with a chunked inflate that abandons a bomb at the ceiling. Truncated compressed
data is now refused too — `decompressobj` otherwise decodes whatever survived, and
that can still be valid JSON, so a half-written card would import as a real one.

**`last_action` persistence.** Raw speech was written into character state, then
re-projected into every later snapshot and re-inserted into every subsequent
prompt. A single secret spoken quietly to one person became a permanent,
undetectable leak into every other character's context on every following turn.
Speech events now store a content-free label. Observable performed actions are
kept, because continuity depends on them and dropping them buys no isolation.

**Possession enforcement.** `check_user_agency` decided whether the Performer
had taken over the user's character by reading `turn.kind` — a field the model
fills in. Relabelling a decision as `reaction` bypassed the check and committed
an invented decision as an authoritative `user_action` for a character the user
owns. Authority is now never derived from `kind`; enforcement is grounding in the
user's own input, the one statement of intent the model did not author. The check
also now covers `proposed_events` and `state_claims`, which named possessed
characters directly while going unchecked entirely.

**Provider warning.** Nothing told the user that no model was configured, so a
fresh install looked like a working app that was silently echoing input back. The
studio now reads the existing `adapter` field and says so.

**Continue with no input.** The client refused to send an empty command, so the
one control whose purpose is to advance the scene without writing anything did
nothing. The engine already handled it correctly; only the guard was wrong.

## Security Before Publishing

Before pushing a public or shared repository:

1. Keep `.env` local. It is ignored by Git.
2. Keep `.env.example` tracked, but leave secrets empty.
3. Search staged files for `BEGIN`, `Bearer`, `sk-`, `api_key`, `token`, and `password` values.
4. Review the staged file list, not only the working tree.
5. Change the Compose database password for any shared deployment.
6. Restrict `NARRATIVE_CORS_ORIGINS` to the actual web origin.
7. Do not expose PostgreSQL or the API directly to the public internet without authentication and a reverse proxy.
8. Use a secret manager or protected environment configuration for provider keys.
9. Back up the PostgreSQL volume before upgrades.
10. Add an appropriate license before distributing the project publicly.

If a secret was already committed, removing it in a later commit is not enough. Rotate the credential first, then rewrite repository history with an approved history-cleaning tool before pushing.

## Publishing to Git

This workspace is not required to be initialized or committed by the assistant. Review the files, then use the following workflow in the repository root.

### 1. Check the working tree

```text
git status --short
```

Review every untracked file. Generated dependencies, local databases, uploads, and secrets should not appear.

### 2. Verify secret exclusions

```text
git check-ignore -v .env
git check-ignore -v node_modules
git check-ignore -v apps/web/dist
git check-ignore -q .env.example
```

The first three commands should identify ignore rules. `git check-ignore -v .env.example` may display the `!.env.example` negation rule, while `git check-ignore -q .env.example` should return a nonzero status because the example file is not ignored. The example file is safe to track because it contains no credential.

If a real `.env` file is already tracked, remove it from the index without deleting the local file:

```text
git rm --cached .env
```

### 3. Stage deliberately

```text
git add .
git status --short
git diff --cached --stat
git diff --cached --name-only
```

Inspect the staged file list for secrets, private exports, local databases, and generated output. `package-lock.json` should be included; `node_modules` and `dist` should not.

A basic staged-content scan can be run with:

```text
git grep --cached -n -I -E "BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|Bearer[[:space:]]+[A-Za-z0-9._-]+|sk-[A-Za-z0-9]|api[_-]?key[[:space:]]*[:=][[:space:]]*[^[:space:]]+" -- . ":(exclude)package-lock.json"
```

Review any matches manually. A scanner cannot prove that a credential is safe.

### 4. Create the first commit

```text
git add .
git commit -m "Build AI Narrative Studio MVP"
```

Do not commit until the staged diff has been reviewed.

### 5. Add a remote and push

Replace the placeholder with the remote URL supplied by the hosting service. Note
the branch name: this repository uses `master`, so a fresh publish needs no
rename.

```text
git remote add origin <your-repository-url>
git push -u origin master
```

Current state of this repository:

```text
origin   https://github.com/sanskar-IT/RP-studios.git
branch   master            f672eff  feat: establish narrative studio engine
         fix/p0-dogfood-blockers   429122d  fix: clear the P0 dogfood blockers
```

`master` and `origin/master` both point at `f672eff`; the P0 work exists only on
`fix/p0-dogfood-blockers`, which is not yet on the remote. Merge it with a fast
forward once the review is done:

```text
git checkout master
git merge --ff-only fix/p0-dogfood-blockers
git push origin master
```

For an existing repository, inspect the remote and history before pushing:

```text
git remote -v
git log --oneline -10
```

Do not force-push a shared branch. If the remote already contains history, fetch and review it first.

## Troubleshooting

### Database connection fails

Confirm PostgreSQL is running and that `NARRATIVE_DATABASE_URL` uses the `NARRATIVE_` prefix. The API expects a SQLAlchemy URL such as `postgresql+psycopg://...`.

### The web page loads but API calls fail

Run the API on port 8000 and keep the Vite proxy configuration intact. Check `http://localhost:8000/health` and the browser network panel.

### Provider configuration is ignored

The API reads settings from the environment at process startup. Restart the API after changing `.env`. Confirm the provider name, base URL, and model are all set for `openai_compatible`.

### A migration fails

Run `alembic current`, inspect the database URL, and read the migration output. Do not delete a production database to fix a migration. Restore from a backup or apply a reviewed corrective migration.

### A character import is rejected

Validate the file as UTF-8 JSON first. For PNG or APNG files, confirm that an embedded `chara`, `ccv2`, or `character` text chunk exists. The importer reports malformed metadata rather than silently discarding it.

### The project is not visible in Git

Run `git status --short` and `git check-ignore -v <path>`. A `.gitignore` rule only affects untracked files. If a secret or generated file was already tracked, use `git rm --cached` after confirming the local copy is safe to retain.

## Current Scope

The MVP intentionally does not include image generation, video, voice, social networking, a public marketplace, hosted inference, multi-user collaboration, a mobile app, training pipelines, or custom model hosting. These can be considered only after the narrative state and branching model are stable.

Authentication is also out of scope for the MVP. The application is designed as a
single-user self-hosted desk. It should not be exposed to a network you do not
control until it has authentication and a reverse proxy in front of it.

## License

No license is declared in the current repository. Add the license you intend to use before publishing or distributing the project.
