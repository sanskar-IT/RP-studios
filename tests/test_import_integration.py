from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from apps.api.app import repository
from apps.api.app.dependencies import db_session
from apps.api.app.main import app
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ScriptedProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(session: Session):
    app.dependency_overrides[db_session] = lambda: session
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_to_scene_keeps_definition_separate_from_runtime_state(client: TestClient, session: Session) -> None:
    project_response = client.post("/api/projects", json={"name": "Imported narrative"})
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]
    with (FIXTURES / "character_v2.json").open("rb") as handle:
        card_response = client.post(
            f"/api/projects/{project_id}/imports/character-card",
            files={"file": ("character_v2.json", handle, "application/json")},
        )
    assert card_response.status_code == 201, card_response.text
    with (FIXTURES / "lorebook_tavern.json").open("rb") as handle:
        lorebook_response = client.post(
            f"/api/projects/{project_id}/imports/lorebook",
            files={"file": ("lorebook_tavern.json", handle, "application/json")},
        )
    assert lorebook_response.status_code == 201, lorebook_response.text
    character = repository.list_characters(session, project_id)[0]
    original_definition = dict(character.definition)
    scene = repository.create_scene(
        session,
        project_id=project_id,
        timeline_id=repository.get_project(session, project_id).active_timeline_id,
        title="Public Safety review",
        participant_ids=[character.id],
    )
    session.commit()
    provider = ScriptedProvider(
        [
            json.loads((FIXTURES / "provider" / "staging.json").read_text(encoding="utf-8")),
            json.loads((FIXTURES / "provider" / "normal_turn.json").read_text(encoding="utf-8")),
        ],
        substitutions={"actor_name": character.name},
    )
    pipeline = NarrativePipeline(session, provider)
    staged = await pipeline.stage(
        project_id=project_id,
        premise="Public Safety Commission officers question Mara in the library.",
        scene_id=scene.id,
        character_ids=[character.id],
    )
    pipeline.approve_scene(project_id=project_id, scene_id=scene.id, staging_revision=staged.structured_output["staging_revision"])
    turn = await pipeline.continue_scene(
        project_id=project_id,
        scene_id=scene.id,
        actor_character_id=character.id,
        user_input="Public Safety Commission arrives.",
    )
    assert turn.state["characters"][character.id]["location_id"] == "hallway"
    assert turn.lore_debug is not None
    assert {entry["name"] for entry in turn.lore_debug["activated"]} >= {"Public Safety", "Constant world rule"}
    assert dict(character.definition) == original_definition
