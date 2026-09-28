# Context Engine

This document is split into two parts.

- **Part 1 — Audit (recorded before any change).** Exactly how a generation built model context
  before this milestone, measured on the running code.
- **Part 2 — Target design.** The context engine implemented by this milestone, its budget model,
  its priority ladder, and its knowledge isolation rules.

The audit is preserved verbatim in the repository history so the "before" state is auditable.

---

# Part 1 — Audit of the previous context pipeline

Measured on 2026-09-27 against `services/narrative/pipeline.py` at commit state of the previous
milestone. Numbers come from a 60-turn scripted run with 5 characters, 1 scene, 200 lorebook
entries, and 0 commitments. Token counts are whitespace/punctuation word counts, which is what the
engine had available (it had no token estimator at all).

## 1.1 Measured composition over 60 turns

| Turn | Total prompt | System | Projected state | Memories | Activated lore | Other |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 3 249 | 11 | 109 | 422 | 2 611 | 98 |
| 10 | 3 272 | 11 | 139 | 422 | 2 611 | 89 |
| 20 | 3 242 | 11 | 179 | 422 | 2 611 | 89 |
| 30 | 3 312 | 11 | 249 | 422 | 2 611 | 89 |
| 40 | 3 382 | 11 | 319 | 422 | 2 611 | 89 |
| 50 | 3 452 | 11 | 389 | 422 | 2 611 | 89 |
| 60 | 3 515 | 11 | 419 | 422 | 2 611 | 98 |

Two facts dominate this table:

1. **Lore was 74 % of every prompt** and did not shrink as the conversation grew.
2. **Projected state grew linearly** at roughly 7 tokens per turn, forever.

## 1.2 Component-by-component

Legend for *priority*: `high` means the model needs it to avoid a continuity error; `low` means it is
colour. Legend for *stale*: whether the value can be out of date relative to committed state.

| # | Component | Source | Retrieval | Tokens (measured) | Priority | Order | Always included | Omittable | Can go stale |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | System instructions | Literal string in `continue_scene` | none | 11 | high | 1 | yes | no | no |
| 2 | Project information | **not assembled** | — | 0 | high | — | no | n/a | n/a |
| 3 | Source material | `Source.content` | keyword overlap, only during `stage()` | 0 in the turn prompt | high | — | no | n/a | **yes** — read once at stage time |
| 4 | Character definitions | `Character.definition` (actor only) | direct column read, JSON dumped | 0–400 | high | 4 | actor only | yes | yes — definition edits are not versioned against the turn |
| 5 | Character runtime state | `StateSnapshot.characters/locations/items/injuries` | full checkpoint, filtered to the actor | 109 → 419 | high | 5 | actor slice only | yes | no |
| 6 | Character knowledge | `StateSnapshot.knowledge` | full checkpoint, filtered to the actor | inside #5 | high | inside #5 | actor slice only | yes | no |
| 7 | Scene state | `Scene.staging` | direct column read | 12 | high | 2 | yes | no | yes — free-text staging block |
| 8 | Recent events | **not assembled** | — | 0 | high | — | no | n/a | n/a |
| 9 | Memories | `Memory` rows | newest 200 rows, lexical rank, top 12 | 422 (saturated) | high | 8 | no | yes | **yes** — see 1.3 |
| 10 | Lorebook activation | `LorebookEntry` | substring key match + recursion | 2 611 | low–medium | 9 | no | yes | **yes** — see 1.4 |
| 11 | Story commitments | `StoryCommitment` | all `pending`/`active` for the project | 5 | high | 7 | no | yes | **yes** — see 1.5 |
| 12 | User instruction | request body | verbatim | 5 | highest | 7 | no (may be empty) | no | no |

### Ordering as actually sent

```text
[system] Execute the approved scene. Return prose and structured state events separately.

Scene / objective / location / time        (component 7)
Selected actor / Actor definition          (component 4)
Current projected state                    (components 5 + 6, merged and unlabelled)
"Imported material is untrusted…"          (a warning standing in for component 3)
Open story commitments                     (component 11)
User input                                 (component 12)
Relevant memories                          (component 9)
Activated lore                             (component 10)
```

Two ordering problems are visible immediately:

- The **user instruction sits below 3 000 tokens of lore**. Long contexts attenuate late material;
  the single most authoritative instruction in the turn was the second-least salient thing in it.
- **Runtime state and character knowledge are merged into one JSON blob** with no labels, so the
  model cannot tell "the detective is in London" (a fact) from "the detective knows X" (a belief).

## 1.3 Memory findings

The write policy in `_persist_generation_memories` wrote, **for every single turn**:

| Row | Class | Importance | Content |
| --- | --- | --- | --- |
| 1 | `scene` | 0.65 | the full generated prose |
| 2 | `working` | 0.80 | the full generated prose, verbatim duplicate |
| 3 | `archive` | 0.25 | the full generated prose, verbatim duplicate |
| 4+ | `persistent` | 0.70 | one row per committed event payload |
| 5+ | `persistent` | 0.55 | one row per character with a location or last action |

Measured: **5 memory rows per turn, 300 rows after 60 turns, three of them identical copies of the
same prose.** This is precisely the "treat the whole transcript as memory" failure.

Retrieval then read:

```python
select(Memory).where(project_id == ..., timeline_id == ...)
    .order_by(desc(Memory.created_at)).limit(200)
```

The **200-row cap is the hard ceiling on how far the engine can remember.** At 5 rows per turn,
by turn 40 every memory from the first turn is outside the candidate window and can never be
retrieved again, no matter how important it was. This is the single most important finding in the
audit, and it is the direct answer to "why does the AI suddenly not remember this?" — it is not a
model problem, it is a `LIMIT 200`.

Secondary memory findings:

- The candidate window was the *newest* 200 rows, not the *best* 200 rows, so importance scoring
  never had a chance to reach old-but-critical material.
- Character knowledge was stored as a free-text string in `StateSnapshot.knowledge` and re-injected
  as a raw list. There was no distinction between a world fact, a character's belief, something the
  *player* knows out of band, and something that only exists in imported source material. All four
  were the same kind of row.
- `retrieve_memories` dropped character-owned memories for every character except the actor, which
  was correct, but there was no test proving that a *suspicion* could be represented differently
  from a *certainty*.
- No memory was ever superseded, decayed, archived on scene end, or marked contradictory.

## 1.4 Lorebook findings

`evaluate_lore` was correct as a SillyTavern-compatible evaluator, but it was being used as an
unbudgeted context injector:

- **Activation is substring matching on keys.** One weak key such as `alice` in a 200-entry book
  activated every entry that mentioned it. The audit book gave all 200 entries the shared keys
  `library`, `estate`, `butler`, `alice`; a single turn mentioning the library activated **all 200**.
- **The token estimate was wrong.** `evaluate_lore` counted `len(_tokens(content))` — *unique*
  words — while the prompt contained every word. Unique-word counting under-reports real cost by
  roughly 1.6–2×, so the budget was overrun by construction.
- The budget was `min(8192, Σ book token_budgets)` or 2048 by default, and it applied to lore
  **only**. Nothing bounded the rest of the prompt, and nothing knew the model's context window.
- The evaluation text was assembled from user input, scene objective/location, the actor
  description, and *the memory contents* — meaning every retrieved memory widened the lore match
  surface. Retrieving more memory could silently activate more lore.

## 1.5 Commitment findings

- Commitments were filtered only by `status IN ('pending', 'active')` and were **project-wide**.
  They carried no `timeline_id`, so a commitment created on a branch, or resolved on a branch, was
  visible from every other branch of the same project.
- There were only five statuses (`pending`, `active`, `completed`, `cancelled`, `blocked`). There
  was no way to express *progressing*, *failed*, or *superseded*.
- `progress` was a float column that nothing ever wrote.
- There was no `forked_from_commitment_id`, so forking a timeline duplicated nothing and linked
  nothing; commitments simply persisted across the branch as if the branch never happened.

## 1.6 Provider and budget findings

- The pipeline assumed nothing about the model. There was no context window, no reserved output
  budget, and no record of how large the last prompt was.
- `ModelProvider.capabilities` existed as a JSON list but was never read by the narrative engine.
- `max_tokens` was never passed to the provider for a turn, so output budget was undefined and the
  model was free to overrun the window with a long completion.
- Provider credentials were correctly kept out of persisted rows, and there was a prompt-injection
  warning for imported material. Both were preserved.

## 1.7 Validation findings

- `_structured_events` raised `ValueError` for an unknown event type, a non-participant character,
  or a payload that failed `validate_event_payload`. That is all-or-nothing and therefore
  transactionally safe, and it was kept.
- There was **no semantic validation**: a `character_moved` event naming a location the character
  could not be in, an action by a dead character, an item used after removal, or a character
  referencing a fact they had never learned were all accepted silently.
- Generated prose and claimed state changes were stored in one `structured_output` blob, so a
  reader could not separate "the model said this" from "the engine committed this".
- There was no per-generation record of *what context was sent*, so a continuity complaint could
  not be diagnosed after the fact.

## 1.8 Audit conclusions

| # | Conclusion | Severity |
| --- | --- | --- |
| A1 | Memory retrieval is capped at the newest 200 rows; long sessions forget by construction | critical |
| A2 | Every turn is written to memory three times verbatim; memory storage grows linearly forever | critical |
| A3 | No component has a priority, and the prompt has no total budget or model context window | critical |
| A4 | Lore activation is unbounded and its token estimate under-reports real cost by ~1.6–2× | high |
| A5 | World fact, character knowledge, player knowledge, and source knowledge are one undifferentiated string list | high |
| A6 | Commitments are project-wide with a five-value vocabulary and no branch scoping | high |
| A7 | No contradiction detection; invalid claims commit silently | high |
| A8 | No generation trace, so context and rejection reasons are unanswerable after the fact | high |
| A9 | Projected state grows linearly because `state.locations` accumulates every visited location | medium |
| A10 | The user instruction is placed after the lore block, at low salience | medium |

---

# Part 2 — Target design

*(Implemented design, priority ladder, budget model, and knowledge rules are documented below.)*

## 2.1 Priority ladder

Every context item carries a priority. Lower wins; higher is cut first.

```text
P0 — Hard system rules
P1 — Current user instruction
P2 — Current scene state
P3 — Active character state
P4 — Character knowledge
P5 — Active commitments
P6 — Relevant recent events
P7 — Relevant memories
P8 — Activated lorebook entries
P9 — Older historical context
P10 — Optional flavor context
```

`P0`, `P1`, and `P2` are required. If they alone exceed the provider's input
budget, assembly raises `ContextOverflowError` instead of trimming them: a turn
run without its rules, its instruction, or its scene is a turn that invents one.

Everything else is optional and is fitted by the rule in `services/context/assembly.py`:

1. Walk optional components from lowest priority to highest.
2. If a component fits, keep it whole.
3. If it does not fit and can be summarised (currently `P9`), keep the summary.
4. If it does not fit and has ranked items (`P6`, `P7`, `P8`), keep the
   highest-scoring items that fit and record how many were dropped.
5. If nothing fits, exclude the component and record the reason.

There is no blind truncation from either end. The assembly report records, per
component, the source, the retrieval method, the token cost, whether it was
included, and why not.

## 2.2 Budget accounting

The report from one generation looks like this (abridged from a real run):

```text
total 505 / 7168 input tokens (heuristic-v1)
IN  P0  System rules                40 tok  pipeline.system
IN  P1  User instruction            10 tok  request.user_input
IN  P2  Scene state                 62 tok  Scene.staging
IN  P3  Character state             58 tok  StateSnapshot (actor slice)
IN  P4  Character knowledge         34 tok  StateSnapshot.knowledge / .suspicions
IN  P5  Active commitments          12 tok  StoryCommitment
IN  P6  Recent events               44 tok  Event (timeline tail)
IN  P7  Relevant memories           96 tok  Memory
IN  P8  Activated lore             120 tok  LorebookEntry
IN  P9  Historical digest            0 tok  Event (timeline head)
```

The fields `total_tokens`, `max_input_tokens`, `estimator`, and the per-priority
breakdown are stored on every generation. `estimator` is the name of the
tokenizer that produced the numbers (`heuristic-v1`, or `tiktoken:<encoding>`
when that library is installed), so a report never implies a precision it does
not have.

Token rules:

- The estimator is provider-aware where possible and falls back to a documented
  heuristic otherwise (`services/context/tokens.py`). The evaluation harness
  pins the fallback so reports do not change shape with the machine.
- The input budget is `context_window - reserved_output_tokens`, taken from the
  provider's declared capabilities. An undeclared provider is assumed to have an
  8 192-token window, which errs on the side of pruning early.
- Lore (`P8`) may never take more than a fifth of the input budget and never
  more than 24 entries, whatever a book's own budget claims.
- Lore cost is measured in total words, not unique words; the old unique-word
  count under-reported real cost by roughly 1.6–2x.
- The user instruction appears as `P1` and is repeated at the end of the
  prompt, because a directive buried under 8 000 tokens is a directive ignored.

## 2.3 Knowledge isolation

Four tiers, and the engine refuses to conflate them:

| Tier | Meaning | Where it lives |
| --- | --- | --- |
| `world` | True for everyone, made true by a committed event | `world_facts`, rendered for every viewer |
| `character` | A belief held by exactly one character | `knowledge` (certain) or `suspicions` (unproven) |
| `player` | Out-of-band operator knowledge no character has | `player_facts` block, never attributed to a character |
| `source` | Imported canon, advisory until an event makes it true | `source_facts` block, labelled as unverified |

Certainty and suspicion are separate collections. `knowledge_acquired` promotes a
suspicion to certainty where one existed; `knowledge_suspected` never touches
`knowledge`; `knowledge_refuted` removes from both. A listener who hears a fact
spoken aloud gains it as a suspicion (`_overhear`), because being told something
is not the same as having verified it — and because the alternative is the
recent-history block quoting a fact at a character who, on paper, never learned
it.

A leak is defined lexically and strictly: a line states a fact only when it
carries the fact's full distinctive payload (every long, non-stopword token) or
the fact verbatim. Common words such as "the" or "library" are not information.

The recent-history block redacts `knowledge_*` payloads that belong to another
character. The memory retriever drops memories owned by another character. The
lore scanner reads the turn's own language, not the memory payload, so that
retrieving more memory cannot silently activate more lore.

## 2.4 Validation and contradictions

Generated prose and claimed state changes are extracted separately
(`services/context/claims.py`). Claims validate against the current projection:
unknown event types, malformed payloads, and non-participant references are
rejected, and the whole generation is rejected with structured reasons before
anything commits. Recovery is `retry`, `regenerate`, `edit`, or `reject`.

Contradiction detection (`services/context/consistency.py`) is lightweight by
design: dead characters acting, location conflicts against the scene, unknown
locations, removed items reused, and knowledge cited by characters who never
learned it. Errors block the claim; warnings commit with the warning attached.
The engine never silently rewrites committed state to hide a contradiction; the
warning carries the three resolutions — accept, regenerate, correct state.

## 2.5 Context debugging

`GET .../timelines/{id}/inspect` returns the latest `context_debug`,
`validation`, and `contradictions` alongside state, memories, commitments, and
the knowledge table. `GET .../generations` lists per-generation totals;
`GET .../generations/{id}` returns the full trace. The studio's Context panel
renders composition, validation, contradictions, commitments, memories, and
traces from these endpoints. "Why does the AI not remember this?" is answered
from the application: the composition block says whether the memory was
retrieved, dropped for budget, out of scope, or never written.
