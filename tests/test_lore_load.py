from __future__ import annotations

import pytest

from apps.api.app import repository
from services.core.models import Lorebook, LorebookEntry
from services.lorebook.evaluator import LoreCandidate, evaluate_lore
from services.narrative.pipeline import LORE_ACTIVATION_CAP, NarrativePipeline


def entry(index: int, **overrides) -> LoreCandidate:
    values = {
        "id": f"e{index}",
        "name": f"Entry {index}",
        "content": f"Lore content number {index} about the library and its east wing.",
        "primary_keys": ["library"],
        "insertion_order": index,
    }
    values.update(overrides)
    return LoreCandidate(**values)


def test_activation_cap_bounds_a_weak_keyword():
    entries = [entry(index) for index in range(120)]
    result = evaluate_lore(entries, "The group enters the library.", max_activations=8)
    assert len(result.activated) == 8
    assert any("activation cap reached" in item["reasons"] for item in result.considered)
    assert result.to_dict()["candidates"] == 120
    assert result.to_dict()["activated_count"] == 8


def test_token_budget_still_binds_below_the_cap():
    entries = [entry(index) for index in range(10)]
    result = evaluate_lore(entries, "library", token_budget=4)
    assert len(result.activated) < 10
    assert any("token budget exceeded" in item["reasons"] for item in result.considered)


def test_missed_entries_are_reported():
    entries = [entry(0), entry(1, enabled=False), entry(2, primary_keys=["harbour"])]
    result = evaluate_lore(entries, "The group enters the library.")
    payload = result.to_dict()
    assert payload["activated_count"] == 1
    assert any(item["entry_id"] == "e1" for item in payload["missed"])


def test_exact_counter_measures_total_words_not_unique_words():
    content = "east wing east wing east wing locked locked locked"
    result = evaluate_lore(
        [LoreCandidate(id="x", name="X", content=content, primary_keys=["wing"])],
        "wing",
        token_counter=lambda value: max(1, len(value.split())),
    )
    assert result.activated[0].token_count == 9


@pytest.mark.asyncio
async def test_turn_with_a_large_lorebook_stays_inside_the_lore_share(session):
    project = repository.create_project(session, "Lore Load")
    character = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id],
    )
    book = Lorebook(project_id=project.id, name="Big book", extra_data={"token_budget": 200_000})
    session.add(book)
    session.flush()
    for index in range(200):
        session.add(
            LorebookEntry(
                lorebook_id=book.id,
                name=f"Filler {index}",
                content=f"Filler lore number {index} about the library and east wing staff.",
                primary_keys=["library", "east wing"],
                insertion_order=index,
            )
        )
    session.commit()

    from services.providers.base import ScriptedProvider

    pipeline = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "location": "library",
                    "time": "night",
                    "characters_present": ["Witness"],
                    "objective": "Wait.",
                    "initial_conditions": [],
                    "environmental_assumptions": [],
                    "potential_consequences": [],
                    "canon_conflicts": [],
                },
                {
                    "selected_actor": character.id,
                    "reason": "only participant",
                    "prose": "The witness waits in the library.",
                    "actions": [],
                    "new_events": [],
                    "state_changes": [],
                    "open_commitments": [],
                },
            ]
        ),
    )
    await pipeline.stage(
        project_id=project.id, premise="A witness waits in the library", scene_id=scene.id, character_ids=[character.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    result = await pipeline.continue_scene(project_id=project.id, scene_id=scene.id)

    lore_component = next(
        component for component in result.context_debug["components"] if component["key"] == "lorebook"
    )
    share = int(result.context_debug["max_input_tokens"] * 0.2)
    assert lore_component["tokens"] <= share + 256
    assert len(result.lore_debug["activated"]) <= LORE_ACTIVATION_CAP
    assert result.context_debug["total_tokens"] <= result.context_debug["max_input_tokens"]
    assert result.lore_debug["candidates"] == 200
