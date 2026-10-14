# ADR 0004: Gate Speech Visibility by Presence, Not by Belief

## Status

Proposed. Supersedes nothing. The behaviour it replaces is the in-window
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
2. Within the active scene, a line is visible to a viewer if the viewer was
   present when it happened, or is its speaker. Beliefs are never consulted for
   visibility.
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

The presence half of rule 2 is currently vacuous, and the ADR records that
plainly rather than claiming credit for it. `ActorBrief.present` is assigned
`not dead`, so today presence means "alive and a participant", which every
viewer of the block satisfies. Presence gating becomes meaningful only when
per-turn physical presence is modelled — when a character can be a participant of
a scene and not be in the room. The rule is written down now so that day does not
reintroduce a belief-based gate.

Finally, `knowledge_leaks` in the evaluation harness could not see any of this.
It inspects facts in `view.withheld`, and invented content has no fact record, so
the metric reported 0 while a stranger read the secret. A presence invariant
replaces it as the signal for this class.
