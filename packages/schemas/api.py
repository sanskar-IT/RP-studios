from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectRead(ApiModel):
    id: str
    name: str
    description: str = ""
    active_timeline_id: str | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)


class CharacterRead(ApiModel):
    id: str
    name: str
    definition: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class SceneRead(ApiModel):
    id: str
    project_id: str
    timeline_id: str
    title: str
    status: str
    staging: dict[str, Any] = Field(default_factory=dict)
    staging_revision: int = 0
    approved_staging_revision: int | None = None
    current: bool = False


class TimelineRead(ApiModel):
    id: str
    project_id: str
    name: str
    parent_timeline_id: str | None = None
    forked_from_node_id: str | None = None


class EventRead(ApiModel):
    id: str
    timeline_id: str
    node_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str
    created_at: str


class CheckpointRead(ApiModel):
    id: str
    timeline_id: str
    sequence: int
    parent_node_id: str | None = None
    event_id: str | None = None
    event_type: str | None = None
    generation_id: str | None = None


class StateRead(ApiModel):
    characters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = Field(default_factory=dict)
    locations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    world_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    knowledge: dict[str, list[str]] = Field(default_factory=dict)
    items: dict[str, list[str]] = Field(default_factory=dict)
    injuries: dict[str, list[str]] = Field(default_factory=dict)
    control_modes: dict[str, str] = Field(default_factory=dict)
    completed_intents: list[str] = Field(default_factory=list)
    canon_divergences: dict[str, dict[str, Any]] = Field(default_factory=dict)
    dead: list[str] = Field(default_factory=list)
    active_scene_id: str | None = None
    revision: int = 0


class StagingProposal(ApiModel):
    scene_id: str | None = None
    location: str = ""
    time: str = ""
    characters_present: list[str] = Field(default_factory=list)
    objective: str = ""
    initial_conditions: list[str] = Field(default_factory=list)
    relevant_source_state: list[str] = Field(default_factory=list)
    relevant_context: list[str] = Field(default_factory=list)
    environmental_assumptions: list[str] = Field(default_factory=list)
    potential_consequences: list[str] = Field(default_factory=list)
    intended_consequences: list[str] = Field(default_factory=list)
    canon_conflicts: list[str] = Field(default_factory=list)
    possible_canon_conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_assumptions: list[str] = Field(default_factory=list)
    staging_revision: int = 0
    status: str = "proposed"


class StageRequest(ApiModel):
    premise: str = Field(min_length=1)
    scene_id: str | None = None
    character_ids: list[str] | None = None


class StagingEditRequest(ApiModel):
    scene_id: str
    staging_revision: int | None = None
    location: str | None = None
    time: str | None = None
    characters_present: list[str] | None = None
    objective: str | None = None
    initial_conditions: list[str] | None = None
    relevant_context: list[str] | None = None
    environmental_assumptions: list[str] | None = None
    potential_consequences: list[str] | None = None
    assumptions: list[str] | None = None
    unresolved_assumptions: list[str] | None = None
    character_ids: list[str] | None = None


class StagingApprovalRequest(ApiModel):
    staging_revision: int | None = None


class ContinueRequest(ApiModel):
    scene_id: str
    mode: str = "auto"
    user_input: str = ""
    possessed_character_id: str | None = None
    actor_character_id: str | None = None


class DirectRequest(ApiModel):
    scene_id: str
    text: str = Field(min_length=1)
    intent_type: str = "story_commitment"
    horizon: str = "short"


class PossessRequest(ApiModel):
    scene_id: str
    character_id: str


class ForkRequest(ApiModel):
    timeline_id: str
    node_id: str | None = None
    name: str = "Branch"


class CanonRequest(ApiModel):
    text: str = Field(min_length=1)
    canon_reference: str = ""


class CommitmentUpdateRequest(ApiModel):
    status: str | None = None
    progress: float | None = None
    note: str = ""


class CommitmentSupersedeRequest(ApiModel):
    replacement_text: str = ""


class GenerationRejectRequest(ApiModel):
    reason: str = "rejected by operator"


# --- DirectorPlan ----------------------------------------------------------
# Edits are intentionally free-form: a plan is guidance, and a user reshaping
# guidance should not have to fit a closed vocabulary. Anything the envelope
# still forbids is rejected on approval, not on input.


class DirectorPlanDecisionRequest(ApiModel):
    decision: Literal["approve", "edit", "cancel", "reject"]
    edits: dict[str, Any] | None = None
    resolution: Literal["follow", "override", "branch", ""] = ""


class DirectorPlanExecuteRequest(ApiModel):
    actor_character_id: str | None = None


class DirectorPlanInterruptRequest(ApiModel):
    reason: str = "user changed direction"


class StateCorrectionRequest(ApiModel):
    fact_id: str = Field(min_length=1)
    changes: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class MemoryQueryRequest(ApiModel):
    query: str = ""
    character_id: str | None = None
    limit: int = 40


class InspectResponse(ApiModel):
    state: StateRead
    memories: list[dict[str, Any]] = Field(default_factory=list)
    lore_debug: dict[str, Any] = Field(default_factory=dict)
    events: list[EventRead] = Field(default_factory=list)
