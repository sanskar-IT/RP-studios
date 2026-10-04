from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.app import repository
from services.context.knowledge import (
    KnowledgeKind,
    build_knowledge_view,
    claim_states_fact,
    knowledge_leaks,
    knowledge_table,
)
from services.core.state import StateSnapshot
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"

MURDERER = "the butler planned the murder in the library"


def test_tiers_are_not_interchangeable():
    state = StateSnapshot(
        world_facts={"f1": {"text": "The library exists."}},
        knowledge={"a": {MURDERER}},
        suspicions={"c": {MURDERER}},
    )
    view_a = build_knowledge_view(state, viewer_character_id="a")
    assert view_a.certain_texts() == [MURDERER]
    assert view_a.suspicion_texts() == []

    view_b = build_knowledge_view(state, viewer_character_id="b")
    assert view_b.certain_texts() == []
    assert view_b.suspicion_texts() == []
    assert {fact.fact for fact in view_b.withheld} == {MURDERER}

    view_c = build_knowledge_view(state, viewer_character_id="c")
    assert view_c.certain_texts() == []
    assert view_c.suspicion_texts() == [MURDERER]
    assert {fact.kind for fact in view_c.suspected} == {KnowledgeKind.CHARACTER}


def test_render_marks_suspicion_as_unproven():
    state = StateSnapshot(suspicions={"c": {MURDERER}})
    rendered = build_knowledge_view(state, viewer_character_id="c").render()
    assert "no proof" in rendered
    assert "Do not state, imply, or act on anything outside this list" in rendered


def test_claim_detection_needs_the_full_distinctive_payload():
    assert claim_states_fact("The butler planned the murder in the library.", MURDERER)
    assert not claim_states_fact("The butler serves tea in the library.", MURDERER)
    assert not claim_states_fact("The library is quiet tonight.", MURDERER)


def test_claim_detection_ignores_sentence_punctuation():
    """A user who types a fact has stated it, with or without a full stop.

    Trailing punctuation is not part of the claim, so "she has a brother." and
    "a brother" have to match. If they did not, a user telling the engine
    something it already knew would be scored as the engine leaking it.
    """
    assert claim_states_fact("Alice hesitates, then admits she has a brother.", "Alice has a brother")
    assert claim_states_fact('"Alice has a brother," she says.', "Alice has a brother")
    assert not claim_states_fact("Alice examines the desk.", "Alice has a brother")


def test_knowledge_table_answers_what_everyone_knows():
    state = StateSnapshot(knowledge={"a": {MURDERER}}, suspicions={"c": {MURDERER}})
    table = knowledge_table(state)
    assert table["a"]["certain"] == [MURDERER]
    assert table["c"]["suspected"] == [MURDERER]
    assert table["c"]["certain"] == []


@pytest.mark.asyncio
async def test_butler_scenario_keeps_a_b_and_c_apart(session):
    project = repository.create_project(session, "Butler")
    names = ["Ainsley", "Barrow", "Corvin"]
    characters = [repository.add_character(session, project_id=project.id, name=name) for name in names]
    ainsley, barrow, corvin = characters
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Drawing room",
        participant_ids=[character.id for character in characters],
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    def turn(actor, events):
        return {
            "selected_actor": actor.id,
            "reason": "scripted",
            "prose": "The scene moves on.",
            "actions": [],
            "new_events": events,
            "state_changes": [],
            "open_commitments": [],
        }

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    pipeline = NarrativePipeline(session, ScriptedProvider([staging]))
    await pipeline.stage(
        project_id=project.id,
        premise="Three suspects discuss the murder",
        scene_id=scene.id,
        character_ids=[character.id for character in characters],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    knower = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                turn(ainsley, [{"event_type": "knowledge_acquired", "fact": MURDERER}]),
                turn(ainsley, [{"event_type": "knowledge_suspected", "character_id": corvin.id, "fact": MURDERER}]),
            ]
        ),
    )
    await knower.continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=ainsley.id
    )
    await knower.continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=ainsley.id
    )
    state = repository.current_state(session, project.active_timeline_id)
    assert MURDERER in state.knowledge[ainsley.id]
    assert MURDERER not in state.knowledge.get(barrow.id, set())
    assert MURDERER in state.suspicions.get(corvin.id, set())
    assert MURDERER not in state.knowledge.get(corvin.id, set())

    class RecordingProvider(ScriptedProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.prompts = []

        async def structured(self, messages, schema):
            self.prompts.append(messages[-1].content)
            return await super().structured(messages, schema)

    def record(actor):
        recorder = RecordingProvider(
            [
                {
                    "selected_actor": actor.id,
                    "reason": "scripted",
                    "prose": "Nothing new.",
                    "actions": [],
                    "new_events": [],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        )
        return recorder

    barrow_recorder = record(barrow)
    corvin_recorder = record(corvin)
    ainsley_recorder = record(ainsley)
    await NarrativePipeline(session, barrow_recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=barrow.id
    )
    await NarrativePipeline(session, corvin_recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=corvin.id
    )
    await NarrativePipeline(session, ainsley_recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=ainsley.id
    )

    # Barrow knows nothing and sees nothing.
    assert MURDERER not in barrow_recorder.prompts[0].replace("withheld", "")
    assert knowledge_leaks(
        build_knowledge_view(
            repository.current_state(session, project.active_timeline_id), viewer_character_id=barrow.id
        ),
        [barrow_recorder.prompts[0]],
    ) == []
    # Corvin sees the suspicion labelled as unproven.
    assert "no proof" in corvin_recorder.prompts[0]
    # Ainsley sees the certainty.
    assert MURDERER in ainsley_recorder.prompts[0]


@pytest.mark.asyncio
async def test_overheard_speech_becomes_suspicion_not_certainty(session):
    project = repository.create_project(session, "Overhear")
    detective = repository.add_character(session, project_id=project.id, name="Detective")
    witness = repository.add_character(session, project_id=project.id, name="Witness")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Hall",
        participant_ids=[detective.id, witness.id],
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    pipeline = NarrativePipeline(session, ScriptedProvider([staging]))
    await pipeline.stage(
        project_id=project.id,
        premise="A detective speaks too freely",
        scene_id=scene.id,
        character_ids=[detective.id, witness.id],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    speaker = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "selected_actor": detective.id,
                    "reason": "scripted",
                    "prose": "The detective says the eastern vault hides the missing ledger.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "knowledge_acquired",
                            "character_id": detective.id,
                            "fact": "the eastern vault hides the missing ledger",
                        },
                        {
                            "event_type": "character_spoke",
                            "character_id": detective.id,
                            "text": "The eastern vault hides the missing ledger, mark my words.",
                        },
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        ),
    )
    await speaker.continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=detective.id
    )
    state = repository.current_state(session, project.active_timeline_id)
    assert "the eastern vault hides the missing ledger" in state.knowledge[detective.id]
    assert "the eastern vault hides the missing ledger" in state.suspicions.get(witness.id, set())
    assert "the eastern vault hides the missing ledger" not in state.knowledge.get(witness.id, set())


@pytest.mark.asyncio
async def test_unspoken_knowledge_reaches_no_other_prompt(session):
    project = repository.create_project(session, "Silent")
    one = repository.add_character(session, project_id=project.id, name="One")
    two = repository.add_character(session, project_id=project.id, name="Two")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Cellar",
        participant_ids=[one.id, two.id],
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    pipeline = NarrativePipeline(session, ScriptedProvider([staging]))
    await pipeline.stage(
        project_id=project.id, premise="Two prisoners wait", scene_id=scene.id, character_ids=[one.id, two.id]
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    first = NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "selected_actor": one.id,
                    "reason": "scripted",
                    "prose": "One keeps the secret.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "knowledge_acquired",
                            "character_id": one.id,
                            "fact": "the loose stone conceals a file",
                        }
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        ),
    )
    await first.continue_scene(project_id=project.id, scene_id=scene.id, actor_character_id=one.id)

    class RecordingProvider(ScriptedProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.prompts = []

        async def structured(self, messages, schema):
            self.prompts.append(messages[-1].content)
            return await super().structured(messages, schema)

    recorder = RecordingProvider(
        [
            {
                "selected_actor": two.id,
                "reason": "scripted",
                "prose": "Two waits.",
                "actions": [],
                "new_events": [],
                "state_changes": [],
                "open_commitments": [],
            }
        ]
    )
    await NarrativePipeline(session, recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=two.id
    )
    assert "the loose stone conceals a file" not in recorder.prompts[0]


SECRET_LINE = "The loose stone hides a file naming the captain as the traitor."


@pytest.mark.parametrize(
    "event_type",
    ["character_spoke", "user_action", "ai_action"],
    ids=["character_spoke", "user_action", "ai_action"],
)
def test_speech_content_is_not_kept_in_a_persisted_last_action(event_type):
    """``last_action`` must not become a permanent copy of what was said.

    The fact that a character spoke is public; the content is not. Storing the
    raw line meant the secret was re-projected into every later state snapshot and
    re-inserted into every subsequent prompt for every character — indefinitely,
    long after the moment of the utterance, and regardless of who was in earshot.
    """
    from services.core.events import AI_ACTION, CHARACTER_SPOKE, USER_ACTION
    from services.core.state import StateSnapshot, apply_event

    by_name = {
        "character_spoke": CHARACTER_SPOKE,
        "user_action": USER_ACTION,
        "ai_action": AI_ACTION,
    }
    snapshot = StateSnapshot.from_dict(
        {"characters": {"one": {"character_id": "one", "name": "One"}}}
    )
    updated = apply_event(
        snapshot,
        by_name[event_type],
        {"character_id": "one", "text": SECRET_LINE},
    )
    stored = updated.characters["one"].get("last_action") or ""
    assert SECRET_LINE not in stored
    # The event is still recorded — the character did do something.
    assert stored
    assert updated.characters["one"]["last_action_type"] == by_name[event_type]


def test_an_observable_action_is_kept_so_continuity_survives():
    """``character_performed_action`` is a visible act, not a private utterance.

    Dropping this too would leave characters with no memory of having moved,
    taken or dropped anything, which is a real continuity cost for no isolation
    win.
    """
    from services.core.events import CHARACTER_PERFORMED_ACTION
    from services.core.state import StateSnapshot, apply_event

    snapshot = StateSnapshot.from_dict(
        {"characters": {"one": {"character_id": "one", "name": "One"}}}
    )
    updated = apply_event(
        snapshot,
        CHARACTER_PERFORMED_ACTION,
        {"character_id": "one", "action": "takes the locket from the table"},
    )
    assert "locket" in updated.characters["one"]["last_action"]


@pytest.mark.asyncio
async def test_a_secret_spoken_aloud_does_not_survive_in_the_projected_state(session):
    """End-to-end: One says the secret, then the scene is projected again.

    This closes the *permanent* half of the leak. The event log still holds the
    utterance — it must, it is the record of what happened — but nothing
    derived from it carries the words forward, so the secret cannot reappear on
    turn 5 having been quietly re-projected on turns 2, 3 and 4.
    """
    project = repository.create_project(session, "Leak")
    one = repository.add_character(session, project_id=project.id, name="One")
    two = repository.add_character(session, project_id=project.id, name="Two")
    outsider = repository.add_character(session, project_id=project.id, name="Outsider")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Passage",
        participant_ids=[one.id, two.id],
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    await NarrativePipeline(session, ScriptedProvider([staging])).stage(
        project_id=project.id,
        premise="Two conspirators speak, one bystander waits",
        scene_id=scene.id,
        character_ids=[one.id, two.id, outsider.id],
    )
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    await NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "selected_actor": one.id,
                    "reason": "scripted",
                    "prose": "One leans in and says it plainly.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "character_spoke",
                            "character_id": one.id,
                            "text": SECRET_LINE,
                        }
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        ),
    ).continue_scene(project_id=project.id, scene_id=scene.id, actor_character_id=one.id)

    class RecordingProvider(ScriptedProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.prompts = []

        async def structured(self, messages, schema):
            self.prompts.append(messages[-1].content)
            return await super().structured(messages, schema)

    recorder = RecordingProvider(
        [
            {
                "selected_actor": outsider.id,
                "reason": "scripted",
                "prose": "The bystander waits.",
                "actions": [],
                "new_events": [],
                "state_changes": [],
                "open_commitments": [],
            }
        ]
    )
    result = await NarrativePipeline(session, recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=outsider.id
    )

    # The secret must not be carried in any character's persisted fields.
    assert SECRET_LINE not in json.dumps(result.state)
    for character in (result.state or {}).get("characters", {}).values():
        assert SECRET_LINE not in json.dumps(character)


@pytest.mark.asyncio
async def test_recent_events_still_expose_speech_within_the_window(session):
    """Tracked limitation — the *in-window* half of the leak is not yet fixed.

    ``last_action`` no longer retains speech, but ``_recent_events`` renders the
    last few events' text to every character regardless of who was present, and
    ``_overhear`` grants no suspicion for a paraphrase. So a bystander prompted
    in the same scene still sees the line.

    This test pins the current behaviour on purpose: it fails when someone fixes
    ``_recent_events`` without also recording the change, and it makes the
    remaining gap visible instead of leaving it implied by the passing test
    above. The principled fix is to make ``_overhear`` fail *open* toward
    suspicion, which is a design change with its own cost — suspicion growth is
    already unbounded — so it is deliberately out of scope for this milestone.
    """
    project = repository.create_project(session, "Window")
    one = repository.add_character(session, project_id=project.id, name="One")
    outsider = repository.add_character(session, project_id=project.id, name="Outsider")
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Passage",
        participant_ids=[one.id, outsider.id],
    )
    session.commit()

    from services.narrative.pipeline import NarrativePipeline

    staging = json.loads((FIXTURES / "staging.json").read_text(encoding="utf-8"))
    await NarrativePipeline(session, ScriptedProvider([staging])).stage(
        project_id=project.id,
        premise="One whispers, one bystander waits",
        scene_id=scene.id,
        character_ids=[one.id, outsider.id],
    )
    pipeline = NarrativePipeline(session, ScriptedProvider([]))
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    await NarrativePipeline(
        session,
        ScriptedProvider(
            [
                {
                    "selected_actor": one.id,
                    "reason": "scripted",
                    "prose": "One says it quietly.",
                    "actions": [],
                    "new_events": [
                        {
                            "event_type": "character_spoke",
                            "character_id": one.id,
                            "text": SECRET_LINE,
                        }
                    ],
                    "state_changes": [],
                    "open_commitments": [],
                }
            ]
        ),
    ).continue_scene(project_id=project.id, scene_id=scene.id, actor_character_id=one.id)

    class RecordingProvider(ScriptedProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.prompts = []

        async def structured(self, messages, schema):
            self.prompts.append(messages[-1].content)
            return await super().structured(messages, schema)

    recorder = RecordingProvider(
        [
            {
                "selected_actor": outsider.id,
                "reason": "scripted",
                "prose": "The bystander waits.",
                "actions": [],
                "new_events": [],
                "state_changes": [],
                "open_commitments": [],
            }
        ]
    )
    await NarrativePipeline(session, recorder).continue_scene(
        project_id=project.id, scene_id=scene.id, actor_character_id=outsider.id
    )
    assert SECRET_LINE in recorder.prompts[0], (
        "in-window recent-events exposure was fixed; update this test and record "
        "the change in docs/KNOWLEDGE_MODEL.md"
    )
