# Performer Architecture

**Status:** the audit below was written *before* Phase C, and is kept as written
because it records what the code actually did then. Section 15 records what
changed. Where the audit found a gap, the gap is named as a gap.

The question this document answers: when the user types "I open the drawer", what
exactly happens between the keystroke and a committed `Event` row?

---

## 1. The current path, end to end

`services/narrative/pipeline.py`, `continue_scene` (lines 479–908).

| # | Lines | Stage |
|---|---|---|
| 1 | 500–504 | Load scene; require `ACTIVE` |
| 2 | 505–518 | Load `SceneParticipant`s; validate actor/possessed ids are participants |
| 3 | 519–526 | **`_choose_actor`** |
| 4 | 527–528 | `character_map` = *all project characters*; `actor` = the chosen one |
| 5 | 529–541 | Timer, state projection, `GenerationTrace` |
| 6 | 546–557 | `route_intent` (pure, synchronous) |
| 7 | 559–588 | `_perform_approved_plan` **or** `_direct_turn` |
| 8 | 590–638 | Long-horizon short-circuit — returns early |
| 9 | 639–668 | Blocked / needs-approval short-circuit — returns early |
| 10 | 669 | `performer_block = director.performer_block` |
| 11 | 670–675 | Planner context, memory retrieval, lore evaluation |
| 12 | 676–677 | `build_knowledge_view(viewer_character_id=actor_id)` |
| 13 | 678–689 | **`_assemble`** → `(assembly, prompt)` |
| 14 | 705–716 | `Generation` row, status `running` |
| 15 | 718–726 | **`provider.structured([system, user], GENERATION_SCHEMA)`** |
| 16 | 729–747 | `_validate_generation_shape` → `extract_claims` → `validate_claims` |
| 17 | 748–758 | provider/shape failure → `failed`, re-raise |
| 18 | 759–777 | invalid claims → `rejected`, re-raise, **nothing committed** |
| 19 | 778–800 | Resolve selected actor, prose, event list |
| 20 | 805–821 | **Commit loop** — `repository.append_event` per event |
| 21 | 822–839 | `_overhear`, `_persist_memories` |
| 22 | 840–885 | `Generation` completed, plan linkage, trace, `db.commit()` |

The State Engine is already correctly positioned: the Performer proposes, and
`apply_event` disposes. That boundary must not move.

---

## 2. Actor selection

`_choose_actor` (`pipeline.py:2070–2098`). Single call site. Returns
`(actor_id, reason)`. Exactly six branches, in strict precedence:

| Priority | Condition | `reason` |
|---|---|---|
| 1 | `actor_override` is a participant | `explicit actor override` |
| 2 | `possessed_character_id` is a participant | `possessed character` |
| 3 | A participant's name appears in `user_input` | `character named in the current instruction` |
| 4 | A participant has `control_mode == user` | `user-controlled participant acts authoritatively` |
| 5 | Any participants remain | `deterministic participant fallback` |
| 6 | No participants | `no participants` |

Findings:

- The `reason` string goes **only** into `trace.actor_selection` (line 540). It
  never reaches the provider and never reaches the prompt.
- Branch 6 is unreachable from `continue_scene`: line 513–514 already raises
  `ValueError("Scene has no participants")`.
- Branch 5 is deterministic but arbitrary — `participants` is ordered by
  `character_id`, so the fallback is the alphabetically-first UUID, not the most
  interesting character and not the creation order.
- Branch 3 iterates `Character` rows in **unspecified DB order**
  (`_character_named_in_input`, lines 2100–2115). If one participant's name is a
  substring of another's, the winner is not deterministic across databases.
- **No dead-character check.** A dead participant can be selected here. The only
  guard is a prompt line (2373–2374): "You are dead in this timeline. Do not act or
  speak." The State Engine catches it later as a rejected claim.

This function is the actor-selection system Phase C must reuse. It is not being
replaced.

---

## 3. Character context construction

`_character_state_block` (lines 2356–2375) renders **one** character, in second
person:

```
You are playing: <name>
Definition: <json, truncated to 1200 chars>
Your state: <json, truncated to 600 chars>
Present scene location: <staging.location>
You carry: <items>
You are carrying these injuries: <injuries>
You are dead in this timeline. Do not act or speak.     # only if dead
```

`_visible_state` (2117–2149) explicitly zeroes `knowledge` and `suspicions`, then
narrows characters, relationships, items, injuries, removed items, control modes,
and dead-set to the single actor.

**There is no prompt concept of "other characters".** Other participants appear
only as:

1. names in the scene block's `Participants:` line,
2. relationship rows inside the actor's own slice,
3. the Director's per-character briefs, when a contract exists,
4. `character_id` inside event payloads.

Both truncations are silent. A character card past 1200 characters is cut off
mid-sentence with no marker.

---

## 4. User-controlled and possessed characters

**Two independent mechanisms coexist, and they are not unified.**

| | Persisted | Per-call |
|---|---|---|
| Mechanism | `SceneParticipant.control_mode` | `possessed_character_id` argument |
| Set by | `pipeline.possess` / `release_possession` (1096–1145) | the API caller, per request |
| Stored | `control_mode` column + `POSSESSION_CHANGED` event | nothing |
| Affects lane | no | **yes** — `classify_lane` returns `DIRECT_ACTOR` unconditionally |
| Affects actor pick | branch 4 | **branch 2** (wins) |
| Affects event type | **yes** — rewrite to `user_action`, `authoritative: true` (807–809) | no |
| Affects event source | **yes** — `EventSource.USER` (816) | no |

The consequences are real:

- Passing `possessed_character_id` on a turn changes routing and the actor pick
  but commits nothing. The character still emits AI-attributed events.
- A persisted `control_mode="user"` makes the character act as the user even when
  the client sends no `possessed_character_id` at all.
- **Possession is never rendered into the prompt.** The model is not told it is
  being controlled. It is not told which actions it may not invent. The only
  proxy is `control_mode` sitting unread inside the actor's state JSON
  (`_visible_state` computes `visible["control_modes"]` at 2145–2147 and
  `_character_state_block` never renders it).
- Because possession forces `DIRECT_ACTOR`, **a possessed turn receives no
  Director guidance at all** — `performer_block` is `""` (`director.py:333–338`).
- `POSSESSION_CHANGED` is projected into `StateSnapshot.control_modes`
  (`state.py:223–226`), so control mode is genuinely part of scene state.

---

## 5. Narration, environment, dialogue

**Narration.** The model is never explicitly permitted to narrate without an
actor. Two code paths suggest it, both unreachable from `continue_scene`:
`f"Selected actor: {actor.name if actor else 'the narrative'}"` (2321) and
`"No actor selected; write the scene rather than a character."` (2359–2360).
`Lane.SIMPLE_WORLD` is the only environment path, and it too yields an empty
`performer_block`.

`staging.environmental_assumptions` is produced by `stage()` (322–325), stored,
and **never rendered into any prompt**. So are `potential_consequences`,
`assumptions`, and `canon_conflicts`.

`KNOWN_EVENT_TYPES` (`services/core/events.py:36–69`) has **no environment,
weather, or atmosphere type**. Rain can only exist as prose, and prose is never
validated.

**Dialogue.** There is no instruction about speech anywhere in `SYSTEM_RULES`
(108–113) or the output contract. `Character.spoke` is never mentioned. The
prompt says "Return the next narrative beat as prose" and nothing else.

There is **no per-actor dialogue concept in the schema at all**.

---

## 6. Multiple characters in one turn

**Multi-character emission is possible by accident, and structurally unsupported.**

The schema has exactly one `selected_actor: string` for the whole turn
(92–106). But:

- `extract_claims` (`claims.py:181–182`) fills a missing `character_id` from that
  single actor — and leaves a *present* `character_id` alone.
- The non-participant check (`claims.py:184–189`) only rejects a claim naming
  someone who is not a participant. **A claim naming a different participant
  passes.**
- So a model returning `{"event_type": "character_spoke", "character_id": <other
  participant>, "text": "..."}` is accepted and committed with that other
  character as speaker.

Then the commit loop undoes it. `pipeline.py:817` sets
`actor_character_id=selected_actor` for **every** event in the turn, so
per-character attribution survives only in the JSON payload and never reaches the
indexed column.

Worse, lines 807–809: on a user-authoritative turn, *every* `character_spoke` /
`character_performed_action` / `ai_action` is rewritten to `user_action` with
`authoritative: true` — **regardless of which character the payload named.** A
witness's dialogue in a possessed turn is recorded as the Detective's
authoritative action at the event-type level, while the payload still carries the
witness's id. That is a silent corruption, and it is the single worst defect in
the current performer path.

`_overhear` (2451–2509) *does* read the payload's `character_id`, so overhearing
fan-out follows per-claim attribution correctly. Only the `Event` row disagrees.

---

## 7. Prose to events, and state commit

`extract_claims` (`claims.py:148–191`) reads four arrays, in order:
`new_events`, `state_changes`, `knowledge_changes`, `relationship_changes`.
Extraction is total — a non-dict entry becomes an error string rather than an
exception, so validation can report every problem in one pass.

Two dead fields in the schema:

- **`actions`** — required by the schema, shape-checked at 1418, and then read
  **nowhere**. It is not in `CLAIM_ARRAYS`.
- **`open_commitments`** — captured into `claims` and stored in the trace, but
  **never committed as a `StoryCommitment` row**.

`validate_claims` (`claims.py:208–267`) per claim: type present, type known,
`validate_event_payload` for required fields, then `detector.for_event` for dead
actors, location conflicts, removed-item reuse, unknown locations, and knowledge
leaks. Any `severity == ERROR` becomes a claim error.

**Verdict is all-or-nothing** (`claims.py:251–258`): one bad claim in a ten-claim
array rejects the entire generation. `validation.accepted` is populated and
discarded. This is the correct choice for narrative state — a half-applied scene
is worse than no scene — and Phase C must preserve it.

Prose is checked but never blocks: `detector.for_prose` (746–747) yields warnings
only.

`repository.append_event` (`repository.py:94–168`) re-validates the type and
payload independently, honours an idempotency key of `f"{generation.id}:{index}"`,
writes the `Event`, calls `apply_event` for the checkpoint, and upserts
`CharacterState` rows. It flushes; the pipeline commits.

Commit points, all inside `continue_scene`:

| Line | Condition | Effect |
|---|---|---|
| 626 | long-horizon early return | commit |
| 657 | blocked/approval early return | commit |
| 755 / 757 | provider or shape failure | commit / flush |
| 772 / 774 | validation rejected | commit / flush |
| 883 / 885 | success | commit / flush |

`commit_on_success` gates **all five**, not just the success path, despite the
name. Its only non-default caller is `regenerate_scene` (1088), which commits
itself and rolls back on exception.

On rejection, **zero `Event` rows are written** — the check precedes the commit
loop. The `Generation` row is committed with status `rejected`, the prose, the
reasons, and the trace. That row is the only durable trace of a rejected turn.

The pipeline never calls `db.rollback()` on its own error paths.

---

## 8. The schema the model actually receives

`GENERATION_SCHEMA`, `pipeline.py:92–106`, verbatim:

```python
{
    "type": "object",
    "properties": {
        "selected_actor": {"type": "string"},
        "reason": {"type": "string"},
        "prose": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "object"}},
        "new_events": {"type": "array", "items": {"type": "object"}},
        "state_changes": {"type": "array", "items": {"type": "object"}},
        "knowledge_changes": {"type": "array", "items": {"type": "object"}},
        "relationship_changes": {"type": "array", "items": {"type": "object"}},
        "open_commitments": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["selected_actor", "reason", "prose", "actions",
                 "new_events", "state_changes", "open_commitments"],
}
```

Structural facts:

- **No nesting. Every array item is `{"type": "object"}` with zero declared
  properties.** No `event_type` enum, no `character_id` field, no item-level
  `required`.
- The model is never told what shape an event must have. That knowledge exists
  only in Python, in `_REQUIRED_EVENT_FIELDS` (`events.py:72–103`).
- `reason` is captured into `claims` and never surfaced in the trace or the API
  response.
- No `$schema`, no `additionalProperties: false`.
- The OpenAI-compatible adapter ignores structured-output modes entirely and
  prepends "Return only valid JSON matching this schema" as a `system` message
  (`openai_compatible.py:90–104`) — landing **before** `SYSTEM_RULES`.

---

## 9. Context assembly and budget

`_assemble` (2299–2353) builds a `GenerationContextRequest` from eleven
components, rendered in priority order:

| key | priority | source | protected |
|---|---|---|---|
| `system_rules` | P0 | `pipeline.system` | **yes** |
| `user_instruction` | P1 | `request.user_input` | **yes** |
| `scene_state` | P2 | staging **+ Performer contract** | **yes** |
| `character_state` | P3 | actor slice | no |
| `character_knowledge` | P4 | actor's knowledge/suspicions | no |
| `commitments` | P5 | `commitment_metadata` | no |
| `recent_events` | P6 | timeline tail, newest first | no |
| `memories` | P7 | importance-scored | no |
| `lorebook` | P8 | activated entries | no |
| `history` | P9 | timeline head, summarisable | no |
| `flavor` | P10 | `staging.flavor` | never appended (`flavor=""`) |

`REQUIRED_COMPONENTS = {system_rules, user_instruction, scene_state}`
(`assembly.py:61–63`).

The Performer contract is protected because it is spliced into `scene_block` as
a 9th line, and every line of a required component becomes a `ContextItem`. So
budget pressure cannot drop the Director's direction — but it also means an
over-long contract triggers `ContextOverflowError` (a `ValueError`, so the API
returns 422) rather than being trimmed. There is no partial-contract trim.

Trim order is highest priority number first: flavor → history → lore → memories
→ recent_events → commitments → character_knowledge → character_state.

Lore retrieval happens **once**, in `continue_scene` step 11, before `_assemble`.
The Performer does not re-retrieve. The context budget is authoritative.

---

## 10. Recovery

Three distinct failure modes, three outcomes:

| Failure | Detection | Status | Committed |
|---|---|---|---|
| provider raises / non-dict | `_structured` 1398–1405 → `ProviderError` | `failed` | Generation row only |
| wrong shape | `_validate_generation_shape` 1407–1427 | `failed` | Generation row only |
| impossible claims | `validate_claims` → `valid is False` | `rejected` | Generation row only |

The design intent is explicit at 1408–1413: shape failure is a *failed* generation;
an impossible claim is a *rejected* generation with recovery options.

**There is no retry anywhere in the pipeline.** One `await self._structured(...)`,
no loop, no backoff. `RECOVERY_ACTIONS = ("retry", "regenerate", "edit", "reject")`
(`claims.py:43`) is a list the API *returns* to the operator, not an action the
engine takes. The nearest thing to a retry is `regenerate_scene` (1035–1094),
which forks a branch and re-runs with `commit_on_success=False`.

`HeuristicProvider` is the offline default, not a recovery fallback — no code path
falls back to it. Its `_structured_for` (base.py:60–75) synthesises a
schema-shaped object *from the schema*, so every array is empty and every string
is `""`. Empty arrays then trigger the `ai_action` fallback (788–800), so the
offline path always commits exactly one event carrying generic prose.

`degradation_plan` (`capabilities.py:179–200`) is **reported into the trace and
never read by any branch**. One of its notes claimed the pipeline would "fall back
to prose-only beats if parsing fails" — that path did not exist; a JSON decode
error became a `ProviderError` and the generation failed. Phase C corrected the
note rather than the code, so the trace no longer describes behaviour the engine
does not have.

---

## 11. Trace

`GenerationTrace` (`services/context/trace.py:112–140`) carries 28 fields. Every
one is scrubbed by `_scrub` in `to_dict()` (142–171), so credential redaction is
already centralised at the single exit point.

The Performer currently has **no trace block of its own**. What a Performer turn
leaves behind is scattered:

- `actor_selection` — actor id and reason only
- `context` — what went into the prompt
- `generated_events` — the claim summary
- `validation` / `contradictions` — the verdict
- `state_changes` / `memory_writes` — what was committed
- `trace.error` — **declared but never assigned anywhere**; provider errors reach
  only `Generation.error`

So a Performer turn is traceable in aggregate but not as a unit: there is no
record of which beat was realised, what the Performer was asked to do, or whether
it completed anything.

---

## 12. The Director's contract, as it exists

`render_performer_contract` (`director.py:244–289`) produces plain text, appended
as the 9th line of the scene block:

```
DIRECTOR DIRECTION (guidance, not instruction to obey literally)
Scene objective: ...
Current beat: <description | "improvise from the situation">
Desired narrative direction: ...
Required consequences: ...
Likely consequences: ...
Optional consequences (do not force): ...
Constraints that must hold: ...
Excluded by the user: ...
Characters decide their own reactions. Do not make everyone respond identically.
  - <Name>: goal=<derived>; knows=<...>|private to them; injuries=<...>; carries=<...>
Combine, skip, or adapt the beat if the scene has moved on. Only assert state
you can support; the engine validates everything.
```

Briefs are viewer-scoped (Phase B): the acting character sees its own
`knows`/`suspects`, every other character is redacted to
`private to them (<name> reasons from their own knowledge)`. Goals are derived
from the projection, never stored.

This is a **string**, not a structure. The Performer receives prose and has no
machine-readable view of required consequences, forbidden outcomes, or the beat's
identity — so it cannot report which of those it satisfied.

---

## 13. Findings summary

Defects, most severe first, **as they stood before Phase C**. Section 15 records
which of these are fixed, which are fixed differently than expected, and which
remain open.

1. **Multi-claim corruption on user-authoritative turns** (807–809). Every
   `character_spoke` is rewritten to `user_action` regardless of which character
   the payload names. Another character's dialogue becomes the possessed
   character's authoritative action.
2. **`Event.actor_character_id` is single-valued per turn** (817). Per-character
   attribution exists only in the payload and never reaches the indexed column,
   so any future per-actor analysis reads the wrong speaker.
3. **Possession is not in the prompt.** The model is never told which character's
   decisions it may not invent. User agency is enforced only by event-type
   rewriting, which is exactly the mechanism that corrupts multi-actor turns.
4. **Two possession mechanisms, ununified.** Per-call `possessed_character_id`
   changes routing and actor pick but not event authority; persisted
   `control_mode` changes event authority but not routing.
5. **Possession suppresses all Director guidance**, because possession forces
   `DIRECT_ACTOR`, which short-circuits the contract.
6. **The schema declares nothing about events.** Item-level shape is undeclared,
   so a model is never told what a valid claim looks like.
7. **No per-actor output structure.** One `selected_actor`, one `prose` blob.
   Multi-actor scenes are unrepresentable, and multi-actor claims pass validation
   only by accident.
8. **`actions` and `open_commitments` are dead fields** — required by the schema,
   consumed by nothing.
9. **Actor selection is a six-branch cascade with two nondeterministic
   orderings** and no dead-character check.
10. **Actorless narration is unreachable** from `continue_scene`; environment has
    no event type at all, and `environmental_assumptions` is stored but never
    rendered.
11. **No engine-initiated retry.** Recovery is entirely operator-driven.
12. **`degradation_plan` documents a fallback that does not exist**, and is read
    by nothing.
13. **Silent 1200/600-character truncations** on character definition and state.
14. **`trace.error` is never assigned.**

Properties that are correct and must be preserved:

- The State Engine is the sole authority; prose is never authoritative.
- Validation is all-or-nothing, so a failed turn commits no narrative state.
- Knowledge is viewer-scoped in both the context block and the Director's briefs.
- Required blocks (`system_rules`, `user_instruction`, `scene_state`) cannot be
  dropped by budget pressure, and overflow raises rather than truncating.
- Lore is retrieved once, before assembly, and the budget is authoritative.
- `GenerationTrace.to_dict()` scrubs centrally, so redaction has one exit point.
- `append_event` re-validates type and payload independently of the pipeline's
  own validation, and is idempotent per `(generation_id, index)`.
- Actor selection already has a single, documented precedence order to build on.

---

## 14. What Phase C must not break

The architectural invariant, restated as constraints on the implementation:

> The Director decides what the scene is trying to accomplish. The Performer
> decides how the scene plays. The State Engine decides what actually happened.

Concretely:

- The Performer **proposes**; only `append_event`/`apply_event` disposes.
- No raw prose is ever read as state.
- A rejected Performer result commits nothing, ever — including the trace-bearing
  `Generation` row, which is a record, not narrative state.
- Possession is an execution authority. It changes who decides, never what a
  character is.
- Knowledge scoping cannot be relaxed to make multi-actor scenes easier.
- The Performer gets one generation per turn, not one per character.

---

## 15. What Phase C changed

`services/performer/` is new. The validation pipeline, the State Engine, memory,
lore, and the Director are unchanged.

### The request

`PerformerRequest` is a structure, not a prompt. Its **shape is the enforcement
mechanism**: a field that does not exist cannot leak, which is how the Performer
stays honest about scope without depending on instructions it might ignore.

Its cast is the scene's **participants**, filtered in `build_actor_briefs`. A
project may hold forty characters; a scene holds the three who are in the room.
Off-stage characters stay legitimate *targets* of direction but are never
performers.

Each `ActorBrief` is viewer-scoped: the acting character sees its own `knows` and
`suspects`, and every other character's beliefs are omitted. Per-actor rather
than per-turn, because a multi-actor turn has as many viewers as it has speakers.

A truncated character card is marked `_truncated` rather than cut mid-sentence.

### The result

`PerformerResult` separates presentation from claims. `prose` is what the user
reads; `proposed_events`, `state_claims`, and `knowledge_changes` are proposals
the existing pipeline judges.

`to_generation_shape` folds the result into the canonical shape so extraction and
validation run on **exactly the code that has always validated them**, rather than
a fork of it. A legacy-shaped provider response still works.

### Three defects fixed

**1. Multi-claim corruption (finding 1).** Authority is now decided **per claim**,
by the character the claim names. Previously every `character_spoke` in a turn
became a `user_action` whenever the selected actor was user-controlled, so a
witness's line was recorded as the detective's authoritative action while the
payload still carried the witness's id. `Event.actor_character_id` now carries
the character the event is *about* (finding 2), so per-actor attribution reaches
the indexed column instead of existing only in the payload.

**2. Possession was never in the prompt (finding 3).** `ActorBrief.agency_withheld`
marks a user-controlled character, the prompt says what the Performer may and may
not do with them, and `check_user_agency` enforces it: a **decision** or a **line**
for a possessed character is refused, while an `observation` or `reaction` is
allowed. That last distinction matters — blocking every action would make
possession produce a scene where the user's character does nothing at all.

**3. Two possession mechanisms (finding 4).** `_resolve_control_modes` is now the
single view. A per-call `possessed_character_id` is authoritative for that call and
reconciles the persisted `control_mode`; routing, the actor pick, event authority,
and the Performer all read the same resolved set.

### Two more, found during Phase C

**`advance_beats` advanced positionally.** It resolved the first N beats rather
than the beat that was actually performed, so a plan could never get past its
second beat: the first was already terminal and the slice skipped over the one in
hand. It now resolves the active beat, and takes a `status` so an unrealised beat
is `skipped` rather than `completed`.

**`requires_choice` suppressed beats entirely.** Under `strict` and
`collaborative`, `build_heuristic_plan` returned no beats, so those plans were
unperformable as a sequence — nothing to pace across turns, and nowhere to hang a
per-turn requirement. A choice is a question about *direction*; a beat is an
instruction about *execution*. They now coexist.

### Pacing

One turn realises one beat. A plan stays `executing` until its beats have all
reached a terminal state, so "completed" means the arc was delivered rather than
that one beat was.

Required consequences are **beat-scoped**. A plan's own required consequences
describe an arc that may take several turns, and demanding the whole arc by the
end of one turn rejects correct scenes for not finishing the story at once.

### Interruption

`_interruption_for` distinguishes two things that were previously conflated:

- The user gives **new direction** → the plan is `superseded`. It was valid and
  overtaken; calling it cancelled would lose the record of what the Director was
  trying to do.
- The user performs an **action** → the active beat is `skipped` and the plan
  continues. Invalidating the whole plan would mean one interruption ended the
  arc.

Possession is what makes an input an action rather than a direction: without it,
"I put my hand on the suspect's arm" is the user directing the scene, and
superseding is correct.

### What was deliberately not changed

- Validation remains all-or-nothing. A rejected Performer result commits nothing.
- Nothing is filtered before validation. A claim naming a stranger is *rejected
  loudly*, because a claim silently deleted by the Performer looks identical to
  one the model never made — and the user would never learn the model tried to
  write a stranger.
- No engine-initiated retry. Recovery remains `retry / regenerate / edit / reject`,
  operator-driven. See the note below.
- One generation per turn. No character-by-character agent swarm.

### A known gap

The Performer has **no engine-initiated retry**. A malformed or unparseable
response fails the generation, exactly as before Phase C. Repair and retry are
operator actions, and the recovery actions are reported on the rejection — but the
engine does not take them itself, and it should not until there is a way to tell a
fixable mistake from a model that has nothing to say. Re-rolling the same request
until it parses would spend tokens and latency to arrive at a different wrong
answer.
