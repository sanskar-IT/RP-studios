"""Transient interpretation of user intent.

``Intent`` represents what the user appears to want — and only that. It is
deliberately NOT a container for plan state: no beats, no steps, no execution
status. Editing or regenerating a ``DirectorPlan`` (Phase B) must never rewrite
the interpreted ``Intent`` unless the user actually changes their request, and
keeping plan-shaped fields out of this model is how that boundary is enforced.

An ``Intent`` is transient interpretation, not narrative state. Persisting an
interpreted record (as ``direct()`` does today with ``DirectorIntent`` rows) is
a separate, explicit step owned by the pipeline — never a side effect of
calling :func:`interpret`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from services.core.enums import AuthorityMode, Lane

# Re-exported so Phase A call sites and the Director package share one source of
# truth for the vocabulary.
__all__ = [
    "AuthorityMode",
    "Consistency",
    "Intent",
    "Lane",
    "Specification",
    "interpret",
]


class Specification(StrEnum):
    """How completely the request determines what should happen."""

    EXPLICIT = "explicit"
    PARTIAL = "partial"
    UNDERSPECIFIED = "underspecified"


class Consistency(StrEnum):
    """Whether the request can be satisfied as stated."""

    CONSISTENT = "consistent"
    AMBIGUOUS = "ambiguous"
    CONTRADICTORY = "contradictory"


@dataclass
class Intent:
    """What the user appears to want. Transient. No plan state, by design."""

    mode: str = "auto"
    objective: str = ""
    target_entities: list[str] = field(default_factory=list)
    desired_outcome: str = ""
    constraints: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    tone: str = ""
    urgency: str = ""
    canon_preference: str = ""
    user_control_level: str = ""
    specification: Specification = Specification.UNDERSPECIFIED
    consistency: Consistency = Consistency.CONSISTENT
    confidence: float = 0.05
    lane: Lane | str = ""
    horizon: str = "short"

    def to_dict(self) -> dict[str, Any]:
        specification = self.specification
        consistency = self.consistency
        lane = self.lane
        return {
            "mode": self.mode,
            "objective": self.objective,
            "target_entities": list(self.target_entities),
            "desired_outcome": self.desired_outcome,
            "constraints": list(self.constraints),
            "exclusions": list(self.exclusions),
            "tone": self.tone,
            "urgency": self.urgency,
            "canon_preference": self.canon_preference,
            "user_control_level": self.user_control_level,
            "specification": specification.value if isinstance(specification, Specification) else str(specification),
            "consistency": consistency.value if isinstance(consistency, Consistency) else str(consistency),
            "confidence": round(self.confidence, 3),
            "lane": lane.value if isinstance(lane, Lane) else str(lane),
            "horizon": self.horizon,
        }


# --- Heuristic tables (deterministic; every entry is pinned by tests) ---

_IMMEDIATE = re.compile(r"\b(immediately|now|right\s+now|at\s+once|right\s+away|asap|instantly)\b", re.IGNORECASE)

_FUTURE = re.compile(
    r"\b(eventually|soon|later|slowly|gradually|over\s+time|someday|one\s+day"
    r"|in\s+the\s+(end|long\s+run|future)|will\b|going\s+to)\b",
    re.IGNORECASE,
)

_CLAUSE_SPLIT = re.compile(r"[.!?;]+|\bbut\b|\byet\b|\bhowever\b", re.IGNORECASE)

_CONSTRAINT_CLAUSE = re.compile(
    r"\b(do\s+not|don't|does\s+not|doesn't|did\s+not|didn't|never|without"
    r"|except|only|must\s+not|can't|cannot|won't|keep\s+\w+\s+(out|away|off)"
    r"|leave\s+\w+\s+out|spare\b)"
    r"|^\s*(no|without)\b",
    re.IGNORECASE,
)

_VAGUE_CORE = re.compile(
    r"\b(something|anything|someone|somebody|things|stuff|whatever)\b"
)

_VAGUE_METHOD = re.compile(
    r"\b(mysteriously|suspiciously|somehow|discreetly|enigmatically)\b"
    r"|\bin\s+a\s+(strange|odd|mysterious|suspicious|unclear|vague)\s+way\b"
)

_EXCLUSION_PATTERNS = (
    re.compile(r"\bexcept\b", re.IGNORECASE),
    re.compile(r"\bleave\b.+\bout\b", re.IGNORECASE),
    re.compile(r"\bspare\b", re.IGNORECASE),
    re.compile(
        r"\b(don't|do\s+not|never)\b.+\b(turn|become|transform|make|let"
        r"|involve|include|kill|harm|hurt|touch)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bno\s+one\b.+\b(dies|dead|hurt|harmed)\b", re.IGNORECASE),
)

# A match only counts as asserted when it is NOT governed by a negation
# ("keep him alive" asserts life; "must not survive" does not assert
# survival). This is what keeps "do not alert anyone" from contradicting the
# guarded action it constrains.
_NEGATION_TOKENS = frozenset(
    {"not", "never", "no", "without", "don't", "doesn't", "didn't",
     "won't", "can't", "cannot", "mustn't", "shouldn't", "couldn't"}
)

_WORD_SPANS = re.compile(r"[A-Za-z']+")


def _negated_spans(text: str) -> list[tuple[int, int]]:
    """Spans governed by a negation: from the negation token to clause end."""
    tokens = [(m.group(0).casefold(), m.start(), m.end()) for m in _WORD_SPANS.finditer(text)]
    spans: list[tuple[int, int]] = []
    for index, (word, _start, end) in enumerate(tokens):
        if word in _NEGATION_TOKENS or word.endswith("n't"):
            clause_end = len(text)
            for later in tokens[index + 1:]:
                if later[0] in {"but", "yet"}:
                    clause_end = later[1]
                    break
            spans.append((end, clause_end))
    return spans


def _asserted_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Matches not governed by a negation."""
    spans = _negated_spans(text)
    return [
        m for m in pattern.finditer(text)
        if not any(start <= m.start() < end for start, end in spans)
    ]


# (side_a, side_b): contradictory when both sides are asserted. Same-clause
# matches count — "leave while keeping her in" is contradictory *because* both
# halves are asserted. The negation guard above is what prevents "do not alert"
# from firing against the action it constrains.
_CONTRADICTION_PAIRS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(r"\b(leave|leaves|leaving|exit|exits|exited|go\s+away|get\s+out)\b", re.IGNORECASE),
        re.compile(r"\b(keep|keeps|keeping|kept)\b.+\bin\b|\bstay\b|\bremains?\b", re.IGNORECASE),
    ),
    (
        re.compile(r"\b(kill|kills|killed|murder|murders|murdered|shoot|shoots|shot|assassinat\w*|execut\w*)\b", re.IGNORECASE),
        re.compile(r"\b(alive|surviv\w*|spare\w*|sav\w*)\b|\bkeep\b.+\balive\b", re.IGNORECASE),
    ),
    (
        re.compile(r"\b(sound\w*|ring\w*|alarms?|shout\w*|scream\w*|nois\w*|loud\w*)\b", re.IGNORECASE),
        re.compile(r"\b(silent|silently|quiet|quietly|unnoticed|unheard)\b", re.IGNORECASE),
    ),
    (
        re.compile(r"\bopen\b.+\b(door|gate|window|chest|box|vault)\b", re.IGNORECASE),
        re.compile(r"\b(keep|leave)\b.+\b(closed|shut|sealed|locked)\b", re.IGNORECASE),
    ),
    (
        re.compile(r"\bforget\b|\bnever\s+knew\b", re.IGNORECASE),
        re.compile(r"\bremember\b|\bknow\b", re.IGNORECASE),
    ),
)

_REFERENT = re.compile(r"\b(him|her|them|it|that|those|this)\b")

_ALTERNATIVE_CLAUSES = re.compile(r"\bor\b", re.IGNORECASE)

_TONE_SETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("suspenseful", re.compile(r"\b(suspicious|suspiciously|mysterious|mysteriously|eerie|creepy|tense|ominous)\b", re.IGNORECASE)),
    ("tense", re.compile(r"\b(panic|terrifying|horror|scream|urgent)\b", re.IGNORECASE)),
    ("romantic", re.compile(r"\b(love|kiss|gentle|tender|embrace)\b", re.IGNORECASE)),
    ("grim", re.compile(r"\b(murder|blood|death|corpse|kill)\b", re.IGNORECASE)),
    ("curious", re.compile(r"\b(discover|strange|odd|investigate)\b", re.IGNORECASE)),
)

_CANON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override", re.compile(r"\b(ignore\s+canon|break\s+canon|against\s+canon|override\s+canon|retcon|canon\s+be\s+damned)\b", re.IGNORECASE)),
    ("follow", re.compile(r"\b(follow\s+canon|stick\s+to\s+canon|canon\s+says|according\s+to\s+canon)\b", re.IGNORECASE)),
    ("branch", re.compile(r"\b(what\s+if|alternate\s+timeline|another\s+timeline)\b|\bbranch\b", re.IGNORECASE)),
)

_ACTION_VERBS = frozenset({
    "kill", "shoot", "murder", "assassinate", "slay", "stab", "poison",
    "strangle", "attack", "fight", "duel", "hit", "strike", "betray",
    "accuse", "arrest", "interrogate", "question", "threaten", "warn",
    "promise", "confess", "deny", "steal", "hide", "seek", "find",
    "discover", "reveal", "expose", "search", "follow", "chase", "flee",
    "escape", "enter", "leave", "open", "close", "lock", "unlock",
    "knock", "break", "climb", "wake", "kiss", "marry", "scream",
    "shout", "whisper", "tell", "ask", "help", "save", "protect",
    "capture", "free", "release", "bribe", "give", "take", "bring",
    "throw", "push", "dig", "bury", "cut", "write", "read", "play",
    "build", "destroy", "create", "make", "cause", "start", "stop",
    "wait", "watch", "touch", "remember", "forget", "learn", "teach",
    "travel", "return", "arrive", "depart", "meet", "visit", "invite",
    "join", "lead", "run",
})


def _split_clauses(text: str) -> list[str]:
    return [part.strip(" .,;:!?") for part in _CLAUSE_SPLIT.split(text) if part.strip(" .,;:!?")]


def _extract_constraints(clauses: list[str]) -> tuple[list[str], list[str]]:
    """Split clauses into (objective material, explicit constraints).

    Constraint text is preserved verbatim: authority may fill gaps around
    constraints but must never drop or reinterpret them.
    """
    objective: list[str] = []
    constraints: list[str] = []
    for clause in clauses:
        if _CONSTRAINT_CLAUSE.search(clause):
            constraints.append(clause)
        else:
            objective.append(clause)
    return objective, constraints


def _has_action_verb(text: str) -> bool:
    lowered = text.casefold()
    for verb in _ACTION_VERBS:
        if re.search(r"\b" + re.escape(verb) + r"(s|es|ed|ing)?\b", lowered):
            return True
    return False


def _concrete_object(text: str) -> str:
    """A non-vague noun phrase directly governed by an action verb."""
    for verb in _ACTION_VERBS:
        match = re.search(
            r"\b" + re.escape(verb) + r"(s|es|ed|ing)?\s+"
            r"(the|a|an|my|your|his|her|their|this|that)\s+([a-z]+)",
            text.casefold(),
        )
        if match and not _VAGUE_CORE.search(match.group(3)):
            return match.group(0).strip()
    return ""


def _urgency_of(text: str) -> str:
    if _IMMEDIATE.search(text):
        return "immediate"
    if _FUTURE.search(text):
        return "eventual"
    return "unspecified"


def _tone_of(text: str) -> str:
    for tone, pattern in _TONE_SETS:
        if pattern.search(text):
            return tone
    return ""


def _canon_preference_of(text: str) -> str:
    for preference, pattern in _CANON_PATTERNS:
        if pattern.search(text):
            return preference
    return ""


def _control_level(mode: str, possessed_character_id: str | None) -> str:
    if possessed_character_id:
        return "actor"
    return {
        "actor": "actor",
        "director": "director",
        "narrator": "narrator",
        "world": "world",
        "retcon": "author",
    }.get(mode.strip().casefold(), "")


def _coerce_authority(value: AuthorityMode | str) -> AuthorityMode:
    """Coerce unknown values to the default instead of raising on the hot path."""
    if isinstance(value, AuthorityMode):
        return value
    try:
        return AuthorityMode(str(value).strip().casefold())
    except ValueError:
        return AuthorityMode.DIRECTOR_ASSISTED


def _split_outcome(objective: str) -> tuple[str, str]:
    """Split "do X so that Y" into (objective, desired outcome)."""
    match = re.search(
        r"\b(so\s+that|in\s+order\s+to|that\s+makes?|that\s+leaves?|which\s+makes?)\b"
        r"|\b(making|leaving)\b",
        objective,
        re.IGNORECASE,
    )
    if not match:
        return objective.strip(), ""
    return objective[: match.start()].strip(" ,"), objective[match.end():].strip(" .,;:")


def interpret(
    text: str,
    *,
    mode: str = "auto",
    participant_names: Sequence[str] = (),
    possessed_character_id: str | None = None,
    possessed_character_name: str | None = None,
    authority: AuthorityMode | str = AuthorityMode.DIRECTOR_ASSISTED,
    horizon: str = "short",
) -> Intent:
    """Interpret user input into a transient :class:`Intent`.

    Pure, synchronous, and total on any string: it never raises on input
    content and never calls a provider, so it is safe to run on the hot path
    before generation. Interpretation is authority-invariant — ``authority``
    is accepted (and coerced) so gap-filling policy has a declared input, but
    the same text always yields the same objective, constraints, and
    exclusions under every authority mode. Enforcement of authority against
    gap-filling lands with the authority modes in a later phase.

    Consistency precedence is fixed: contradictory > ambiguous > consistent.
    """
    # Coerced (never enforced here): interpretation is authority-invariant, so
    # the same text yields the same result under every authority mode.
    _authority = _coerce_authority(authority)
    del _authority
    del possessed_character_name  # Used by the router for lane context, not interpretation.

    cleaned = " ".join(str(text).split())
    normalized_mode = str(mode or "auto").strip().casefold()
    names = [name for name in participant_names if name]
    lowered_names = [name.casefold() for name in names]

    if not cleaned:
        return Intent(
            mode=normalized_mode,
            user_control_level=_control_level(normalized_mode, possessed_character_id),
            specification=Specification.UNDERSPECIFIED,
            consistency=Consistency.CONSISTENT,
            confidence=0.05,
            horizon=str(horizon or "short"),
        )

    clauses = _split_clauses(cleaned)
    objective_clauses, constraints = _extract_constraints(clauses)
    objective_text = " ".join(objective_clauses).strip()
    objective, desired_outcome = _split_outcome(objective_text)

    targets = [name for name, lowered in zip(names, lowered_names, strict=True) if lowered and lowered in cleaned.casefold()]
    exclusions = [
        clause for clause in constraints
        if any(pattern.search(clause) for pattern in _EXCLUSION_PATTERNS)
    ]

    has_verb = _has_action_verb(cleaned)
    if not has_verb or (_VAGUE_CORE.search(cleaned) and not targets):
        specification = Specification.UNDERSPECIFIED
    elif _VAGUE_METHOD.search(cleaned):
        specification = Specification.PARTIAL
    elif targets or _concrete_object(cleaned):
        specification = Specification.EXPLICIT
    elif has_verb:
        specification = Specification.PARTIAL
    else:
        specification = Specification.UNDERSPECIFIED

    consistency = Consistency.CONSISTENT
    for side_a, side_b in _CONTRADICTION_PAIRS:
        if _asserted_matches(side_a, cleaned) and _asserted_matches(side_b, cleaned):
            consistency = Consistency.CONTRADICTORY
            break
    if consistency == Consistency.CONSISTENT:
        referents = _REFERENT.findall(cleaned.casefold())
        if referents and len(names) > 1:
            consistency = Consistency.AMBIGUOUS
        elif _ALTERNATIVE_CLAUSES.search(cleaned) and has_verb:
            consistency = Consistency.AMBIGUOUS

    confidence = 0.6
    if targets:
        confidence += 0.1
    if specification == Specification.EXPLICIT:
        confidence += 0.1
    if consistency == Consistency.AMBIGUOUS:
        confidence -= 0.2
    if specification == Specification.UNDERSPECIFIED:
        confidence -= 0.15
    confidence = max(0.05, min(0.95, round(confidence, 3)))

    return Intent(
        mode=normalized_mode,
        objective=objective,
        target_entities=targets,
        desired_outcome=desired_outcome,
        constraints=constraints,
        exclusions=exclusions,
        tone=_tone_of(cleaned),
        urgency=_urgency_of(cleaned),
        canon_preference=_canon_preference_of(cleaned),
        user_control_level=_control_level(normalized_mode, possessed_character_id),
        specification=specification,
        consistency=consistency,
        confidence=confidence,
        horizon=str(horizon or "short"),
    )
