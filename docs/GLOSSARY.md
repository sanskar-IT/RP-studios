# Glossary

Terms whose meaning is load-bearing, and which are easy to read two ways. Where a
term has a contested meaning, the reading used here is stated rather than assumed.

## Belief and exposure

**Knowledge** — A fact a character holds as true. Certain, because it was
acquired from a source the character trusts or verified. Stored per character;
projected from the event log; never scene-scoped.

**Suspicion** — A fact a character believes *might* be true and has not verified.
A distinct collection from knowledge, deliberately. Promoted by
`knowledge_acquired`; never created by `knowledge_suspected`. Overheard
statements of facts land here, because hearing something said is not the same as
knowing it is true.

Suspicion is not a general "uncertainty" bucket. Treating it as one is what makes
it grow without bound — see [Heard](#heard).

**Withheld** — A fact some character holds and a given viewer does not, rendered
to that viewer as a marker rather than as the fact. Withholding is scoped to the
belief's owner.

**Overheard** — The state reached when a character hears a fact they do not hold
and does not understand it. Recorded as suspicion, and only when the speaker
actually held the fact and stated its distinctive payload.

## Hearing and presence

**Heard** — A relation between a character and an event: the character was
present when it happened. Not a belief, and not stored.

This distinction is the subject of [ADR 0004](ADR/0004-presence-gated-speech-visibility.md).
"I heard this" describes a character's relationship to a moment, not a property
of their beliefs. Storing it would mean a row per line of dialogue to record
something scene participation already answers.

**Presence** — Whether a character was in the room when something happened. This
is currently assigned `not dead` (`ActorBrief.present`), so presence means "alive
and a participant" and every viewer of the recent-events block satisfies it. No
within-scene line is withheld today; the rule is written down before it becomes
selective.

**Ambient content** — Something that happened in the room and is not anyone's
knowledge: dialogue that states no fact, incidental observation, a question.
Visible to everyone present, unrecorded, because presence justifies it. The
model's invented dialogue is ambient by definition — no character holds it, so
there is nothing to withhold and nothing to record.

**Scene boundary as trust boundary** — What happened in one scene is not shown in
another scene's prompts. Durable facts still cross, via belief collections.

## Authority and action

**Proposable event** — An event type the Performer may propose, from
`MODEL_WRITABLE_EVENT_TYPES` (12 types). Distinct from the 30 types the engine
applies: the other 18 — scene lifecycle, possession, forks, `world_fact_created`
— are engine-originated and are not advertised to the model.

**Engine-originated** — An event only the engine or an operator may create. The
Performer narrates the room; it does not author the world.

**Authority** — What may be decided about a character. Never derived from
model-supplied metadata: `ActorTurn.kind` is filled in by the model, so a check
reading it is a request. Derived instead from grounding — whether the user's own
input accounts for the action.

**Grounding** — Whether an action's distinctive words are explained by the user's
`user_input`. Measured on the words the input does *not* explain, with a budget
of two. Not overlap: overlap is satisfiable by prefixing a takeover with
something the user did ask for.

**Connective action** — Texture the Performer supplies around a user's action:
"steadies the drawer", "hesitates". Distinct from a decision, which commits the
character to a course of action. Grounding is deliberately permissive enough to
allow these.

## State and record

**`last_action`** — The most recent action for a character, rendered into their
brief. Stores the content of a visible action; stores *nothing* for speech, and
`last_action_type` records that something happened.

Empty rather than a placeholder, because the field is read as intent: any
non-empty value becomes `"continue: <action>"` as a goal and reaches the Performer
as beat direction. `"continue: spoke"` describes nothing while looking like an
instruction.

**Speech is not an observable action.** Anyone in the room can witness a visible
act, so `character_performed_action` keeps its text. Nobody outside the room can
witness speech, so speech content is not persisted into character state at all.
The event log still holds the utterance — it is the record of what happened.

**Event log** — Append-only and permanent. Holds what was said, including
content that is not projected into any character's state.
