# ADR 0004: Gate Speech Visibility by Presence, Not by Belief

## Status

Implemented. Supersedes nothing. The behaviour it replaces is the in-window
exposure recorded as a known limitation in `README.md` and
`docs/CONTEXT_ENGINE.md` §2.3.2.

## Context

The recent-events block renders the tail of the timeline into every actor's
prompt. It redacts `knowledge_acquired`, `knowledge_suspected`, and
`knowledge_refuted` when the viewer is not the belief's owner, and it renders
speech verbatim.

Speech was deliberately never in that redaction set. The reasoning at the time
was that a listener who hears a fact should gain it as suspicion, so the words
would be accounted for. `_overhear` does that — for a fact the speaker actually
holds and restates verbatim. Two cases fall outside it:

- The speaker asserts something no character has a record of, because the model
  invented it. Nothing is withheld, so nothing is redacted, and no belief record
  is created.
- The speaker paraphrases a fact they hold. `claim_states_fact` requires the
  fact's full distinctive payload, so the restatement does not match and no
  suspicion is created.

Measured, before this decision: a character who had never been present in a
scene received a secret spoken in that scene, verbatim, in their prompt:

```
Stranger (never present)   sees the scene-1 secret: True
   #3 character_spoke: Emperor Vane burned the granary to hide the ledger
```

Two proposals were considered and rejected before reaching the invariant below.

**Redact speech that no belief covers.** This is the strict reading, and it
destroys the block. Ordinary dialogue states no fact in the knowledge table, so
the rule redacts all of it. Measured over eight representative lines — questions,
denials, one incidental observation — coverage was 0/8. A scene in which every
line reads `[a fact another character has not shared is withheld]` is not a scene.

**Persist a "heard" tier** — a third collection beside `knowledge` and
`suspicions` for "I heard this and do not understand it". It would fix the
accounting gap, and it is the wrong shape. "I heard this" is not a property of a
character; it is a relation between a character and an event, and it is already
derivable from scene participation. Persisting it would add a row per line of
dialogue — growing faster than suspicion — to record information that a join
already answers.

That reframes the defect. Showing speech to a character who was in the room is
not a leak; it is accurate. The leak is showing it to a character who was not.

## Decision

Visibility of a speech line is gated by **presence**, never by belief:

1. The recent-events block is scoped to the **active scene**. A scene is a trust
   boundary; a line spoken in one scene does not appear in another's prompts.
   This filter is the mechanism that enforces isolation.
2. Within the active scene, the block renders the scene's events to the scene's
   viewers. Per-viewer presence is **not modelled yet**, so no within-scene line
   is withheld and there is no per-viewer marker. Presence becomes selective only
   when a character can be a participant of a scene and not be in the room.
3. The existing `knowledge_*` redaction continues to apply on top of both.
4. `_overhear` is unchanged. Suspicion remains reserved for a fact the speaker
   held and stated — a belief that changes behaviour, bounded by how many facts
   exist. Model-invented dialogue is ambient and creates no record.

## Consequences

The cross-scene exposure closes, and the "accounting gap" dissolves rather than
being filled: invented dialogue is ambient scene content, and ambient content
needs no belief record because presence justifies it.

Continuity across scene breaks now rests entirely on the belief collections. A
character who learned something in scene 1 still knows it in scene 5, because
`knowledge` is projected from the event log and is not scene-scoped. Only the
event *tail* narrows. This is the intended trade: durable facts travel as beliefs,
incidental texture does not.

The presence half of rule 2 is not implemented, and the ADR records that plainly
rather than claiming credit for it. `ActorBrief.present` is assigned `not dead`,
so today presence means "alive and a participant", which every viewer of the
block satisfies, and the scene filter alone produces the isolation. An earlier
draft carried a `character_spoke` redaction branch that could never fire for a
participant viewer; it was removed rather than left as a guard that reads as a
control but changes nothing. Presence gating becomes meaningful only when
per-turn physical presence is modelled — when a character can be a participant of
a scene and not be in the room. The rule is written down now so that day does not
reintroduce a belief-based gate.

Finally, `knowledge_leaks` in the evaluation harness could not see any of this.
It inspects facts in `view.withheld`, and invented content has no fact record, so
the metric reported 0 while a stranger read the secret. A presence invariant
replaces it as the signal for this class.

## Implementation notes

Scene attribution is a nullable `scene_id` column on `events` (migration
`0006_event_scene_visibility`), stamped by `repository.append_event` at every
scene-bound commit site in the pipeline. `append_event` takes `scene_id` as a
required keyword with no default, so a forgotten attribution raises `TypeError`
instead of silently producing a row that renders in every scene — attribution is
a decision, not a default. Operator- and timeline-scoped commits (canon
divergence, state correction, commitment lifecycle, forks) pass `scene_id=None`
explicitly: they change what is true for the whole timeline and are meant to
reach every scene. Rows committed before the migration are NULL and keep
rendering everywhere: no backfill is attempted, because the scene of a
historical event cannot be derived reliably and silently redacting history
would be worse than showing it.

The history digest obeys the same boundary as the recent-events block: it counts
only the active scene's events plus legacy NULL rows, so one scene's event
composition cannot describe itself into another scene's prompt.

The harness runs single-scene scenarios, so its `no_scene_leak` scorer passes
vacuously on today's fixtures. The primary signals are the deterministic tests in
`tests/test_knowledge_isolation.py` (replays the measured case from this ADR — a
stranger reading the scene-1 secret — and fails on the pre-change code) and
`tests/test_scene_attribution.py` (pins the operator-versus-scene distinction,
the digest's scene scope, and explicit attribution).
