from __future__ import annotations

from services.lorebook.evaluator import LoreCandidate, evaluate_lore


def test_primary_secondary_constant_and_debug_activation():
    entries = [
        LoreCandidate(id="constant", name="World", content="The moon is red.", constant=True, recursive=False),
        LoreCandidate(
            id="library",
            name="Library",
            content="The library has a locked east wing.",
            primary_keys=["library"],
            secondary_keys=["east wing"],
        ),
        LoreCandidate(id="unused", name="Unused", content="Never included.", primary_keys=["moon"]),
    ]
    result = evaluate_lore(entries, "The group enters the library and notices the east wing.", token_budget=100)
    assert [entry.entry_id for entry in result.activated] == ["constant", "library"]
    assert result.considered[-1]["reasons"] == ["no primary key"]
    assert result.to_dict()["activated"][1]["scope"] == "project"


def test_budget_and_order_are_enforced():
    entries = [
        LoreCandidate(id="late", name="Late", content="late entry", primary_keys=["library"], insertion_order=20),
        LoreCandidate(id="early", name="Early", content="early entry", primary_keys=["library"], insertion_order=1),
    ]
    result = evaluate_lore(entries, "library", token_budget=2)
    assert [entry.entry_id for entry in result.activated] == ["early"]


def test_probability_is_deterministic_when_random_is_injected():
    entry = LoreCandidate(id="chance", name="Chance", content="A hidden detail.", primary_keys=["library"], probability=0.2)
    activated = evaluate_lore([entry], "library", random_value=lambda: 0.1)
    rejected = evaluate_lore([entry], "library", random_value=lambda: 0.9)
    assert len(activated.activated) == 1
    assert not rejected.activated


def test_recursive_scan_activates_secondary_entry():
    entries = [
        LoreCandidate(id="root", name="Root", content="The archive points to the index.", primary_keys=["archive"]),
        LoreCandidate(id="child", name="Child", content="The index is a living map.", primary_keys=["index"]),
    ]
    result = evaluate_lore(entries, "archive", token_budget=100)
    assert {entry.entry_id for entry in result.activated} == {"root", "child"}
    assert "recursive" in result.to_dict()["considered"][-1]["reasons"][0]
