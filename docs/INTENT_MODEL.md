# The Intent Model

## The one distinction everything rests on

**Intent is what the user wants. Plan is how it should happen.**

They are separate objects, they are stored separately, and they change for
different reasons. A user can edit a plan a hundred times without changing a
single word of what they asked for — and when they do change what they want, a
new intent is recorded, because the old one was a true record of a request they
have since replaced.

Collapsing the two is the most common way a narrative engine starts lying to its
user. If the plan overwrites the intent, then after enough improvisation there
is nothing left that says what was actually requested, and every later
explanation becomes a reconstruction. The user cannot check it, and neither can
the engine.

## Intent

`services/director/intent.py`. Transient: an `Intent` is a value, not a stored
row. It is produced per turn, and only the fields that are worth keeping
afterwards are persisted.

| Field | Meaning |
|---|---|
| `objective` | What the user wants to happen |
| `target_entities` | Who or what it concerns |
| `desired_outcome` | The end state they are asking for |
| `constraints` | Conditions that must hold |
| `exclusions` | Outcomes that must not occur |
| `tone` | Register and emotional temperature |
| `urgency` | Immediate, ongoing, or eventual |
| `canon_preference` | How strictly established fact binds |
| `user_control_level` | How much they want to hand over |
| `specification` | `explicit` / `partial` / `underspecified` |
| `consistency` | `consistent` / `ambiguous` / `contradictory` |
| `confidence` | How sure the interpreter is |
| `lane` | Which execution path the request needs |
| `horizon` | How far ahead it reaches |

### Specification

How completely the request determines what happens. This is what lets the
Director tell "the witness confesses" from "make something happen with the
witness" without guessing: the first is a decision, the second is a delegation.

`underspecified` is not a defect and is not a request for clarification by
default. It is a signal that the Director is *expected* to fill in the gaps —
subject to the envelope.

### Consistency

`contradictory` always requires a human decision, at every authority level
including `ai_directed`. Guessing which half of a contradiction the user meant is
precisely the silent reinterpretation the Director exists to prevent. An engine
that resolves contradictions on its own is not being helpful; it is deciding what
the user wanted without them.

`ambiguous` is different. It is a warning, not a wall: the Director may proceed,
and the plan it produces should be the more conservative reading.

This paragraph used to describe the intent without the code implementing it.
`requires_approval` gated on `consistency != "consistent"`, so `ambiguous` halted
the turn — and because `_REFERENT` marks *any* pronoun as ambiguous, ordinary
sentences were halted with it: "I follow her down the hall", "I look at it", "I
hand the letter to him" all produced an empty turn and an approval prompt at every
authority level, including the default `director_assisted`. A scene with two
characters could not be played in ordinary language.

The gate is now on `CONTRADICTORY` only. The classifier is unchanged, and
ambiguity still does its job: it discounts plan confidence and biases the
Performer toward the conservative reading. `test_a_pronoun_still_marks_the_intent_
ambiguous` in `tests/test_director_pipeline.py` guards against "fixing" this by
deleting the rule instead of the wall.

## Lanes

`services/director/router.py` assigns exactly one lane, deterministically,
synchronously, and totally — every string maps to some lane, and no input can
make the router fail. It never calls a provider, so routing cannot perturb
provider accounting or latency.

| Lane | What it is | What the Director does |
|---|---|---|
| `direct_actor` | The user is acting through their own character | Nothing. No plan. |
| `simple_world` | Time passage or ambience | Nothing. No plan. |
| `direction` | Shaping how a scene unfolds | Proposes a plan |
| `long_horizon` | An outcome wanted eventually | Records a commitment, performs nothing |

Lanes 1 and 2 exist so that the Director is *absent* where it has no job. A plan
row created for a turn the user performed themselves implies the Director
claimed authorship of something the user already did, which is the first step
toward a system that narrates rather than serves.

Routing is deliberately boring. It is a pure function with a table, pinned by
tests, because every routing surprise is a user-visible surprise.

## Plan

`services/director/plan.py`. Persisted as `DirectorPlan`. This is the *how*.

A plan carries an objective, a horizon, a set of beats, consequences
(required / likely / optional), assumptions, allowed actions, and the authority
mode it was built under. It also embeds the intent it was built from — a copy,
not a reference, so a later edit cannot reach back and rewrite the request.

### Beats

A beat is **guidance, not a script**. It has a status:

`pending` · `active` · `completed` · `skipped` · `invalidated`

The Performer may combine beats, skip them, reorder them, or adapt them. Two
properties make that safe:

- **Beats are independently invalidatable.** When a character dies, only the
  beats that depended on them are invalidated. A death resets the dependent
  beats, not the plan.
- **Invalidated is not failed.** A beat that can no longer happen became
  impossible; the engine did not break. Improvisation makes this routine, and
  treating it as an error would punish the user for changing their mind.

### Optional consequences are not instructions

An optional consequence is a possibility the plan raises, not one it commits to.
The envelope does not police them: proposing an *option* the user forbade is
still worth surfacing, but it does not make the plan invalid. A **beat**, by
contrast, is a proposal to do something, and is policed whether or not the
Performer is obliged to realise it.

## Authority

How much the Director may fill in. Resolved per project, overridable per scene.

| Mode | May infer | Requires approval |
|---|---|---|
| `strict` | Nothing beyond the request | Always |
| `collaborative` | Only what is directly implied | Always |
| `director_assisted` | Method, staging, reactions | Never for a well-formed turn |
| `ai_directed` | Substantial creative latitude | Never, except on contradiction or canon conflict |

**Authority governs how much the Director may fill in. It never governs whether
the Director may contradict.** No mode, including `ai_directed`, permits
violating a stated constraint or exclusion. This is the single most important
rule in the model, and it is why `ai_directed` is safe: the mode with the most
freedom still cannot do the one thing the user forbade.

`director_assisted` is the default because approval that fires on ordinary turns
trains users to approve reflexively. A gate that is always on is not a gate.

## The constraint envelope

`services/director/envelope.py`. Two independent gates stand between a proposed
plan and a performed turn.

**The envelope gate** asks one question: does this plan do something the user
explicitly excluded? It matches by *meaning*, not by the word the user used —
"do not alert anyone" catches a plan where a guard "warns", "shouts", or
"announces", because those are the same act. Matching alone is not enough either:
the plan has to actually *assert* the forbidden outcome, so "nobody is alerted"
does not trip an alert prohibition.

**The validation gate** asks whether the plan is performable at all: does it name
entities that exist, does it require a character who is dead on this timeline,
does it propose something the projection cannot accept.

A rejected plan commits **no** narrative state. That is what makes
`regenerate / edit / return to user` a real recovery rather than a repair of
half-applied work.

## Plan lifecycle

```
proposed ──▶ approved ──▶ executing ──▶ completed
    │            │             │
    └────────────┴─────────────┴──▶ cancelled / superseded
```

Transitions are validated, not assumed: a completed plan cannot be reopened, and
a proposed plan cannot be executed.

- **proposed** — awaiting a decision. Persisted so the decision can land later,
  but it is not narrative state: no events, no projection change.
- **approved** — the user accepted the plan.
- **executing** — a beat is being performed.
- **completed** — every beat reached a terminal state.

Editing a plan returns it to `proposed`. Approval is re-verified against the
constraint envelope at that moment, because a user correcting a plan is not
granting permission to run one the State Engine would reject.

## Canon conflict

`services/director/canon.py`. When a request contradicts established fact, the
Director surfaces the conflict rather than silently choosing. Three resolutions:
**follow** the canon, **override** it knowingly, or **branch** to a new timeline.
An unresolved conflict requires a decision at every authority level.

## Long horizon

A request for something that should happen *eventually* becomes a
`StoryCommitment`, using the existing commitment lifecycle rather than a parallel
system. The turn produces no generation.

This is the most user-visible correctness property in the model. "Eventually the
General will betray the Emperor" must never produce a betrayal in the same breath
as the request for it. The request becomes a promise the story is now obliged to
keep, and nothing else happens.

One request yields one retained intent, however many commitments it implies.
Otherwise the intent layer stops being an account of what was asked.

## What the Director is not

- It does not commit state. The State Engine remains the sole authority on what
  became true; the Performer proposes, the engine disposes.
- It does not plan beyond the commitment. No autonomous campaigns, no
  multi-chapter plotting, no quest systems.
- It does not store memory or restructure context. The existing memory and
  context architecture is unchanged.
- It does not ask for clarification by default. `underspecified` is a delegation,
  not a question.
