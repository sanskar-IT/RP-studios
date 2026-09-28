from __future__ import annotations

from apps.api.app.main import app


def test_app_exposes_health_and_api() -> None:
    paths = set(app.openapi()["paths"])
    assert "/health" in paths
    assert "/api/projects" in paths
    assert "/api/projects/{project_id}/stage" in paths
    assert "/api/projects/{project_id}/scenes/{scene_id}/staging" in paths
    assert "/api/projects/{project_id}/scenes/{scene_id}/staging/regenerate" in paths
    assert "/api/projects/{project_id}/scenes/{scene_id}/actors" in paths
    assert "/api/projects/{project_id}/timelines/{timeline_id}/checkpoints" in paths
    assert "/api/projects/{project_id}/imports/character-card/preview" in paths
    assert "/api/projects/{project_id}/imports/lorebook/preview" in paths
    assert "/api/projects/{project_id}/lorebooks" in paths
    assert "/api/projects/{project_id}/characters/{character_id}" in paths
    assert "/api/projects/{project_id}/lorebooks" in paths
    assert "/api/projects/{project_id}/lorebooks/{lorebook_id}/preview" in paths
    assert "/api/projects/{project_id}/imports/character-card/preview" in paths
    assert "/api/projects/{project_id}/timelines/{timeline_id}/generations" in paths
    assert "/api/projects/{project_id}/generations/{generation_id}" in paths
    assert "/api/projects/{project_id}/generations/{generation_id}/reject" in paths
    assert "/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}" in paths
    assert (
        "/api/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}/supersede"
        in paths
    )
    assert "/api/projects/{project_id}/timelines/{timeline_id}/memories" in paths
    assert "/api/projects/{project_id}/timelines/{timeline_id}/correct-state" in paths
    assert "/api/providers/capabilities" in paths
