from __future__ import annotations

import hashlib
import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoreCandidate:
    id: str
    content: str
    primary_keys: list[str] = field(default_factory=list)
    secondary_keys: list[str] = field(default_factory=list)
    enabled: bool = True
    constant: bool = False
    selective: bool = True
    recursive: bool = True
    insertion_order: int = 0
    probability: float = 1.0
    scan_depth: int = 4
    scope: str = "project"
    name: str = ""


@dataclass
class ActivatedLore:
    entry_id: str
    name: str
    content: str
    scope: str
    reasons: list[str]
    token_count: int
    insertion_order: int


@dataclass
class LoreDebug:
    input_text: str
    token_budget: int
    used_tokens: int
    activated: list[ActivatedLore]
    considered: list[dict[str, Any]]
    candidates: int = 0

    @property
    def false_activations(self) -> int:
        """Entries that matched a key but carried no usable content for this turn."""
        return sum(
            1
            for entry in self.activated
            if not entry.content.strip() or entry.token_count <= 1
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_text": self.input_text,
            "token_budget": self.token_budget,
            "used_tokens": self.used_tokens,
            "candidates": self.candidates or len(self.considered),
            "activated_count": len(self.activated),
            "considered_count": len(self.considered),
            "missed": [
                item
                for item in self.considered
                if not item["activated"] and "no primary key" not in item["reasons"]
            ],
            "activated": [
                {
                    "entry_id": entry.entry_id,
                    "name": entry.name,
                    "scope": entry.scope,
                    "reasons": entry.reasons,
                    "token_count": entry.token_count,
                    "insertion_order": entry.insertion_order,
                    "content": entry.content,
                }
                for entry in self.activated
            ],
            "considered": self.considered,
        }


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[\w'-]+", value, flags=re.UNICODE)}


def _phrase_hits(phrase: str, text: str) -> bool:
    phrase = phrase.casefold().strip()
    return bool(phrase) and phrase in text.casefold()


def _passes_probability(candidate: LoreCandidate, random_value: Callable[[], float] | None) -> bool:
    probability = max(0.0, min(1.0, candidate.probability))
    if probability >= 1.0:
        return True
    if random_value is not None:
        draw = random_value()
    else:
        digest = hashlib.sha256(f"{candidate.id}:{candidate.content}".encode()).hexdigest()
        draw = int(digest[:8], 16) / 0xFFFFFFFF
    return draw <= probability


def _candidate_from_value(value: LoreCandidate | dict[str, Any]) -> LoreCandidate:
    if isinstance(value, LoreCandidate):
        return value
    return LoreCandidate(
        id=str(value.get("id", value.get("entry_id", ""))),
        name=str(value.get("name", value.get("comment", ""))),
        content=str(value.get("content", "")),
        primary_keys=[str(key) for key in value.get("primary_keys", value.get("keys", []))],
        secondary_keys=[str(key) for key in value.get("secondary_keys", [])],
        enabled=bool(value.get("enabled", True)),
        constant=bool(value.get("constant", False)),
        selective=bool(value.get("selective", True)),
        recursive=bool(value.get("recursive", value.get("recursive_scanning", True))),
        insertion_order=int(value.get("insertion_order", value.get("order", 0))),
        probability=float(value.get("probability", 1.0)),
        scan_depth=int(value.get("scan_depth", 4)),
        scope=str(value.get("scope", "project")),
    )


def evaluate_lore(
    entries: Iterable[LoreCandidate | dict[str, Any]],
    text: str,
    *,
    token_budget: int = 2048,
    random_value: Callable[[], float] | None = None,
    token_counter: Callable[[str], int] | None = None,
    max_activations: int | None = None,
) -> LoreDebug:
    """Activate entries against a scan text, inside a hard budget.

    ``token_counter`` lets the caller supply a provider-aware estimate. The
    default counts *total* words, not unique words: counting unique words
    under-reports real prompt cost by roughly 1.6-2x, which is how a lore
    budget ends up overrun by construction.

    ``max_activations`` caps how many entries may be injected regardless of
    budget. A single weak keyword matching dozens of entries is a recall failure
    wearing a recall success's clothes, and a hard cap makes the failure visible
    in ``considered`` instead of invisible in the prompt.
    """
    count_tokens = token_counter or (lambda value: max(1, len(_tokens(value))))
    candidates = [_candidate_from_value(entry) for entry in entries]
    seen_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not candidate.id or candidate.id in seen_ids:
            candidate.id = f"{candidate.id or 'entry'}-{index}"
        seen_ids.add(candidate.id)
    activated: dict[str, tuple[LoreCandidate, list[str]]] = {}
    considered: list[dict[str, Any]] = []
    considered_index: dict[str, dict[str, Any]] = {}

    def record(entry_id: str, is_active: bool, reasons: list[str]) -> None:
        existing = considered_index.get(entry_id)
        if existing is None:
            item = {"entry_id": entry_id, "activated": is_active, "reasons": reasons}
            considered.append(item)
            considered_index[entry_id] = item
            return
        if is_active and not existing["activated"]:
            existing["activated"] = True
            existing["reasons"] = reasons
            return
        if not is_active:
            for reason in reasons:
                if reason not in existing["reasons"]:
                    existing["reasons"].append(reason)

    initial_text = text.casefold()
    for candidate in candidates:
        reasons: list[str] = []
        if not candidate.enabled:
            record(candidate.id, False, ["disabled"])
            continue
        if candidate.constant:
            reasons.append("constant")
        else:
            primary_hits = [key for key in candidate.primary_keys if _phrase_hits(key, initial_text)]
            if candidate.selective and not primary_hits:
                record(candidate.id, False, ["no primary key"])
                continue
            if primary_hits:
                reasons.append(f"primary key: {primary_hits[0]}")
            if candidate.secondary_keys:
                secondary_hits = [key for key in candidate.secondary_keys if _phrase_hits(key, initial_text)]
                if candidate.selective and not secondary_hits:
                    record(candidate.id, False, ["no secondary key"])
                    continue
                if secondary_hits:
                    reasons.append(f"secondary key: {secondary_hits[0]}")
        if not _passes_probability(candidate, random_value):
            record(candidate.id, False, ["probability"])
            continue
        if candidate.probability < 1.0:
            reasons.append("probability")
        activated[candidate.id] = (candidate, reasons)
        record(candidate.id, True, reasons)

    queue = deque((candidate, 0) for candidate, _ in activated.values())
    while queue:
        candidate, depth = queue.popleft()
        if not candidate.recursive or depth >= max(0, candidate.scan_depth):
            continue
        content_text = candidate.content.casefold()
        for other in candidates:
            if other.id in activated or other.id == candidate.id or not other.enabled:
                continue
            primary_hits = [key for key in other.primary_keys if _phrase_hits(key, content_text)]
            if not primary_hits:
                continue
            if other.secondary_keys:
                secondary_hits = [key for key in other.secondary_keys if _phrase_hits(key, content_text)]
                if other.selective and not secondary_hits:
                    continue
            if not _passes_probability(other, random_value):
                record(other.id, False, ["recursive probability"])
                continue
            recursive_reasons = [f"recursive from {candidate.id}", f"primary key: {primary_hits[0]}"]
            if other.probability < 1.0:
                recursive_reasons.append("probability")
            activated[other.id] = (other, recursive_reasons)
            record(other.id, True, recursive_reasons)
            queue.append((other, depth + 1))

    ordered = sorted(activated.values(), key=lambda item: (item[0].insertion_order, item[0].id))
    result: list[ActivatedLore] = []
    used = 0
    for candidate, reasons in ordered:
        count = count_tokens(candidate.content)
        if max_activations is not None and len(result) >= max_activations:
            record(candidate.id, False, [*reasons, "activation cap reached"])
            continue
        if used + count > token_budget:
            record(candidate.id, False, [*reasons, "token budget exceeded"])
            continue
        used += count
        result.append(
            ActivatedLore(
                entry_id=candidate.id,
                name=candidate.name,
                content=candidate.content,
                scope=candidate.scope,
                reasons=reasons,
                token_count=count,
                insertion_order=candidate.insertion_order,
            )
        )
    return LoreDebug(
        input_text=text,
        token_budget=token_budget,
        used_tokens=used,
        activated=result,
        considered=considered,
        candidates=len(candidates),
    )


evaluate_lorebook = evaluate_lore
