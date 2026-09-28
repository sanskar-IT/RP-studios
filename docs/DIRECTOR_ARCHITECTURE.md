# Director Architecture

This document has two parts. Part 1 is the audit of the intent pipeline as it
existed before the Director layer — what each piece does, and which pieces
already correspond to Director responsibilities. Part 2 (added with the
implementation phases) describes the target design.

The pipeline invariant for the whole milestone:

```text
User Intent
    ↓
Director Interpretation
    ↓
DirectorPlan
    ↓
Performance
    ↓
Proposed Events
    ↓
Validation
    ↓
Committed State
```

The Director never directly mutates authoritative state. The State Engine
(`validate_claims` + `apply_event`) remains the final authority over what
became true.

---

# Part 1 — Audit of the pre-Director intent pipeline

Recorded before any Director code was written. All references are to
`services/narrative/pipeline.py` unless stated otherwise.

## 1.1 How user input is classified

`classify_intent(text, mode="auto")` (`pipeline.py:175-185`) is the entire
classifier. It is two regexes plus a mode short-circuit:

- explicit `mode` in `{direction, story_commitment, world, retcon, narration}`
  passes through untouched;
- `/\b(immediately|now|right now)\b/` → `immediate_action` (reachable only via
  `auto`/unknown mode — never via an explicit mode);
- `/\b(make|have|ensure|eventually|eventually|goal|plan|want)\b/` →
  `story_commitment` (note the duplicated `eventually` alternative);
- otherwise → `narration`.

Its only caller is `direct()` (`:675`). `continue_scene()` never calls it.
There is no confidence, no specificity notion, no ambiguity or contradiction
detection.

## 1.2 How actor / director / narrator / world input differs

Modes originate as six frontend tabs (`CommandDock.tsx`, `InputMode` in
`types.ts`) and are mapped to routes in `App.tsx:submitCommand`:

| Tab | Route | Payload |
|---|---|---|
| Retcon | `canon-override` | text + reference |
| Director | `direct` | `intent_type="story_commitment"` |
| World | `direct` | `intent_type="world"` |
| Actor / Narrator / Auto | `continue` | `mode` lowercased, user/actor ids |

`ContinueRequest.mode` and `DirectRequest.intent_type` are unconstrained `str`
(`packages/schemas/api.py:135-148`). Inside `continue_scene`, `mode` is
**traced but never branched on** — no `if mode == ...` exists; it is persisted
to `GenerationTrace.input_mode`, echoed in fallback event payloads, and used
only as retrieval query text. Actor choice ignores it.

`direct()` (`:663-731`) always creates both a `DirectorIntent(PENDING)` and a
`StoryCommitment(CREATED)` row plus a `DIRECTOR_INTENT_CREATED` event, then
commits — with exactly one branch: `immediate_action` additionally appends an
authoritative `USER_ACTION` event and flips both rows to completed/fulfilled.
All other types (`direction`, `world`, `retcon`, `narration`) behave identically
to `story_commitment` except for the stored `type` string. `direct()` produces
no prose and no plan.

`horizon` (`DirectRequest.horizon`, default `"short"`) is stored on the intent
row and event payload but **never read anywhere**.

## 1.3 How staging currently works

`stage()` (`:187-321`) prompts the provider with "infer reasonable missing
details, but do not execute the premise" and builds a proposal carrying
`location, time, characters_present, objective, initial_conditions`,
`environmental_assumptions`, consequences, `canon_conflicts`,
`assumptions`/`unresolved_assumptions`, plus heuristic `_source_context` and
`_canon_conflicts` matches. Status flow is
`stage → edit/regenerate (STAGED) → approve (STAGED→ACTIVE) → continue`;
`continue_scene` refuses non-ACTIVE scenes, and active scenes can only be
re-cut via branch regeneration. This is the closest existing analogue to
Director planning: assumption-marked inference behind an approval gate.

## 1.4 How actor selection works

`_choose_actor` (`:1393-1421`): explicit override → possessed character →
character named in input (casefold substring, participant-filtered) →
user-controlled participant → deterministic `participants[0]` fallback. The
reason string is recorded in the trace (`actor_selection`, `:455`) and the
provider may override the pick via `selected_actor` when no explicit override
was given (`:517-518`).

## 1.5 Where planning / generation / state changes happen

`continue_scene` (`:404-661`) runs, in order: pre-checks + actor pick →
`current_state` + trace init → planner context (open commitments only) →
memory retrieval → lore evaluation → knowledge view + contradiction detector →
context assembly → `add_generation(RUNNING)` → provider `structured()` call →
shape validation → claim extraction → claim validation (+ prose check) →
accepted-claim commit loop (idempotent keys) → overhearing fan-out
(`KNOWLEDGE_SUSPECTED`) → memory lifecycle → COMPLETED + trace persist.

"Planning" today is only the open-commitment list fed to the prompt as
metadata. Generation is exactly one provider `structured()` call against
`GENERATION_SCHEMA`. State changes happen exclusively in the commit loop via
`repository.append_event` → `apply_event`, a pure copy-on-write projector
(`services/core/state.py:112-263`).

## 1.6 Which pieces already correspond to a Director layer

- **Staging + approval gate** → the draft/approve pattern DirectorPlan reuses.
- **`DirectorIntent` rows + `StoryCommitment` lifecycle** → the persistence
  layer long-horizon intents attach to (no new tables needed in Phase A).
- **Planner context + trace** → the observation surface the router annotates
  (`GenerationTrace` gains a `director` block; additive only).
- **Claim validation + `apply_event`** → the State Engine boundary the
  Director must never cross; unchanged by this milestone.

## 1.7 Gaps the Director closes (later phases)

No intent interpretation on the `continue` path (verbatim `user_input`
injection); no plan object; unused `horizon`; canon detection without a
follow/override/branch choice flow; actor selection unaware of beats, goals,
or proximity; no consequence tiers, completion conditions, per-character
agency briefs, or authority modes. LLM refinement of ambiguous intents is
deferred to Phase B (Phase A is heuristic-only so no extra provider call can
disturb existing `ScriptedProvider` response counts).

---

# Part 2 — Target design

*(Added in Phase B: router lanes enacted, DirectorPlan lifecycle, performer
contract, authority enforcement. Phase A ships the audit above plus the
transient Intent model and the annotating router.)*

## 2.1 The shape of a turn

```
user input
    │
    ▼
router ──────────── deterministic, synchronous, total
    │  lane
    ▼
Director ────────── interpret intent, build plan (lanes 3–4)
    │              validate against the constraint envelope
    │              decide: perform / propose / block / record
    ▼
Performer contract  rendered into the required scene block
    │
    ▼
Performer ───────── prose + proposed events (never state)
    │
    ▼
State Engine ────── the sole authority on what became true
    │
    ▼
memory / checkpoint / trace
```

The load-bearing property: **the Director never commits state.** It proposes,
the engine disposes. Everything below the contract is unchanged from before the
Director existed, which is why Phase B could ship without touching the memory or
context architecture.

## 2.2 What each lane does now

| Lane | Behaviour |
|---|---|
| `direct_actor` | No plan, no plan row. The user is already performing. |
| `simple_world` | No plan. Time and ambience are the world's business. |
| `direction` | Builds a plan, validates it, and either performs it or proposes it for a decision. |
| `long_horizon` | Records a `StoryCommitment`, performs nothing, completes. |

Lanes 1 and 2 are the "no job" lanes, and they matter: a plan row for a turn the
user performed themselves implies the Director claimed authorship of something
they already did. Evaluation scenario A pins this.

## 2.3 Plan persistence and the turn that auto-proceeds

An auto-approved direction turn still persists its plan, linked to the generation
that performed it, moving `approved → executing → completed`. Without that link
the Director is invisible exactly when it is doing work, and "why did this
happen" has no answer once the turn is over.

Long-horizon plans move through `executing` as well, because that is literally
what happened — the commitment was written — and they carry no `generation_id`,
since there is no generation. They are completed at the moment of recording.

## 2.4 Execution does not re-route

`execute_plan` hands the approved plan to `continue_scene` directly. The beat
text is deliberately not routed again: a plan is already a decision, and treating
its prose as a fresh user request would either propose a second plan for the same
beat or silently replace the one the user approved.

Approval is re-verified against the envelope at decision time and the plan is
re-validated at performance time. Both are necessary: a user correcting a plan is
not granting permission to run one the State Engine would reject, and a scene can
change between proposal and performance.

## 2.5 Two bugs the integration surfaced

Recorded because both were silent — structurally valid output, wrong meaning.

**Knowledge leak in briefs.** Character briefs initially rendered every
character's `knows` and `suspects`. The knowledge isolation layer guards the
prompt *it* builds; a brief is text the Director appends to that prompt, and the
guard cannot see inside it. The Detective's private fact reached the Witness's
prompt. Briefs are now viewer-scoped, and the non-viewer brief is deliberately
weaker — an observer only sees what was done, carried, or suffered.

**Planning with off-stage characters.** The Director was handed every character
in the project but validated against the scene's participants, so any plan
naming an off-stage character was rejected as unperformable. The Director now
plans over participants; off-stage characters remain legitimate *targets* of
direction but can never be plan participants, because a beat that assigns action
to someone the State Engine cannot see here would act off-stage.

## 2.6 The pipeline lifecycle guard

`PLAN_TRANSITIONS` makes plan state transitions explicit and validated rather
than assumed. It caught two real defects during the integration: a long-horizon
plan jumping straight to `completed` (skipping `executing`), and `interrupt_plan`
marking a never-performed plan `completed` — which claimed work that never
happened. An interrupted plan that had not performed is now `cancelled`.

## 2.7 What Phase B did not change

Memory retrieval, context assembly, lore evaluation, the State Engine, event
validation, branch isolation, and the existing commitment lifecycle. Long-horizon
intent reuses `StoryCommitment` rather than introducing a parallel forward-planning
system, which is why there is no separate "director memory" and no second source
of narrative truth.

## 2.8 Where the pieces are

| Concern | File |
|---|---|
| Interpretation | `services/director/intent.py` |
| Lane assignment | `services/director/router.py` |
| Plan, beats, heuristic planner | `services/director/plan.py` |
| Constraint envelope, validation | `services/director/envelope.py` |
| Structured refinement + fallback | `services/director/refine.py` |
| Canon conflict | `services/director/canon.py` |
| Orchestration, briefs, contract | `services/director/director.py` |
| Turn integration, plan API | `services/narrative/pipeline.py` |
| Persistence | `apps/api/app/repository.py` |
| Schema | `alembic/versions/0005_narrative_director.py` |
| HTTP | `apps/api/app/api/routes.py` |

## 2.9 A third silent bug: the harness's own tokenizer

Re-running the engine evaluation after the Director work turned up three failing
`mystery` runs reporting a knowledge leak at turn 14 — a turn where the *user*
says "Alice hesitates, then admits she has a brother" and the detective is the one
who learns the fact.

The leak was real in the report and absent in reality. `_tokens` in
`services/context/knowledge.py` split on whitespace without stripping
punctuation, so `"brother." != "brother"` and the harness's exemption for facts the
user stated themselves never fired. The user's own sentence was being scored as
the engine volunteering something it should not.

Worth recording because the cause was the *detector*, not the thing it detects.
An evaluation that fails loudly is worth more than one that quietly drifts, and
this one had been quietly wrong for long enough to hide a real defect elsewhere —
it was reporting the harness's bug at the same turn a genuine leak would have
surfaced.

## 2.10 Verification

Phase B alone was 206 tests. With Phase C the suite is 227, and there is a third
evaluation suite, because the Performer's failures are silent in a different way
again.

```
python -m pytest                                    # 227 passed, 12 skipped
python -m ruff check apps services packages tests
python -m mypy apps services packages
python -m services.evaluation.harness --print        # 20/20 runs
python -m services.evaluation.director --print       # 8/8 scenarios
python -m services.evaluation.performer --print      # 10/10 scenarios
```

The Director has its own evaluation suite because its characteristic failures are
silent: a plan that quietly contradicted an exclusion, a commitment performed in
the turn that asked for it eventually, a private fact handed to the wrong
character. Each produces a structurally valid generation that says the wrong
thing, so none of them show up as a validation error in the engine harness.
