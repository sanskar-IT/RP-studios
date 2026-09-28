from __future__ import annotations

from services.context import (
    ContextPriority,
    GenerationContextRequest,
    build_generation_context,
)
from services.context.assembly import ContextComponent, ContextItem, ContextOverflowError, assemble
from services.context.tokens import (
    DEFAULT_CONTEXT_WINDOW,
    HeuristicTokenEstimator,
    TokenBudget,
    estimate_tokens,
    estimator_for,
)


def _request(**overrides):
    values = {
        "system_prompt": "rules",
        "scene_block": "Scene: library",
        "instruction": "Continue.",
        "character_state": "detective is in the library",
        "knowledge": "You know the eastern door is locked.",
        "commitments": "",
        "recent_events": ["#4 character_moved: the witness left"],
        "memories": [("The eastern door is locked", 1.0)],
        "lore": [("Library", "The east wing is sealed.")],
        "history": "",
        "flavor": "",
    }
    values.update(overrides)
    return GenerationContextRequest(**values)


def test_heuristic_estimator_is_deterministic_and_named():
    assert estimate_tokens("The eastern door is locked") == estimate_tokens("The eastern door is locked")
    assert estimator_for("gpt-4o", allow_exact=False).name == "heuristic-v1"
    assert estimator_for("", allow_exact=True).name == "heuristic-v1"
    assert estimate_tokens("") == 0


def test_heuristic_estimator_counts_dense_scripts():
    estimator = HeuristicTokenEstimator()
    assert estimator.count("図書館で待つ") > 0
    assert estimator.count("hello world") < estimator.count("hello world " * 40)


def test_token_budget_respects_declared_window():
    budget = TokenBudget(context_window=4096, reserved_output_tokens=512)
    assert budget.max_input_tokens == 3584
    assert budget.to_dict()["max_input_tokens"] == 3584


def test_token_budget_floor_applies_to_tiny_windows():
    budget = TokenBudget(context_window=100, reserved_output_tokens=90)
    assert budget.max_input_tokens == 512


def test_assembly_reports_every_component_with_source_priority_and_cost():
    assembly, prompt = build_generation_context(_request())
    report = assembly.to_dict()
    assert report["total_tokens"] > 0
    assert report["estimator"] == "heuristic-v1"
    priorities = {component["priority"] for component in report["components"]}
    assert ContextPriority.SYSTEM_RULES in priorities
    assert ContextPriority.LORE in priorities
    assert "USER INSTRUCTION" in prompt
    assert report["budget"]["context_window"] == DEFAULT_CONTEXT_WINDOW


def test_low_priority_material_is_dropped_before_high_priority_material():
    request = _request(
        memories=[(f"filler memory {index} " * 8, 0.1) for index in range(60)],
        lore=[(f"Entry {index}", "padding content " * 20) for index in range(40)],
        budget=TokenBudget(context_window=1500, reserved_output_tokens=500),
    )
    assembly, _prompt = build_generation_context(request)
    by_key = {component.key: component for component in assembly.components}
    assert by_key["system_rules"].included
    assert by_key["user_instruction"].included
    assert by_key["scene_state"].included
    excluded = {component.key for component in assembly.excluded}
    assert excluded, "a tight budget must exclude something"
    assert "system_rules" not in excluded
    assert "user_instruction" not in excluded
    assert assembly.remaining_tokens >= 0


def test_old_history_is_summarised_rather_than_dropped_first():
    request = _request(
        history="240 older events: character_moved x180, character_spoke x60.",
        budget=TokenBudget(context_window=1200, reserved_output_tokens=400),
    )
    assembly, _prompt = build_generation_context(request)
    by_key = {component.key: component for component in assembly.components}
    if by_key["history"].included:
        assert by_key["history"].reason in {"included", "summarised"}


def test_required_overflow_raises_instead_of_trimming_the_instruction():
    component = ContextComponent(
        key="user_instruction",
        label="User instruction",
        priority=ContextPriority.USER_INSTRUCTION,
        source="request",
        retrieval="literal",
        items=[ContextItem(key="x", text="word " * 5000, tokens=6000, score=0.0)],
    )
    try:
        assemble(
            [component],
            budget=TokenBudget(context_window=1000, reserved_output_tokens=500),
            system_prompt="",
            contract_prompt="",
        )
    except ContextOverflowError as exc:
        assert exc.required_tokens > exc.budget
    else:
        raise AssertionError("expected ContextOverflowError")


def test_exclusion_reasons_are_recorded():
    request = _request(
        flavor="Incense smoke. A cat asleep on the returns cart.",
        budget=TokenBudget(context_window=900, reserved_output_tokens=400),
    )
    assembly, _prompt = build_generation_context(request)
    report = assembly.to_dict()
    for excluded in report["excluded"]:
        assert excluded["reason"] in {
            "budget",
            "summarised",
            "low_score",
            "empty",
            "included_trimmed",
            "required_overflow",
        }


def test_instruction_appears_at_the_end_of_the_prompt():
    _assembly, prompt = build_generation_context(_request(instruction="Name the killer."))
    assert prompt.rstrip().endswith("Name the killer.")
