from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import db_session
from apps.api.app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[db_session] = lambda: session
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def create_workspace(client: TestClient) -> tuple[str, str, str]:
    response = client.post("/api/projects", json={"name": "Reliability API"})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    character = client.post(
        f"/api/projects/{project_id}/characters", json={"name": "Witness", "definition": {}}
    )
    assert character.status_code == 201, character.text
    character_id = character.json()["id"]
    staged = client.post(
        f"/api/projects/{project_id}/stage",
        json={"premise": "A witness waits in the library.", "character_ids": [character_id]},
    )
    assert staged.status_code == 200, staged.text
    scenes = client.get(f"/api/projects/{project_id}/scenes")
    assert scenes.status_code == 200, scenes.text
    scene_id = scenes.json()[0]["id"]
    revision = scenes.json()[0]["staging_revision"]
    approved = client.post(
        f"/api/projects/{project_id}/scenes/{scene_id}/approve",
        json={"staging_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    return project_id, scene_id, character_id


def test_reliability_routes_cover_traces_memories_and_commitments(client: TestClient) -> None:
    project_id, scene_id, _character_id = create_workspace(client)
    timelines = client.get(f"/api/projects/{project_id}/timelines")
    assert timelines.status_code == 200, timelines.text
    timeline_id = timelines.json()[0]["id"]

    continued = client.post(
        f"/api/projects/{project_id}/scenes/{scene_id}/continue",
        json={"scene_id": scene_id, "mode": "auto", "user_input": "The witness waits."},
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["generation_id"]
    assert continued.json()["context_debug"]["total_tokens"] > 0
    assert continued.json()["validation"]["status"] in {"valid", "accepted_with_warnings"}

    generations = client.get(f"/api/projects/{project_id}/timelines/{timeline_id}/generations")
    assert generations.status_code == 200, generations.text
    assert len(generations.json()) >= 1
    generation_id = generations.json()[0]["generation_id"]
    assert generations.json()[0]["total_tokens"] > 0

    trace = client.get(f"/api/projects/{project_id}/generations/{generation_id}")
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["context"]["total_tokens"] > 0
    assert body["validation"]["status"] in {"valid", "accepted_with_warnings"}
    assert body["trace"]["status"] == "traced"
    assert "api_key" not in trace.text.casefold().replace("max_output_tokens", "")

    memories = client.post(
        f"/api/projects/{project_id}/timelines/{timeline_id}/memories",
        json={"query": "witness", "limit": 10},
    )
    assert memories.status_code == 200, memories.text
    assert memories.json()["timeline_lineage"] == [timeline_id]
    assert "knowledge" in memories.json()

    inspected = client.get(f"/api/projects/{project_id}/timelines/{timeline_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    assert "context_debug" in inspected.json()
    assert "commitments" in inspected.json()
    assert "knowledge" in inspected.json()

    rejected = client.post(
        f"/api/projects/{project_id}/generations/{generation_id}/reject",
        json={"reason": "wrong tone for the scene"},
    )
    assert rejected.status_code == 200, rejected.text
    reread = client.get(f"/api/projects/{project_id}/generations/{generation_id}")
    assert reread.json()["status"] == "rejected"


def test_session_route_reports_the_rp_loop_state(client: TestClient) -> None:
    """One call, everything the studio's main screen needs.

    Assembling this client-side from four endpoints would put the orchestration in
    the UI, which is the one place it must not live.
    """
    project_id, scene_id, character_id = create_workspace(client)
    response = client.get(f"/api/projects/{project_id}/scenes/{scene_id}/session")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scene"]["scene_id"] == scene_id
    assert body["can_continue"] is True
    assert [member["character_id"] for member in body["cast"]] == [character_id]
    assert body["current_actor"] is None
    assert "plan" in body
    assert isinstance(body["open_commitments"], list)


def test_session_route_reflects_possession(client: TestClient) -> None:
    project_id, scene_id, character_id = create_workspace(client)
    possessed = client.post(
        f"/api/projects/{project_id}/scenes/{scene_id}/possess",
        json={"scene_id": scene_id, "character_id": character_id},
    )
    assert possessed.status_code == 200, possessed.text
    body = client.get(f"/api/projects/{project_id}/scenes/{scene_id}/session").json()
    assert body["current_actor"] == character_id
    assert body["cast"][0]["user_controlled"] is True
    released = client.post(
        f"/api/projects/{project_id}/scenes/{scene_id}/release",
        json={"scene_id": scene_id, "character_id": character_id},
    )
    assert released.status_code == 200, released.text
    body = client.get(f"/api/projects/{project_id}/scenes/{scene_id}/session").json()
    assert body["current_actor"] is None


def test_plan_routes_expose_the_lifecycle(client: TestClient) -> None:
    project_id, scene_id, _character_id = create_workspace(client)
    listed = client.get(f"/api/projects/{project_id}/plans")
    assert listed.status_code == 200, listed.text
    assert listed.json() == []
    scoped = client.get(f"/api/projects/{project_id}/plans", params={"scene_id": scene_id})
    assert scoped.status_code == 200, scoped.text
    missing = client.get(f"/api/projects/{project_id}/plans/does-not-exist")
    assert missing.status_code == 404, missing.text


def test_plan_routes_reject_a_foreign_project(client: TestClient) -> None:
    project_id, _scene_id, _character_id = create_workspace(client)
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    plans = client.get(f"/api/projects/{other}/plans")
    assert plans.status_code == 200, plans.text
    assert plans.json() == [], "plans leaked across projects"


def test_commitment_lifecycle_routes(client: TestClient) -> None:
    project_id, scene_id, _character_id = create_workspace(client)
    timelines = client.get(f"/api/projects/{project_id}/timelines")
    timeline_id = timelines.json()[0]["id"]

    directed = client.post(
        f"/api/projects/{project_id}/scenes/{scene_id}/direct",
        json={"scene_id": scene_id, "text": "The General eventually betrays the Emperor."},
    )
    assert directed.status_code == 200, directed.text
    commitment_id = directed.json()["commitment_id"]

    progressed = client.post(
        f"/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}",
        json={"status": "progressing", "progress": 0.4},
    )
    assert progressed.status_code == 200, progressed.text

    illegal = client.post(
        f"/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}",
        json={"status": "created"},
    )
    assert illegal.status_code == 422, illegal.text

    superseded = client.post(
        f"/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}/supersede",
        json={"replacement_text": ""},
    )
    assert superseded.status_code == 200, superseded.text

    corrected = client.post(
        f"/api/projects/{project_id}/timelines/{timeline_id}/correct-state",
        json={"fact_id": "harbour-open", "changes": {"text": "the harbour gate stands open"}, "note": ""},
    )
    assert corrected.status_code == 200, corrected.text


def test_provider_capabilities_route_reports_a_window() -> None:
    from fastapi.testclient import TestClient as RawClient

    raw = RawClient(app)
    response = raw.get("/api/providers/capabilities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context_window"] >= 512
    assert body["supports_structured_output"] in {True, False}
    assert "api_key" not in response.text.casefold()


def test_provider_capabilities_route_makes_the_unconfigured_adapter_detectable() -> None:
    """The client needs one field to tell the user no model is configured.

    With no key set the adapter is ``heuristic``, which echoes the user's input
    back with no model involved. Nothing in the response previously said so, and
    the frontend never asked, so a fresh install looked like a working app that
    was silently incapable of writing prose.
    """
    from fastapi.testclient import TestClient as RawClient

    raw = RawClient(app)
    body = raw.get("/api/providers/capabilities").json()
    assert "adapter" in body
    assert body["adapter"] in {"heuristic", "openai_compatible"}
    if body["adapter"] == "heuristic":
        assert body["supports_structured_output"] in {True, False}
