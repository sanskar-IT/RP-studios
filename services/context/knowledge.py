"""Character knowledge isolation.

The engine keeps four kinds of "knowing" apart, because collapsing them is the
single fastest way to destroy a mystery:

``world``
    True for everyone, because a committed event made it true.
``character``
    A belief held by exactly one character. Two characters may hold different
    beliefs, or different confidence in the same belief.
``player``
    Knowledge the operator supplied out of band. No character has it, and it is
    never attributed to one.
``source``
    Material that exists only in imported canon. It is advisory. It informs
    generation but is not true on this timeline until an event makes it so.

The projection in :class:`services.core.state.StateSnapshot` records certainty
in ``knowledge`` and suspicion in ``suspicions``. Keeping them in separate
collections is what lets the engine say that one character is sure and another
merely suspects, instead of storing both as the same opaque string.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import KnowledgeKind
from services.core.state import StateSnapshot

CERTAIN = "certain"
SUSPECTED = "suspected"
REFUTED = "refuted"

# Minimum shared-word overlap before a spoken line is treated as a knowledge
# claim. A fact is identified by its distinctive words — long, non-stopword
# tokens — because common words such as "the" or "library" appear in every
# prompt of a library scene and are not information. A claim leaks a fact only
# when it carries the fact's full distinctive payload.
DISTINCTIVE_MIN_LENGTH = 5

# Edge punctuation carries no content in a knowledge fact, so it is stripped
# rather than treated as part of the word. "a brother." and "a brother" are the
# same claim, and treating them as different is how a user's own words get
# miscounted as a leak.
_TOKEN_PUNCTUATION = ".,;:!?\"'()[]{}\u2014\u2013-*"

DISTINCTIVE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "before",
        "because",
        "between",
        "could",
        "every",
        "first",
        "never",
        "other",
        "shall",
        "should",
        "still",
        "their",
        "there",
        "these",
        "those",
        "under",
        "until",
        "where",
        "which",
        "while",
        "would",
        "your",
    }
)


@dataclass(frozen=True)
class KnowledgeFact:
    fact: str
    kind: KnowledgeKind
    character_id: str | None = None
    confidence: str = CERTAIN
    source: str = ""
    refuted: bool = False

    @property
    def certain(self) -> bool:
        return self.confidence == CERTAIN and not self.refuted

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "kind": self.kind.value,
            "character_id": self.character_id,
            "confidence": self.confidence,
            "source": self.source,
            "refuted": self.refuted,
        }


@dataclass
class KnowledgeView:
    """What one viewer is allowed to be told."""

    viewer_character_id: str | None
    world_facts: list[KnowledgeFact] = field(default_factory=list)
    known: list[KnowledgeFact] = field(default_factory=list)
    suspected: list[KnowledgeFact] = field(default_factory=list)
    player_facts: list[KnowledgeFact] = field(default_factory=list)
    source_facts: list[KnowledgeFact] = field(default_factory=list)
    withheld: list[KnowledgeFact] = field(default_factory=list)

    def visible_texts(self) -> list[str]:
        return [fact.fact for fact in (*self.known, *self.suspected, *self.world_facts)]

    def certain_texts(self) -> list[str]:
        return [fact.fact for fact in self.known if fact.certain]

    def suspicion_texts(self) -> list[str]:
        return [fact.fact for fact in self.suspected if not fact.certain]

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewer_character_id": self.viewer_character_id,
            "world_facts": [fact.to_dict() for fact in self.world_facts],
            "known": [fact.to_dict() for fact in self.known],
            "suspected": [fact.to_dict() for fact in self.suspected],
            "player_facts": [fact.to_dict() for fact in self.player_facts],
            "source_facts": [fact.to_dict() for fact in self.source_facts],
            "withheld": [fact.to_dict() for fact in self.withheld],
        }

    def render(self) -> str:
        """The P4 context block for the viewer.

        Certainty is stated explicitly. A model that sees only a list of facts
        will treat a suspicion as a fact, which is the failure this whole module
        exists to prevent.
        """
        lines: list[str] = []
        if self.world_facts:
            lines.append("True for everyone:")
            lines.extend(f"- {fact.fact}" for fact in self.world_facts)
        if self.known:
            lines.append("You know this to be true:")
            lines.extend(f"- {fact.fact}" for fact in self.known if fact.certain)
        if self.suspected:
            lines.append("You suspect this but have no proof:")
            lines.extend(f"- {fact.fact}" for fact in self.suspected)
        if self.player_facts:
            lines.append("The operator knows this; no character in the scene does:")
            lines.extend(f"- {fact.fact}" for fact in self.player_facts)
        if self.source_facts:
            lines.append("Imported canon claims the following. It is unverified reference, not truth:")
            lines.extend(f"- {fact.fact}" for fact in self.source_facts)
        if not lines:
            return "You have no established knowledge in this scene."
        if self.viewer_character_id:
            lines.append(
                "Do not state, imply, or act on anything outside this list. "
                "If you would need to know more, say so in character instead of assuming."
            )
        return "\n".join(lines)


def _tokens(value: str) -> set[str]:
    """Case-folded content words, with punctuation stripped off the edges.

    Stripping matters: without it "a brother." and "a brother" tokenise
    differently, so a user who states a fact in their own instruction is scored as
    leaking it — what they typed becomes indistinguishable from the engine
    volunteering something it should not.
    """
    return {
        stripped.casefold()
        for part in value.replace("_", " ").split()
        if len(stripped := part.strip(_TOKEN_PUNCTUATION)) > 2
    }


def _distinctive(value: str) -> set[str]:
    """The words that carry a fact's information content."""
    return {
        token
        for token in _tokens(value)
        if len(token) >= DISTINCTIVE_MIN_LENGTH and token not in DISTINCTIVE_STOPWORDS
    }


def claim_states_fact(claim: str, fact: str) -> bool:
    """Whether a line of text plausibly asserts a specific fact.

    The test is the fact's distinctive payload: every long, non-stopword token
    must be present, or the whole fact verbatim. A library prompt that says
    "Alice" and "library" does not state "Alice was in the library tonight";
    a prompt that says "Alice", "library", and "tonight" does.
    """
    distinctive = _distinctive(fact)
    if len(distinctive) >= 2:
        if distinctive <= _tokens(claim):
            return True
    return bool(fact.strip()) and fact.casefold() in claim.casefold()


def shares_words(claim: str, fact: str) -> bool:
    return claim_states_fact(claim, fact)


def world_facts_from_state(state: StateSnapshot) -> list[KnowledgeFact]:
    facts: list[KnowledgeFact] = []
    for fact_id, payload in sorted(state.world_facts.items()):
        text = str(payload.get("text") or payload.get("fact") or payload.get("description") or fact_id)
        facts.append(
            KnowledgeFact(
                fact=text,
                kind=KnowledgeKind.WORLD,
                source=str(payload.get("source", "event")),
                refuted=bool(payload.get("retracted")),
            )
        )
    return facts


def build_knowledge_view(
    state: StateSnapshot,
    *,
    viewer_character_id: str | None,
    player_facts: Sequence[str] = (),
    source_facts: Sequence[str] = (),
    include_world: bool = True,
) -> KnowledgeView:
    """Assemble exactly what one viewer may be told, and record what was withheld.

    Withheld is not decoration: the contradiction detector and the evaluation
    harness both use it to prove that a fact the character does not know never
    reached their prompt.
    """
    view = KnowledgeView(viewer_character_id=viewer_character_id)
    if include_world:
        view.world_facts = world_facts_from_state(state)
    for fact in sorted(state.knowledge.get(viewer_character_id or "", set())):
        view.known.append(KnowledgeFact(fact=str(fact), kind=KnowledgeKind.CHARACTER, character_id=viewer_character_id))
    for fact in sorted(state.suspicions.get(viewer_character_id or "", set())):
        view.suspected.append(
            KnowledgeFact(
                fact=str(fact),
                kind=KnowledgeKind.CHARACTER,
                character_id=viewer_character_id,
                confidence=SUSPECTED,
            )
        )
    view.player_facts = [KnowledgeFact(fact=str(fact), kind=KnowledgeKind.PLAYER, source="operator") for fact in player_facts]
    view.source_facts = [KnowledgeFact(fact=str(fact), kind=KnowledgeKind.SOURCE, source="import") for fact in source_facts]
    if viewer_character_id:
        allowed = {fact.fact.casefold() for fact in (*view.known, *view.suspected)}
        for character_id, facts in state.knowledge.items():
            if character_id == viewer_character_id:
                continue
            for fact in sorted(facts):
                if str(fact).casefold() not in allowed:
                    view.withheld.append(
                        KnowledgeFact(
                            fact=str(fact),
                            kind=KnowledgeKind.CHARACTER,
                            character_id=character_id,
                            source="other character",
                        )
                    )
    return view


def character_knowledge(
    state: StateSnapshot,
    character_id: str | None,
) -> tuple[list[str], list[str]]:
    """Return ``(certain, suspected)`` for one character."""
    if not character_id:
        return [], []
    return (
        sorted(state.knowledge.get(character_id, set())),
        sorted(state.suspicions.get(character_id, set())),
    )


def knowledge_leaks(view: KnowledgeView, texts: Iterable[str]) -> list[KnowledgeFact]:
    """Facts this viewer does not know that appear in the given texts.

    Used by tests, the contradiction detector, and the evaluation harness to
    assert isolation rather than assume it.
    """
    permitted = {fact.fact.casefold() for fact in (*view.known, *view.world_facts)}
    permitted_suspected = {fact.fact.casefold() for fact in view.suspected}
    leaks: list[KnowledgeFact] = []
    for fact in view.withheld:
        for text in texts:
            if fact.fact.casefold() in permitted or fact.fact.casefold() in permitted_suspected:
                continue
            if shares_words(str(text), fact.fact):
                leaks.append(fact)
                break
    return leaks


def knowledge_table(state: StateSnapshot) -> dict[str, dict[str, list[str]]]:
    """The full "what does everyone know" table used by inspection and reports."""
    table: dict[str, dict[str, list[str]]] = {}
    for character_id in sorted({*state.knowledge, *state.suspicions}):
        table[character_id] = {
            "certain": sorted(state.knowledge.get(character_id, set())),
            "suspected": sorted(state.suspicions.get(character_id, set())),
        }
    return table
