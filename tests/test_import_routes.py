from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import db_session
from apps.api.app.main import app
from services.importers.character_cards import MAX_IMPORT_BYTES

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(session):
    app.dependency_overrides[db_session] = lambda: session
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "Import API"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_character_card_png_import_persists_card_book_metadata(client: TestClient) -> None:
    project_id = create_project(client)
    with (FIXTURES / "character_v2.png").open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/imports/character-card",
            files={"file": ("character_v2.png", handle, "image/png")},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_format"] == "png"
    assert body["card_version"] == "2"
    assert body["entry_count"] == 0
    character = client.get(f"/api/projects/{project_id}/characters/{body['character_id']}")
    assert character.status_code == 200, character.text
    assert character.json()["card"]["payload"]["data"]["name"] == "Mara PNG"
    with (FIXTURES / "character_v2.json").open("rb") as handle:
        embedded = client.post(
            f"/api/projects/{project_id}/imports/character-card",
            files={"file": ("character_v2.json", handle, "application/json")},
        )
    assert embedded.status_code == 201, embedded.text
    assert embedded.json()["entry_count"] == 2
    assert embedded.json()["lorebook_id"]


def test_character_card_preview_does_not_persist_until_confirmation(client: TestClient) -> None:
    project_id = create_project(client)
    with (FIXTURES / "character_v2.json").open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/imports/character-card/preview",
            files={"file": ("character_v2.json", handle, "application/json")},
        )
    assert response.status_code == 200, response.text
    assert response.json()["entry_count"] == 2
    assert client.get(f"/api/projects/{project_id}/characters").json() == []


def test_character_card_import_rejects_malformed_oversized_or_wrong_type(client: TestClient) -> None:
    project_id = create_project(client)
    malformed = client.post(
        f"/api/projects/{project_id}/imports/character-card",
        files={"file": ("card.json", b"{}", "application/json")},
    )
    assert malformed.status_code == 422
    oversized = client.post(
        f"/api/projects/{project_id}/imports/character-card",
        files={"file": ("card.json", b"x" * (MAX_IMPORT_BYTES + 1), "application/json")},
    )
    assert oversized.status_code == 413
    wrong_type = client.post(
        f"/api/projects/{project_id}/imports/lorebook",
        files={"file": ("book.exe", b"not json", "application/octet-stream")},
    )
    assert wrong_type.status_code == 415


def test_lorebook_import_and_preview(client: TestClient) -> None:
    project_id = create_project(client)
    with (FIXTURES / "lorebook_tavern.json").open("rb") as handle:
        response = client.post(
            f"/api/projects/{project_id}/imports/lorebook",
            files={"file": ("lorebook_tavern.json", handle, "application/json")},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["entry_count"] == 3
    books = client.get(f"/api/projects/{project_id}/lorebooks")
    assert books.status_code == 200
    assert books.json()[0]["entry_count"] == 3
    preview = client.post(
        f"/api/projects/{project_id}/lorebooks/{body['lorebook_id']}/preview",
        json={"text": "Public Safety Commission officers enter."},
    )
    assert preview.status_code == 200, preview.text
    assert {entry["name"] for entry in preview.json()["activated"]} >= {"Public Safety", "Constant world rule"}
    character = client.post(f"/api/projects/{project_id}/characters", json={"name": "Mara"}).json()
    associated = client.post(
        f"/api/projects/{project_id}/lorebooks/{body['lorebook_id']}/associate",
        json={"character_id": character["id"]},
    )
    assert associated.status_code == 200
    assert associated.json()["scope"] == "character"
