from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from apps.api.app import repository
from apps.api.app.config import get_settings
from apps.api.app.dependencies import DbSession
from packages.schemas.api import (
    CanonRequest,
    CharacterRead,
    CheckpointRead,
    CommitmentSupersedeRequest,
    CommitmentUpdateRequest,
    ContinueRequest,
    DirectorPlanDecisionRequest,
    DirectorPlanExecuteRequest,
    DirectorPlanInterruptRequest,
    DirectRequest,
    ForkRequest,
    GenerationRejectRequest,
    MemoryQueryRequest,
    PossessRequest,
    ProjectCreate,
    ProjectRead,
    SceneRead,
    StageRequest,
    StagingApprovalRequest,
    StagingEditRequest,
    StagingProposal,
    StateCorrectionRequest,
    TimelineRead,
)
from services.core.enums import ControlMode, LorebookScope, SceneStatus
from services.core.models import (
    Character,
    CharacterCard,
    Lorebook,
    LorebookEntry,
    Project,
    SceneParticipant,
    Source,
)
from services.importers.character_cards import (
    MAX_IMPORT_BYTES,
    CardImportError,
    import_character_card,
    import_lorebook,
)
from services.lorebook.evaluator import LoreCandidate, evaluate_lore
from services.narrative.pipeline import NarrativePipeline
from services.providers import HeuristicProvider, LLMProvider, OpenAICompatibleProvider
from services.providers.capabilities import (
    HEURISTIC_CAPABILITIES,
    capabilities_from_mapping,
)

router = APIRouter(prefix="/api")


def build_pipeline(db) -> NarrativePipeline:
    settings = get_settings()
    provider: LLMProvider
    capabilities: dict[str, Any]
    if settings.provider_name.casefold() in {"openai", "openai_compatible", "local", "generic"}:
        if not settings.provider_base_url or not settings.provider_model:
            raise HTTPException(status_code=503, detail="The configured provider is missing base URL or model")
        provider = OpenAICompatibleProvider(
            settings.provider_base_url,
            settings.provider_model,
            settings.provider_api_key or None,
        )
        capabilities = capabilities_from_mapping(
            {
                "model": settings.provider_model,
                "adapter": "openai_compatible",
                "context_window": settings.provider_context_window,
                "max_output_tokens": settings.provider_max_output_tokens,
                "features": settings.provider_feature_list,
            },
            model=settings.provider_model,
            adapter="openai_compatible",
        ).to_dict()
    else:
        provider = HeuristicProvider()
        capabilities = HEURISTIC_CAPABILITIES.to_dict()
    return NarrativePipeline(
        db,
        provider,
        capabilities=capabilities,
        memory_limit=settings.memory_retrieval_limit,
        memory_pool=settings.memory_candidate_pool,
        lore_budget=settings.lore_token_budget,
        # Structured plan refinement only runs for a real configured provider;
        # the heuristic provider has nothing to refine with, so the deterministic
        # plan is used directly.
        director_refinement=settings.provider_name.casefold()
        in {"openai", "openai_compatible", "local", "generic"},
    )


def handle_errors(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def handle_errors_async(awaitable):
    try:
        return await awaitable
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def ensure_timeline_project(db, project_id: str, timeline_id: str) -> None:
    timeline = handle_errors(repository.get_timeline, db, timeline_id)
    if timeline.project_id != project_id:
        raise HTTPException(status_code=422, detail="Timeline does not belong to project")


def ensure_scene_project(db, project_id: str, scene_id: str) -> None:
    scene = handle_errors(repository.get_scene, db, scene_id)
    if scene.project_id != project_id:
        raise HTTPException(status_code=422, detail="Scene does not belong to project")


def validate_import_file(file: UploadFile, kind: str) -> None:
    filename = (file.filename or "").casefold()
    extension = Path(filename).suffix
    content_type = (file.content_type or "").split(";", 1)[0].casefold()
    if kind == "card":
        allowed_extensions = {".json", ".png", ".apng"}
        allowed_types = {"", "application/json", "image/png", "image/apng", "application/octet-stream", "text/plain"}
    else:
        allowed_extensions = {".json"}
        allowed_types = {"", "application/json", "text/plain", "application/octet-stream"}
    if extension and extension not in allowed_extensions:
        raise HTTPException(status_code=415, detail=f"Unsupported {kind} file extension")
    if content_type and content_type not in allowed_types and extension not in allowed_extensions:
        raise HTTPException(status_code=415, detail=f"Unsupported {kind} file type")
    if file.size is not None and file.size > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"Imported file exceeds the {MAX_IMPORT_BYTES} byte limit")


async def read_import_file(file: UploadFile) -> bytes:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"Imported file exceeds the {MAX_IMPORT_BYTES} byte limit")
    return raw


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-narrative-studio"}


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db=DbSession) -> Project:
    project = repository.create_project(db, payload.name, payload.description)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(db=DbSession) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.created_at.desc())))


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db=DbSession) -> Project:
    return handle_errors(repository.get_project, db, project_id)


@router.get("/projects/{project_id}/characters", response_model=list[CharacterRead])
def list_characters(project_id: str, db=DbSession) -> list[Character]:
    handle_errors(repository.get_project, db, project_id)
    return repository.list_characters(db, project_id)


@router.post("/projects/{project_id}/characters", response_model=CharacterRead, status_code=201)
def create_character(project_id: str, payload: dict[str, Any], db=DbSession) -> Character:
    handle_errors(repository.get_project, db, project_id)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Character name is required")
    character = repository.add_character(
        db,
        project_id=project_id,
        name=name,
        definition=payload.get("definition", {}),
    )
    db.commit()
    db.refresh(character)
    return character


@router.post("/projects/{project_id}/sources", status_code=201)
def create_source(project_id: str, payload: dict[str, Any], db=DbSession) -> dict[str, str]:
    handle_errors(repository.get_project, db, project_id)
    source = Source(
        project_id=project_id,
        kind=str(payload.get("kind", "text")),
        title=str(payload.get("title", "")),
        content=str(payload.get("content", "")),
        uri=payload.get("uri"),
        extra_data=payload.get("extra_data", {}),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return {"id": source.id, "title": source.title}


@router.get("/projects/{project_id}/scenes", response_model=list[SceneRead])
def list_scenes(project_id: str, db=DbSession) -> list[Any]:
    handle_errors(repository.get_project, db, project_id)
    return repository.list_scenes(db, project_id)


@router.get("/projects/{project_id}/timelines", response_model=list[TimelineRead])
def list_timelines(project_id: str, db=DbSession) -> list[Any]:
    handle_errors(repository.get_project, db, project_id)
    return repository.list_timelines(db, project_id)


@router.post("/projects/{project_id}/stage", response_model=StagingProposal)
async def stage_scene(project_id: str, payload: StageRequest, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = await build_pipeline(db).stage(
        project_id=project_id,
        premise=payload.premise,
        scene_id=payload.scene_id,
        character_ids=payload.character_ids,
    )
    return result.structured_output or {}


@router.patch("/projects/{project_id}/scenes/{scene_id}/staging")
def edit_staging(
    project_id: str,
    scene_id: str,
    payload: StagingEditRequest,
    db=DbSession,
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    updates = payload.model_dump(exclude_unset=True, exclude={"scene_id", "staging_revision"})
    result = build_pipeline(db).edit_staging(
        project_id=project_id,
        scene_id=scene_id,
        updates=updates,
        expected_revision=payload.staging_revision,
    )
    return result.to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/staging/regenerate")
async def regenerate_staging(
    project_id: str,
    scene_id: str,
    payload: dict[str, Any],
    db=DbSession,
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = await build_pipeline(db).regenerate_staging(
        project_id=project_id,
        scene_id=scene_id,
        premise=payload.get("premise"),
        character_ids=payload.get("character_ids"),
    )
    return result.to_dict()


@router.get("/projects/{project_id}/scenes/{scene_id}/actors")
def list_scene_actors(project_id: str, scene_id: str, db=DbSession) -> list[dict[str, Any]]:
    handle_errors(repository.get_project, db, project_id)
    scene = handle_errors(repository.get_scene, db, scene_id)
    if scene.project_id != project_id:
        raise HTTPException(status_code=422, detail="Scene does not belong to project")
    participants = db.scalars(
        select(SceneParticipant)
        .where(SceneParticipant.scene_id == scene_id)
        .order_by(SceneParticipant.character_id)
    )
    characters = {
        character.id: character
        for character in db.scalars(select(Character).where(Character.project_id == project_id))
    }
    return [
        {
            "character_id": participant.character_id,
            "name": characters[participant.character_id].name,
            "control_mode": participant.control_mode,
            "presence": participant.presence,
        }
        for participant in participants
        if participant.character_id in characters
    ]


@router.get("/projects/{project_id}/scenes/{scene_id}/session")
def scene_session(project_id: str, scene_id: str, db=DbSession) -> dict[str, Any]:
    """Everything the RP loop needs, in one call.

    The studio's normal screen is: who am I playing, what is the current beat,
    what is the plan doing, and what can I do next. Assembling that from four
    endpoints made the client own the orchestration — and the client is the one
    place the complexity must not leak to.

    Plan status is included but deliberately summarised. A plan stays inspectable
    without being the thing the user is looking at.
    """
    from services.core.enums import BeatStatus

    handle_errors(repository.get_project, db, project_id)
    scene = handle_errors(repository.get_scene, db, scene_id)
    if scene.project_id != project_id:
        raise HTTPException(status_code=422, detail="Scene does not belong to project")
    participants = list(
        db.scalars(
            select(SceneParticipant)
            .where(SceneParticipant.scene_id == scene_id)
            .order_by(SceneParticipant.character_id)
        )
    )
    characters = {
        character.id: character
        for character in db.scalars(select(Character).where(Character.project_id == project_id))
    }
    state = repository.current_state(db, scene.timeline_id)
    plan = repository.active_director_plan(db, scene_id=scene_id)
    plan_payload: dict[str, Any] = {}
    if plan is not None:
        plan_payload = build_pipeline(db)._plan_payload(plan)
        beats = plan_payload.get("beats") or []
        current = next(
            (
                beat
                for beat in beats
                if beat.get("status") in {BeatStatus.PENDING.value, BeatStatus.ACTIVE.value}
            ),
            beats[0] if beats else None,
        )
        plan_payload["current_beat"] = current
        plan_payload["remaining_beats"] = [
            beat
            for beat in beats
            if beat.get("status") in {BeatStatus.PENDING.value, BeatStatus.ACTIVE.value}
        ]
    cast = [
        {
            "character_id": participant.character_id,
            "name": characters[participant.character_id].name,
            "control_mode": participant.control_mode,
            "user_controlled": participant.control_mode == ControlMode.USER.value,
            "alive": participant.character_id not in state.dead,
            "presence": participant.presence,
        }
        for participant in participants
        if participant.character_id in characters
    ]
    return {
        "scene": {
            "scene_id": scene.id,
            "title": scene.title,
            "status": scene.status,
            "location": (scene.staging or {}).get("location", ""),
            "objective": (scene.staging or {}).get("objective", ""),
        },
        "cast": cast,
        "current_actor": next(
            (entry["character_id"] for entry in cast if entry["user_controlled"]), None
        ),
        "plan": plan_payload,
        "can_continue": scene.status == SceneStatus.ACTIVE.value,
        "open_commitments": build_pipeline(db).all_commitments(
            project_id, scene.timeline_id
        ),
    }


@router.post("/projects/{project_id}/scenes/{scene_id}/approve")
def approve_scene(
    project_id: str,
    scene_id: str,
    payload: StagingApprovalRequest | None = None,
    db=DbSession,
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return build_pipeline(db).approve_scene(
        project_id=project_id,
        scene_id=scene_id,
        staging_revision=payload.staging_revision if payload else None,
    ).to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/cancel")
def cancel_scene(project_id: str, scene_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return build_pipeline(db).cancel_scene(project_id=project_id, scene_id=scene_id).to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/end")
def end_scene(project_id: str, scene_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return build_pipeline(db).end_scene(project_id=project_id, scene_id=scene_id).to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/continue")
async def continue_scene(project_id: str, scene_id: str, payload: ContinueRequest, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = await build_pipeline(db).continue_scene(
        project_id=project_id,
        scene_id=scene_id,
        mode=payload.mode,
        user_input=payload.user_input,
        possessed_character_id=payload.possessed_character_id,
        actor_character_id=payload.actor_character_id,
    )
    return result.to_dict()

@router.post("/projects/{project_id}/scenes/{scene_id}/regenerate")
async def regenerate_scene(
    project_id: str, scene_id: str, payload: ContinueRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = await build_pipeline(db).regenerate_scene(
        project_id=project_id,
        scene_id=scene_id,
        user_input=payload.user_input,
        mode=payload.mode,
    )
    return result.to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/direct")
async def direct_scene(project_id: str, scene_id: str, payload: DirectRequest, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = await build_pipeline(db).direct(
        project_id=project_id,
        scene_id=scene_id,
        text=payload.text,
        intent_type=payload.intent_type,
        horizon=payload.horizon,
    )
    return result.to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/possess")
def possess_character(project_id: str, scene_id: str, payload: PossessRequest, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = build_pipeline(db).possess(project_id=project_id, scene_id=scene_id, character_id=payload.character_id)
    return result.to_dict()


@router.post("/projects/{project_id}/scenes/{scene_id}/release")
def release_character(project_id: str, scene_id: str, payload: PossessRequest, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    result = build_pipeline(db).release_possession(
        project_id=project_id, scene_id=scene_id, character_id=payload.character_id
    )
    return result.to_dict()


@router.post("/projects/{project_id}/timelines/{timeline_id}/canon-override")
def override_canon(
    project_id: str, timeline_id: str, payload: CanonRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    result = build_pipeline(db).create_canon_divergence(
        project_id=project_id,
        timeline_id=timeline_id,
        text=payload.text,
        canon_reference=payload.canon_reference,
    )
    return result.to_dict()


@router.post("/projects/{project_id}/timelines/{timeline_id}/fork")
def fork_timeline(project_id: str, timeline_id: str, payload: ForkRequest, db=DbSession) -> dict[str, Any]:
    project = handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    node_id = payload.node_id or repository.latest_node(db, timeline_id).id
    branch = repository.fork_timeline(
        db,
        source_timeline_id=timeline_id,
        source_node_id=node_id,
        name=payload.name,
    )
    repository.update_project_timeline(db, project, branch.id)
    db.commit()
    return {
        "id": branch.id,
        "project_id": branch.project_id,
        "name": branch.name,
        "parent_timeline_id": branch.parent_timeline_id,
        "forked_from_node_id": branch.forked_from_node_id,
    }


@router.get("/projects/{project_id}/timelines/{timeline_id}/checkpoints", response_model=list[CheckpointRead])
def list_checkpoints(project_id: str, timeline_id: str, db=DbSession) -> list[dict[str, Any]]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return repository.list_checkpoints(db, timeline_id)


@router.get("/projects/{project_id}/timelines/{timeline_id}/inspect")
def inspect_timeline(project_id: str, timeline_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return build_pipeline(db).inspect(project_id, timeline_id)


@router.get("/projects/{project_id}/timelines/{timeline_id}/generations")
def list_generations(project_id: str, timeline_id: str, limit: int = 25, db=DbSession) -> list[dict[str, Any]]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return build_pipeline(db).list_generation_traces(
        project_id=project_id, timeline_id=timeline_id, limit=max(1, min(limit, 100))
    )


@router.get("/projects/{project_id}/generations/{generation_id}")
def get_generation_trace(project_id: str, generation_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return handle_errors(
        build_pipeline(db).generation_trace, project_id=project_id, generation_id=generation_id
    )


@router.post("/projects/{project_id}/generations/{generation_id}/reject")
def reject_generation(
    project_id: str, generation_id: str, payload: GenerationRejectRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return handle_errors(
        build_pipeline(db).reject_generation,
        project_id=project_id,
        generation_id=generation_id,
        reason=payload.reason,
    ).to_dict()


# --- DirectorPlan lifecycle -------------------------------------------------
# A plan is inspected and decided by id rather than through the scene, because
# a proposal can outlive the turn that produced it: the user may come back to it
# after the conversation has moved on, and the decision still has to land on the
# plan it was made about.


@router.get("/projects/{project_id}/plans")
def list_director_plans(
    project_id: str,
    scene_id: str | None = None,
    timeline_id: str | None = None,
    db=DbSession,
) -> list[dict[str, Any]]:
    handle_errors(repository.get_project, db, project_id)
    if timeline_id:
        ensure_timeline_project(db, project_id, timeline_id)
    if scene_id:
        ensure_scene_project(db, project_id, scene_id)
    return handle_errors(
        build_pipeline(db).list_plans,
        project_id=project_id,
        scene_id=scene_id,
        timeline_id=timeline_id,
    )


@router.get("/projects/{project_id}/plans/{plan_id}")
def get_director_plan(project_id: str, plan_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return handle_errors(build_pipeline(db).get_plan, project_id=project_id, plan_id=plan_id)


@router.post("/projects/{project_id}/plans/{plan_id}/decide")
def decide_director_plan(
    project_id: str, plan_id: str, payload: DirectorPlanDecisionRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return handle_errors(
        build_pipeline(db).decide_plan,
        project_id=project_id,
        plan_id=plan_id,
        decision=payload.decision,
        edits=payload.edits,
        resolution=payload.resolution,
    )


@router.post("/projects/{project_id}/plans/{plan_id}/execute")
def execute_director_plan(
    project_id: str,
    plan_id: str,
    payload: DirectorPlanExecuteRequest,
    db=DbSession,
) -> dict[str, Any]:
    import asyncio as _asyncio

    handle_errors(repository.get_project, db, project_id)
    return _asyncio.run(
        handle_errors_async(
            build_pipeline(db).execute_plan(
                project_id=project_id,
                plan_id=plan_id,
                actor_character_id=payload.actor_character_id,
            )
        )
    ).to_dict()


@router.post("/projects/{project_id}/plans/{plan_id}/interrupt")
def interrupt_director_plan(
    project_id: str, plan_id: str, payload: DirectorPlanInterruptRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    return handle_errors(
        build_pipeline(db).interrupt_plan,
        project_id=project_id,
        plan_id=plan_id,
        reason=payload.reason,
    )


@router.post("/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}")
def update_commitment(
    project_id: str, timeline_id: str, commitment_id: str, payload: CommitmentUpdateRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return handle_errors(
        build_pipeline(db).update_commitment,
        project_id=project_id,
        commitment_id=commitment_id,
        status=payload.status,
        progress=payload.progress,
        note=payload.note,
    ).to_dict()


@router.post("/projects/{project_id}/timelines/{timeline_id}/commitments/{commitment_id}/supersede")
def supersede_commitment(
    project_id: str, timeline_id: str, commitment_id: str, payload: CommitmentSupersedeRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    import asyncio as _asyncio

    return _asyncio.run(
        handle_errors_async(
            build_pipeline(db).supersede_commitment(
                project_id=project_id,
                commitment_id=commitment_id,
                replacement_text=payload.replacement_text,
            )
        )
    ).to_dict()


@router.post("/projects/{project_id}/timelines/{timeline_id}/memories")
def inspect_memories(
    project_id: str, timeline_id: str, payload: MemoryQueryRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return build_pipeline(db).memory_inspection(
        project_id=project_id,
        timeline_id=timeline_id,
        query=payload.query,
        character_id=payload.character_id,
        limit=max(1, min(payload.limit, 200)),
    )


@router.post("/projects/{project_id}/timelines/{timeline_id}/correct-state")
def correct_state(
    project_id: str, timeline_id: str, payload: StateCorrectionRequest, db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    ensure_timeline_project(db, project_id, timeline_id)
    return handle_errors(
        build_pipeline(db).correct_state,
        project_id=project_id,
        timeline_id=timeline_id,
        fact_id=payload.fact_id,
        changes=payload.changes,
        note=payload.note,
    ).to_dict()


@router.get("/providers/capabilities")
def provider_capabilities() -> dict[str, Any]:
    settings = get_settings()
    if settings.provider_name.casefold() in {"openai", "openai_compatible", "local", "generic"}:
        return capabilities_from_mapping(
            {
                "model": settings.provider_model,
                "adapter": "openai_compatible",
                "context_window": settings.provider_context_window,
                "max_output_tokens": settings.provider_max_output_tokens,
                "features": settings.provider_feature_list,
            },
            model=settings.provider_model,
            adapter="openai_compatible",
        ).to_dict()
    return HEURISTIC_CAPABILITIES.to_dict()


@router.get("/projects/{project_id}/characters/{character_id}")
def get_character_detail(project_id: str, character_id: str, db=DbSession) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    character = handle_errors(db.get, Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail="Character not found")
    card = db.scalar(select(CharacterCard).where(CharacterCard.character_id == character_id))
    books = list(db.scalars(select(Lorebook).where(Lorebook.owner_character_id == character_id)))
    return {
        "character": {
            "id": character.id,
            "name": character.name,
            "definition": character.definition,
            "extra_data": character.extra_data,
        },
        "card": {
            "card_version": card.card_version,
            "extensions": card.extensions,
            "payload": card.payload,
        } if card else None,
        "lorebooks": [
            {"id": book.id, "name": book.name, "scope": book.scope, "extra_data": book.extra_data}
            for book in books
        ],
    }


@router.get("/projects/{project_id}/lorebooks")
def list_lorebooks(project_id: str, db=DbSession) -> list[dict[str, Any]]:
    handle_errors(repository.get_project, db, project_id)
    books = list(db.scalars(select(Lorebook).where(Lorebook.project_id == project_id).order_by(Lorebook.created_at)))
    return [
        {
            "id": book.id,
            "name": book.name,
            "scope": book.scope,
            "owner_character_id": book.owner_character_id,
            "entry_count": len(list(db.scalars(select(LorebookEntry).where(LorebookEntry.lorebook_id == book.id)))),
            "extra_data": book.extra_data,
        }
        for book in books
    ]


@router.post("/projects/{project_id}/lorebooks/{lorebook_id}/associate")
def associate_lorebook(
    project_id: str,
    lorebook_id: str,
    payload: dict[str, Any],
    db=DbSession,
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    book = handle_errors(db.get, Lorebook, lorebook_id)
    if book is None or book.project_id != project_id:
        raise HTTPException(status_code=404, detail="Lorebook not found")
    character_id = str(payload.get("character_id", ""))
    character = handle_errors(db.get, Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=422, detail="Character does not belong to project")
    book.owner_character_id = character.id
    book.scope = LorebookScope.CHARACTER.value
    db.add(book)
    db.commit()
    return {"lorebook_id": book.id, "character_id": character.id, "scope": book.scope}


@router.post("/projects/{project_id}/lorebooks/{lorebook_id}/preview")
def preview_lorebook(
    project_id: str,
    lorebook_id: str,
    payload: dict[str, Any],
    db=DbSession,
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    book = handle_errors(db.get, Lorebook, lorebook_id)
    if book is None or book.project_id != project_id:
        raise HTTPException(status_code=404, detail="Lorebook not found")
    actor_id = payload.get("character_id")
    entries = [
        LoreCandidate(
            id=entry.id,
            name=entry.name,
            content=entry.content,
            primary_keys=list(entry.primary_keys or []),
            secondary_keys=list(entry.secondary_keys or []),
            enabled=entry.enabled,
            constant=entry.constant,
            selective=entry.selective,
            recursive=entry.recursive,
            insertion_order=entry.insertion_order,
            probability=entry.probability,
            scan_depth=entry.scan_depth,
            scope=book.scope,
        )
        for entry in db.scalars(select(LorebookEntry).where(LorebookEntry.lorebook_id == lorebook_id).order_by(LorebookEntry.insertion_order))
        if actor_id is None or book.owner_character_id is None or book.owner_character_id == actor_id
    ]
    text = str(payload.get("text", ""))
    try:
        token_budget = int(book.extra_data.get("token_budget", 2048)) if isinstance(book.extra_data, dict) else 2048
    except (TypeError, ValueError):
        token_budget = 2048
    return evaluate_lore(entries, text, token_budget=token_budget).to_dict()


@router.post("/projects/{project_id}/imports/character-card/preview")
async def preview_character_card_route(
    project_id: str, file: Annotated[UploadFile, File()], db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    validate_import_file(file, "card")
    raw = await read_import_file(file)
    try:
        imported = import_character_card(raw, file.filename or "card.json")
    except (CardImportError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    book = imported.character_book or {}
    return {
        "kind": "character_card",
        "name": imported.name,
        "card_version": imported.card_version,
        "source_format": imported.source_format,
        "source_filename": imported.source_filename,
        "warnings": imported.warnings,
        "extensions_preserved": sorted(imported.extensions),
        "entry_count": len(book.get("entries", [])),
        "lorebook": {
            "name": book.get("name"),
            "scan_depth": book.get("scan_depth"),
            "token_budget": book.get("token_budget"),
        } if book else None,
    }


@router.post("/projects/{project_id}/imports/lorebook/preview")
async def preview_lorebook_route(
    project_id: str, file: Annotated[UploadFile, File()], db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    validate_import_file(file, "lorebook")
    raw = await read_import_file(file)
    try:
        imported = import_lorebook(raw)
    except (CardImportError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "kind": "lorebook",
        "name": imported.get("name", "Imported lorebook"),
        "source_filename": file.filename or "",
        "warnings": imported.get("warnings", []),
        "entry_count": len(imported.get("entries", [])),
        "token_budget": imported.get("token_budget", 2048),
        "scan_depth": imported.get("scan_depth", 4),
    }


@router.post("/projects/{project_id}/imports/character-card", status_code=201)
async def import_character_card_route(
    project_id: str, file: Annotated[UploadFile, File()], db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    validate_import_file(file, "card")
    raw = await read_import_file(file)
    try:
        imported = import_character_card(raw, file.filename or "card.json")
    except (CardImportError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = Source(
        project_id=project_id,
        kind="character_card",
        title=imported.name,
        content="",
        extra_data={
            "warnings": imported.warnings,
            "source_format": imported.source_format,
            "source_filename": imported.source_filename,
        },
    )
    db.add(source)
    db.flush()
    character = repository.add_character(
        db,
        project_id=project_id,
        name=imported.name,
        definition=imported.definition,
        source_id=source.id,
    )
    card = CharacterCard(
        character_id=character.id,
        card_version=imported.card_version,
        payload=imported.raw,
        extensions=imported.extensions,
    )
    db.add(card)
    lorebook_id: str | None = None
    if imported.character_book:
        book = Lorebook(
            project_id=project_id,
            owner_character_id=character.id,
            name=imported.character_book.get("name", f"{imported.name} book"),
            scope=LorebookScope.CHARACTER.value,
            extra_data={
                "description": imported.character_book.get("description", ""),
                "scan_depth": imported.character_book.get("scan_depth", 4),
                "token_budget": imported.character_book.get("token_budget", 2048),
                "recursive_scanning": imported.character_book.get("recursive_scanning", True),
                "warnings": imported.warnings,
            },
        )
        db.add(book)
        db.flush()
        lorebook_id = book.id
        for entry in imported.character_book.get("entries", []):
            db.add(
                LorebookEntry(
                    lorebook_id=book.id,
                    name=entry.get("comment", ""),
                    content=entry.get("content", ""),
                    primary_keys=entry.get("keys", []),
                    secondary_keys=entry.get("secondary_keys", []),
                    enabled=entry.get("enabled", True),
                    constant=entry.get("constant", False),
                    selective=entry.get("selective", True),
                    recursive=entry.get("recursive", imported.character_book.get("recursive_scanning", True)),
                    insertion_order=entry.get("order", entry.get("priority", 0)),
                    probability=entry.get("probability", 1.0),
                    scan_depth=entry.get("scan_depth", imported.character_book.get("scan_depth", 4)),
                    enabled_for_model=entry.get("enabled_for_model", []),
                    extra_data={
                        **dict(entry.get("extensions", {})),
                        "source_id": entry.get("source_id", entry.get("id")),
                        "position": entry.get("position", 0),
                        "priority": entry.get("priority", entry.get("order", 0)),
                    },
                )
            )
    db.commit()
    return {
        "character_id": character.id,
        "name": character.name,
        "card_version": imported.card_version,
        "source_format": imported.source_format,
        "source_filename": imported.source_filename,
        "lorebook_id": lorebook_id,
        "entry_count": len(imported.character_book.get("entries", [])) if imported.character_book else 0,
        "warnings": imported.warnings,
        "extensions_preserved": sorted(imported.extensions),
    }


@router.post("/projects/{project_id}/imports/lorebook", status_code=201)
async def import_lorebook_route(
    project_id: str, file: Annotated[UploadFile, File()], db=DbSession
) -> dict[str, Any]:
    handle_errors(repository.get_project, db, project_id)
    validate_import_file(file, "lorebook")
    raw = await read_import_file(file)
    try:
        imported = import_lorebook(raw)
    except (CardImportError, ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = Source(
        project_id=project_id,
        kind="lorebook",
        title=imported.get("name", "Imported lorebook"),
        content=raw.decode("utf-8", errors="replace") if len(raw) <= MAX_IMPORT_BYTES else "",
        extra_data={
            "source_filename": file.filename or "",
            "warnings": imported.get("warnings", []),
        },
    )
    db.add(source)
    db.flush()
    book = Lorebook(
        project_id=project_id,
        name=imported.get("name", "Imported lorebook"),
        scope=LorebookScope.PROJECT.value,
        extra_data={
            "source_id": source.id,
            **{key: imported[key] for key in ("description", "scan_depth", "token_budget", "recursive_scanning", "warnings") if key in imported},
        },
    )
    db.add(book)
    db.flush()
    for entry in imported.get("entries", []):
        db.add(
            LorebookEntry(
                lorebook_id=book.id,
                name=entry.get("comment", ""),
                content=entry.get("content", ""),
                primary_keys=entry.get("keys", []),
                secondary_keys=entry.get("secondary_keys", []),
                enabled=entry.get("enabled", True),
                constant=entry.get("constant", False),
                selective=entry.get("selective", True),
                recursive=entry.get("recursive", imported.get("recursive_scanning", True)),
                insertion_order=entry.get("order", entry.get("priority", 0)),
                probability=entry.get("probability", 1.0),
                scan_depth=entry.get("scan_depth", imported.get("scan_depth", 4)),
                enabled_for_model=entry.get("enabled_for_model", []),
                extra_data={
                    **dict(entry.get("extensions", {})),
                    "source_id": entry.get("id"),
                    "position": entry.get("position", 0),
                    "priority": entry.get("priority", entry.get("order", 0)),
                },
            )
        )
    db.commit()
    return {
        "lorebook_id": book.id,
        "entry_count": len(imported.get("entries", [])),
        "source_filename": file.filename or "",
        "warnings": imported.get("warnings", []),
    }
