"""Canon and source-material awareness.

Three things must stay distinct, and this module is where that separation is
enforced:

```text
canon state        what imported source material asserts
runtime state      what committed events have made true
user divergence    where the user has deliberately departed from canon
```

A conflict is a *finding*, not a restriction. The purpose is to make the
divergence visible at the moment it happens, so the user chooses between
following the source, overriding it, or branching from it. Nothing here ever
prevents intentional divergence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.state import StateSnapshot

RESOLUTION_FOLLOW = "follow"
RESOLUTION_OVERRIDE = "override"
RESOLUTION_BRANCH = "branch"
RESOLUTIONS = (RESOLUTION_FOLLOW, RESOLUTION_OVERRIDE, RESOLUTION_BRANCH)

CONFLICT_NONE = "none"
CONFLICT_ADVISORY = "advisory"
CONFLICT_HARD = "hard"

# A source only conflicts when the request shares its distinctive vocabulary.
# Two documents about the same topic are not in conflict; a request that
# contradicts one of them is.
_MIN_DISTINCTIVE = 3

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

_PROHIBITION = re.compile(
    r"\b(must\s+never|never|shall\s+not|cannot|forbidden|prohibited|is\s+sealed|must\s+not)\b",
    re.IGNORECASE,
)


def _distinctive(text: str) -> set[str]:
    return {
        word
        for word in (token.casefold() for token in _WORD.findall(text))
        if len(word) >= 4
    }


@dataclass
class CanonConflict:
    """A detected divergence between source material and the requested plan."""

    severity: str = CONFLICT_ADVISORY
    source_title: str = ""
    source_claim: str = ""
    requested: str = ""
    resolution: str | None = None
    rationale: str = ""
    options: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "source_title": self.source_title,
            "source_claim": self.source_claim,
            "requested": self.requested,
            "resolution": self.resolution,
            "rationale": self.rationale,
            "options": [dict(option) for option in self.options],
        }

    def with_resolution(self, resolution: str) -> CanonConflict:
        return CanonConflict(
            severity=self.severity,
            source_title=self.source_title,
            source_claim=self.source_claim,
            requested=self.requested,
            resolution=resolution,
            rationale=self.rationale,
            options=list(self.options),
        )


def _options(canon_preference: str) -> list[dict[str, str]]:
    return [
        {"key": RESOLUTION_FOLLOW, "label": "Follow source", "detail": "Imported canon stays true."},
        {"key": RESOLUTION_OVERRIDE, "label": "Override source", "detail": "Record an accepted divergence."},
        {"key": RESOLUTION_BRANCH, "label": "Branch from source", "detail": "Fork a timeline that keeps canon."},
    ]


def detect_canon_conflict(
    *,
    requested_text: str,
    sources: Iterable[tuple[str, str]],
    state: StateSnapshot | None = None,
    canon_preference: str = "",
    recorded_divergences: Sequence[str] = (),
) -> CanonConflict | None:
    """Compare a request against imported source material.

    Returns ``None`` when nothing conflicts, so the caller can treat "no conflict"
    as the common case and avoid a user prompt on an ordinary turn.
    """
    if not requested_text.strip():
        return None
    requested_distinctive = _distinctive(requested_text)
    for title, content in sources:
        if not content.strip():
            continue
        claim = _claim_from(content)
        if not claim:
            continue
        shared = requested_distinctive & _distinctive(content)
        if len(shared) < _MIN_DISTINCTIVE:
            continue
        already_diverged = any(
            phrase and phrase.casefold() in requested_text.casefold()
            for phrase in recorded_divergences
        )
        if already_diverged:
            continue
        severity = CONFLICT_HARD if _PROHIBITION.search(content) else CONFLICT_ADVISORY
        resolution = _implied_resolution(canon_preference)
        return CanonConflict(
            severity=severity,
            source_title=title or "Source material",
            source_claim=claim,
            requested=requested_text.strip()[:280],
            resolution=resolution,
            rationale=(
                "The request shares the source's subject matter and may contradict it. "
                "Canon is advisory unless configured as a hard constraint."
            ),
            options=_options(canon_preference),
        )
    del state
    return None


def _claim_from(content: str) -> str:
    """The sentence in a source that states a rule, if it has one."""
    for sentence in re.split(r"[.!?\n]+", content):
        stripped = sentence.strip()
        if len(stripped) > 12 and _PROHIBITION.search(stripped):
            return stripped[:280]
    return ""


def _implied_resolution(canon_preference: str) -> str | None:
    """How a preference resolves without asking.

    ``override`` and ``branch`` are decided by the user already; ``follow`` is a
    statement of intent, not a licence to rewrite canon, so it still asks. An
    empty preference always asks.
    """
    if canon_preference in {RESOLUTION_OVERRIDE, RESOLUTION_BRANCH}:
        return canon_preference
    return None
