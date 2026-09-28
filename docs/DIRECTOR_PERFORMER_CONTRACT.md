# The Director–Performer Contract

## What the contract is

A block of text the Director renders into the prompt, telling the Performer what
situation it is performing. It carries the scene objective, the current beat, the
constraints that bind, the consequences to expect, and one brief per character.

It is **guidance, not script.** The Performer realises it; it does not recite it.
The line at the end of the block says so explicitly, because a Performer that
treats direction as instruction produces a screenplay, and the user asked for a
scene.

The contract is part of the required scene block, which is deliberate: it is
rendered where budget pressure cannot drop it. If the Director's direction could
be silently truncated away under token pressure, then long scenes would drift
off-plan with no signal, and the plan row would claim direction that never
reached the model.

## Why it exists

Without it, the Director would plan and then have no way to influence anything.
With it as instruction, the Performer becomes an executor of a plot. The contract
sits between those two failures.

## The block

```
DIRECTOR DIRECTION (guidance, not instruction to obey literally)
Scene objective: <objective from staging or the plan>
Current beat: <the active beat, or "improvise from the situation">
Desired narrative direction: <direction>
Required consequences: <...>
Likely consequences: <...>
Optional consequences (do not force): <...>
Constraints that must hold: <...>
Excluded by the user: <...>
Characters decide their own reactions. Do not make everyone respond identically.
  - <Name>: goal=<derived goal>; knows=<...>; suspects=<...>; injuries=<...>; carries=<...>
  ...
Combine, skip, or adapt the beat if the scene has moved on. Only assert state
you can support; the engine validates everything.
```

Rendered by `render_performer_contract` in `services/director/director.py`.

## Character briefs

Each character gets a brief: a short, derived read of what they are plausibly
after, what they hold, what they are hurt by, and who they are tied to.

**Briefs are derived, never stored.** Every brief is recomputed from the state
projection on each turn. A stored brief would go stale the moment the character
did something else, and the Performer would be acting on a stale motive.

**Knowledge is viewer-scoped.** The acting character sees its own certainties
and suspicions. Every other character's beliefs are replaced with a redaction
marker. This is not a nicety: the knowledge isolation layer guards the prompt
it builds, and a brief is text the Director *adds* to that prompt. The guard
cannot see inside what the Director appends, so the Director has to not append
it. An earlier version of this rendered every character's knowledge and leaked a
private fact straight into another character's prompt.

This is why the brief for a non-viewer character is deliberately *weaker* than
the viewer's. Without the character's own knowledge, the only defensible read of
their intent is what an observer can see: what they just did, what they carry,
what injuries they have. A Performer handed that has to invent a plausible
reasoning, which is exactly the point — it reasons from what the character could
actually know.

**Goals are weak on purpose.** The goal is read from the projection, not from a
stored motive: a character with a recent action is pursuing it, a character
holding something has a reason to keep holding it, a character who suspects
something is chasing it. Anything stronger would be the Director dictating
interiority, and interiority is the Performer character-agency layer's job.

## Agency

The contract establishes the situation; **characters decide their own
reactions.** The line forbidding uniform responses is in the block on purpose,
because a Performer that has been handed a list of characters and a shared mood
will default to making all of them react the same way, and that uniformity is
the single most artificial thing a scene can do.

The brief supplies inputs; the Performer supplies the reaction. Briefs name
constraints on the reaction, not the reaction itself.

## What the contract must never contain

- **Chain-of-thought or deliberation.** The trace records decisions, not reasoning
  transcripts. Free-text deliberation in the trace becomes the de facto
  explanation the user reads instead of the structured verdict.
- **Another character's private knowledge.** See above.
- **Commands to obey literally.** The header and footer both say the block is
  guidance.
- **A forced sequence.** Beats are guidance; the Performer may combine, skip,
  reorder, or adapt them.

## Re-validation before performance

Approval is not a guarantee of executability. Between proposing a plan and
performing it, the scene can change: a character can die, a location can become
unreachable. So the constraint envelope is re-checked against current state at
performance time rather than trusted from proposal time.

A plan that no longer validates is blocked with a reason, and stays in a state the
user can edit or cancel. It is not performed partially.

## Traces

`GenerationTrace.director` carries the structured decision: lane, plan status,
validation verdict, authority mode, beats, constraints, and timings. That is
enough to answer "why did this happen" without a transcript.

The trace is additive. Phases that predate the Director leave it empty rather
than failing, so an old generation row still reads correctly.
