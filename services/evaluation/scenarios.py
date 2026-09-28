"""The five canonical evaluation scenarios.

Each scenario is data, not code. That keeps the harness honest: the same runner
drives all five, so a difference in the report is a difference in the engine's
behaviour rather than in how the scenario was written.

Every scenario declares its characters, its world setup, the invariants it must
hold, and a deterministic recipe for generating turns of any length. The recipe
is a pure function of the turn index, so a 10-turn and a 100-turn run of the
same scenario share a prefix and a regression at turn 90 is reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Locations are declared so the unknown-location detector has something to check
# against, rather than silently passing because the project defines nothing.


@dataclass(frozen=True)
class CharacterSpec:
    key: str
    name: str
    definition: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnPlan:
    """One scripted turn: what the model is scripted to return."""

    user_input: str
    actor: str
    prose: str
    events: tuple[dict[str, Any], ...] = ()
    knowledge_changes: tuple[dict[str, Any], ...] = ()
    relationship_changes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Invariant:
    """One checkable expectation, evaluated after every turn or at the end."""

    key: str
    description: str
    kind: str


@dataclass(frozen=True)
class SuspicionProbe:
    """One belief that must stay a suspicion and never become a certainty."""

    character_key: str
    fact: str
    from_turn: int = 0


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    summary: str
    premise: str
    objective: str
    location: str
    characters: tuple[CharacterSpec, ...]
    plan: Callable[[int, dict[str, str]], TurnPlan]
    lore: tuple[dict[str, Any], ...] = ()
    sources: tuple[dict[str, str], ...] = ()
    commitments: tuple[str, ...] = ()
    invariants: tuple[Invariant, ...] = ()
    suspicion_probes: tuple[SuspicionProbe, ...] = ()

    def invariant_keys(self) -> tuple[str, ...]:
        return tuple(invariant.key for invariant in self.invariants)

    def character_ids(self) -> dict[str, str]:
        return {spec.key: spec.name for spec in self.characters}


def _validate() -> None:
    for scenario in ALL_SCENARIOS:
        unknown = {invariant.key for invariant in scenario.invariants} - INVARIANT_KINDS
        if unknown:
            raise ValueError(
                f"Scenario {scenario.key} declares unknown invariant kinds: {sorted(unknown)}"
            )


# Invariant kinds. Kept as constants because they are the vocabulary of the
# machine-readable report.
STATE_CONSISTENT = "state_consistent"
NO_KNOWLEDGE_LEAK = "no_knowledge_leak"
MEMORY_RETRIEVAL = "memory_retrieval"
COMMITMENT_PERSISTENCE = "commitment_persistence"
BRANCH_CORRECTNESS = "branch_correctness"
LORE_ACTIVATION = "lore_activation"
CONTEXT_BOUNDED = "context_bounded"
EVENT_VALIDITY = "event_validity"
NO_TRANSCRIPT_AS_MEMORY = "no_transcript_as_memory"
SUSPICION_NOT_CERTAINTY = "suspicion_not_certainty"

# Every invariant kind the harness knows how to evaluate. A scenario may not
# declare a kind that is not in this set, so a typo fails loudly rather than
# silently becoming an invariant nobody checks.
INVARIANT_KINDS = frozenset(
    {
        STATE_CONSISTENT,
        NO_KNOWLEDGE_LEAK,
        MEMORY_RETRIEVAL,
        COMMITMENT_PERSISTENCE,
        BRANCH_CORRECTNESS,
        LORE_ACTIVATION,
        CONTEXT_BOUNDED,
        EVENT_VALIDITY,
        NO_TRANSCRIPT_AS_MEMORY,
        SUSPICION_NOT_CERTAINTY,
    }
)

# The worst acceptable lore activation per turn. A single weak keyword matching
# dozens of entries is a recall failure, not a success.
LORE_ACTIVATION_TARGET = 12


def _mystery_plan(turn: int, ids: dict[str, str]) -> TurnPlan:
    """A long investigation with disclosures spaced far apart.

    The disclosures are deliberately tens of turns apart so retrieval has to
    bridge a real distance rather than benefiting from recency.
    """
    detective, butler, alice, witness = (
        ids["detective"],
        ids["butler"],
        ids["alice"],
        ids["witness"],
    )
    if turn == 3:
        return TurnPlan(
            user_input="The detective questions Alice in the library.",
            actor=detective,
            prose="The detective finds Alice in the library and asks how she came to be there.",
            events=({"event_type": "knowledge_acquired", "character_id": detective, "fact": "Alice was in the library tonight"},),
        )
    if turn == 14:
        return TurnPlan(
            user_input="Alice hesitates, then admits she has a brother.",
            actor=alice,
            prose="Alice admits, reluctantly, that she has a brother nobody in the house has mentioned.",
            events=(
                {"event_type": "knowledge_acquired", "character_id": detective, "fact": "Alice has a brother"},
            ),
        )
    if turn == 20:
        return TurnPlan(
            user_input="The detective begins to doubt the obvious answer.",
            actor=detective,
            prose=(
                "The detective looks at the butler's twenty spotless years and begins to wonder "
                "whether the obvious answer is even possible."
            ),
            events=(
                {
                    "event_type": "knowledge_suspected",
                    "character_id": detective,
                    "fact": "the butler may be innocent after all",
                },
            ),
        )
    if turn == 31:
        return TurnPlan(
            user_input="The detective asks after the brother.",
            actor=detective,
            prose="A clerk confirms on paper that Alice's brother disappeared last spring.",
            events=(
                {
                    "event_type": "knowledge_acquired",
                    "character_id": detective,
                    "fact": "Alice's brother disappeared last spring",
                },
            ),
        )
    if turn == 57:
        return TurnPlan(
            user_input="Ask the detective what they remember about Alice's family.",
            actor=detective,
            prose=(
                "The detective answers without hesitation: Alice has a brother, and he disappeared "
                "last spring."
            ),
        )
    if turn % 17 == 0:
        return TurnPlan(
            user_input="The detective keeps working the room.",
            actor=detective,
            prose=f"Turn {turn}: the detective circles the room and notes who avoids the east wing.",
            events=({"event_type": "character_spoke", "text": f"Turn {turn}: the detective keeps working the room."},),
        )
    if turn % 7 == 0 and turn > 0:
        return TurnPlan(
            user_input="The butler watches from the doorway.",
            actor=butler,
            prose=f"Turn {turn}: the butler watches from the doorway and says nothing useful.",
        )
    if turn % 5 == 0 and turn > 0:
        return TurnPlan(
            user_input="The witness drifts toward the hall.",
            actor=witness,
            prose=f"Turn {turn}: the witness drifts toward the hall, looking back once.",
            events=({"event_type": "character_moved", "character_id": witness, "location_id": "hall"},),
        )
    return TurnPlan(
        user_input="The detective examines the desk.",
        actor=detective,
        prose=f"Turn {turn}: the detective examines the desk and finds nothing new.",
        events=({"event_type": "character_spoke", "text": f"Turn {turn}: the detective examines the desk."},),
    )


def _intrigue_plan(turn: int, ids: dict[str, str]) -> TurnPlan:
    general, emperor, chancellor, spy = (
        ids["general"],
        ids["emperor"],
        ids["chancellor"],
        ids["spy"],
    )
    if turn == 5:
        return TurnPlan(
            user_input="The chancellor flatters the General in front of the Emperor.",
            actor=chancellor,
            prose="The chancellor praises the General's loyalty where the Emperor can hear it.",
            events=(
                {
                    "event_type": "relationship_changed",
                    "source_character_id": chancellor,
                    "target_character_id": general,
                    "relationship_type": "flattering",
                    "strength": 0.4,
                },
            ),
        )
    if turn == 12:
        return TurnPlan(
            user_input="The spy passes a sealed note to the General.",
            actor=spy,
            prose="The spy presses a sealed note into the General's hand without a word.",
            events=(
                {"event_type": "item_acquired", "character_id": general, "item": "sealed note"},
                {
                    "event_type": "knowledge_acquired",
                    "character_id": general,
                    "fact": "the sealed note orders the eastern garrison to move",
                },
            ),
        )
    if turn == 40:
        return TurnPlan(
            user_input="The Emperor asks the General directly about the garrison.",
            actor=emperor,
            prose="The Emperor asks why the eastern garrison moved without orders.",
            events=(
                {
                    "event_type": "knowledge_suspected",
                    "character_id": general,
                    "fact": "the Emperor suspects the General of disobedience",
                },
            ),
        )
    if turn % 11 == 0 and turn > 0:
        return TurnPlan(
            user_input="The court moves through the hall.",
            actor=general,
            prose=f"Turn {turn}: the General holds court and watches the door.",
        )
    return TurnPlan(
        user_input="The court murmurs.",
        actor=chancellor,
        prose=f"Turn {turn}: the chancellor rearranges the room's alliances without a word.",
    )


def _combat_plan(turn: int, ids: dict[str, str]) -> TurnPlan:
    captain, lieutenant, raider = ids["captain"], ids["lieutenant"], ids["raider"]
    banner = "the standard"
    if turn == 4:
        return TurnPlan(
            user_input="The raider wounds the lieutenant.",
            actor=raider,
            prose="The raider's blade opens the lieutenant's forearm.",
            events=(
                {"event_type": "injury_added", "character_id": lieutenant, "injury": "forearm cut"},
                {"event_type": "item_removed", "character_id": lieutenant, "item": "shield"},
            ),
        )
    if turn == 19:
        return TurnPlan(
            user_input="The captain takes the banner.",
            actor=captain,
            prose="The captain tears the banner free and holds it overhead.",
            events=({"event_type": "item_acquired", "character_id": captain, "item": banner},),
        )
    if turn == 33:
        return TurnPlan(
            user_input="The lieutenant is struck down.",
            actor=raider,
            prose="The lieutenant falls and does not rise.",
            events=({"event_type": "character_died", "character_id": lieutenant},),
        )
    if turn % 9 == 0 and turn > 0:
        return TurnPlan(
            user_input="The lines shift across the field.",
            actor=captain,
            prose=f"Turn {turn}: the captain shifts the line a step to the left.",
        )
    return TurnPlan(
        user_input="Steel meets steel.",
        actor=raider,
        prose=f"Turn {turn}: the raider presses the advance and the line bends.",
        events=({"event_type": "character_performed_action", "text": f"Turn {turn}: the raider presses the advance."},),
    )


def _romance_plan(turn: int, ids: dict[str, str]) -> TurnPlan:
    isaac, elena, friend_rival = ids["isaac"], ids["elena"], ids["rival"]
    if turn == 6:
        return TurnPlan(
            user_input="Elena admits she has been avoiding Isaac.",
            actor=elena,
            prose="Elena admits she has been avoiding Isaac because she cannot say the next part.",
            events=(
                {
                    "event_type": "knowledge_acquired",
                    "character_id": isaac,
                    "fact": "Elena has been avoiding Isaac on purpose",
                },
            ),
        )
    if turn == 22:
        return TurnPlan(
            user_input="Isaac promises to wait.",
            actor=isaac,
            prose="Isaac promises to wait, however long it takes, and means it.",
            events=(
                {
                    "event_type": "knowledge_acquired",
                    "character_id": elena,
                    "fact": "Isaac promised to wait for Elena",
                },
                {
                    "event_type": "relationship_changed",
                    "source_character_id": isaac,
                    "target_character_id": elena,
                    "relationship_type": "devoted",
                    "strength": 0.7,
                },
            ),
        )
    if turn == 45:
        return TurnPlan(
            user_input="The rival makes Elena choose.",
            actor=friend_rival,
            prose="The rival gives Elena one evening to decide, and leaves.",
            events=(
                {
                    "event_type": "relationship_changed",
                    "source_character_id": friend_rival,
                    "target_character_id": elena,
                    "relationship_type": "rival",
                    "strength": 0.3,
                },
            ),
        )
    if turn % 8 == 0 and turn > 0:
        return TurnPlan(
            user_input="Rain on the window.",
            actor=elena,
            prose=f"Turn {turn}: rain on the window, and neither of them speaks first.",
        )
    return TurnPlan(
        user_input="The evening moves slowly.",
        actor=isaac,
        prose=f"Turn {turn}: Isaac finds something small to do with his hands.",
    )


def _canon_plan(turn: int, ids: dict[str, str]) -> TurnPlan:
    captain, chronicler, shade = ids["captain"], ids["chronicler"], ids["shade"]
    if turn == 2:
        return TurnPlan(
            user_input="The chronicler reads the founding decree aloud.",
            actor=chronicler,
            prose="The chronicler reads the founding decree: the harbour is sealed to outsiders.",
            events=(
                {
                    "event_type": "knowledge_acquired",
                    "character_id": captain,
                    "fact": "imported canon states the harbour is sealed to outsiders",
                },
            ),
        )
    if turn == 25:
        return TurnPlan(
            user_input="The captain ignores the decree and opens the gate.",
            actor=captain,
            prose="The captain opens the sealed gate anyway, and the canon is simply no longer true.",
            events=(
                {
                    "event_type": "world_fact_created",
                    "fact_id": "harbour-open",
                    "text": "the harbour gate stands open despite the founding decree",
                },
            ),
        )
    if turn == 50:
        return TurnPlan(
            user_input="The shade asks who gave the order.",
            actor=shade,
            prose="The shade asks who gave the order, and the harbour keeps its new answer.",
            events=(
                {
                    "event_type": "world_fact_created",
                    "fact_id": "gate-opener",
                    "text": "the captain personally opened the harbour gate",
                },
            ),
        )
    if turn % 10 == 0 and turn > 0:
        return TurnPlan(
            user_input="The dock keeps working.",
            actor=chronicler,
            prose=f"Turn {turn}: the chronicler records that the decree is no longer observed.",
        )
    return TurnPlan(
        user_input="Work continues on the pier.",
        actor=captain,
        prose=f"Turn {turn}: the captain walks the pier and the crew keeps working.",
    )


MYSTERY = Scenario(
    key="mystery",
    title="Mystery: the sealed library",
    summary="Hidden information, character knowledge, investigation, and long-distance memory.",
    premise="A murder in a sealed library where the obvious suspect did not do it.",
    objective="Establish who killed the victim without the detective knowing more than they have earned.",
    location="library",
    characters=(
        CharacterSpec("detective", "Detective", {"description": "A patient investigator who trusts evidence."}),
        CharacterSpec("butler", "Butler", {"description": "The obvious suspect, and the wrong one."}),
        CharacterSpec("alice", "Alice", {"description": "A witness with a secret she has not told anyone."}),
        CharacterSpec("witness", "Witness", {"description": "A nervous onlooker who sees too much."}),
    ),
    lore=(
        {"name": "East wing", "keys": ["library", "east wing"], "content": "The east wing of the library is sealed by a lock that was oiled recently."},
        {"name": "Study desk", "keys": ["library", "desk"], "content": "The study desk has a false bottom that once hid a letter."},
        {"name": "Harbour", "keys": ["harbour", "ship"], "content": "The harbour is four days away and irrelevant to the library."},
        {"name": "Servants", "keys": ["library", "butler"], "content": "The butler has served the house for twenty years and is never careless."},
    ),
    commitments=("The detective will name the real killer before the house is sealed.",),
    plan=_mystery_plan,
    invariants=(
        Invariant("no_knowledge_leak", "No character states a fact they have not learned", NO_KNOWLEDGE_LEAK),
        Invariant("suspicion_not_certainty", "Suspicion stays separate from certainty", SUSPICION_NOT_CERTAINTY),
        Invariant("memory_retrieval", "A fact learned at turn 14 is recalled at turn 57", MEMORY_RETRIEVAL),
        Invariant("lore_activation", "Only the library entries activate, not the harbour", LORE_ACTIVATION),
        Invariant("no_transcript_as_memory", "Memory rows do not grow with the transcript", NO_TRANSCRIPT_AS_MEMORY),
        Invariant("context_bounded", "Every prompt stays inside the provider budget", CONTEXT_BOUNDED),
    ),
    suspicion_probes=(
        SuspicionProbe("detective", "the butler may be innocent after all", from_turn=20),
    ),
)

INTRIGUE = Scenario(
    key="intrigue",
    title="Political intrigue: the eastern garrison",
    summary="Commitments, relationships, deception, and multiple actors with competing goals.",
    premise="A court where the General has been ordered to move a garrison he was told never to move.",
    objective="The General will eventually betray the Emperor without betraying him this turn.",
    location="court",
    characters=(
        CharacterSpec("general", "General", {"description": "Loyal by oath, and holding a sealed order."}),
        CharacterSpec("emperor", "Emperor", {"description": "Suspicious, patient, and dangerous."}),
        CharacterSpec("chancellor", "Chancellor", {"description": "Flattering in public, scheming in private."}),
        CharacterSpec("spy", "Spy", {"description": "Delivers messages and remembers every one."}),
    ),
    lore=(
        {"name": "Eastern garrison", "keys": ["garrison", "eastern"], "content": "The eastern garrison answers only to a sealed order."},
        {"name": "Court etiquette", "keys": ["court", "emperor"], "content": "No one addresses the Emperor first without being addressed."},
        {"name": "Chancellor's ledgers", "keys": ["chancellor", "ledger"], "content": "The Chancellor keeps a ledger of favours that has never been balanced."},
    ),
    commitments=("The General eventually betrays the Emperor.",),
    plan=_intrigue_plan,
    invariants=(
        Invariant("commitment_persistence", "The betrayal commitment survives many turns", COMMITMENT_PERSISTENCE),
        Invariant("no_knowledge_leak", "The spy's message stays private to the General", NO_KNOWLEDGE_LEAK),
        Invariant("state_consistent", "Relationships accumulate without corrupting the projection", STATE_CONSISTENT),
        Invariant("event_validity", "Every committed event validates", EVENT_VALIDITY),
    ),
)

COMBAT = Scenario(
    key="combat",
    title="Combat: the broken line",
    summary="Injuries, location, possessions, and rapidly changing state.",
    premise="A line breaks, a shield is lost, and a standard changes hands.",
    objective="Keep the fight coherent while injuries, positions, and possessions change every few turns.",
    location="field",
    characters=(
        CharacterSpec("captain", "Captain", {"description": "Holds the line and the standard."}),
        CharacterSpec("lieutenant", "Lieutenant", {"description": "Aggressive, and first to be hit."}),
        CharacterSpec("raider", "Raider", {"description": "Fast, and unhurried."}),
    ),
    lore=(
        {"name": "Broken line", "keys": ["line", "field"], "content": "A broken line cannot be restored without a banner in hand."},
        {"name": "Standard", "keys": ["banner", "standard"], "content": "The standard identifies the unit that holds the field."},
    ),
    plan=_combat_plan,
    invariants=(
        Invariant("state_consistent", "Dead and wounded characters stop acting and are reflected in state", STATE_CONSISTENT),
        Invariant("no_transcript_as_memory", "Position changes are recorded as state, not as memory", NO_TRANSCRIPT_AS_MEMORY),
        Invariant("context_bounded", "Every prompt stays inside the provider budget", CONTEXT_BOUNDED),
    ),
)

ROMANCE = Scenario(
    key="romance",
    title="Romance: the evening before",
    summary="Relationship state, emotional continuity, and long-term commitments.",
    premise="Two people who cannot say the thing, and a rival who will not wait forever.",
    objective="Hold an emotional thread across many turns without flattening it.",
    location="parlour",
    characters=(
        CharacterSpec("isaac", "Isaac", {"description": "Patient to a fault."}),
        CharacterSpec("elena", "Elena", {"description": "Sharp, and avoiding."}),
        CharacterSpec("rival", "Rival", {"description": "Impatient, and owed an answer."}),
    ),
    lore=(
        {"name": "The parlour", "keys": ["parlour", "evening"], "content": "The parlour holds two chairs and one uncomfortable silence."},
    ),
    commitments=("Isaac waits for Elena however long it takes.",),
    plan=_romance_plan,
    invariants=(
        Invariant("commitment_persistence", "The waiting commitment survives many turns", COMMITMENT_PERSISTENCE),
        Invariant("state_consistent", "Relationship strength accumulates correctly", STATE_CONSISTENT),
        Invariant("no_knowledge_leak", "Elena's avoidance is not known before she says it", NO_KNOWLEDGE_LEAK),
    ),
)

CANON = Scenario(
    key="canon",
    title="Canon divergence: the sealed harbour",
    summary="Imported source material, user override, canon divergence, and an alternate timeline.",
    premise="A founding decree the story will later ignore, on a branch that keeps the old answer.",
    objective="Establish imported canon, then diverge from it and prove the branches stay separate.",
    location="harbour",
    characters=(
        CharacterSpec("captain", "Captain", {"description": "Obeys the decree until he decides not to."}),
        CharacterSpec("chronicler", "Chronicler", {"description": "Records what is true, not what should be."}),
        CharacterSpec("shade", "Shade", {"description": "Asks the questions the record does not answer."}),
    ),
    sources=(
        {
            "title": "Founding decree",
            "content": "The harbour gate is sealed to outsiders and must never be opened. The harbour is sealed.",
        },
    ),
    lore=(
        {"name": "Founding decree", "keys": ["harbour", "decree"], "content": "The harbour gate is sealed to outsiders and must never be opened."},
        {"name": "Pier customs", "keys": ["harbour", "pier"], "content": "Pier custom requires a harbour seal on every manifest."},
    ),
    plan=_canon_plan,
    invariants=(
        Invariant("branch_correctness", "A branch keeps its own world facts and never inherits the other branch's", BRANCH_CORRECTNESS),
        Invariant("no_knowledge_leak", "Imported canon is advisory, not character knowledge", NO_KNOWLEDGE_LEAK),
        Invariant("state_consistent", "World facts created on a branch stay on that branch", STATE_CONSISTENT),
    ),
)

SCENARIOS: dict[str, Scenario] = {
    scenario.key: scenario
    for scenario in (MYSTERY, INTRIGUE, COMBAT, ROMANCE, CANON)
}

ALL_SCENARIOS: tuple[Scenario, ...] = (MYSTERY, INTRIGUE, COMBAT, ROMANCE, CANON)

_validate()


def scenario_by_key(key: str) -> Scenario:
    try:
        return SCENARIOS[key]
    except KeyError as exc:
        raise LookupError(f"Unknown evaluation scenario: {key}") from exc
