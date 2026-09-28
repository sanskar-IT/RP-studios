# Narrative Engine

## Pipeline stages

### Input and intent

The input surface carries a mode and text. Mode-specific classification distinguishes story commitments, direction, narration, world changes, retcons, and explicit immediate actions. High-level direction creates a pending `DirectorIntent` and `StoryCommitment`; it does not emit the requested literal event. Pending commitments are queried for every later planner context.

### Context retrieval

The engine assembles a budgeted, prioritised context (P0 system rules through
P10 flavor) from the current checkpoint, scene staging, the actor's state slice
and knowledge view, timeline-scoped commitments, the recent event tail, ranked
memories, and budgeted lore. The full accounting is stored on the generation.
See `docs/CONTEXT_ENGINE.md`.

### Scene staging

An underspecified premise becomes a proposal containing location, time, participants, objective, initial conditions, source state, environmental assumptions, consequences, canon conflicts, explicit assumptions, and unresolved assumptions. Staging creates a staged scene and `scene_staged` event. `PATCH /staging` edits the proposal, `staging/regenerate` asks the provider for a new proposal, and `cancel` records `scene_cancelled`. Approval records the accepted revision and starts the scene with `scene_started`. Continuation requires an active scene.

### Actor selection

Participants are ordered deterministically and selected using an explicit override, user-controlled participants, names in the current input, and then a participant fallback. The provider may suggest an actor, but the engine accepts that suggestion only when it identifies an actual scene participant.

### Generation and validation

The provider returns prose and structured fields separately. The engine
extracts claimed state changes from the response, validates them against the
current projection, runs the contradiction detectors, and commits only what
passes. Unknown or malformed events, non-participant references, impossible
claims, and contradiction errors reject the whole generation before anything
commits; warnings commit with the warning attached. If no state event is
returned, the engine commits an `ai_action` carrying the generated beat.
Provider failures and invalid output create a failed `Generation` record without
a narrative event. Rejected generations record their reasons and recovery
options (`retry`, `regenerate`, `edit`, `reject`). See
`docs/GENERATION_TRACING.md`.

### State and memory commit

Each event advances the state projection and latest checkpoint. The turn then
runs the memory lifecycle — one beat seed plus one fact seed per durable event,
scored and classified, with only the survivors persisted — and records the
retention report and the completed checkpoint association. Character state rows
are updated from the same projection. Forking copies the checkpoint, derived
character state, timeline-scoped memories, and open commitments to the new
branch, recording the ancestry links. See `docs/MEMORY.md`.

### Canon

A detected conflict is surfaced during staging. An accepted user decision appends `canon_divergence`. Future generation reads the changed projection, so the system does not continuously force the original source canon back onto the user.

## Invariants

- A generation without a valid structured result or event payload cannot mutate state.
- A director intent is not a literal action unless explicitly classified as immediate.
- A story commitment moves through `created → active → progressing → fulfilled`,
  with `failed`, `cancelled`, and `superseded` as exits; illegal transitions are
  refused, and a forked copy permanently replaces its ancestor on that branch.
- Possession changes control for a scene participant, is projected into checkpoint state, and does not rewrite the character definition.
- A user-controlled participant is authoritative on the next turn even when the client does not resend the possession ID.
- Every historical edit is a branch operation.
- State reconstruction from a checkpoint and event sequence is deterministic.
