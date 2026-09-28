# API Reference

The API is a single-user, self-hosted FastAPI service. The default local URL is `http://localhost:8000`. Interactive OpenAPI documentation is available at `/docs`.

Narrative state changes are committed through events. API responses contain rendered text for display, but the event and checkpoint records are authoritative.

## Error semantics

- `404`: the project, scene, timeline, or checkpoint does not exist.
- `422`: the request violates a domain contract, such as continuing a scene that has not been approved, selecting a non-participant actor, or submitting a malformed event.
- `502`: the configured provider failed or returned unusable output. A failed generation is recorded without appending a narrative event.

## Health and projects

```text
GET /health
GET /
```

Create a project:

```text
POST /api/projects
```

```json
{
  "name": "The Ashen Accord",
  "description": "A political narrative"
}
```

List or read projects:

```text
GET /api/projects
GET /api/projects/{project_id}
```

## Cast and source material

Add a character definition:

```text
POST /api/projects/{project_id}/characters
```

```json
{
  "name": "Detective",
  "definition": {
    "description": "Observant and skeptical"
  }
}
```

List characters:

```text
GET /api/projects/{project_id}/characters
```

Store advisory source material:

```text
POST /api/projects/{project_id}/sources
```

```json
{
  "title": "Oath of the General",
  "content": "The General is loyal and must never betray the Emperor."
}
```

## Scene staging

Create or revise a staging proposal:

```text
POST /api/projects/{project_id}/stage
```

```json
{
  "premise": "I want a suspicious murder to happen in a library, and I want the other characters to panic.",
  "scene_id": null,
  "character_ids": ["character-id"]
}
```

Omitting `character_ids` selects all active project characters. The response is a `StagingProposal` containing:

- `objective`
- `location`
- `time`
- `characters_present`
- `initial_conditions`
- `relevant_context`
- `environmental_assumptions`
- `intended_consequences`
- `canon_conflicts`
- `assumptions`
- `unresolved_assumptions`
- `staging_revision`
- `status`

A staged proposal is not an executed scene. Accept it explicitly:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/approve
```

```json
{
  "staging_revision": 1
}
```

Edit a proposal without calling the model:

```text
PATCH /api/projects/{project_id}/scenes/{scene_id}/staging
```

```json
{
  "scene_id": "scene-id",
  "staging_revision": 1,
  "location": "archive",
  "character_ids": ["detective-id", "witness-id"],
  "unresolved_assumptions": ["The archive key is still hidden."]
}
```

Regenerate a staged proposal:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/staging/regenerate
```

```json
{
  "premise": "A revised library staging"
}
```

Cancel a staged or active scene:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/cancel
```

## Participants and generation

List scene participants and their current control modes:

```text
GET /api/projects/{project_id}/scenes/{scene_id}/actors
```

Continue an approved scene:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/continue
```

```json
{
  "scene_id": "scene-id",
  "mode": "auto",
  "user_input": "The detective moves first.",
  "actor_character_id": "detective-id"
}
```

`actor_character_id` is an explicit override and must identify a scene participant. If it is omitted, the engine considers the user-controlled participant, names in the input, and then a deterministic participant fallback. A provider-selected actor is accepted only when it is a participant.

A successful response includes:

- rendered `output_text`
- structured provider output
- committed event IDs
- the selected actor
- planner context, including pending commitments
- the checkpoint node ID
- the projected state after the turn

Provider failure and invalid structured output are recorded as failed generations and do not append a narrative event.

## Director intent

Record a high-level intent:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/direct
```

```json
{
  "scene_id": "scene-id",
  "text": "The General will betray the Emperor",
  "intent_type": "story_commitment",
  "horizon": "short"
}
```

The default result is a pending `DirectorIntent` and `StoryCommitment`. It does not immediately emit a betrayal event. Pending commitments are included in later planner context and inspection responses.

## Performer session

The RP loop's single read, so the studio does not assemble it client-side:

```text
GET /api/projects/{project_id}/scenes/{scene_id}/session
```

```json
{
  "scene": { "scene_id": "...", "title": "Archive", "status": "active", "location": "Archive Room", "objective": "..." },
  "cast": [
    { "character_id": "...", "name": "Detective", "control_mode": "user", "user_controlled": true, "alive": true, "presence": "present" }
  ],
  "current_actor": "...",
  "plan": {
    "plan_id": "...",
    "status": "executing",
    "lane": "direction",
    "objective": "...",
    "summary": "...",
    "beats": [],
    "current_beat": { "description": "...", "status": "active" },
    "remaining_beats": []
  },
  "can_continue": true,
  "open_commitments": []
}
```

`current_actor` is the character whose possession the user holds, or `null` when
the Performer is acting freely. `plan` is the plan currently driving the scene, or
`null`.

A `continue` turn that produced a plan awaiting a decision returns
`requires_approval: true` and a `plan_id` with **no generation, no prose, and no
committed events** — nothing happened, and something is waiting for you.

## Performer presentation

`NarrativePipeline(presentation_style=...)` accepts `hybrid` (default),
`literary`, or `dialogue_focused`. An unrecognised value falls back to `hybrid`
rather than failing: presentation is a preference, and a typo in a UI field should
not fail a turn.

## Possession

```text
POST /api/projects/{project_id}/scenes/{scene_id}/possess   { "scene_id", "character_id" }
POST /api/projects/{project_id}/scenes/{scene_id}/release   { "scene_id", "character_id" }
```

Possession is an **execution authority**, not a state mutation. It changes who
decides, never what a character is: identity, knowledge, and memory are untouched.
Either endpoint is safe to call at any time; neither requires the other first.

## Director plans

A `continue` call that routes to direction or long-horizon intent returns plan
fields alongside the turn result. When the plan needs a human decision, the turn
is not performed: there is no `generation_id`, no prose, and no committed events.

```json
{
  "plan_id": "plan-id",
  "requires_approval": true,
  "plan": { "status": "proposed", "lane": "direction", "beats": [], "validation": {} }
}
```

Under `director_assisted` the ordinary turn proceeds and the plan is persisted as
`completed`, linked to the generation it directed.

List, inspect, and decide plans:

```text
GET  /api/projects/{project_id}/plans?scene_id=&timeline_id=
GET  /api/projects/{project_id}/plans/{plan_id}
POST /api/projects/{project_id}/plans/{plan_id}/decide
POST /api/projects/{project_id}/plans/{plan_id}/execute
POST /api/projects/{project_id}/plans/{plan_id}/interrupt
```

`decide` takes `approve`, `edit`, `cancel`, or `reject`:

```json
{ "decision": "edit", "edits": { "objective": "Force the confrontation now" } }
```

`edits` is free-form and changes the **plan only**. The retained intent is never
rewritten, because the user's request has not changed. An edit returns the plan to
`proposed` and re-verifies it against the constraint envelope on approval, so a
corrected plan is not permission to run one the State Engine would reject.

`execute` performs the next beat of an approved plan. One turn realises one beat,
so a plan stays `executing` until its beats have all reached a terminal state.

`interrupt` invalidates the plan's open beats after the user changes direction. A
plan that never performed becomes `cancelled`; one that was mid-performance
becomes `completed`. A plan that is `superseded` was overtaken by a new direction,
not cancelled.

## Possession

Give the user control of a participant:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/possess
```

```json
{
  "scene_id": "scene-id",
  "character_id": "detective-id"
}
```

Return control to the AI:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/release
```

Possession is stored on the scene participant and projected into checkpoint state. A turn for a user-controlled participant is committed with a user event source, even if the client does not resend the character ID.

## Checkpoints and inspection

List timeline checkpoints:

```text
GET /api/projects/{project_id}/timelines/{timeline_id}/checkpoints
```

Each checkpoint includes its node ID, sequence, parent node, associated event, and generation when one exists. A checkpoint ID is the stable input for a fork.

Inspect the current timeline:

```text
GET /api/projects/{project_id}/timelines/{timeline_id}/inspect
```

The inspection payload contains projected state, the knowledge table, memories
with scope and activity, timeline-scoped commitments, the latest context
budget report, validation verdict, contradiction warnings, event history,
checkpoints, planner context, and the most recent lore activation debug data.
Generation traces, memory inspection, commitment lifecycle transitions,
generation rejection, and state correction are documented in
`docs/GENERATION_TRACING.md`; the evaluated reliability numbers are in
`docs/NARRATIVE_EVALUATION.md`.

## Forking

Fork from a selected checkpoint:

```text
POST /api/projects/{project_id}/timelines/{timeline_id}/fork
```

```json
{
  "timeline_id": "source-timeline-id",
  "node_id": "checkpoint-node-id",
  "name": "The General survives"
}
```

The source timeline is never modified. The branch copies the source checkpoint, derived character state, and timeline-scoped memories before appending new events.

Regenerate a performance on a branch:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/regenerate
```

## Reliability: traces, memories, commitments

List recent generations with their context totals and validation status:

```text
GET /api/projects/{project_id}/timelines/{timeline_id}/generations
```

Read the full trace for one generation — context composition, validation,
contradictions, memory writes, and per-stage timings, with no credentials:

```text
GET /api/projects/{project_id}/generations/{generation_id}
```

Mark a committed generation as rejected after the fact. Events are
append-only, so this appends a `generation_rejected` event rather than erasing:

```text
POST /api/projects/{project_id}/generations/{generation_id}/reject
```

Inspect which memories are active, which were superseded, and what retrieval
sees for a query, using the same filter and rank as the pipeline:

```text
POST /api/projects/{project_id}/timelines/{timeline_id}/memories
```

Move a commitment through its lifecycle (`created`, `active`, `progressing`,
`fulfilled`, `failed`, `cancelled`, `superseded`). Illegal transitions return
`422`:

```text
POST /api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}
POST /api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}/supersede
```

Apply an operator correction to a world fact. This is the third option offered
on a contradiction warning and appends a modification rather than editing history:

```text
POST /api/projects/{project_id}/timelines/{timeline_id}/correct-state
```

Read the configured provider's declared context window and features:

```text
GET /api/providers/capabilities
```

## Character cards and lorebooks

Preview a Character Card before persisting it:

```text
POST /api/projects/{project_id}/imports/character-card/preview
```

Preview a lorebook without creating a `Lorebook` row:

```text
POST /api/projects/{project_id}/imports/lorebook/preview
```

Both preview endpoints validate the file and return name, version or entry count, source format, warnings, preserved extension keys, and book budget metadata. They do not create a character or lorebook.

Confirm a Character Card V1/V2 JSON file or a PNG/APNG file with embedded card metadata:

```text
POST /api/projects/{project_id}/imports/character-card
```

The request is `multipart/form-data` with a `file` field. The response includes `character_id`, `card_version`, `source_format`, `source_filename`, `lorebook_id`, `entry_count`, `warnings`, and `extensions_preserved`.

List or retrieve imported characters:

```text
GET /api/projects/{project_id}/characters
GET /api/projects/{project_id}/characters/{character_id}
```

The retrieval endpoint returns the normalized character definition, preserved card payload and extensions, and associated character books.

Confirm a standalone lorebook:

```text
POST /api/projects/{project_id}/imports/lorebook
```

The request is also `multipart/form-data` with a `file` field. The response includes `lorebook_id`, `entry_count`, `source_filename`, and `warnings`.

List imported books and preview activation without running a generation:

```text
GET /api/projects/{project_id}/lorebooks
POST /api/projects/{project_id}/lorebooks/{lorebook_id}/associate
POST /api/projects/{project_id}/lorebooks/{lorebook_id}/preview
```

Association accepts `{"character_id": "..."}` and changes the book to a character-scoped book. Preview accepts `text` and an optional `character_id`. It returns activated and considered entries with reasons, ordering, scope, and token-budget information. Character-owned books are filtered by `character_id`.

Uploads are limited to 8 MiB. Extensions and MIME types are checked before reading, JSON depth is limited, and books are truncated to a bounded entry count with a warning. Imported text is treated as untrusted reference data and is never executed as application code.

## Provider smoke testing

The deterministic test suite never calls an external model. An opt-in smoke test is available for a configured OpenAI-compatible endpoint:

```text
NARRATIVE_PROVIDER=openai_compatible
NARRATIVE_PROVIDER_BASE_URL=https://your-endpoint.example/v1
NARRATIVE_PROVIDER_MODEL=your-model
NARRATIVE_PROVIDER_API_KEY=your-secret
pytest -m smoke
```

Without those settings, the smoke test is skipped. The regular `pytest` command does not require credentials.
