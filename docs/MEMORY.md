# Memory

Memory is a classified projection used to build generation context. It is not a transcript log.

## Lifecycle

Every memory passes through the same stages, and every stage is inspectable:

```text
Event
 ↓
Memory seed               what the event asserts
 ↓
Importance evaluation     deterministic score in [0, 1]; no model call
 ↓
Classification            class (how long it lives) and scope (whose eyes may see it)
 ↓
Memory persistence        only surviving proposals are written; the rest are reported as dropped
 ↓
Retrieval                 ancestry-scoped, budgeted, character-filtered
 ↓
Context injection         P7, never privileged over state, knowledge, or the instruction
```

Not every event becomes memory. A turn writes at most one narrative-beat record
plus one record per durable fact it established. Position changes are transient
by definition — they are state, not memory — and filler prose is scored and
reported as dropped.

## Classes

- **Permanent**: stable character or world information that remains valid across timelines unless explicitly changed.
- **Persistent**: facts currently true on a timeline, such as current location, injuries, relationships, or known commitments.
- **Scene**: information relevant to the active scene and its participants.
- **Working**: bounded context assembled for the next generation.
- **Archive**: older events and beats retained for retrieval but not injected wholesale.
- **Transient**: scored, classified, and then discarded; recorded in state only.

Class answers "how long should this live". Scope answers a different question —
"whose eyes may see this" — and is stored separately:

- **World**: true for everyone.
- **Character**: owned by exactly one character.
- **Scene**: valid for the active scene and its participants.
- **Transient**: discarded after scoring.

## Retrieval

Retrieval is deterministic, ancestry-scoped, and budgeted. A memory is visible
on a timeline only if it was written there or on one of its ancestors, and only
from the sequence at which it became valid. A memory written on a sibling branch
is invisible, no matter how many turns later it is asked for.

The candidate pool is a bound on what is ranked, not a truncation of what the
engine can see; its size is reported in every trace. Selection ranks by lexical
overlap, importance, class weight, scope weight, and recency. A branch copy of
an inherited memory collapses with its original instead of being injected twice.

Character-owned memories are only returned for that character. Global persistent
memories require a world or public visibility marker. Character knowledge is
explicit in state and the prompt receives only the selected character's state
slice, so one character's knowledge is not exposed to another. Suspicions are
kept apart from certainties and are rendered as unproven.

A provider prompt receives only selected memory records, pending commitments,
and activated lore entries. The selection and lore decisions are exposed through
inspection responses for debugging. Memory rows include a nullable pgvector
embedding column for future semantic retrieval; lexical retrieval remains
deterministic for the current milestone.

## Write policy

The previous policy wrote every turn three times verbatim and is kept in the
audit section of `docs/CONTEXT_ENGINE.md` as a warning. The current policy:

- one beat seed per generation, classified by importance;
- one fact seed per durable event (knowledge, world facts, items, injuries,
  death, relationships), each with its owning character where there is one;
- transient seeds are never persisted;
- every proposal carries its reason, and the retention report
  (`proposed`, `persisted`, `dropped`, `by_class`, `by_scope`, `drop_reasons`)
  is stored in the generation trace.

A branch copies timeline-scoped memories at fork time, recording
`inherited_from_memory_id` so retrieval can collapse the copy. Retiring a memory
marks it inactive rather than deleting it, because memory rows are evidence of
what the engine believed and when.

## Answering "which memories are active"

`POST .../timelines/{id}/memories` runs the same filter and rank the pipeline
uses and reports totals, active and superseded counts, class and scope
breakdowns, the retrieval report, the knowledge table, and the selected rows.
The studio's Context panel renders this directly.
