from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.api.app import repository
from services.core.enums import CommitmentStatus, SceneStatus
from services.core.models import Generation
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures" / "provider"


def fixture_provider(*names: str, substitutions: dict[str, str] | None = None) -> ScriptedProvider:
    responses = [json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in names]
    return ScriptedProvider(responses, substitutions=substitutions)


def create_project_scene(session, names: list[str]):
    project = repository.create_project(session, "Vertical Slice")
    characters = [
        repository.add_character(session, project_id=project.id, name=name)
        for name in names
    ]
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Library",
        participant_ids=[character.id for character in characters],
    )
    session.commit()
    return project, characters, scene


@pytest.mark.asyncio
async def test_full_narrative_loop_creates_events_state_memory_checkpoint_and_fork(session):
    project, characters, scene = create_project_scene(session, ["Detective", "Witness", "General"])
    detective, witness, general = characters
    pipeline = NarrativePipeline(
        session,
        fixture_provider(
            "staging.json",
            "normal_turn.json",
            substitutions={"actor_name": detective.name},
        ),
    )
    await pipeline.direct(
        project_id=project.id,
        scene_id=scene.id,
        text="The General will betray the Emperor",
    )
    staged = await pipeline.stage(
        project_id=project.id,
        premise="I want a suspicious murder to happen in a library, and I want the other characters to panic.",
        scene_id=scene.id,
        character_ids=[character.id for character in characters],
    )
    assert staged.structured_output["status"] == "proposed"
    assert staged.structured_output["staging_revision"] == 1
    assert staged.structured_output["scene_id"] == scene.id
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id, staging_revision=1)
    turn = await pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        user_input="The detective moves first.",
    )
    assert turn.state["characters"][detective.id]["location_id"] == "hallway"
    assert turn.checkpoint_node_id
    assert turn.planner_context["commitments"][0]["description"] == "The General will betray the Emperor"
    assert turn.structured_output["committed_event_ids"]
    generation = session.get(Generation, turn.generation_id)
    assert generation.status == "completed"
    assert generation.checkpoint_node_id == turn.checkpoint_node_id
    memory_rows = repository.list_events(session, project.active_timeline_id)
    assert any(event.event_type == "character_moved" for event in memory_rows)
    assert session.scalar(
        select(Generation).where(Generation.id == turn.generation_id).where(Generation.checkpoint_node_id == turn.checkpoint_node_id)
    )
    checkpoints = repository.list_checkpoints(session, project.active_timeline_id)
    assert checkpoints[-1]["id"] == turn.checkpoint_node_id
    assert checkpoints[-1]["generation_id"] == turn.generation_id

    original_event_ids = [event.id for event in repository.list_events(session, project.active_timeline_id)]
    branch = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=turn.checkpoint_node_id,
        name="Branch where the detective dies",
    )
    session.commit()
    branch_scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=branch.id,
        title="Library branch",
        participant_ids=[detective.id, witness.id],
    )
    branch_scene.status = SceneStatus.STAGED.value
    session.add(branch_scene)
    session.commit()
    branch_pipeline = NarrativePipeline(
        session,
        fixture_provider("staging.json", "death_turn.json", substitutions={"actor_name": detective.name, "actor_id": detective.id}),
    )
    await branch_pipeline.stage(
        project_id=project.id,
        premise="The branch continues after the checkpoint.",
        scene_id=branch_scene.id,
        character_ids=[detective.id, witness.id],
    )
    branch_pipeline.approve_scene(project_id=project.id, scene_id=branch_scene.id)
    death_turn = await branch_pipeline.continue_scene(
        project_id=project.id,
        scene_id=branch_scene.id,
        actor_character_id=detective.id,
    )
    assert death_turn.state["dead"] == [detective.id]
    assert repository.current_state(session, project.active_timeline_id).dead == set()

    surviving_turn = await NarrativePipeline(
        session,
        fixture_provider("normal_turn.json"),
    ).continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=witness.id,
        user_input="The witness survives the branch.",
    )
    assert surviving_turn.state["dead"] == []
    assert [event.id for event in repository.list_events(session, project.active_timeline_id)][: len(original_event_ids)] == original_event_ids
    assert any(event.event_type == "character_died" for event in repository.list_events(session, branch.id))


@pytest.mark.asyncio
async def test_an_empty_continue_advances_the_beat(session):
    """Continue with no text is a legitimate turn, and the engine runs it.

    The Continue button is the "let the scene keep going" control: the user is
    asking the engine to advance without speaking. The client refused to send an
    empty command, so the button did nothing at all — the one interaction that
    exists purely to avoid writing a line of dialogue had no effect.

    This pins the server contract the client depends on. The client-side guard
    is what was wrong; the engine was already correct.
    """
    project, characters, scene = create_project_scene(session, ["Detective", "Witness"])
    detective, _witness = characters
    pipeline = NarrativePipeline(session, fixture_provider("staging.json"))
    await pipeline.stage(
        project_id=project.id,
        premise="Two people wait",
        scene_id=scene.id,
        character_ids=[character.id for character in characters],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)

    result = await NarrativePipeline(
        session, fixture_provider("normal_turn.json")
    ).continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=detective.id,
        user_input="",
    )
    assert result.generation_id is not None
    assert result.output_text
    assert not result.requires_approval


@pytest.mark.asyncio
async def test_commitment_survives_unrelated_turns_and_enters_planner_context(session):
    project, characters, scene = create_project_scene(session, ["General", "Emperor"])
    general, _emperor = characters
    provider = fixture_provider(
        "staging.json",
        "normal_turn.json",
        "normal_turn.json",
        "normal_turn.json",
        substitutions={"actor_name": general.name},
    )
    pipeline = NarrativePipeline(session, provider)
    commitment = await pipeline.direct(
        project_id=project.id,
        scene_id=scene.id,
        text="The General will betray the Emperor",
    )
    await pipeline.stage(
        project_id=project.id,
        premise="A council decides who speaks next.",
        scene_id=scene.id,
        character_ids=[character.id for character in characters],
    )
    pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    for index in range(3):
        result = await pipeline.continue_scene(
            project_id=project.id,
            scene_id=scene.id,
            user_input=f"Unrelated turn {index}",
        )
        assert result.planner_context["commitments"][0]["id"] == commitment.commitment_id
        assert result.planner_context["commitments"][0]["status"] == CommitmentStatus.CREATED.value

    pending = pipeline.pending_commitments(project.id, project.active_timeline_id)

    assert len(pending) == 1
    assert pending[0]["description"] == "The General will betray the Emperor"


@pytest.mark.asyncio
async def test_memory_path_is_scoped_to_the_character_who_knows(session):
    project, characters, scene = create_project_scene(session, ["Detective", "Witness"])
    detective, witness = characters
    first_pipeline = NarrativePipeline(
        session,
        fixture_provider("staging.json", "knowledge_turn.json", substitutions={"actor_name": detective.name}),
    )
    await first_pipeline.stage(
        project_id=project.id,
        premise="A witness knows a hidden route.",
        scene_id=scene.id,
        character_ids=[detective.id, witness.id],
    )
    first_pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    await first_pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=detective.id,
    )

    class RecordingProvider(ScriptedProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.prompts = []

        async def structured(self, messages, schema):
            self.prompts.append(messages[-1].content)
            return await super().structured(messages, schema)

    recorder = RecordingProvider([json.loads((FIXTURES / "normal_turn.json").read_text(encoding="utf-8"))])
    second_pipeline = NarrativePipeline(session, recorder)
    await second_pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=witness.id,
    )
    assert "the eastern door is locked" not in recorder.prompts[0]
    detective_recorder = RecordingProvider([json.loads((FIXTURES / "normal_turn.json").read_text(encoding="utf-8"))])
    await NarrativePipeline(session, detective_recorder).continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=detective.id,
    )
    assert "the eastern door is locked" in detective_recorder.prompts[0]
    memories = repository.list_events(session, project.active_timeline_id)
    assert any(event.event_type == "knowledge_acquired" for event in memories)


@pytest.mark.asyncio
async def test_actor_selection_uses_provider_choice_and_explicit_override(session):
    project, characters, scene = create_project_scene(session, ["Detective", "Witness"])
    detective, witness = characters
    provider_pipeline = NarrativePipeline(
        session,
        fixture_provider("staging.json", "actor_selection.json", substitutions={"actor_name": detective.name, "actor_id": detective.id}),
    )
    await provider_pipeline.stage(
        project_id=project.id,
        premise="A detective decides how to question a witness.",
        scene_id=scene.id,
        character_ids=[detective.id, witness.id],
    )
    provider_pipeline.approve_scene(project_id=project.id, scene_id=scene.id)
    selected = await provider_pipeline.continue_scene(project_id=project.id, scene_id=scene.id)
    assert selected.structured_output["selected_actor"] == detective.id
    assert selected.state["characters"][detective.id]["last_action"]

    override_pipeline = NarrativePipeline(session, fixture_provider("normal_turn.json"))
    overridden = await override_pipeline.continue_scene(
        project_id=project.id,
        scene_id=scene.id,
        actor_character_id=witness.id,
    )
    assert overridden.structured_output["selected_actor"] == witness.id
    assert overridden.state["characters"][witness.id]["location_id"] == "hallway"
