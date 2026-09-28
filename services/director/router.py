"""Intent Router: cheap deterministic lane assignment before the Director.

The Director is an expensive reasoning and orchestration layer. The router is
the inexpensive gate in front of it: a pure function of the input text, mode,
possession state, and horizon that assigns one of four lanes.

```text
simple instruction            → cheap / direct execution (lanes 1–2)
narrative ambiguity / outcome → Director (lane 3)
long-horizon intent           → Director + commitment (lane 4)
explicit user-controlled act  → direct actor execution (lane 1)
```

Precedence is fixed and documented: possession beats everything (the user is
acting *now* through their character), then an explicit non-short horizon,
then content patterns. In Phase A the router only *annotates* — lanes 1 and 2
follow the exact current code paths; lanes 3 and 4 are enacted in Phase B.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from services.director.intent import (
    AuthorityMode,
    Intent,
    Lane,
    interpret,
)

_SIMPLE_WORLD_PATTERNS = (
    # Time passage.
    re.compile(r"\b(\d+|three|several|a\s+few)\s+(hours?|minutes?|days?|weeks?|months?|years?)\s+pass"),
    re.compile(r"^(later|meanwhile|afterwards?)\b"),
    re.compile(r"\b(hours?|days?|weeks?)\s+later\b"),
    re.compile(r"\b(dawn\s+breaks|night\s+falls|morning\s+comes|dusk\s+settles)\b"),
    # Ambience and environment.
    re.compile(r"\bit\s+starts?\s+(raining|snowing|hailing)\b"),
    re.compile(r"\b(rain|snow)\s+begins?\b"),
    re.compile(r"\bthe\s+lights?\s+go\s+out\b"),
    re.compile(r"\bthunder\b|\ba\s+bell\s+tolls\b"),
    re.compile(r"\b(silence|darkness|cold)\s+falls?\b"),
    re.compile(r"\bwind\s+picks\s+up\b"),
    re.compile(r"\bit\s+gets?\s+(dark|cold|quiet|warm)\b"),
)

_LONG_HORIZONS = frozenset({"long-term", "long term", "longterm", "end-state", "end state", "endstate"})

_IMMEDIATE = re.compile(r"\b(immediately|now|right\s+now|at\s+once|right\s+away|asap|instantly)\b")

_FUTURE_MARKERS = re.compile(
    r"\b(eventually|slowly|gradually|over\s+time|someday|one\s+day"
    r"|in\s+the\s+(end|long\s+run|future)|going\s+to)\b"
    r"|\bwill\b"
    r"|\b(make|have|ensure|eventually|goal|plan|want)\b"
)

_OUTCOME_NOUNS = re.compile(
    r"\b(betray\w*|relationship\w*|trust\w*|war|peace|lov\w*|power|throne"
    r"|reveng\w*|downfall|allian\w*|marri\w*|empire|kingdom|future|destin\w*|ruin)\b"
)


def classify_lane(
    text: str,
    *,
    mode: str = "auto",
    possessed_character_id: str | None = None,
    participant_names: Sequence[str] = (),
    horizon: str = "short",
) -> Lane:
    """Assign an execution lane. Pure, synchronous, and total on any string."""
    normalized = text.casefold().strip()
    if possessed_character_id:
        # The user is acting now through their character. No Director required.
        return Lane.DIRECT_ACTOR
    if horizon.strip().casefold() in _LONG_HORIZONS:
        return Lane.LONG_HORIZON
    if not normalized:
        return Lane.DIRECTION
    if _IMMEDIATE.search(normalized):
        # An explicit "now" pins execution to this turn; the Director may still
        # plan *how* in Phase B, but the lane stays near-term.
        return Lane.DIRECTION
    if mode.strip().casefold() == "world" or any(pattern.search(normalized) for pattern in _SIMPLE_WORLD_PATTERNS):
        return Lane.SIMPLE_WORLD
    if _FUTURE_MARKERS.search(normalized):
        lowered_names = [name.casefold() for name in participant_names if name]
        third_party = any(name and name in normalized and name not in {"i", "you"} for name in lowered_names)
        if _OUTCOME_NOUNS.search(normalized) or (third_party and re.search(r"\bwill\b", normalized)):
            return Lane.LONG_HORIZON
    return Lane.DIRECTION


def route(
    text: str,
    *,
    mode: str = "auto",
    participant_names: Sequence[str] = (),
    possessed_character_id: str | None = None,
    possessed_character_name: str | None = None,
    authority: AuthorityMode | str = AuthorityMode.DIRECTOR_ASSISTED,
    horizon: str = "short",
) -> Intent:
    """Interpret the input and assign its lane. Never calls a provider.

    Interpretation is authority-invariant: the same text yields the same
    objective, constraints, and exclusions under every authority mode.
    Authority only governs gap-filling, which is enforced by later phases.
    """
    intent = interpret(
        text,
        mode=mode,
        participant_names=participant_names,
        possessed_character_id=possessed_character_id,
        possessed_character_name=possessed_character_name,
        authority=authority,
        horizon=horizon,
    )
    intent.lane = classify_lane(
        text,
        mode=mode,
        possessed_character_id=possessed_character_id,
        participant_names=participant_names,
        horizon=horizon,
    )
    return intent


def route_intent_for_lane(
    text: str,
    *,
    mode: str = "auto",
    participant_names: Sequence[str] = (),
    possessed_character_id: str | None = None,
    horizon: str = "short",
) -> Intent:
    """Interpret and route in one step, for callers that do not need the split.

    The Director uses this so interpretation and lane selection can never drift
    apart: the router remains the single authority for lane choice.
    """
    return route(
        text,
        mode=mode,
        participant_names=participant_names,
        possessed_character_id=possessed_character_id,
        horizon=horizon,
    )
