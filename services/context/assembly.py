"""Context priority ladder and budgeted assembly.

A generation prompt is not a document to be truncated. It is a set of
independently sourced facts with different consequences when they are missing,
and the assembler treats it that way: every item is tagged with a priority, a
source, a retrieval method, and a cost, and the budget is spent from the top
down. When the budget runs out the lowest-priority material is dropped or
summarised first, and the reason is recorded so the developer panel can answer
"why is this not in the prompt?".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from services.context.tokens import (
    HEURISTIC,
    TokenBudget,
    TokenEstimator,
    estimate_tokens,
)


class ContextPriority(IntEnum):
    """Lower number wins when the budget is exhausted.

    The ladder answers "what must survive if everything else is cut", which is a
    narrative question rather than a technical one.
    """

    SYSTEM_RULES = 0
    USER_INSTRUCTION = 1
    SCENE_STATE = 2
    CHARACTER_STATE = 3
    CHARACTER_KNOWLEDGE = 4
    COMMITMENTS = 5
    RECENT_EVENTS = 6
    MEMORIES = 7
    LORE = 8
    HISTORY = 9
    FLAVOR = 10


PRIORITY_LABELS: dict[int, str] = {
    ContextPriority.SYSTEM_RULES: "Hard system rules",
    ContextPriority.USER_INSTRUCTION: "Current user instruction",
    ContextPriority.SCENE_STATE: "Current scene state",
    ContextPriority.CHARACTER_STATE: "Active character state",
    ContextPriority.CHARACTER_KNOWLEDGE: "Character knowledge",
    ContextPriority.COMMITMENTS: "Active commitments",
    ContextPriority.RECENT_EVENTS: "Relevant recent events",
    ContextPriority.MEMORIES: "Relevant memories",
    ContextPriority.LORE: "Activated lorebook entries",
    ContextPriority.HISTORY: "Older historical context",
    ContextPriority.FLAVOR: "Optional flavor context",
}

# Components that describe the turn itself. They are never dropped, because a
# prompt without them does not describe the turn, it invents one.
REQUIRED_COMPONENTS: frozenset[str] = frozenset({"system_rules", "user_instruction", "scene_state"})

# Drop reasons, kept as constants so the API, the UI, and the docs agree.
REASON_BUDGET = "budget"
REASON_SUMMARISED = "summarised"
REASON_LOW_SCORE = "low_score"
REASON_EMPTY = "empty"
REASON_REQUIRED_OVERFLOW = "required_overflow"


class ContextOverflowError(ValueError):
    """Required components alone exceed the provider's input budget.

    A ``ValueError`` rather than a bare ``RuntimeError`` so the API reports an
    oversized turn as a 422 with the numbers attached. This is deliberately an
    error rather than a silent trim: truncating a hard rule or the current
    instruction would produce a confidently wrong turn.
    """

    def __init__(self, message: str, *, required_tokens: int, budget: int) -> None:
        super().__init__(message)
        self.required_tokens = required_tokens
        self.budget = budget


@dataclass
class ContextItem:
    """One independently droppable line of context."""

    key: str
    text: str
    tokens: int = 0
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def ensure_tokens(self, estimator: TokenEstimator) -> None:
        if not self.tokens:
            self.tokens = estimate_tokens(self.text, estimator)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "tokens": self.tokens,
            "score": round(self.score, 4),
            "metadata": self.metadata,
        }


@dataclass
class ContextComponent:
    """A group of items that share a priority, a source, and a retrieval method."""

    key: str
    label: str
    priority: ContextPriority
    source: str
    retrieval: str
    items: list[ContextItem] = field(default_factory=list)
    summary: str = ""
    can_summarise: bool = False
    stale: bool = False
    note: str = ""
    included: bool = True
    reason: str = "included"
    dropped_items: int = 0
    tokens: int = 0

    @property
    def required(self) -> bool:
        return self.key in REQUIRED_COMPONENTS

    def render(self) -> str:
        if self.items:
            lines = [f"- {item.text}" for item in self.items]
            if self.dropped_items:
                lines.append(f"- … {self.dropped_items} lower-ranked item(s) omitted for budget")
            return "\n".join(lines)
        if self.summary and self.reason == REASON_SUMMARISED:
            return f"- {self.summary}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "priority": int(self.priority),
            "priority_label": PRIORITY_LABELS.get(self.priority, str(self.priority)),
            "source": self.source,
            "retrieval": self.retrieval,
            "tokens": self.tokens,
            "required": self.required,
            "stale": self.stale,
            "note": self.note,
            "included": self.included,
            "reason": self.reason,
            "item_count": len(self.items),
            "dropped_items": self.dropped_items,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class ContextAssembly:
    """The result of budgeted assembly, with the full accounting attached."""

    components: list[ContextComponent]
    budget: TokenBudget
    estimator_name: str
    system_tokens: int = 0
    contract_tokens: int = 0

    @property
    def included(self) -> list[ContextComponent]:
        return [component for component in self.components if component.included]

    @property
    def excluded(self) -> list[ContextComponent]:
        return [component for component in self.components if not component.included]

    @property
    def component_tokens(self) -> int:
        return sum(component.tokens for component in self.components)

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.component_tokens + self.contract_tokens

    @property
    def remaining_tokens(self) -> int:
        return self.budget.max_input_tokens - self.total_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "component_tokens": self.component_tokens,
            "system_tokens": self.system_tokens,
            "contract_tokens": self.contract_tokens,
            "max_input_tokens": self.budget.max_input_tokens,
            "remaining_tokens": self.remaining_tokens,
            "estimator": self.estimator_name,
            "budget": self.budget.to_dict(),
            "components": [component.to_dict() for component in self.components],
            "by_priority": {
                PRIORITY_LABELS.get(priority, str(priority)): sum(
                    component.tokens
                    for component in self.components
                    if component.included and component.priority == priority
                )
                for priority in sorted({component.priority for component in self.components})
            },
            "excluded": [
                {
                    "key": component.key,
                    "label": component.label,
                    "reason": component.reason,
                    "dropped_items": component.dropped_items,
                    "would_cost": component.tokens,
                }
                for component in self.excluded
            ],
        }

    def prompt_body(self) -> str:
        """Render included components in priority order, highest priority first."""
        blocks: list[str] = []
        for component in sorted(self.included, key=lambda item: (int(item.priority), item.key)):
            if component.key == "system_rules":
                continue
            body = component.render()
            if not body:
                continue
            header = component.label.upper()
            blocks.append(f"## {header}\n{body}")
        return "\n\n".join(blocks)

    def debug_lines(self) -> list[str]:
        lines = ["Context composition", "-" * 20]
        for component in sorted(self.components, key=lambda item: int(item.priority)):
            state = "IN " if component.included else "OUT"
            reason = "" if component.included else f"  ({component.reason})"
            lines.append(
                f"{state} P{int(component.priority):<2} {component.label:<26} "
                f"{component.tokens:>6} tok  {component.source}{reason}"
            )
        lines.append("-" * 20)
        lines.append(
            f"total {self.total_tokens} / {self.budget.max_input_tokens} "
            f"input tokens ({self.estimator_name})"
        )
        return lines


def _measure(component: ContextComponent, estimator: TokenEstimator) -> int:
    total = 0
    for item in component.items:
        item.ensure_tokens(estimator)
        total += item.tokens
    component.tokens = total
    return total


def _fit_component(
    component: ContextComponent,
    allowance: int,
    estimator: TokenEstimator,
) -> None:
    """Keep the highest-scoring items that fit, recording how many were dropped."""
    ordered = sorted(component.items, key=lambda item: (-item.score, item.key))
    kept: list[ContextItem] = []
    dropped = 0
    used = 0
    for item in ordered:
        item.ensure_tokens(estimator)
        if used + item.tokens <= allowance:
            kept.append(item)
            used += item.tokens
        else:
            dropped += 1
    component.items = kept
    component.dropped_items = dropped
    component.tokens = used


def assemble(
    components: Sequence[ContextComponent],
    *,
    budget: TokenBudget,
    estimator: TokenEstimator | None = None,
    system_prompt: str = "",
    contract_prompt: str = "",
    summary_builder: Callable[[ContextComponent], str] | None = None,
) -> ContextAssembly:
    """Fit components into the budget by priority, never by blind truncation."""
    active = estimator or HEURISTIC
    ordered = sorted(components, key=lambda item: (int(item.priority), item.key))
    assembly = ContextAssembly(
        components=list(ordered),
        budget=budget,
        estimator_name=active.name,
        system_tokens=estimate_tokens(system_prompt, active),
        contract_tokens=estimate_tokens(contract_prompt, active),
    )
    for component in ordered:
        _measure(component, active)
    ceiling = budget.max_input_tokens - assembly.system_tokens - assembly.contract_tokens
    required_tokens = sum(
        component.tokens for component in ordered if component.required
    )
    if required_tokens > ceiling:
        for component in ordered:
            component.included = component.required
            component.reason = REASON_REQUIRED_OVERFLOW if component.required else REASON_BUDGET
        raise ContextOverflowError(
            "Required context components exceed the provider input budget: "
            f"{required_tokens} tokens needed, {ceiling} available",
            required_tokens=required_tokens,
            budget=ceiling,
        )

    used = required_tokens
    optional = [component for component in ordered if not component.required]
    # Lowest priority first, so the budget is reclaimed from the least valuable
    # material before anything that carries narrative consequence.
    for component in sorted(optional, key=lambda item: -int(item.priority)):
        component.included = True
        component.reason = "included"
        if used + component.tokens <= ceiling:
            used += component.tokens
            continue
        if component.can_summarise and (summary_builder is not None or component.summary):
            summary = summary_builder(component) if summary_builder else component.summary
            cost = estimate_tokens(summary, active)
            if used + cost <= ceiling:
                component.dropped_items = len(component.items)
                component.items = []
                component.summary = summary
                component.tokens = cost
                component.reason = REASON_SUMMARISED
                used += cost
                continue
        if not component.items:
            component.included = False
            component.reason = REASON_EMPTY
            continue
        _fit_component(component, max(0, ceiling - used), active)
        if not component.items:
            component.included = False
            component.reason = REASON_BUDGET
            component.tokens = 0
            continue
        component.reason = REASON_LOW_SCORE if component.dropped_items else "included_trimmed"
        used += component.tokens
    return assembly


def generation_prompt(assembly: ContextAssembly, *, instruction: str) -> str:
    """The final user message.

    The instruction is repeated at the end of the prompt as well as holding its
    own P1 slot. A long context attenuates late material, and a directive that
    exists only in the middle of 8 000 tokens is a directive that gets ignored.
    """
    body = assembly.prompt_body()
    parts = [body] if body else []
    parts.append(
        "\n".join(
            [
                "## Output contract",
                "Return the next narrative beat as prose, and any resulting state as structured",
                "events. Do not invent facts outside the material above. If the material is",
                "insufficient, describe the uncertainty instead of resolving it.",
            ]
        )
    )
    parts.append(f"## Current instruction\n{instruction or 'Continue the scene.'}")
    return "\n\n".join(part for part in parts if part)


@dataclass
class GenerationContextRequest:
    """Everything the assembler needs, gathered by the pipeline.

    Kept as an explicit request object so the assembler stays free of database
    access and can be exercised in isolation by tests and the harness.
    """

    system_prompt: str
    scene_block: str
    instruction: str
    character_state: str = ""
    knowledge: str = ""
    commitments: str = ""
    recent_events: Sequence[str] = ()
    memories: Sequence[tuple[str, float]] = ()
    lore: Sequence[tuple[str, str]] = ()
    history: str = ""
    flavor: str = ""
    estimator: TokenEstimator = HEURISTIC
    budget: TokenBudget = TokenBudget()
    commitment_metadata: list[dict[str, Any]] = field(default_factory=list)
    stale_notes: dict[str, str] = field(default_factory=dict)


def build_generation_context(request: GenerationContextRequest) -> tuple[ContextAssembly, str]:
    """Assemble a generation context and return it with the rendered prompt."""
    estimator = request.estimator
    system_component = ContextComponent(
        key="system_rules",
        label="System rules",
        priority=ContextPriority.SYSTEM_RULES,
        source="pipeline.system",
        retrieval="literal",
        items=[ContextItem(key="system", text=request.system_prompt)],
    )
    instruction_component = ContextComponent(
        key="user_instruction",
        label="User instruction",
        priority=ContextPriority.USER_INSTRUCTION,
        source="request.user_input",
        retrieval="literal",
        items=[ContextItem(key="instruction", text=request.instruction or "Continue the scene.")],
    )
    scene_component = ContextComponent(
        key="scene_state",
        label="Scene state",
        priority=ContextPriority.SCENE_STATE,
        source="Scene.staging",
        retrieval="direct read",
        items=[ContextItem(key="scene", text=line) for line in request.scene_block.splitlines() if line.strip()],
    )
    components: list[ContextComponent] = [system_component, instruction_component, scene_component]

    def optional(
        key: str,
        label: str,
        priority: ContextPriority,
        source: str,
        retrieval: str,
        text: str,
        *,
        summarisable: bool = False,
        can_stale: bool = False,
    ) -> ContextComponent:
        return ContextComponent(
            key=key,
            label=label,
            priority=priority,
            source=source,
            retrieval=retrieval,
            items=[ContextItem(key=key, text=line) for line in text.splitlines() if line.strip()],
            summary=text,
            can_summarise=summarisable,
            stale=can_stale and key in request.stale_notes,
            note=request.stale_notes.get(key, ""),
        )

    components.append(
        optional(
            "character_state",
            "Character state",
            ContextPriority.CHARACTER_STATE,
            "StateSnapshot (actor slice)",
            "checkpoint read",
            request.character_state,
        )
    )
    components.append(
        optional(
            "character_knowledge",
            "Character knowledge",
            ContextPriority.CHARACTER_KNOWLEDGE,
            "StateSnapshot.knowledge / .suspicions",
            "checkpoint read, actor filter",
            request.knowledge,
        )
    )
    commitment_lines = [
        f"{item['description']} [{item['status']}]" for item in request.commitment_metadata
    ]
    components.append(
        optional(
            "commitments",
            "Active commitments",
            ContextPriority.COMMITMENTS,
            "StoryCommitment",
            "status + timeline ancestry filter",
            "\n".join(commitment_lines) if commitment_lines else request.commitments,
        )
    )
    event_component = ContextComponent(
        key="recent_events",
        label="Recent events",
        priority=ContextPriority.RECENT_EVENTS,
        source="Event (timeline tail)",
        retrieval="reverse sequence read, reverse lexical rank",
        items=[
            ContextItem(key=f"event:{index}", text=line, score=float(index))
            for index, line in enumerate(request.recent_events)
        ],
    )
    components.append(event_component)
    memory_component = ContextComponent(
        key="memories",
        label="Relevant memories",
        priority=ContextPriority.MEMORIES,
        source="Memory",
        retrieval="lexical rank, class weight, ancestry scope",
        items=[
            ContextItem(key=f"memory:{index}", text=line, score=score)
            for index, (line, score) in enumerate(request.memories)
        ],
    )
    components.append(memory_component)
    lore_component = ContextComponent(
        key="lorebook",
        label="Activated lore",
        priority=ContextPriority.LORE,
        source="LorebookEntry",
        retrieval="key match + recursion, budgeted",
        items=[
            ContextItem(key=f"lore:{name}", text=f"{name}: {content}", score=float(index))
            for index, (name, content) in enumerate(request.lore)
        ],
        can_summarise=False,
    )
    components.append(lore_component)
    if request.history:
        components.append(
            optional(
                "history",
                "Historical digest",
                ContextPriority.HISTORY,
                "Event (timeline head)",
                "counted digest of superseded events",
                request.history,
                summarisable=True,
                can_stale=True,
            )
        )
    if request.flavor:
        components.append(
            optional(
                "flavor",
                "Optional flavor",
                ContextPriority.FLAVOR,
                "Scene.staging.flavor",
                "optional read",
                request.flavor,
                can_stale=True,
            )
        )

    contract = (
        "Return the next narrative beat as prose, and any resulting state as structured events. "
        "Do not invent facts outside the material above. If the material is insufficient, describe "
        "the uncertainty instead of resolving it."
    )
    assembly = assemble(
        components,
        budget=request.budget,
        estimator=estimator,
        system_prompt=request.system_prompt,
        contract_prompt=contract,
    )
    return assembly, generation_prompt(assembly, instruction=request.instruction)
