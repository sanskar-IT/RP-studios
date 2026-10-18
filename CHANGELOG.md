# Changelog

Notable changes to AI Narrative Studio, newest first. The README keeps a short
digest; this file keeps the full record.

## Local models can reach the engine

`NARRATIVE_PROVIDER=ollama` did nothing except look correct. The provider name
set recognised `openai`, `openai_compatible`, `local`, and `generic`, and
anything outside it fell back to the heuristic provider without raising. So the
obvious spelling of the obvious provider gave a studio that started cleanly,
reported `adapter: heuristic`, and never contacted a model. There was no error to
notice, only a capability report to read carefully. `ollama` and
`openai-compatible` are now recognised, the set lives in one place instead of
being duplicated across two call sites, and `tests/test_reliability_routes.py`
pins every spelling plus the near-miss ones that must still fall back.

Then two things that had to be fixed before any local model could work.

**Two leading system messages.** `structured()` prepended the schema as its own
system message while the pipeline already sent `SYSTEM_RULES` as one, so every
structured call went out as `[system, system, user]`. Ollama's llama.cpp chat
templates accept at most one leading system message and answer with HTTP 500 and
`Jinja Exception: System message must be at the beginning.` This was not a weak
model: no structured call could succeed against a stock local server at any model
size, and no configuration setting could reach it. The instruction is now folded
into the caller's leading system messages, so one goes on the wire with the schema
still first. Verified against Ollama 0.34.0, where the same request went from 500
to a well-formed staging object.

**A live smoke test that reported healthy endpoints as broken.** The opt-in
provider smoke test asked for a one-word answer with `max_tokens=8`. Any
reasoning-capable model spends that budget on thinking before it emits a single
answer token, returns an empty completion, and the test fails against a server
that was answering perfectly well. Raised to 512.

Two configuration gaps came with this. `NARRATIVE_PROVIDER_TIMEOUT_SECONDS` is
new, because the previous 120 second default is short for CPU-only inference and
had no way to be expressed at all. And `docker-compose.yml` forwarded the four
original provider variables but not `CONTEXT_WINDOW`, `MAX_OUTPUT_TOKENS`, or
`FEATURES`, so a Compose deployment silently kept the conservative 8192-token
window while a local server defaults to 4096.

`tests/test_local_smoke.py` is the new dogfooding scenario: three characters with
separate private knowledge, one of them user-possessed, one turn each against a
live local model. Its assertions cover seeding, possession, and prompt assembly.
Everything that depends on the model is reported rather than asserted, and a run
in which no turn committed fails loudly instead of passing quietly, because a
green tick there would mean nothing was tested.

## `.env.example` now loads as copied

`NARRATIVE_PROVIDER_CONTEXT_WINDOW` and `NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS`
are declared as empty strings in the template, because they are optional. Pydantic
read those as invalid integers and raised at import, so the first documented
setup step, copying `.env.example` to `.env`, made the API fail to start before
anything else ran. Both fields now treat an empty or whitespace-only value as
"not declared" and fall back to `0`, which is what the documented default
already was. A genuinely non-numeric value is still an error, since that is a
typo rather than an unset field. `tests/test_config.py` covers all four cases,
and the env-file load is what the first test reproduces.

## Empty completions now fail with the real cause

A thinking model that spends its whole budget on reasoning returns `content:
null`, with the reply sitting in a `reasoning` field the adapter does not read.
The adapter stringified that null into the literal word "None", so the turn
failed later at JSON parsing with a message about structured output that
pointed away from the cause. `generate()` now raises `ProviderError: Provider
returned an empty completion` on a null, empty, or blank content, and the error
names the fix: raise `max_tokens` or disable thinking. The old test asserted
the stringification; it now asserts the error across all three shapes.
`docs/PROVIDERS.md` records the open `reasoning` field work this does not fix.

## Fenced structured replies now parse

The first live run against a local 4B model failed at staging: the model
returned a complete, correct staging object wrapped in ```` ```json ```` fences
while told not to, and the adapter handed it to `json.loads` verbatim.
`structured()` now strips one fence pair and surrounding whitespace before
parsing. A correct object in fences behaves exactly like the same object bare;
commentary-embedded JSON, empty replies, and truncations still fail as before,
and two scenario tests that pinned the old rejection were rewritten to pin the
new contract instead. The same run then staged cleanly, committed one of three
turns, correctly rejected the other two on malformed `character_moved` claims,
and held knowledge isolation throughout. `response_format` and brace salvage remain
open (see `docs/PROVIDERS.md`).

## Twelve local runs classified the model failures, then fixed the prompt

The multi-character scenario was run twelve times against the on-machine 4B
model with settings frozen, capturing raw replies, parsed payloads, and
validation errors per call. Turn acceptance went 4 of 12, then 6 of 9 after the
prompt fix, then 9 of 9, 5 of 6, and 3 of 3 once retries were evaluated.
Staging succeeded 12 of 12. Isolation held in all 12 runs. Three failure
classes appeared, and each got a different response. Missing `location_id` on
`character_moved` was a prompt-validator mismatch: the schema declared the
property but never said any type requires it, so the schema now states each
type's required fields and the validator is untouched. Prose returned instead
of JSON and invented event types are model variance: recorded as model
limitations, recovered by retry where possible. Truncated mid-object replies
are capacity failures no retry can fix: all three attempts truncated, so the
turn fails loudly rather than committing a fragment.

## Failed Performer calls are retried twice with their errors fed back

A turn gets up to three Performer attempts: the first call plus two retries.
Each retry appends the previous validation errors to the prompt and the whole
new response is revalidated; a repaired fragment is never merged into a
rejected payload. Each attempt leaves its own Generation row, rejected or
failed, so retries are auditable. The retry covers parse failures, claim
rejections, and performer-contract violations; anything else propagates
immediately. Recovery tests that pinned fail-fast now pin three-attempt
exhaustion instead. Measured live: one rejected turn recovered to completed on
retry; one turn failed all three attempts on truncated replies and surfaced the
last error unchanged.

## The documented provider variable now reaches the API

Setting `NARRATIVE_PROVIDER=ollama` did nothing: the settings field is
`provider_name`, which pydantic-settings reads from `NARRATIVE_PROVIDER_NAME`,
a name nothing in the repo references. Every documented setup therefore ran
the heuristic provider while looking configured. `provider_name` now accepts
both spellings, with the full name winning when both are set, and three config
tests pin the behaviour. Found during alpha launch when `/api/providers/capabilities`
reported `heuristic` against a fully configured environment.

## The recent-events block is scoped to the active scene

ADR 0004 is now implemented. Events carry the scene that produced them
(migration `0006_event_scene_visibility`), and `_recent_events` renders only
the active scene plus legacy rows from before attribution existed, which keep
showing everywhere rather than being silently redacted. Within the scene, a
speech line is visible to whoever was present or spoke it; beliefs are never
consulted. `_overhear` is unchanged, including the paraphrase gap, which stays
an open limitation above. The harness gains a `no_scene_leak` scorer, but its
scenarios run single scenes, so the deterministic cross-scene test in
`tests/test_knowledge_isolation.py` is the primary signal: it replays the
measured stranger-reading-the-secret case and fails on the pre-change code.

## Review fixes to the P0 work

The P0 fixes were reviewed adversarially after landing and three defects were
found in them — two of them in the security controls themselves. All three are
fixed here; the details are below because the failure modes are the useful part.

**The decompression ceiling rejected valid cards.** `_bounded_inflate` fed zlib
slices of the original buffer but never drained `unconsumed_tail`. When
`max_length` halts decompression, zlib parks the unused input there and stops;
it has to be fed back before the stream advances. Not doing so meant `eof` was
never reached, and a 1 MiB payload compressing to 1 KiB was refused as
"truncated". Repetitive data spans the most steps, which is the shape of a real
character description — so the DoS defence was a card rejecter. Now the tail is
fed back, with round-trip coverage at 1, 3, and 4 MB, exactly at the ceiling, one
byte over, and empty.

**Possession could be bypassed through `relationship_changed`.** The check read
`character_id` and `entity_id`, but the fields the engine validates are
`character_id`, `source_character_id`, and `target_character_id` — and
`relationship_changed` names two characters precisely that way. The model could
therefore rewrite the user's relationships with no check performed at all. Both
positions are now covered.

**Agency grounding was satisfiable for free.** Requiring only that one action
word appear in the user's input is not an enforcement mechanism, it is a
suggestion the model can meet for free by appending irreversible acts to a
legitimate clause: "opens the drawer, retrieves the ledger, burns it" matched
"drawer" and passed. The budget is now on words the input does *not* explain, so
adding an unasked act costs budget whether or not a grounded verb is present.

**Speech also produced a phantom goal.** `last_action` feeds `_goal_for` in the
Director, which turns any non-empty value into `"continue: <action>"` and passes
it to the Performer as beat direction. Storing the placeholder `"spoke"`
therefore produced `"continue: spoke"` — a real-looking instruction describing
nothing, displacing a usable fallback. Speech now stores nothing; `last_action_type`
still records that something happened.

## P0 dogfood blockers

Seven defects found by external validation, each reproduced with a failing
regression test before being fixed.

**Event vocabulary.** The Performer schema advertised `item_obtained`,
`item_destroyed`, `character_injured`, and `scene_ended` — none of which the
engine accepts. Because claim validation is all-or-nothing, a schema-compliant
model producing one of those names lost the entire turn, including the prose:
picking up a locket could not be committed. Both schemas now derive their enum
from a single `MODEL_WRITABLE_EVENT_TYPES` allowlist of the 12 proposable types,
which also stops advertising the 18 engine-only ones — including
`world_fact_created`, a private belief promoted to canon for every viewer. The
three names already in the wild are normalised on read, so a model carrying a
cached prompt degrades instead of returning a 422.

**Approval gate.** `requires_approval` gated on any consistency other than
`consistent`, so an ordinary pronoun — `_REFERENT` marks "I follow her down the
hall" ambiguous — returned an empty turn and an approval prompt at *every*
authority level, including the default `director_assisted`. A scene with two
characters could not be played with ordinary sentences. Only
`CONTRADICTORY` now gates; ambiguity still discounts plan confidence. This is
what `docs/INTENT_MODEL.md` already promised.

**Unbounded zlib.** PNG text chunks were inflated with `zlib.decompress`, which
has no size ceiling: roughly 16 KiB of upload reached 52 MiB of memory before
the importer's own limit, and that limit only bounds the *uploaded* file. Replaced
with a chunked inflate that abandons a bomb at a 4 MiB ceiling on the
*decompressed* size. Truncated compressed data is refused too — `decompressobj`
otherwise decodes whatever survived, which for a card payload can still be valid
JSON, so a half-written card imported as a real one.

**`last_action` persistence.** Raw speech was written into character state, then
re-projected into every later snapshot and re-inserted into every subsequent
prompt. A single secret spoken quietly to one person became a permanent,
undetectable leak into every other character's context on every following turn.
Speech events now store nothing at all. Observable performed actions are kept,
because continuity depends on them and dropping them buys no isolation.

**Possession enforcement.** `check_user_agency` decided whether the Performer
had taken over the user's character by reading `turn.kind` — a field the model
fills in. Relabelling a decision as `reaction` bypassed the check and committed
an invented decision as an authoritative `user_action` for a character the user
owns. Authority is now never derived from `kind`; enforcement is grounding in the
user's own input, the one statement of intent the model did not author. An action
passes when at most two of its distinctive words go unexplained by that input,
which absorbs paraphrase ("draws the drawer open") and short connective clauses
while a real takeover leaves four or five. The check also covers
`proposed_events` and `state_claims` across every field through which a claim can
name a character.

**Provider warning.** Nothing told the user that no model was configured, so a
fresh install looked like a working app that was silently echoing input back. The
studio now reads the existing `adapter` field and says so.

**Continue with no input.** The client refused to send an empty command, so the
one control whose purpose is to advance the scene without writing anything did
nothing. The engine already handled it correctly; only the guard was wrong.
