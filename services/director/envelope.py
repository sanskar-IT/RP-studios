"""Constraint envelope and plan validation.

Two independent gates stand between a proposed plan and a performed turn.

**The envelope gate** answers one question: does this plan do something the user
explicitly excluded? Authority governs how much the Director may *fill in*; it
never governs whether the Director may *contradict*. An ``ai_directed`` plan may
invent a method, a location, a witness, a reaction — anything inside the
envelope — but it may not introduce the alarm the user said must not happen. If
the only way to satisfy a request violates a constraint, the correct result is a
plan conflict, not a violated constraint.

**The validation gate** answers whether the plan is performable at all: does it
name entities that exist, does it require a character who is dead, does it
propose a state transition the projection cannot accept.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import AuthorityMode, Horizon, Lane, PlanStatus
from services.core.state import StateSnapshot
from services.director.intent import Consistency
from services.director.plan import DirectorPlanDraft

_NEGATION = re.compile(
    r"\b(do\s+not|don'?t|does\s+not|doesn'?t|did\s+not|didn'?t|never|no|without|"
    r"must\s+not|can'?t|cannot|won'?t|avoid|without)\b",
    re.IGNORECASE,
)

# Words that carry no constraint meaning once the negation is stripped.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "any", "one", "anyone", "anybody", "someone",
        "somebody", "something", "there", "here", "it", "them", "they",
        "of", "to", "in", "on", "at", "be", "is", "are", "was", "were",
        "and", "or", "but", "so", "that", "this", "these", "those", "me",
        "us", "him", "her", "his", "hers", "their", "my", "your", "our",
        "get", "gets", "got", "make", "makes", "made", "do", "does", "did",
        "let", "lets", "please", "just", "very", "really", "quite", "ever",
        "still", "also", "then", "than", "when", "what", "which", "who",
    }
)

# Negation vocabulary, including the negative pronouns and contractions. Missing
# "nobody" here would let a plan say "nobody is alerted" and be flagged for
# violating "do not alert anyone", which is the exact false positive the guard
# exists to prevent.
_NEGATIVE_WORDS = frozenset(
    {
        "not",
        "never",
        "no",
        "none",
        "nobody",
        "nothing",
        "nowhere",
        "neither",
        "nor",
        "without",
    }
)

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

MAX_BEATS = 12

# Prohibition terms are matched against a small, deliberately tight synonym
# family. "do not alert anyone" has to catch a plan that says "an alarm is
# triggered", and pure stem matching does not: ``\balert\w*`` never matches
# "alarm". Every family here is a genuine paraphrase of the same narrative act;
# nothing looser than that is included, because a loose family turns the envelope
# into a reason to refuse plans that are perfectly fine.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Announcing something to a room is alerting that room; omitting it would
    # leave "do not alert anyone" trivially bypassable by a paraphrase.
    "alert": ("alarm", "shout", "scream", "yell", "holler", "announce"),
    "alarm": ("alert", "shout", "scream"),
    "warn": ("alert", "alarm", "notify"),
    "notify": ("warn", "alert", "tell"),
    # A prohibition on violence has to reach the acts, not just the noun: a plan
    # that "has the General attack the witness" violates "no violence" even
    # though the word violence never appears.
    "violence": ("violent", "attack", "assault", "hurt", "harm", "wound", "kill", "fight"),
    "violent": ("violence", "attack", "hurt", "kill"),
    "weapon": ("violence", "attack", "threaten"),
    "tell": ("inform", "notify", "reveal"),
    "reveal": ("expose", "disclose", "confess", "betray"),
    "expose": ("reveal", "disclose"),
    "disclose": ("reveal", "expose"),
    "confess": ("admit", "reveal"),
    "betray": ("double-cross", "treacher"),
    "kill": ("murder", "slay", "assassinate", "execute"),
    "murder": ("kill", "slay"),
    "shoot": ("fire", "gun"),
    "leave": ("exit", "depart", "flee", "escape", "quit"),
    "exit": ("leave", "depart"),
    "flee": ("leave", "escape", "run"),
    "escape": ("flee", "leave"),
    "stay": ("remain",),
    "remain": ("stay",),
    "transform": ("become", "turn", "change"),
    "become": ("transform", "turn"),
    "hurt": ("harm", "injure", "wound", "injury"),
    "harm": ("hurt", "injure", "wound"),
    "injure": ("hurt", "harm", "wound"),
    "wound": ("hurt", "harm", "injure"),
    "die": ("death", "dead", "perish"),
    "survive": ("live", "alive", "escape"),
    "fight": ("attack", "assault", "strike", "battle"),
    "attack": ("fight", "assault", "strike"),
    "see": ("notice", "spot", "observe", "witness"),
    "notice": ("see", "spot", "observe"),
    "witness": ("see", "observe", "watch"),
    "hide": ("conceal",),
    "conceal": ("hide",),
    "speak": ("say", "talk", "tell"),
    "shout": ("yell", "scream", "holler"),
    "scream": ("shout", "yell"),
}


def _build_families() -> dict[str, frozenset[str]]:
    """Bidirectional synonym closure.

    The table is written one way but read both ways: "alert" and "warn" are
    paraphrases of the same act, so a prohibition on alerting must also catch a
    plan that says the guard "is warned". A one-directional lookup would miss
    exactly the cases the envelope exists to catch.
    """
    direct: dict[str, set[str]] = {key: {key, *value} for key, value in _SYNONYMS.items()}
    reverse: dict[str, set[str]] = {}
    for key, members in _SYNONYMS.items():
        for member in members:
            reverse.setdefault(member, {member}).add(key)
    families: dict[str, frozenset[str]] = {}
    for term in {*direct, *reverse}:
        families[term] = frozenset({term, *direct.get(term, set()), *reverse.get(term, set())})
    return families


_FAMILIES = _build_families()

# English inflection is irregular in the places that matter here: "notify" is
# not a prefix of "notifies" (the y becomes i), and "die" becomes "dying". A plain
# ``stem\w*`` pattern silently misses both, which would let a plan that "notifies
# the household" pass a prohibition on alerting anyone.
_IRREGULAR: dict[str, tuple[str, ...]] = {
    "die": ("dying", "died", "dies", "die"),
    "lie": ("lying", "lied", "lies"),
    "flee": ("fleeing", "fled"),
    "slay": ("slaying", "slew", "slain", "slays"),
    "betray": ("betraying", "betrayed", "betrayals"),
    "alarm": ("alarm", "alarming", "alarmed", "alarms"),
}

_STEM_CACHE: dict[str, re.Pattern[str]] = {}


def _stem_pattern(term: str) -> re.Pattern[str]:
    """Match a verb and its inflections without swallowing unrelated words."""
    lowered = term.casefold()
    cached = _STEM_CACHE.get(lowered)
    if cached is not None:
        return cached
    if lowered in _IRREGULAR:
        alternatives = "|".join(re.escape(form) for form in _IRREGULAR[lowered])
        pattern = re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)
    elif lowered.endswith("y") and len(lowered) > 2:
        base = re.escape(lowered[:-1])
        pattern = re.compile(rf"\b{base}(?:y|ies|ied|ing)\b", re.IGNORECASE)
    else:
        base = re.escape(lowered)
        pattern = re.compile(rf"\b{base}(?:e?s|es|ed|ing|d)?\b", re.IGNORECASE)
    _STEM_CACHE[lowered] = pattern
    return pattern


def _expansions(term: str) -> tuple[str, ...]:
    """The stem plus its paraphrases, deduplicated."""
    return tuple(sorted(_FAMILIES.get(term.casefold(), {term.casefold()})))


def prohibited_terms(*clauses: str) -> dict[str, list[str]]:
    """Extract what the user forbade, as normalised word sets.

    A prohibition is the payload of a negation clause. "do not alert anyone"
    forbids alerting; "must not survive" forbids surviving. Every term of a
    prohibition must be present for a match, so a shared function word can never
    trip it on its own.
    """
    prohibited: dict[str, list[str]] = {}
    for clause in clauses:
        if not _NEGATION.search(clause):
            continue
        payload = _NEGATION.sub(" ", clause)
        words = [
            word.casefold()
            for word in _WORD.findall(payload)
            if len(word) > 2 and word.casefold() not in _STOPWORDS
        ]
        if words:
            prohibited[clause] = sorted(set(words))
    return prohibited


def _negated_spans(lowered: str) -> list[tuple[int, int]]:
    """Spans governed by a negation, used to avoid false positives."""
    spans: list[tuple[int, int]] = []
    tokens = [(m.group(0).casefold(), m.start(), m.end()) for m in _WORD.finditer(lowered)]
    for index, (word, _start, end) in enumerate(tokens):
        base = word[:-3] if word.endswith("n't") and len(word) > 3 else word
        if base in _NEGATIVE_WORDS or word.endswith("n't"):
            clause_end = len(lowered)
            for later in tokens[index + 1:]:
                if later[0] in {"but", "yet"}:
                    clause_end = later[1]
                    break
            spans.append((end, clause_end))
    return spans


@dataclass
class EnvelopeViolation:
    """A plan that contradicts an explicit user constraint."""

    code: str
    clause: str
    summary: str
    asserted_by: str = ""
    terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "clause": self.clause,
            "summary": self.summary,
            "asserted_by": self.asserted_by,
            "terms": list(self.terms),
        }


def check_envelope(
    plan: DirectorPlanDraft,
    *,
    intent_constraints: Sequence[str] = (),
    intent_exclusions: Sequence[str] = (),
) -> list[EnvelopeViolation]:
    """Find every place the plan contradicts an explicit user constraint.

    Checked against required and likely consequences (things the plan will cause
    or expects) and against beat descriptions (things the Performer is asked to
    realise). Optional consequences are advisory and are not policed: proposing
    an *optional* possibility the user forbade is still worth flagging, but it
    does not block the plan.
    """
    violations: list[EnvelopeViolation] = []
    prohibited = prohibited_terms(*intent_constraints, *intent_exclusions)
    if not prohibited:
        return violations
    claims: list[tuple[str, str]] = []
    claims.extend((text, "required consequence") for text in plan.required_consequences)
    claims.extend((text, "likely consequence") for text in plan.likely_consequences)
    claims.extend((beat.description, "beat") for beat in plan.beats)
    claims.extend((text, "commitment") for text in plan.commitments)
    for clause, terms in prohibited.items():
        for text, source in claims:
            asserted = _asserts_asserting(text, terms)
            if asserted:
                violations.append(
                    EnvelopeViolation(
                        code="constraint_violation",
                        clause=clause,
                        summary=(
                            f"Plan {source} asserts something the user explicitly excluded: "
                            f"{clause!r}"
                        ),
                        asserted_by=text,
                        terms=sorted(set(terms)),
                    )
                )
    return violations


def _asserts_asserting(text: str, terms: Sequence[str]) -> bool:
    """True when the text affirmatively asserts one of a prohibition's terms.

    Negation-aware: "nobody is alerted" does not trip an alert prohibition. The
    first matching term decides, and a negated match returns False rather than
    continuing, so a line that both mentions and negates a term is not counted
    as an assertion.
    """
    lowered = text.casefold()
    negated_spans = _negated_spans(lowered)
    for term in terms:
        for stem in _expansions(term):
            match = _stem_pattern(stem).search(lowered)
            if not match:
                continue
            if any(start <= match.start() < end for start, end in negated_spans):
                return False
            return True
    return False


@dataclass
class PlanValidation:
    """The verdict on a plan before any performance is attempted."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    envelope: list[EnvelopeViolation] = field(default_factory=list)
    recovery_actions: tuple[str, ...] = ("regenerate", "edit", "return_to_user")

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "envelope": [violation.to_dict() for violation in self.envelope],
            "recovery_actions": list(self.recovery_actions),
        }


def validate_plan(
    plan: DirectorPlanDraft,
    *,
    state: StateSnapshot,
    participant_ids: Iterable[str] = (),
    known_locations: Iterable[str] = (),
    known_character_names: Iterable[str] = (),
    intent_constraints: Sequence[str] = (),
    intent_exclusions: Sequence[str] = (),
) -> PlanValidation:
    """Reject plans that cannot be performed, before performance is attempted.

    A rejected plan commits no narrative state, which is what makes
    "regenerate / edit / return to user" a real recovery rather than a repair of
    half-applied work.
    """
    validation = PlanValidation()
    participants = set(participant_ids)
    locations = set(known_locations)
    names = {name.casefold() for name in known_character_names}

    envelope = check_envelope(
        plan, intent_constraints=intent_constraints, intent_exclusions=intent_exclusions
    )
    if envelope:
        validation.envelope = envelope
        validation.valid = False
        validation.errors.extend(violation.summary for violation in envelope)
        return validation

    if plan.requires_choice and not plan.options:
        validation.valid = False
        validation.errors.append("Plan requires a choice but offers no options")

    if not plan.objective and not plan.beats and not plan.desired_direction:
        validation.valid = False
        validation.errors.append("Plan has no objective, beat, or direction to act on")

    for name in plan.participants:
        if participants and name not in participants:
            validation.valid = False
            validation.errors.append(f"Plan references a non-participant: {name}")

    for beat in plan.beats:
        if not beat.description.strip():
            validation.valid = False
            validation.errors.append("Plan contains a beat with no description")
            continue
        if beat.requires_knowledge and beat.requires_knowledge.casefold() not in names:
            validation.valid = False
            validation.errors.append(
                f"Beat requires knowledge held by an unknown entity: {beat.requires_knowledge}"
            )
        for name in beat.participants:
            if participants and name not in participants:
                validation.valid = False
                validation.errors.append(f"Beat references a non-participant: {name}")
            if name in state.dead:
                validation.valid = False
                validation.errors.append(f"Beat requires {name}, who is dead on this timeline")

    for name in plan.intent.target_entities:
        if participants and name not in participants and name.casefold() not in names:
            validation.valid = False
            validation.errors.append(f"Plan targets an unknown entity: {name}")

    if locations:
        for text in (*plan.required_consequences, *plan.desired_direction):
            for candidate in re.findall(r"\bin the ([A-Z][\w'-]*)", text):
                if candidate not in locations:
                    validation.warnings.append(
                        f"Plan mentions a location the project does not define: {candidate}"
                    )

    if len(plan.beats) > MAX_BEATS:
        validation.warnings.append(
            f"Plan has {len(plan.beats)} beats; a long plan invites rigid sequencing"
        )
    if plan.horizon is Horizon.LONG_TERM and not plan.commitments:
        validation.warnings.append(
            "Long-term plan records no commitment; the outcome could be forgotten"
        )
    if plan.horizon is not Horizon.LONG_TERM and plan.lane is Lane.LONG_HORIZON:
        validation.valid = False
        validation.errors.append("Long-horizon lane must carry a long-term horizon")
    return validation


def requires_approval(
    plan: DirectorPlanDraft,
    *,
    authority: AuthorityMode,
) -> bool:
    """Whether this plan must stop and wait for a human decision.

    Approval is required under ``strict`` and ``collaborative``, when the intent
    contradicts itself, and when a canon conflict is unresolved.

    It is deliberately *not* required for an ambiguous intent, even though
    ambiguous is not ``consistent``. ``docs/INTENT_MODEL.md`` draws the line
    explicitly: "Ambiguous is different. It is a warning, not a wall." Gating on
    it stopped the ordinary path dead — ``_REFERENT`` marks *any* pronoun as
    ambiguous, so "I follow her down the hall", "I look at it" and "I hand the
    letter to him" all produced an empty turn and an approval prompt, at every
    authority level including the default ``director_assisted``. A scene with two
    characters in it could not be played with ordinary sentences.

    Ambiguity still does its job elsewhere: it discounts the plan's confidence
    and biases the Performer toward the conservative reading.
    """
    if plan.intent.consistency is Consistency.CONTRADICTORY:
        return True
    if plan.canon_conflict and plan.canon_conflict.get("resolution") in {None, "", "ask"}:
        return True
    return authority.requires_approval


def plan_status_for(approved: bool) -> PlanStatus:
    return PlanStatus.APPROVED if approved else PlanStatus.PROPOSED
