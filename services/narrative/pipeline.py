from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from apps.api.app import repository
from services.context.assembly import (
    ContextAssembly,
    GenerationContextRequest,
    build_generation_context,
)
from services.context.claims import (
    ACCEPTED_WITH_WARNINGS,
    VALID,
    ClaimValidation,
    GenerationClaims,
    claim_summary,
    extract_claims,
    validate_claims,
)
from services.context.consistency import ContradictionDetector
from services.context.knowledge import KnowledgeView, build_knowledge_view, knowledge_table
from services.context.tokens import TokenBudget, TokenEstimator, estimator_for
from services.context.trace import GenerationTrace, StageTimer
from services.core.enums import (
    COMMITMENT_TRANSITIONS,
    OPEN_COMMITMENT_STATUSES,
    BeatStatus,
    CommitmentStatus,
    ControlMode,
    EventSource,
    GenerationStatus,
    IntentStatus,
    IntentType,
    Lane,
    MemoryClass,
    MemoryScope,
    PlanStatus,
    SceneStatus,
)
from services.core.events import (
    AI_ACTION,
    CANON_DIVERGENCE,
    COMMITMENT_STATUS_CHANGED,
    DIRECTOR_INTENT_CREATED,
    GENERATION_REJECTED,
    KNOWLEDGE_SUSPECTED,
    MODEL_WRITABLE_EVENT_TYPES,
    POSSESSION_CHANGED,
    SCENE_STAGED,
    SCENE_STAGING_EDITED,
    USER_ACTION,
    WORLD_FACT_MODIFIED,
)
from services.core.models import (
    Character,
    DirectorIntent,
    Event,
    Generation,
    Location,
    Lorebook,
    LorebookEntry,
    Memory,
    Scene,
    SceneParticipant,
    Source,
    StoryCommitment,
)
from services.core.state import StateSnapshot
from services.director.director import DirectorDecision
from services.director.plan import DirectorPlanView
from services.director.router import route as route_intent
from services.lorebook.evaluator import LoreCandidate, evaluate_lore
from services.memory import lifecycle as memory_lifecycle
from services.memory.retrieval import (
    CANDIDATE_POOL,
    MemoryCandidate,
    RetrievalReport,
    retrieve_with_report,
)
from services.performer import (
    PerformerRequest,
    PerformerResult,
    PerformerViolation,
    build_performer_request,
    check_offstage_actors,
    check_required_consequences,
    check_user_agency,
    normalize_style,
    read_performer_result,
    render_performer_prompt,
)
from services.providers.base import HeuristicProvider, LLMProvider, ProviderError, ProviderMessage
from services.providers.capabilities import (
    capabilities_for,
    capabilities_from_mapping,
    degradation_plan,
)

# The Performer schema. Declared with real item shapes: the previous schema
# described every array as ``{"type": "object"}`` with no properties, so a model
# was never told what a valid claim looked like and had to infer it from prose.
#
# Legacy fields (``selected_actor``, ``reason``, ``new_events``, ``state_changes``,
# ``actions``, ``open_commitments``) remain accepted and are not required, so a
# provider that returns the old shape still works. The Performer reads whichever
# shape arrives and folds it into the canonical generation shape for validation.
GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_actor": {"type": "string"},
        "reason": {"type": "string"},
        "prose": {"type": "string"},
        "narration": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "object"}},
        "new_events": {"type": "array", "items": {"type": "object"}},
        "state_changes": {"type": "array", "items": {"type": "object"}},
        "knowledge_changes": {"type": "array", "items": {"type": "object"}},
        "relationship_changes": {"type": "array", "items": {"type": "object"}},
        "open_commitments": {"type": "array", "items": {"type": "object"}},
        "dialogue": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "string"},
                    "name": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["character_id", "line"],
            },
        },
        "actor_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "string"},
                    "name": {"type": "string"},
                    "action": {"type": "string"},
                    "kind": {"type": "string", "enum": ["reaction", "decision", "observation"]},
                },
                "required": ["character_id", "action"],
            },
        },
        "proposed_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": list(MODEL_WRITABLE_EVENT_TYPES),
                    },
                    "character_id": {"type": "string"},
                    "source_character_id": {"type": "string"},
                    "target_character_id": {"type": "string"},
                    "text": {"type": "string"},
                    "action": {"type": "string"},
                    "item": {"type": "string"},
                    "injury": {"type": "string"},
                    "location_id": {"type": "string"},
                    "fact": {"type": "string"},
                    "severity": {"type": "string"},
                },
                "required": ["event_type"],
            },
        },
        "completion_signals": {
            "type": "object",
            "properties": {
                "beat_realised": {"type": "boolean"},
                "beat_partial": {"type": "boolean"},
                "required_satisfied": {"type": "array", "items": {"type": "string"}},
                "required_unmet": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["prose"],
}

SYSTEM_RULES = (
    "Execute the approved scene. Return prose and structured state events separately. "
    "Only assert what the supplied context supports. A character may not know, remember, "
    "possess, or reach anything that is not listed for them. If the context is insufficient, "
    "write the uncertainty into the prose instead of inventing a resolution."
)


@dataclass
class PipelineResult:
    scene_id: str | None = None
    generation_id: str | None = None
    output_text: str = ""
    structured_output: dict[str, Any] | None = None
    lore_debug: dict[str, Any] | None = None
    intent_id: str | None = None
    commitment_id: str | None = None
    timeline_id: str | None = None
    state: dict[str, Any] | None = None
    planner_context: dict[str, Any] | None = None
    checkpoint_node_id: str | None = None
    context_debug: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    contradictions: list[dict[str, Any]] | None = None
    director: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    plan_id: str | None = None
    requires_approval: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


def _apply_plan_edits(plan: Any, edits: dict[str, Any]) -> Any:
    """Apply user edits to a plan, leaving the retained intent untouched."""
    from services.director.plan import Beat

    editable = (
        "objective",
        "summary",
        "desired_direction",
        "completion_condition",
        "required_consequences",
        "likely_consequences",
        "optional_consequences",
        "assumptions",
        "allowed_actions",
        "information_reveals",
        "foreshadowing",
        "participants",
    )
    for key in editable:
        if key in edits and edits[key] is not None:
            setattr(plan, key, edits[key])
    if "beats" in edits and isinstance(edits["beats"], list):
        plan.beats = [Beat.from_dict(beat) if isinstance(beat, dict) else Beat(description=str(beat)) for beat in edits["beats"]]
    if "horizon" in edits and edits["horizon"]:
        from services.core.enums import Horizon

        plan.horizon = Horizon(str(edits["horizon"]))
    return plan


def _scene_participant_ids(db: Any, scene_id: str | None) -> set[str]:
    if not scene_id:
        return set()
    return {
        participant.character_id
        for participant in db.scalars(
            select(SceneParticipant).where(SceneParticipant.scene_id == scene_id)
        )
    }


def _apply_canon_resolution(conflict: dict[str, Any] | None, resolution: str) -> dict[str, Any]:
    payload = dict(conflict or {})
    payload["resolution"] = resolution
    return payload


def _invalidate_on_interruption(plan: Any, reason: str) -> list[str]:
    from services.core.enums import BeatStatus

    invalidated: list[str] = []
    for beat in plan.beats:
        if beat.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}:
            beat.status = BeatStatus.INVALIDATED
            beat.note = reason
            invalidated.append(beat.description)
    return invalidated

# Recent history is the tail of the timeline, never the whole transcript.
RECENT_EVENT_LIMIT = 8
HISTORY_DIGEST_AFTER = 24
DEFAULT_MEMORY_LIMIT = 10
LORE_ACTIVATION_CAP = 24

# Events whose payload is a private belief. Their content is redacted from the
# recent-history block for any viewer other than the belief's owner.
PRIVATE_KNOWLEDGE_EVENTS = frozenset(
    {"knowledge_acquired", "knowledge_suspected", "knowledge_refuted"}
)
WITHHELD_KNOWLEDGE = "[a fact another character has not shared is withheld]"

# Events that are spoken aloud. Whatever they state in front of the scene is
# overheard: listeners gain it as suspicion, never as certainty, because hearing
# something said is not the same as having verified it.
OVERHEARD_EVENT_TYPES = frozenset(
    {"character_spoke", "character_performed_action", "user_action", "ai_action"}
)


class NarrativePipeline:
    def __init__(
        self,
        db: Session,
        provider: LLMProvider | None = None,
        *,
        capabilities: dict[str, Any] | None = None,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        memory_pool: int = CANDIDATE_POOL,
        lore_budget: int | None = None,
        allow_exact_tokens: bool = True,
        director_refinement: bool = False,
        presentation_style: str = "hybrid",
    ) -> None:
        self.db = db
        self.provider = provider or HeuristicProvider()
        self.memory_limit = memory_limit
        self.memory_pool = memory_pool
        self.lore_budget = lore_budget
        self.allow_exact_tokens = allow_exact_tokens
        # Director refinement is opt-in so the deterministic suite, which drives
        # the pipeline with a scripted provider holding a fixed response list,
        # keeps its response accounting. The API enables it for configured
        # providers; the heuristic plan is always available as the fallback.
        self.director_refinement = director_refinement
        # Scene presentation. Coerced rather than rejected: a bad style name is a
        # UI typo, not a reason to fail a turn.
        self.presentation_style = normalize_style(presentation_style)
        self.capabilities = (
            capabilities_from_mapping(capabilities, model=str(getattr(self.provider, "model", "") or ""))
            if capabilities
            else capabilities_for(self.provider)
        )

    def classify_intent(self, text: str, mode: str = "auto") -> str:
        normalized = text.casefold().strip()
        if mode in {IntentType.DIRECTION.value, IntentType.STORY_COMMITMENT.value}:
            return mode
        if mode in {IntentType.WORLD.value, IntentType.RETCON.value, IntentType.NARRATION.value}:
            return mode
        if re.search(r"\b(immediately|now|right now)\b", normalized):
            return IntentType.IMMEDIATE_ACTION.value
        if re.search(r"\b(make|have|ensure|eventually|eventually|goal|plan|want)\b", normalized):
            return IntentType.STORY_COMMITMENT.value
        return IntentType.NARRATION.value

    async def stage(
        self,
        *,
        project_id: str,
        premise: str,
        scene_id: str | None = None,
        character_ids: list[str] | None = None,
    ) -> PipelineResult:
        project = repository.get_project(self.db, project_id)
        if not project.active_timeline_id:
            raise ValueError("Project has no active timeline")
        characters = self._project_characters(project_id, character_ids)
        structured = await self._structured(
            [
                ProviderMessage(
                    role="system",
                    content=(
                        "Stage a narrative premise. Infer reasonable missing details, but do not execute the premise. "
                        "Return assumptions, relevant participants, objective, initial conditions, environmental assumptions, "
                        "potential consequences, and canon conflicts."
                    ),
                ),
                ProviderMessage(role="user", content=premise),
            ],
            {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "time": {"type": "string"},
                    "characters_present": {"type": "array", "items": {"type": "string"}},
                    "objective": {"type": "string"},
                    "initial_conditions": {"type": "array", "items": {"type": "string"}},
                    "environmental_assumptions": {"type": "array", "items": {"type": "string"}},
                    "potential_consequences": {"type": "array", "items": {"type": "string"}},
                    "canon_conflicts": {"type": "array", "items": {"type": "string"}},
                },
            },
        )
        names = [character.name for character in characters]
        provided_characters = structured.get("characters_present")
        staged_names = (
            [str(value) for value in provided_characters if str(value)]
            if isinstance(provided_characters, list) and provided_characters
            else names
        )
        participant_ids = [character.id for character in characters]
        location = str(structured.get("location") or self._infer_location(project_id, premise))
        time = str(structured.get("time") or self._infer_time(premise))
        detected_conflicts = self._canon_conflicts(project_id, premise)
        reported_conflicts = [str(value) for value in structured.get("canon_conflicts", []) if str(value)]
        canon_conflicts = list(dict.fromkeys([*reported_conflicts, *detected_conflicts]))
        source_context = self._source_context(project_id, premise)
        proposal = {
            "location": location,
            "time": time,
            "characters_present": staged_names,
            "objective": str(structured.get("objective") or premise),
            "initial_conditions": [str(value) for value in structured.get("initial_conditions", []) if str(value)],
            "relevant_source_state": source_context,
            "relevant_context": source_context,
            "environmental_assumptions": [
                str(value) for value in structured.get("environmental_assumptions", []) if str(value)
            ]
            or ["The scene is staged from available project context."],
            "potential_consequences": [str(value) for value in structured.get("potential_consequences", []) if str(value)],
            "intended_consequences": [str(value) for value in structured.get("potential_consequences", []) if str(value)],
            "canon_conflicts": canon_conflicts,
            "possible_canon_conflicts": canon_conflicts,
            "assumptions": [
                "Unspecified details are provisional and may be edited before approval.",
                "The premise is an objective, not an already committed event.",
            ],
            "unresolved_assumptions": [
                "The scene still needs an approved participant list."
            ] if not staged_names else [],
            "staging_revision": 0,
            "status": "proposed",
        }
        created_scene = scene_id is None
        if scene_id:
            scene = repository.get_scene(self.db, scene_id)
            if scene.project_id != project_id:
                raise ValueError("Scene does not belong to project")
            if scene.status == SceneStatus.DRAFT.value:
                created_scene = True
            scene.title = self._scene_title(premise, scene.title)
            scene.staging_revision += 1
            scene.approved_staging_revision = None
            proposal["staging_revision"] = scene.staging_revision
            proposal["scene_id"] = scene.id
            scene.staging = dict(proposal)
            scene.status = SceneStatus.STAGED.value
            self.db.add(scene)
            repository.replace_scene_participants(
                self.db,
                scene,
                participant_ids,
            )
        else:
            scene = repository.create_scene(
                self.db,
                project_id=project_id,
                timeline_id=project.active_timeline_id,
                title=self._scene_title(premise),
                staging={},
                participant_ids=participant_ids,
            )
            scene.staging_revision = 1
            scene.approved_staging_revision = None
            proposal["staging_revision"] = scene.staging_revision
            proposal["scene_id"] = scene.id
            scene.staging = dict(proposal)
            scene.status = SceneStatus.STAGED.value
            self.db.add(scene)
        self.db.flush()
        repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=project.active_timeline_id,
            event_type=SCENE_STAGED if created_scene else SCENE_STAGING_EDITED,
            payload={
                "scene_id": scene.id,
                "proposal": proposal,
                "premise": premise,
                "staging_revision": scene.staging_revision,
            },
            source=EventSource.SYSTEM,
        )
        self.db.commit()
        return PipelineResult(
            scene_id=scene.id,
            timeline_id=project.active_timeline_id,
            structured_output=proposal,
            state=repository.current_state(self.db, project.active_timeline_id).to_dict(),
        )

    def edit_staging(
        self,
        *,
        project_id: str,
        scene_id: str,
        updates: dict[str, Any],
        expected_revision: int | None = None,
    ) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if scene.status == SceneStatus.ACTIVE.value:
            raise ValueError("Active scenes must be regenerated on a branch")
        if expected_revision is not None and expected_revision != scene.staging_revision:
            raise ValueError("Staging revision is stale")
        proposal = dict(scene.staging or {})
        character_ids = updates.pop("character_ids", None)
        for key, value in updates.items():
            if value is not None:
                proposal[key] = value
        scene.staging_revision += 1
        proposal["staging_revision"] = scene.staging_revision
        proposal["scene_id"] = scene.id
        if character_ids is not None:
            repository.replace_scene_participants(self.db, scene, character_ids)
            proposal["characters_present"] = [
                character.name
                for character in self._project_characters(project_id, character_ids)
            ]
        scene.staging = dict(proposal)
        scene.status = SceneStatus.STAGED.value
        scene.approved_staging_revision = None
        self.db.add(scene)
        self.db.flush()
        repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=scene.timeline_id,
            event_type=SCENE_STAGING_EDITED,
            payload={
                "scene_id": scene.id,
                "proposal": proposal,
                "staging_revision": scene.staging_revision,
            },
            source=EventSource.USER,
        )
        self.db.commit()
        return PipelineResult(
            scene_id=scene.id,
            timeline_id=scene.timeline_id,
            structured_output=proposal,
            state=repository.current_state(self.db, scene.timeline_id).to_dict(),
        )

    async def regenerate_staging(
        self,
        *,
        project_id: str,
        scene_id: str,
        premise: str | None = None,
        character_ids: list[str] | None = None,
    ) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if scene.status == SceneStatus.ACTIVE.value:
            raise ValueError("Active scenes must be regenerated on a branch")
        if character_ids is None:
            character_ids = [
                participant.character_id
                for participant in self.db.scalars(
                    select(SceneParticipant).where(SceneParticipant.scene_id == scene_id)
                )
            ]
        return await self.stage(
            project_id=project_id,
            premise=premise or scene.title,
            scene_id=scene_id,
            character_ids=character_ids,
        )

    async def continue_scene(
        self,
        *,
        project_id: str,
        scene_id: str,
        mode: str = "auto",
        user_input: str = "",
        possessed_character_id: str | None = None,
        actor_character_id: str | None = None,
        commit_on_success: bool = True,
        director_plan: Any = None,
        director_plan_id: str | None = None,
    ) -> PipelineResult:
        """Advance an approved scene.

        ``director_plan`` carries an already-approved plan straight through to
        the Performer. It exists so executing a plan does not re-run the router
        on the beat text: a plan is already a decision, and re-routing it would
        either propose a second plan or silently replace the one the user
        approved.
        """
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if scene.status != SceneStatus.ACTIVE.value:
            raise ValueError("Scene must be approved before continuation")
        participants = list(
            self.db.scalars(
                select(SceneParticipant)
                .where(SceneParticipant.scene_id == scene_id)
                .order_by(SceneParticipant.character_id)
            )
        )
        participant_ids = {participant.character_id for participant in participants}
        if not participant_ids:
            raise ValueError("Scene has no participants")
        if actor_character_id and actor_character_id not in participant_ids:
            raise ValueError("Actor override must be a scene participant")
        if possessed_character_id and possessed_character_id not in participant_ids:
            raise ValueError("Possessed character must be a scene participant")
        # Possession is an *execution authority*, and there is one of it.
        #
        # Previously a per-call ``possessed_character_id`` changed routing and the
        # actor pick while a persisted ``control_mode`` changed event authority, so
        # the two could disagree: a client could send possession and get AI-sourced
        # events, or omit it and still get user-sourced ones. The per-call value now
        # *reconciles* the persisted control modes, and both downstream consumers —
        # routing and event attribution — read the same resolved set.
        control_modes = self._resolve_control_modes(participants, possessed_character_id)
        actor_id, actor_reason = self._choose_actor(
            project_id,
            scene,
            participants,
            user_input,
            possessed_character_id,
            actor_character_id,
        )
        character_map = {character.id: character for character in self._project_characters(project_id)}
        actor = character_map.get(actor_id or "")
        timer = StageTimer()
        state = repository.current_state(self.db, scene.timeline_id)
        trace = GenerationTrace(
            project_id=project_id,
            timeline_id=scene.timeline_id,
            scene_id=scene.id,
            actor_character_id=actor_id or None,
            input_mode=mode,
            provider=type(self.provider).__name__,
            model=str(getattr(self.provider, "model", "heuristic")),
            capabilities={**self.capabilities.to_dict(), "degradation": degradation_plan(self.capabilities)},
            actor_selection={"actor_character_id": actor_id, "reason": actor_reason},
        )
        # Phase A: the router only annotates. Lanes 1 and 2 follow the exact
        # current code paths below; lanes 3 and 4 are enacted in Phase B.
        # Routing is a pure synchronous function — it never calls the provider,
        # so scripted response counts are unaffected.
        with timer.measure("intent_routing"):
            routed_intent = route_intent(
                user_input,
                mode=mode,
                participant_names=[character.name for character in character_map.values()],
                possessed_character_id=possessed_character_id,
                possessed_character_name=(
                    character_map[possessed_character_id].name
                    if possessed_character_id and possessed_character_id in character_map
                    else None
                ),
            )
        director_lane = Lane(routed_intent.lane) if routed_intent.lane else Lane.DIRECTION
        interruption: dict[str, Any] = {"interrupted": False}
        if director_plan is None and user_input.strip():
            with timer.measure("interruption"):
                interruption = self._interruption_for(
                    project_id=project_id,
                    scene=scene,
                    user_input=user_input,
                    lane=director_lane,
                )
            if interruption.get("interrupted") and interruption.get("mode") == "superseded":
                # The user's new direction replaces the plan entirely, so there is
                # no running plan left to perform. Fall through to the Director,
                # which will read the new request and propose from it.
                interruption["superseded_plan_id"] = interruption.get("plan_id")
        if director_plan is not None:
            # An approved plan is already a decision. Re-validate it against the
            # state as it stands now, render the Performer contract from it, and
            # propose nothing new.
            director = self._perform_approved_plan(
                project_id=project_id,
                plan=director_plan,
                plan_id=director_plan_id,
                scene=scene,
                state=state,
                characters=character_map,
                participant_ids=participant_ids,
                actor_id=actor_id,
            )
        else:
            with timer.measure("director"):
                director = await self._direct_turn(
                    project_id=project_id,
                    scene=scene,
                    text=user_input,
                    mode=mode,
                    state=state,
                    characters=character_map,
                    participants=participants,
                    participant_ids=participant_ids,
                    possessed_character_id=possessed_character_id,
                    routed_intent=routed_intent,
                    director_lane=director_lane,
                    actor_id=actor_id,
                )
        trace.director = director.trace()
        if director.commitments_created:
            # Long-horizon intent: the outcome is recorded, never executed. The
            # turn stops here so a betrayal cannot happen on the same request
            # that asked for it eventually.
            commitment_ids, commitment_payloads = self._persist_director_commitments(
                director, scene=scene, project_id=project_id
            )
            plan_row = self._persist_director_plan(director, scene=scene, project_id=project_id)
            from services.director import director as director_service

            # A long-horizon request is complete the moment it is recorded: the
            # plan's work *was* the commitment, not a performed scene. It moves
            # through ``executing`` because that is exactly what happened — the
            # commitment was written — and there is no generation to point at.
            director_service.set_plan_status(self.db, plan_row, PlanStatus.EXECUTING)
            director_service.set_plan_status(self.db, plan_row, PlanStatus.COMPLETED)
            plan_row.generation_id = None
            self.db.add(plan_row)
            director.view.plan_id = plan_row.id
            director.view.status = PlanStatus.COMPLETED
            director.view.execution = {
                "mode": "commitment_only",
                "commitment_ids": commitment_ids,
                "commitments": commitment_payloads,
            }
            director.view.plan = director_service.plan_from_row(plan_row)
            director.view.plan.status = PlanStatus.COMPLETED
            trace.director = director.trace()
            trace.status = "traced"
            trace.timings = timer.to_list()
            payload = trace.to_dict()
            payload["performance"] = {
                "stages": timer.to_list(),
                "total_ms": timer.total_ms,
                "by_stage": timer.by_name(),
            }
            self.db.commit()
            return PipelineResult(
                scene_id=scene.id,
                timeline_id=scene.timeline_id,
                intent_id=director.view.intent_id,
                output_text=director.committed_summary(),
                state=state.to_dict(),
                planner_context=self._planner_context(project_id, scene.timeline_id),
                director=payload,
                plan=self._plan_payload(plan_row),
                plan_id=plan_row.id,
                requires_approval=False,
            )
        if director.blocked or director.requires_approval:
            # No performance, no events, no state. The turn returns a decision the
            # user can approve, edit, regenerate, or reject.
            plan_row = self._persist_director_plan(director, scene=scene, project_id=project_id)
            director.view.execution = {
                "mode": "awaiting_decision",
                "blocked": director.blocked,
                "reason": director.block_reason,
            }
            trace.director = director.trace()
            trace.status = "traced"
            trace.timings = timer.to_list()
            payload = trace.to_dict()
            payload["performance"] = {
                "stages": timer.to_list(),
                "total_ms": timer.total_ms,
                "by_stage": timer.by_name(),
            }
            self.db.commit()
            return PipelineResult(
                scene_id=scene.id,
                timeline_id=scene.timeline_id,
                intent_id=director.view.intent_id,
                output_text="",
                state=state.to_dict(),
                director=payload,
                plan=director.view.to_dict(),
                plan_id=plan_row.id,
                requires_approval=director.requires_approval,
            )
        performer_block = director.performer_block
        with timer.measure("planner_context"):
            planner_context = self._planner_context(project_id, scene.timeline_id)
        with timer.measure("memory_retrieval"):
            memories, retrieval_report = self._memories(project_id, scene, actor, user_input, state)
        with timer.measure("lore_evaluation"):
            lore_debug = self._lore_debug(project_id, scene, actor, user_input, memories)
        knowledge = build_knowledge_view(state, viewer_character_id=actor_id)
        detector = self._detector(state, scene, actor, knowledge)
        with timer.measure("performer_request"):
            performer_request = self._performer_request(
                project_id=project_id,
                scene=scene,
                state=state,
                characters=character_map,
                participants=participants,
                participant_ids=participant_ids,
                actor=actor,
                actor_id=actor_id,
                actor_reason=actor_reason,
                user_input=user_input,
                director=director,
                planner_context=planner_context,
                recent_events=self._recent_events(scene.timeline_id, actor_id=actor.id if actor else None),
                control_modes=control_modes,
            )
        with timer.measure("context_assembly"):
            assembly, prompt = self._assemble(
                scene=scene,
                actor=actor,
                user_input=user_input,
                state=state,
                knowledge=knowledge,
                planner_context=planner_context,
                memories=memories,
                lore_debug=lore_debug,
                performer_block=performer_block,
                performer_request=performer_request,
            )
        trace.context = assembly.to_dict()
        trace.retrieved_memories = [memory.to_dict() for memory in memories]
        trace.retrieval = retrieval_report.to_dict()
        trace.activated_lore = [
            {
                "entry_id": entry.entry_id,
                "name": entry.name,
                "scope": entry.scope,
                "tokens": entry.token_count,
                "reasons": entry.reasons,
            }
            for entry in lore_debug.activated
        ]
        trace.lore_debug = lore_debug.to_dict()
        trace.active_commitments = list(planner_context.get("commitments", []))
        generation = repository.add_generation(
            self.db,
            project_id=project_id,
            scene_id=scene.id,
            timeline_id=scene.timeline_id,
            status=GenerationStatus.RUNNING.value,
            input_text=user_input,
            provider_name=type(self.provider).__name__,
            model_name=getattr(self.provider, "model", "heuristic"),
            lore_debug=lore_debug.to_dict(),
            context_debug=assembly.to_dict(),
        )
        trace.generation_id = generation.id
        try:
            with timer.measure("llm_request"):
                structured = await self._structured(
                    [
                        ProviderMessage(role="system", content=SYSTEM_RULES),
                        ProviderMessage(role="user", content=prompt),
                    ],
                    GENERATION_SCHEMA,
                )
            with timer.measure("performer_result"):
                # The Performer reads its own structured result, so a claim that
                # names a character who is not in the scene is dropped here rather
                # than reaching validation. Validation is a safety net, not the
                # Performer's first line of defence about its own cast.
                performer_result = read_performer_result(structured, request=performer_request)
                performer_violations = self._performer_violations(
                    performer_result, request=performer_request, state=state
                )
                if performer_violations:
                    raise ValueError(
                        "Performer proposal violated the scene contract: "
                        + "; ".join(violation.summary for violation in performer_violations)
                    )
            with timer.measure("claim_extraction"):
                self._validate_generation_shape(structured, performer_result=performer_result)
                provider_actor = str(structured.get("selected_actor") or "")
                event_actor_id = actor_id or ""
                if not actor_character_id and provider_actor in participant_ids:
                    event_actor_id = provider_actor
                # Fold the Performer's structured result into the canonical
                # generation shape, then extract and validate through the existing
                # pipeline unchanged. The Performer gets a richer output structure
                # without forking the validation path.
                claims = extract_claims(
                    performer_result.to_generation_shape(
                        actor_id=event_actor_id,
                        participant_ids=participant_ids,
                    ),
                    actor_id=event_actor_id,
                    participant_ids=participant_ids,
                )
            with timer.measure("claim_validation"):
                validation = validate_claims(claims, state, detector=detector)
                # The prose is presentation, not truth, so a prose problem is
                # recorded as a warning and never blocks the turn. But it is
                # still checked: prose that states what the actor never learned
                # is how a leak hides in plain sight.
                for warning in detector.for_prose(claims.prose):
                    validation.warnings.append(warning)
                if validation.status == VALID and validation.warnings:
                    validation.status = ACCEPTED_WITH_WARNINGS
        except Exception as exc:
            if generation.status == GenerationStatus.RUNNING.value:
                generation.status = GenerationStatus.FAILED.value
                generation.error = str(exc)[:500]
            generation.trace = self._trace_payload(trace, timer, None, None, None)
            self.db.add(generation)
            if commit_on_success:
                self.db.commit()
            else:
                self.db.flush()
            raise
        if not validation.valid:
            # Nothing is committed. The generation is recorded as rejected with
            # the reasons and the recovery options, and the turn can be retried,
            # regenerated, edited, or discarded without touching narrative state.
            generation.status = GenerationStatus.REJECTED.value
            generation.output_text = claims.prose
            generation.validation = validation.to_dict()
            generation.error = "; ".join(validation.errors)[:500]
            trace.generated_events = claim_summary(claims)
            trace.contradictions = [warning.to_dict() for warning in validation.warnings]
            generation.trace = self._trace_payload(trace, timer, claims, validation, None)
            self.db.add(generation)
            if commit_on_success:
                self.db.commit()
            else:
                self.db.flush()
            raise ValueError(
                "Generated claims were rejected before commit: " + "; ".join(validation.errors)
            )
        # A required consequence the Performer did not realise is a demand that
        # failed, and the turn must not report success while quietly dropping it.
        # The user would be told the plan is progressing when it is not, which is
        # the same silent dishonesty as an unmet approval gate.
        satisfied_required, unmet_required = check_required_consequences(
            performer_result, request=performer_request
        )
        if unmet_required:
            generation.status = GenerationStatus.REJECTED.value
            generation.output_text = claims.prose
            generation.validation = validation.to_dict()
            generation.error = "Required consequences not realised: " + "; ".join(
                unmet_required
            )[:500]
            trace.performer = self._performer_trace(
                request=performer_request,
                result=performer_result,
                plan=director.view.plan,
                plan_status=PlanStatus.PROPOSED,
                validation=validation,
                interruption=interruption,
            )
            trace.generated_events = claim_summary(claims)
            generation.trace = self._trace_payload(trace, timer, claims, validation, None)
            self.db.add(generation)
            if commit_on_success:
                self.db.commit()
            else:
                self.db.flush()
            raise ValueError(
                "The Performer did not realise required consequences: "
                + "; ".join(unmet_required)
            )
        selected_actor = event_actor_id
        actor = character_map.get(selected_actor)
        # Read the same resolved map actor selection and routing used, so a
        # per-call possession produces user-authoritative events rather than only
        # a possessed actor pick.
        user_controlled_ids = {
            character_id
            for character_id, mode in control_modes.items()
            if mode == ControlMode.USER.value
        }
        user_authoritative = selected_actor in user_controlled_ids
        prose = claims.prose.strip() or self._fallback_prose(scene, actor, user_input)
        events = [(claim.event_type, dict(claim.payload)) for claim in validation.accepted]
        if not events:
            events = [
                (
                    USER_ACTION if user_authoritative else AI_ACTION,
                    {
                        "character_id": selected_actor,
                        "text": prose,
                        "actor_id": selected_actor,
                        "mode": mode,
                        "authoritative": user_authoritative,
                    },
                )
            ]
        committed_event_ids: list[str] = []
        committed_events: list[tuple[str, dict[str, Any], Any]] = []
        last_node = None
        state = repository.current_state(self.db, scene.timeline_id)
        with timer.measure("state_projection"):
            for index, (event_type, payload) in enumerate(events):
                # Authority is decided **per claim**, by the character the claim
                # names, not once per turn by the selected actor.
                #
                # The previous code rewrote every `character_spoke` and
                # `character_performed_action` in the turn to `user_action`
                # whenever the selected actor was user-controlled. In a
                # multi-actor turn that silently recorded the witness's dialogue
                # as the detective's authoritative action while the payload still
                # carried the witness's id. The event type, the source, and the
                # indexed actor all said one thing and the payload another.
                claim_actor = str(payload.get("character_id") or "") or selected_actor
                claim_is_user = claim_actor in user_controlled_ids
                if claim_is_user and event_type in {
                    AI_ACTION,
                    "character_spoke",
                    "character_performed_action",
                }:
                    event_type = USER_ACTION
                    payload = {**payload, "authoritative": True}
                event, last_node, state = repository.append_event(
                    self.db,
                    project_id=project_id,
                    timeline_id=scene.timeline_id,
                    event_type=event_type,
                    payload={**payload, "generation_id": generation.id},
                    source=EventSource.USER if claim_is_user else EventSource.AI,
                    # The indexed actor is the character this event is about, not
                    # the turn's focal actor, so per-actor attribution survives
                    # into the column instead of existing only in the payload.
                    actor_character_id=claim_actor or None,
                    idempotency_key=f"{generation.id}:{index}",
                )
                committed_event_ids.append(event.id)
                committed_events.append((event_type, payload, event))
        with timer.measure("overhearing"):
            overheard_ids = self._overhear(
                project_id=project_id,
                generation_id=generation.id,
                timeline_id=scene.timeline_id,
                participant_ids=participant_ids,
                committed=committed_events,
                state=state,
            )
            committed_event_ids.extend(overheard_ids)
        with timer.measure("memory_lifecycle"):
            retention = self._persist_memories(
                project_id,
                scene,
                actor_id=selected_actor,
                prose=prose,
                committed_events=committed_events,
            )
        generation.status = GenerationStatus.COMPLETED.value
        generation.output_text = prose
        generation.validation = validation.to_dict()
        generation.structured_output = {
            **structured,
            "selected_actor": selected_actor,
            "committed_event_ids": committed_event_ids,
            "planner_context": planner_context,
            "checkpoint_node_id": last_node.id if last_node else None,
            "claims": claims.to_dict(),
            "rejected_claims": [claim.to_dict() for claim in validation.rejected],
            "memory_retention": {key: value for key, value in retention.items() if key != "proposals"},
        }
        generation.checkpoint_node_id = last_node.id if last_node else None
        auto_row: Any = None
        if director_plan_id:
            # An approved plan has now produced a real, validated generation, so
            # the plan row can be tied to the artifact that performed it.
            plan_row = repository.get_director_plan(self.db, director_plan_id)
            plan_row.generation_id = generation.id
            plan_row.checkpoint_node_id = generation.checkpoint_node_id
            self.db.add(plan_row)
        elif director.plan_row is None and director.view.plan.lane in {Lane.DIRECTION, Lane.LONG_HORIZON}:
            # A lane-3 or lane-4 turn that auto-proceeded still has a plan, and
            # the plan is the record of what directed this generation. Persisting
            # it linked to the generation is what makes "why did this happen"
            # answerable after the fact.
            auto_row = self._persist_director_plan(director, scene=scene, project_id=project_id)
            self._advance_plan_beats(director, plan_row=auto_row, scene=scene, generation_id=generation.id)
        trace.performer = self._performer_trace(
            request=performer_request,
            result=performer_result,
            plan=director.view.plan,
            plan_status=(
                PlanStatus(
                    (auto_row.status if auto_row is not None else "")
                    or (repository.get_director_plan(self.db, director_plan_id).status if director_plan_id else "")
                    or PlanStatus.PROPOSED.value
                )
            ),
            validation=validation,
            interruption=interruption,
        )
        trace.state_changes = [self._event_dict(event) for _type, _payload, event in committed_events]
        trace.memory_writes = retention["proposals"]
        generation.trace = self._trace_payload(trace, timer, claims, validation, retention)
        self.db.add(generation)
        if commit_on_success:
            self.db.commit()
        else:
            self.db.flush()
        return PipelineResult(
            scene_id=scene.id,
            generation_id=generation.id,
            output_text=prose,
            structured_output=generation.structured_output,
            lore_debug=lore_debug.to_dict(),
            timeline_id=scene.timeline_id,
            state=state.to_dict(),
            planner_context=planner_context,
            checkpoint_node_id=generation.checkpoint_node_id,
            context_debug=assembly.to_dict(),
            validation=validation.to_dict(),
            contradictions=[warning.to_dict() for warning in validation.warnings],
            # The full trace, not just the Director block, so every path returns
            # the same shape and a caller never has to know which branch it took.
            director=trace.to_dict(),
            plan=self._plan_payload(
                repository.get_director_plan(self.db, director_plan_id)
            )
            if director_plan_id
            else (self._plan_payload(auto_row) if auto_row is not None else None),
            plan_id=director_plan_id or (auto_row.id if auto_row is not None else None),
        )

    async def direct(
        self,
        *,
        project_id: str,
        scene_id: str,
        text: str,
        intent_type: str = IntentType.STORY_COMMITMENT.value,
        horizon: str = "short",
    ) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        resolved_type = self.classify_intent(text, intent_type)
        intent = DirectorIntent(
            project_id=project_id,
            scene_id=scene_id,
            intent_type=resolved_type,
            text=text,
            goal=text,
            horizon=horizon,
            status=IntentStatus.PENDING.value,
        )
        self.db.add(intent)
        self.db.flush()
        commitment = StoryCommitment(
            project_id=project_id,
            intent_id=intent.id,
            timeline_id=scene.timeline_id,
            description=text,
            status=CommitmentStatus.CREATED.value,
            priority=1,
            created_sequence=repository.latest_node(self.db, scene.timeline_id).sequence,
        )
        self.db.add(commitment)
        self.db.flush()
        repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=scene.timeline_id,
            event_type=DIRECTOR_INTENT_CREATED,
            payload={
                "intent_id": intent.id,
                "commitment_id": commitment.id,
                "type": resolved_type,
                "goal": text,
                "horizon": horizon,
                "literal_action": resolved_type == IntentType.IMMEDIATE_ACTION.value,
            },
            source=EventSource.USER,
        )
        if resolved_type == IntentType.IMMEDIATE_ACTION.value:
            repository.append_event(
                self.db,
                project_id=project_id,
                timeline_id=scene.timeline_id,
                event_type=USER_ACTION,
                payload={"text": text, "intent_id": intent.id, "immediate": True, "authoritative": True},
                source=EventSource.USER,
            )
            intent.status = IntentStatus.COMPLETED.value
            commitment.status = CommitmentStatus.FULFILLED.value
        self.db.commit()
        return PipelineResult(
            scene_id=scene_id,
            intent_id=intent.id,
            commitment_id=commitment.id,
            timeline_id=scene.timeline_id,
            state=repository.current_state(self.db, scene.timeline_id).to_dict(),
        )

    def approve_scene(
        self,
        *,
        project_id: str,
        scene_id: str,
        staging_revision: int | None = None,
    ) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if staging_revision is not None and staging_revision != scene.staging_revision:
            raise ValueError("Staging revision is stale")
        if scene.status == SceneStatus.STAGED.value:
            scene.approved_staging_revision = scene.staging_revision
            self.db.add(scene)
            repository.start_scene_event(self.db, scene)
        elif scene.status != SceneStatus.ACTIVE.value:
            raise ValueError("Only staged or active scenes can be approved")
        self.db.commit()
        return PipelineResult(
            scene_id=scene.id,
            timeline_id=scene.timeline_id,
            state=repository.current_state(self.db, scene.timeline_id).to_dict(),
        )

    def cancel_scene(self, *, project_id: str, scene_id: str) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if scene.status in {SceneStatus.DRAFT.value, SceneStatus.STAGED.value, SceneStatus.ACTIVE.value}:
            repository.cancel_scene_event(self.db, scene)
        elif scene.status != SceneStatus.CANCELLED.value:
            raise ValueError("Scene cannot be cancelled in its current state")
        self.db.commit()
        return PipelineResult(
            scene_id=scene.id,
            timeline_id=scene.timeline_id,
            state=repository.current_state(self.db, scene.timeline_id).to_dict(),
        )

    def end_scene(self, *, project_id: str, scene_id: str) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if scene.status == SceneStatus.ACTIVE.value:
            repository.end_scene_event(self.db, scene)
        else:
            repository.set_scene_status(self.db, scene, SceneStatus.COMPLETED)
        self.db.commit()
        return PipelineResult(
            scene_id=scene.id,
            timeline_id=scene.timeline_id,
            state=repository.current_state(self.db, scene.timeline_id).to_dict(),
        )

    async def regenerate_scene(
        self,
        *,
        project_id: str,
        scene_id: str,
        user_input: str = "",
        mode: str = "auto",
    ) -> PipelineResult:
        source_scene = repository.get_scene(self.db, scene_id)
        if source_scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        try:
            source_node = repository.latest_node(self.db, source_scene.timeline_id)
            branch = repository.fork_timeline(
                self.db,
                source_timeline_id=source_scene.timeline_id,
                source_node_id=source_node.id,
                name=f"{source_scene.title} / regenerated",
            )
            project = repository.get_project(self.db, project_id)
            repository.update_project_timeline(self.db, project, branch.id)
            participant_ids = [
                participant.character_id
                for participant in self.db.scalars(
                    select(SceneParticipant).where(SceneParticipant.scene_id == source_scene.id)
                )
            ]
            for existing_scene in repository.list_scenes(self.db, project_id):
                if existing_scene.timeline_id == source_scene.timeline_id and existing_scene.current:
                    existing_scene.current = False
                    self.db.add(existing_scene)
            regenerated_scene = repository.create_scene(
                self.db,
                project_id=project_id,
                timeline_id=branch.id,
                title=source_scene.title,
                staging=source_scene.staging,
                participant_ids=participant_ids,
            )
            regenerated_scene.staging = {
                **dict(source_scene.staging or {}),
                "scene_id": regenerated_scene.id,
                "staging_revision": 1,
            }
            regenerated_scene.staging_revision = 1
            regenerated_scene.status = SceneStatus.STAGED.value
            self.db.add(regenerated_scene)
            repository.start_scene_event(self.db, regenerated_scene)
            result = await self.continue_scene(
                project_id=project_id,
                scene_id=regenerated_scene.id,
                mode=mode,
                user_input=user_input,
                commit_on_success=False,
            )
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def possess(self, *, project_id: str, scene_id: str, character_id: str) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        participant = self.db.scalar(
            select(SceneParticipant).where(
                SceneParticipant.scene_id == scene_id,
                SceneParticipant.character_id == character_id,
            )
        )
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if participant is None:
            raise ValueError("Character is not a scene participant")
        participant.control_mode = ControlMode.USER.value
        self.db.add(participant)
        event, _, state = repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=scene.timeline_id,
            event_type=POSSESSION_CHANGED,
            payload={"scene_id": scene_id, "character_id": character_id, "control_mode": ControlMode.USER.value},
            source=EventSource.USER,
            actor_character_id=character_id,
        )
        self.db.commit()
        return PipelineResult(scene_id=scene_id, timeline_id=scene.timeline_id, state=state.to_dict(), generation_id=event.id)

    def release_possession(self, *, project_id: str, scene_id: str, character_id: str) -> PipelineResult:
        scene = repository.get_scene(self.db, scene_id)
        participant = self.db.scalar(
            select(SceneParticipant).where(
                SceneParticipant.scene_id == scene_id,
                SceneParticipant.character_id == character_id,
            )
        )
        if scene.project_id != project_id:
            raise ValueError("Scene does not belong to project")
        if participant is None:
            raise ValueError("Character is not a scene participant")
        participant.control_mode = ControlMode.AI.value
        self.db.add(participant)
        _, _, state = repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=scene.timeline_id,
            event_type=POSSESSION_CHANGED,
            payload={"scene_id": scene_id, "character_id": character_id, "control_mode": ControlMode.AI.value},
            source=EventSource.USER,
        )
        self.db.commit()
        return PipelineResult(scene_id=scene_id, timeline_id=scene.timeline_id, state=state.to_dict())

    def create_canon_divergence(
        self,
        *,
        project_id: str,
        timeline_id: str,
        text: str,
        canon_reference: str = "",
    ) -> PipelineResult:
        event, _, state = repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=timeline_id,
            event_type=CANON_DIVERGENCE,
            payload={"text": text, "canon_reference": canon_reference, "accepted": True},
            source=EventSource.USER,
        )
        self.db.commit()
        return PipelineResult(timeline_id=timeline_id, generation_id=event.id, state=state.to_dict())

    def reject_generation(
        self, *, project_id: str, generation_id: str, reason: str = "rejected by operator"
    ) -> PipelineResult:
        """Record that a committed generation was rejected after the fact.

        Events are append-only, so this cannot un-happen. What it does is mark
        the generation as rejected in the projection, so a later reader can tell
        the difference between a beat the engine stands behind and one the
        operator has disowned.
        """
        generation = repository.get_generation(self.db, generation_id)
        if generation.project_id != project_id:
            raise ValueError("Generation does not belong to project")
        timeline_id = generation.timeline_id or self._default_timeline(project_id)
        event, _, state = repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=timeline_id,
            event_type=GENERATION_REJECTED,
            payload={
                "generation_id": generation.id,
                "reason": reason,
                "scene_id": generation.scene_id,
            },
            source=EventSource.USER,
        )
        generation.status = GenerationStatus.REJECTED.value
        generation.error = reason[:500]
        self.db.add(generation)
        self.db.commit()
        return PipelineResult(
            timeline_id=timeline_id,
            generation_id=generation.id,
            state=state.to_dict(),
        )

    def correct_state(
        self,
        *,
        project_id: str,
        timeline_id: str,
        fact_id: str,
        changes: dict[str, Any],
        note: str = "",
    ) -> PipelineResult:
        """Apply an operator's correction to a world fact.

        This is the third option offered on a contradiction warning. It appends
        a modification rather than editing the projection, so the correction is
        itself auditable.
        """
        event, _, state = repository.append_event(
            self.db,
            project_id=project_id,
            timeline_id=timeline_id,
            event_type=WORLD_FACT_MODIFIED,
            payload={"fact_id": fact_id, **changes, "note": note, "corrected": True},
            source=EventSource.USER,
        )
        self.db.commit()
        return PipelineResult(timeline_id=timeline_id, generation_id=event.id, state=state.to_dict())

    def generation_trace(self, *, project_id: str, generation_id: str) -> dict[str, Any]:
        """The stored trace for one generation, with nothing sensitive in it."""
        generation = repository.get_generation(self.db, generation_id)
        if generation.project_id != project_id:
            raise ValueError("Generation does not belong to project")
        return {
            "generation_id": generation.id,
            "project_id": generation.project_id,
            "timeline_id": generation.timeline_id,
            "scene_id": generation.scene_id,
            "status": generation.status,
            "provider_name": generation.provider_name,
            "model_name": generation.model_name,
            "input_text": generation.input_text,
            "output_text": generation.output_text,
            "error": generation.error,
            "context": generation.context_debug or {},
            "validation": generation.validation or {},
            "trace": generation.trace or {},
            "lore_debug": generation.lore_debug or {},
            "created_at": generation.created_at.isoformat() if generation.created_at else None,
        }

    def list_generation_traces(self, *, project_id: str, timeline_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = repository.list_generations(
            self.db, project_id=project_id, timeline_id=timeline_id, limit=limit
        )
        return [
            {
                "generation_id": row.id,
                "scene_id": row.scene_id,
                "status": row.status,
                "provider_name": row.provider_name,
                "model_name": row.model_name,
                "input_text": row.input_text,
                "total_tokens": (row.context_debug or {}).get("total_tokens"),
                "max_input_tokens": (row.context_debug or {}).get("max_input_tokens"),
                "validation_status": (row.validation or {}).get("status"),
                "contradiction_count": len((row.trace or {}).get("contradictions", [])),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def memory_inspection(
        self,
        *,
        project_id: str,
        timeline_id: str,
        query: str = "",
        character_id: str | None = None,
        limit: int = 40,
    ) -> dict[str, Any]:
        """Which memories are active, which were superseded, and what retrieval sees.

        Answering "which memories are active" from the application is a
        milestone requirement, so the same filter and rank the pipeline uses is
        exposed rather than a second, divergent implementation.
        """
        state = repository.current_state(self.db, timeline_id)
        rows = repository.list_memories(
            self.db, project_id=project_id, timeline_id=timeline_id, limit=2000
        )
        candidates = [
            MemoryCandidate(
                id=memory.id,
                content=memory.content,
                memory_class=MemoryClass(memory.memory_class),
                importance=memory.importance,
                created_at=memory.created_at,
                character_id=memory.character_id,
                scene_id=memory.scene_id,
                metadata={**(memory.metadata_json or {}), "timeline_id": memory.timeline_id},
                scope=MemoryScope(memory.scope) if memory.scope else MemoryScope.SCENE,
                valid_from_sequence=memory.valid_from_sequence,
                is_active=bool(memory.is_active),
                source_event_id=memory.source_event_id,
                inherited_from_id=str((memory.metadata_json or {}).get("inherited_from_memory_id") or "")
                or None,
            )
            for memory in rows
        ]
        lineage = repository.timeline_lineage(self.db, timeline_id)
        selected, report = retrieve_with_report(
            candidates,
            query,
            limit=limit,
            character_ids={character_id} if character_id else None,
            timeline_ids=lineage,
            lineage_sequences=repository.timeline_lineage_sequences(self.db, timeline_id),
        )
        by_class: dict[str, int] = {}
        by_scope: dict[str, int] = {}
        for memory in rows:
            by_class[memory.memory_class] = by_class.get(memory.memory_class, 0) + 1
            by_scope[memory.scope or "scene"] = by_scope.get(memory.scope or "scene", 0) + 1
        return {
            "project_id": project_id,
            "timeline_id": timeline_id,
            "timeline_lineage": sorted(lineage),
            "total": len(rows),
            "active": sum(1 for memory in rows if memory.is_active),
            "superseded": sum(1 for memory in rows if not memory.is_active),
            "by_class": by_class,
            "by_scope": by_scope,
            "retrieval": report.to_dict(),
            "knowledge": knowledge_table(state),
            "selected": [memory.to_dict() for memory in selected],
            "memories": [
                {
                    "id": memory.id,
                    "content": memory.content,
                    "memory_class": memory.memory_class,
                    "scope": memory.scope,
                    "importance": memory.importance,
                    "is_active": memory.is_active,
                    "superseded_by_id": memory.superseded_by_id,
                    "source_event_id": memory.source_event_id,
                    "character_id": memory.character_id,
                    "scene_id": memory.scene_id,
                    "timeline_id": memory.timeline_id,
                    "valid_from_sequence": memory.valid_from_sequence,
                    "created_at": memory.created_at.isoformat() if memory.created_at else None,
                }
                for memory in rows[:limit]
            ],
        }

    def inspect(self, project_id: str, timeline_id: str) -> dict[str, Any]:
        project = repository.get_project(self.db, project_id)
        state = repository.current_state(self.db, timeline_id)
        memories = repository.list_memories(
            self.db, project_id=project_id, timeline_id=timeline_id, limit=200
        )
        latest_generation = self.db.scalar(
            select(Generation)
            .where(Generation.project_id == project_id, Generation.timeline_id == timeline_id)
            .order_by(desc(Generation.created_at))
        )
        events = repository.list_events(self.db, timeline_id)
        return {
            "project_id": project_id,
            "timeline_id": timeline_id,
            "state": state.to_dict(),
            "knowledge": knowledge_table(state),
            "memories": [
                {
                    "id": memory.id,
                    "content": memory.content,
                    "memory_class": memory.memory_class,
                    "scope": memory.scope,
                    "importance": memory.importance,
                    "is_active": memory.is_active,
                    "character_id": memory.character_id,
                    "scene_id": memory.scene_id,
                    "created_at": memory.created_at.isoformat() if memory.created_at else None,
                }
                for memory in memories
            ],
            "commitments": self.all_commitments(project_id, timeline_id),
            "context_debug": latest_generation.context_debug if latest_generation else {},
            "validation": latest_generation.validation if latest_generation else {},
            "contradictions": (latest_generation.trace or {}).get("contradictions", []) if latest_generation else [],
            "lore_debug": latest_generation.lore_debug if latest_generation else {},
            "events": [self._event_dict(event) for event in events],
            "checkpoints": repository.list_checkpoints(self.db, timeline_id),
            "planner_context": self._planner_context(project_id, timeline_id),
            "active_timeline_id": project.active_timeline_id,
        }

    async def _structured(self, messages: list[ProviderMessage], schema: dict[str, Any]) -> dict[str, Any]:
        try:
            value = await self.provider.structured(messages, schema)
        except Exception as exc:
            raise ProviderError("Provider generation failed") from exc
        if not isinstance(value, dict):
            raise ProviderError("Provider structured response must be an object")
        return value

    def _validate_generation_shape(
        self, structured: dict[str, Any], *, performer_result: PerformerResult | None = None
    ) -> None:
        """Validate the response envelope, not its claims.

        A provider that returns the wrong *shape* has failed in a different way
        from a provider that returns a well-formed but impossible claim, and the
        two need different statuses: shape failure is a failed generation, an
        impossible claim is a rejected generation with recovery options.

        When the Performer has already read the response, the legacy fields are
        optional: they are redundant with the Performer's structured result, and
        requiring a model to restate its dialogue in a second legacy array is a
        shape rule with no purpose.
        """
        if performer_result is None:
            for name in ("selected_actor", "reason", "prose"):
                if not isinstance(structured.get(name), str):
                    raise ValueError(f"Structured generation field must be a string: {name}")
        for name in ("new_events", "state_changes"):
            value = structured.get(name)
            if value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError(f"Structured generation field must be an object array: {name}")
        for name in ("actions", "open_commitments", "knowledge_changes", "relationship_changes"):
            value = structured.get(name)
            if value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError(f"Structured generation field must be an object array: {name}")
        for name in ("dialogue", "actor_actions", "proposed_events", "knowledge_changes"):
            value = structured.get(name)
            if value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError(f"Performer field must be an object array: {name}")

    def _planner_context(self, project_id: str, timeline_id: str) -> dict[str, Any]:
        commitments = self._open_commitments(project_id, timeline_id)
        return {
            "timeline_id": timeline_id,
            "commitments": [self._commitment_dict(commitment) for commitment in commitments],
        }

    def _open_commitments(self, project_id: str, timeline_id: str) -> list[StoryCommitment]:
        """Open commitments valid on this timeline.

        Scope is the timeline *and its ancestry*, so a commitment created on a
        branch is invisible to the timeline it branched from, and a commitment
        created before a fork is still open on the branch.
        """
        lineage = repository.timeline_lineage(self.db, timeline_id)
        rows = list(
            self.db.scalars(
                select(StoryCommitment)
                .where(
                    StoryCommitment.project_id == project_id,
                    (StoryCommitment.timeline_id.is_(None)) | (StoryCommitment.timeline_id.in_(sorted(lineage))),
                )
                .order_by(StoryCommitment.priority.desc(), StoryCommitment.created_at)
            )
        )
        # A forked copy replaces its ancestor on that branch permanently. The
        # ancestor stays out of the planner even after the copy is resolved,
        # because resolving the copy must not resurrect the ancestor.
        superseded_ids = {
            commitment.forked_from_commitment_id
            for commitment in rows
            if commitment.forked_from_commitment_id
        }
        return [
            commitment
            for commitment in rows
            if commitment.status in OPEN_COMMITMENT_STATUSES and commitment.id not in superseded_ids
        ]

    def _commitment_dict(self, commitment: StoryCommitment) -> dict[str, Any]:
        return {
            "id": commitment.id,
            "description": commitment.description,
            "status": CommitmentStatus.coerce(commitment.status).value,
            "raw_status": commitment.status,
            "priority": commitment.priority,
            "progress": commitment.progress,
            "timeline_id": commitment.timeline_id,
            "forked_from_commitment_id": commitment.forked_from_commitment_id,
            "created_sequence": commitment.created_sequence,
        }

    def pending_commitments(self, project_id: str, timeline_id: str) -> list[dict[str, Any]]:
        return self._planner_context(project_id, timeline_id)["commitments"]

    def all_commitments(self, project_id: str, timeline_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(StoryCommitment).where(StoryCommitment.project_id == project_id)
        if timeline_id:
            lineage = repository.timeline_lineage(self.db, timeline_id)
            statement = statement.where(
                (StoryCommitment.timeline_id.is_(None)) | (StoryCommitment.timeline_id.in_(sorted(lineage)))
            )
        statement = statement.order_by(StoryCommitment.created_at, StoryCommitment.id)
        return [self._commitment_dict(commitment) for commitment in self.db.scalars(statement)]

    def update_commitment(
        self,
        *,
        project_id: str,
        commitment_id: str,
        status: str | None = None,
        progress: float | None = None,
        note: str = "",
    ) -> PipelineResult:
        """Move a commitment through its lifecycle, recording the transition.

        Illegal transitions are refused rather than silently applied, because a
        commitment that quietly moves from fulfilled back to active is exactly
        the kind of unreliability this milestone exists to remove.
        """
        commitment = self.db.get(StoryCommitment, commitment_id)
        if commitment is None or commitment.project_id != project_id:
            raise LookupError("Story commitment not found")
        current = CommitmentStatus.coerce(commitment.status)
        timeline_id = commitment.timeline_id or self._default_timeline(project_id)
        if status is not None:
            target = CommitmentStatus.coerce(status)
            allowed = COMMITMENT_TRANSITIONS.get(current.value, frozenset())
            if target != current and target.value not in allowed:
                raise ValueError(
                    f"Commitment cannot move from {current.value} to {target.value}"
                )
            event, _node, state = repository.append_event(
                self.db,
                project_id=project_id,
                timeline_id=timeline_id,
                event_type=COMMITMENT_STATUS_CHANGED,
                payload={
                    "commitment_id": commitment.id,
                    "from_status": current.value,
                    "to_status": target.value,
                    "note": note,
                    "progress": progress if progress is not None else commitment.progress,
                },
                source=EventSource.USER,
            )
            commitment.status = target.value
            if progress is not None:
                commitment.progress = progress
            self.db.add(commitment)
            self.db.commit()
            return PipelineResult(
                timeline_id=timeline_id,
                commitment_id=commitment.id,
                generation_id=event.id,
                state=state.to_dict(),
            )
        if progress is not None:
            commitment.progress = progress
            self.db.add(commitment)
            self.db.commit()
        return PipelineResult(
            timeline_id=timeline_id,
            commitment_id=commitment.id,
            state=repository.current_state(self.db, timeline_id).to_dict(),
        )

    async def supersede_commitment(
        self,
        *,
        project_id: str,
        commitment_id: str,
        replacement_text: str = "",
    ) -> PipelineResult:
        """Retire a commitment and optionally record what replaces it."""
        result = self.update_commitment(
            project_id=project_id,
            commitment_id=commitment_id,
            status=CommitmentStatus.SUPERSEDED.value,
            note="superseded",
        )
        if replacement_text:
            scene = self._current_scene(project_id, result.timeline_id or "")
            if scene is not None:
                return await self.direct(
                    project_id=project_id,
                    scene_id=scene.id,
                    text=replacement_text,
                    intent_type=IntentType.STORY_COMMITMENT.value,
                )
        return result

    def _default_timeline(self, project_id: str) -> str:
        project = repository.get_project(self.db, project_id)
        if not project.active_timeline_id:
            raise ValueError("Project has no active timeline")
        return project.active_timeline_id

    def _current_scene(self, project_id: str, timeline_id: str) -> Any | None:
        return self.db.scalar(
            select(Scene)
            .where(
                Scene.project_id == project_id,
                Scene.timeline_id == timeline_id,
                Scene.current.is_(True),
            )
            .order_by(desc(Scene.updated_at))
        )

    def _project_characters(self, project_id: str, character_ids: list[str] | None = None) -> list[Character]:
        characters = repository.list_characters(self.db, project_id)
        if character_ids is None:
            return characters
        allowed = set(character_ids)
        return [character for character in characters if character.id in allowed]

    async def _direct_turn(
        self,
        *,
        project_id: str,
        scene: Any,
        text: str,
        mode: str,
        state: StateSnapshot,
        characters: dict[str, Character],
        participants: list[SceneParticipant],
        participant_ids: set[str],
        possessed_character_id: str | None,
        routed_intent: Any,
        director_lane: Lane,
        actor_id: str | None = None,
    ) -> DirectorDecision:
        """Run the Director for this turn.

        Lanes 1 and 2 return immediately with no plan. Lanes 3 and 4 build one,
        validate it, and decide whether a human decision is required before any
        performance happens.
        """
        from services.director import director as director_service

        # The Director plans over the characters that can actually act in this
        # scene. Off-stage characters stay legitimate *targets* of direction, but
        # they can never be plan participants: a beat that assigns action to
        # someone the State Engine cannot see here would be unvalidatable and,
        # if it slipped through, would act off-stage.
        scene_characters = [
            character for character in characters.values() if character.id in participant_ids
        ]
        return await director_service.direct_turn(
            self.db,
            project_id=project_id,
            scene_id=scene.id,
            scene_objective=str((scene.staging or {}).get("objective", "")),
            timeline_id=scene.timeline_id,
            text=text,
            mode=mode,
            horizon=director_service.horizon_for(director_lane, routed_intent),
            possessed_character_id=possessed_character_id,
            provider=self.provider,
            state=state,
            characters=scene_characters,
            participant_ids=participant_ids,
            known_locations=sorted(self._known_locations(project_id)),
            scene_location=str((scene.staging or {}).get("location", "")),
            viewer_character_id=actor_id,
            refine=self.director_refinement,
        )

    def _performer_request(
        self,
        *,
        project_id: str,
        scene: Any,
        state: StateSnapshot,
        characters: dict[str, Character],
        participants: list[SceneParticipant],
        participant_ids: set[str],
        actor: Character | None,
        actor_id: str | None,        actor_reason: str,
        user_input: str,
        director: DirectorDecision,
        planner_context: dict[str, Any],
        recent_events: list[str],
        control_modes: dict[str, str],
    ) -> PerformerRequest:
        """Build the Performer's request for this turn.

        The Director's plan is passed through as *guidance*; the Performer
        realises the beat, and only the beat. The plan is the Director's artifact
        and never becomes the Performer's own state.
        """
        scene_characters = [
            character for character in characters.values() if character.id in participant_ids
        ]
        return build_performer_request(
            scene=scene,
            state=state,
            characters=scene_characters,
            participant_ids=participant_ids,
            control_modes=control_modes,
            viewer_character_id=actor_id,
            actor_selection_reason=actor_reason,
            user_input=user_input,
            plan=director.view.plan if director is not None else None,
            style=self.presentation_style,
            recent_events=recent_events,
            active_commitments=list(planner_context.get("commitments", [])),
        )

    def _performer_violations(
        self,
        result: PerformerResult,
        *,
        request: PerformerRequest,
        state: StateSnapshot,
    ) -> list[PerformerViolation]:
        """Everything the Performer proposed that it was not permitted to do."""
        return [
            *check_user_agency(result, request=request),
            *check_offstage_actors(result, request=request, state=state),
        ]

    def _advance_plan_beats(
        self,
        director: DirectorDecision,
        *,
        plan_row: Any,
        scene: Any,
        generation_id: str,
    ) -> None:
        """Record which beat this turn realised, and whether the plan is done.

        A plan spans turns. Marking it ``completed`` after the first turn would
        claim the whole arc was delivered when only one beat was, and the user
        would lose the plan they were still relying on. So the plan stays
        ``executing`` until its beats have all reached a terminal state.
        """
        from services.director import director as director_service

        plan = director.view.plan
        performed = plan.active_beat()
        director_service.set_plan_status(self.db, plan_row, PlanStatus.EXECUTING)
        director_service.advance_beats(plan, upto=1) if performed is not None else None
        if plan.all_beats_resolved():
            director_service.set_plan_status(self.db, plan_row, PlanStatus.COMPLETED)
        plan_row.generation_id = generation_id
        plan_row.plan_json = plan.to_dict()
        plan_row.current_beat_index = next(
            (
                index
                for index, beat in enumerate(plan.beats)
                if beat.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}
            ),
            len(plan.beats),
        )
        director.view.plan_id = plan_row.id
        director.view.status = PlanStatus(plan_row.status)
        self.db.add(plan_row)

    def _interruption_for(
        self, *, project_id: str, scene: Any, user_input: str, lane: Lane
    ) -> dict[str, Any]:
        """Reconcile a running plan with what the user just asked for.

        Two different things can interrupt a plan, and conflating them is how an
        improvisation session breaks.

        **The user gives a new direction.** Their request supersedes the plan
        outright. The plan is marked ``superseded`` rather than cancelled: it was
        valid, it was overtaken, and saying otherwise would lose the record of
        what the Director had been trying to do.

        **The user performs an action.** The scene simply moved, so the beat that
        was being realised is ``skipped`` — not failed — and the plan continues
        from wherever the intervention left it. Invalidating the whole plan here
        would mean one interruption ended the arc, which is the opposite of what
        an interrupting user is asking for.

        A possession counts as an action, not a direction: the user taking the
        wheel is them doing a thing, not rewriting the narrative.
        """
        from services.director import director as director_service

        active = repository.active_director_plan(self.db, scene_id=scene.id)
        if active is None:
            return {"interrupted": False}
        plan = director_service.plan_from_row(active)
        if lane in {Lane.DIRECTION, Lane.LONG_HORIZON}:
            director_service.set_plan_status(self.db, active, PlanStatus.SUPERSEDED)
            active.decision_json = {
                **(active.decision_json or {}),
                "superseded_by": user_input[:200],
            }
            self.db.add(active)
            return {
                "interrupted": True,
                "mode": "superseded",
                "plan_id": active.id,
                "reason": "the user gave new narrative direction",
            }
        skipped = director_service.advance_beats(plan, status=BeatStatus.SKIPPED)
        if not skipped:
            return {"interrupted": False, "plan_id": active.id}
        active.plan_json = plan.to_dict()
        active.decision_json = {
            **(active.decision_json or {}),
            "interrupted_by": user_input[:200],
            "skipped_beats": skipped,
        }
        self.db.add(active)
        return {
            "interrupted": True,
            "mode": "beat_skipped",
            "plan_id": active.id,
            "skipped_beats": skipped,
            "reason": "the user intervened before this beat was realised",
        }

    def _performer_trace(
        self,
        *,
        request: PerformerRequest,
        result: PerformerResult,
        plan: Any,
        plan_status: PlanStatus,
        validation: Any,
        interruption: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The Performer's own trace block.

        Structured decisions and outcomes only. The prompt is summarised rather
        than stored verbatim, and no deliberation is recorded: a trace that
        carries the model's scratch work becomes the explanation the user reads
        instead of the state that actually changed.
        """
        satisfied, unmet = check_required_consequences(result, request=request)
        return {
            "style": request.style,
            "beat": request.beat.to_dict() if request.beat is not None else None,
            "beat_index": request.beat_index,
            "beat_total": request.beat_total,
            "plan_status": plan_status.value,
            "actor_set": [
                {
                    "character_id": brief.character_id,
                    "name": brief.name,
                    "is_viewer": brief.is_viewer,
                    "user_controlled": brief.user_controlled,
                    "agency_withheld": brief.agency_withheld,
                    "present": brief.present,
                    "alive": brief.alive,
                    "location_id": brief.location_id,
                    # Which knowledge the brief was allowed to carry, not what it
                    # carried: the contents are already in the context debug.
                    "knowledge_scope": (
                        "viewer" if brief.is_viewer else "observational_only"
                    ),
                    "user_control_level": (
                        "user" if brief.user_controlled else "performer"
                    ),
                }
                for brief in request.actors
            ],
            "possession": {
                "viewer_character_id": request.viewer_character_id,
                "user_controlled": request.user_controlled_ids,
                "actor_selection_reason": request.actor_selection_reason,
            },
            "input_context": {
                "scene_id": request.scene_id,
                "location": request.location,
                "user_input": request.user_input,
                "actor_count": len(request.actors),
                "recent_event_count": len(request.recent_events),
                "open_commitments": len(request.active_commitments),
                "narration_allowed": request.narration_allowed,
            },            "prose_characters": len(result.prose),
            "narration_characters": len(result.narration),
            "dialogue_turns": [turn.to_dict() for turn in result.dialogue],
            "actor_actions": [turn.to_dict() for turn in result.actor_actions],
            "proposed_events": list(result.proposed_events),
            "required_consequences": {
                "satisfied": satisfied,
                "unmet": unmet,
            },
            "completion_signals": dict(result.completion_signals),
            "completion_result": (
                "beat_realised"
                if request.beat is None or request.beat.status is BeatStatus.COMPLETED
                else "beat_open"
            ),
            "interruption": dict(interruption or {"interrupted": False}),
            "validation": {
                "status": getattr(validation, "status", ""),
                "valid": bool(getattr(validation, "valid", False)),
                "accepted": len(getattr(validation, "accepted", []) or []),
                "rejected": len(getattr(validation, "rejected", []) or []),
            },
            "notes": list(result.notes),
        }

    def _perform_approved_plan(
        self,
        *,
        project_id: str,
        plan: Any,
        plan_id: str | None,
        scene: Any,
        state: StateSnapshot,
        characters: dict[str, Character],
        participant_ids: set[str],
        actor_id: str | None,
    ) -> DirectorDecision:
        """Turn an approved plan into a Performer contract, or block it.

        Approval is not a guarantee of executability. The user may approve a
        plan whose beat has since become impossible, so the constraint envelope
        is re-checked against current state here rather than trusted from
        proposal time.
        """
        from services.core.enums import PlanStatus
        from services.director import director as director_service
        from services.director.envelope import validate_plan

        scene_characters = [
            character for character in characters.values() if character.id in participant_ids
        ]
        validation = validate_plan(
            plan,
            state=state,
            participant_ids=participant_ids,
            known_locations=sorted(self._known_locations(project_id)),
            known_character_names=[character.name for character in characters.values()],
            intent_constraints=list(plan.intent.constraints),
            intent_exclusions=list(plan.intent.exclusions),
        )
        view = DirectorPlanView(
            plan=plan,
            status=PlanStatus.EXECUTING,
            requires_approval=False,
            validation=validation.to_dict(),
        )
        view.plan_id = plan_id
        view.intent_id = getattr(plan, "intent_id", None)
        blocked = not validation.valid
        if blocked:
            view.execution = {
                "mode": "blocked",
                "reason": "; ".join(validation.errors),
            }
            return DirectorDecision(
                view=view,
                intent_payload=plan.intent.to_dict(),
                validation=validation,
                blocked=True,
                block_reason="; ".join(validation.errors),
            )
        decision = DirectorDecision(
            view=view,
            intent_payload=plan.intent.to_dict(),
            validation=validation,
        )
        decision.performer_block = director_service.render_performer_contract(
            plan,
            briefs=director_service.character_briefs(
                scene_characters,
                state,
                str((scene.staging or {}).get("location", "")),
                viewer_character_id=actor_id,
            ),
            scene_objective=str((scene.staging or {}).get("objective", "")),
        )
        return decision

    def _persist_director_plan(
        self, director: DirectorDecision, *, scene: Any, project_id: str
    ) -> Any:
        from services.director import director as director_service

        if director.view.intent_id:
            # Long-horizon requests already recorded their intent when they
            # created the commitments; recording it twice would give the same
            # request two retained intents.
            intent_id = director.view.intent_id
        else:
            intent_id = repository.record_intent(
                self.db,
                project_id=project_id,
                scene_id=scene.id,
                intent_type=IntentType.DIRECTION.value,
                text=director.view.plan.objective,
                horizon=director.view.plan.horizon.value,
                intent=director.view.plan.intent,
                lane=director.view.plan.lane.value,
                interpretation_source=(
                    director.refinement.source if director.refinement else "heuristic"
                ),
            ).id
            director.view.intent_id = intent_id
        return director_service.persist_plan(
            self.db,
            director,
            project_id=project_id,
            timeline_id=scene.timeline_id,
            scene_id=scene.id,
            intent_id=intent_id,
        )

    def _persist_director_commitments(
        self, director: DirectorDecision, *, scene: Any, project_id: str
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Record long-horizon intent as commitments, reusing the existing lifecycle.

        The outcome is never executed here. A request that says "eventually X"
        produces a commitment and, at most, a note on the plan; the betrayal
        cannot happen in the turn that asked for it eventually.

        The commitment rows already exist — ``direct_turn`` created them through
        the ordinary model. This only appends the audit event, so the commitment
        and the intent that justified it are both visible on the timeline.
        """
        commitment_ids: list[str] = []
        payloads: list[dict[str, Any]] = []
        for intent_id, row in director.commitments_created:
            commitment_ids.append(row["id"])
            payloads.append(dict(row))
            repository.append_event(
                self.db,
                project_id=project_id,
                timeline_id=scene.timeline_id,
                event_type=DIRECTOR_INTENT_CREATED,
                payload={
                    "intent_id": intent_id,
                    "commitment_id": row["id"],
                    "type": IntentType.STORY_COMMITMENT.value,
                    "goal": row["description"],
                    "horizon": row["horizon"],
                    "literal_action": False,
                    "source_lane": director.view.plan.lane.value,
                    "executed": False,
                },
                source=EventSource.USER,
            )
        return commitment_ids, payloads

    # --- DirectorPlan decision API -------------------------------------

    def get_plan(self, *, project_id: str, plan_id: str) -> dict[str, Any]:

        row = repository.get_director_plan(self.db, plan_id)
        if row.project_id != project_id:
            raise ValueError("Director plan does not belong to project")
        return self._plan_payload(row)

    def list_plans(
        self, *, project_id: str, scene_id: str | None = None, timeline_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = repository.list_director_plans(
            self.db, project_id=project_id, scene_id=scene_id, timeline_id=timeline_id
        )
        return [self._plan_payload(row) for row in rows]

    def decide_plan(
        self,
        *,
        project_id: str,
        plan_id: str,
        decision: str,
        edits: dict[str, Any] | None = None,
        resolution: str = "",
    ) -> dict[str, Any]:
        """Approve, edit, cancel, or resolve a plan.

        Editing changes the *plan* only. The retained intent in ``intent_json``
        is never rewritten, because the user's request has not changed.
        """
        from services.director import director as director_service
        from services.director.envelope import validate_plan

        row = repository.get_director_plan(self.db, plan_id)
        if row.project_id != project_id:
            raise ValueError("Director plan does not belong to project")
        if row.status in {PlanStatus.COMPLETED.value, PlanStatus.CANCELLED.value, PlanStatus.SUPERSEDED.value}:
            raise ValueError(f"Plan is already {row.status} and cannot be decided")
        plan = director_service.plan_from_row(row)
        if edits:
            plan = _apply_plan_edits(plan, edits)
        row.plan_json = plan.to_dict()
        row.summary = plan.summary
        row.objective = plan.objective
        row.decision_json = {
            "decision": decision,
            "edits": dict(edits or {}),
            "resolution": resolution,
        }
        if resolution:
            row.canon_conflict_json = _apply_canon_resolution(row.canon_conflict_json, resolution)
        if decision == "approve":
            director_service.set_plan_status(self.db, row, PlanStatus.APPROVED)
            row.required_approval = False
        elif decision == "cancel":
            director_service.set_plan_status(self.db, row, PlanStatus.CANCELLED)
        elif decision == "edit":
            # An edit is a proposal again: it must be re-approved before running.
            # Editing does not go through the forward lifecycle, because the
            # plan has not been performed yet.
            row.status = PlanStatus.PROPOSED.value
            row.required_approval = True
        elif decision == "reject":
            director_service.set_plan_status(self.db, row, PlanStatus.CANCELLED)
            row.decision_json = {**row.decision_json, "rejected": True}
        else:
            raise ValueError(f"Unknown plan decision: {decision}")
        # An approved edit must still satisfy the envelope: approval of a plan the
        # user corrected is not permission to run one the State Engine would reject.
        timeline_id = row.timeline_id or ""
        if row.status == PlanStatus.APPROVED.value and timeline_id:
            reverified = validate_plan(
                plan,
                state=repository.current_state(self.db, timeline_id),
                participant_ids=_scene_participant_ids(self.db, row.scene_id),
                known_locations=sorted(self._known_locations(project_id)),
                known_character_names=[character.name for character in self._project_characters(project_id)],
                intent_constraints=list(plan.intent.constraints),
                intent_exclusions=list(plan.intent.exclusions),
            )
            row.validation_json = reverified.to_dict()
            if not reverified.valid:
                raise ValueError("Edited plan is still outside the constraint envelope: " + "; ".join(reverified.errors))
        self.db.add(row)
        self.db.commit()
        return self._plan_payload(row)

    def _plan_payload(self, row: Any) -> dict[str, Any]:
        from services.director import director as director_service

        plan = director_service.plan_from_row(row)
        payload = plan.to_dict()
        payload.update(
            {
                "plan_id": row.id,
                "project_id": row.project_id,
                "status": row.status,
                "intent_id": row.intent_id,
                "generation_id": row.generation_id,
                "timeline_id": row.timeline_id,
                "scene_id": row.scene_id,
                "required_approval": row.required_approval,
                "validation": row.validation_json or {},
                "canon_conflict": row.canon_conflict_json,
                "decision": row.decision_json or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
        return payload

    async def execute_plan(
        self,
        *,
        project_id: str,
        plan_id: str,
        actor_character_id: str | None = None,
    ) -> PipelineResult:
        """Perform an approved plan's next beat.

        The plan is set to ``executing`` and handed to :meth:`continue_scene`
        directly. The beat text is deliberately *not* re-routed: the plan is
        already the decision, and treating its prose as a fresh user request
        would propose a second plan for the same beat.
        """
        from services.director import director as director_service

        row = repository.get_director_plan(self.db, plan_id)
        if row.project_id != project_id:
            raise ValueError("Director plan does not belong to project")
        if row.status == PlanStatus.PROPOSED.value:
            raise ValueError("Plan must be approved before execution")
        if row.status in {PlanStatus.COMPLETED.value, PlanStatus.CANCELLED.value}:
            raise ValueError(f"Plan is already {row.status}")
        if not row.scene_id:
            raise ValueError("Plan is not bound to a scene")
        plan = director_service.plan_from_row(row)
        beat = plan.active_beat()
        if beat is not None:
            beat.status = BeatStatus.ACTIVE
        director_service.set_plan_status(self.db, row, PlanStatus.EXECUTING)
        row.plan_json = plan.to_dict()
        self.db.commit()
        result = await self.continue_scene(
            project_id=project_id,
            scene_id=row.scene_id,
            actor_character_id=actor_character_id,
            director_plan=plan,
            director_plan_id=row.id,
            user_input=beat.description if beat else plan.objective,
        )
        if result.requires_approval or result.plan_id != row.id:
            # The plan could not be performed: the envelope rejected it against
            # current state. It stays ``executing`` with nothing performed, so
            # the user can edit or cancel it rather than having it silently die.
            return result
        resolved = director_service.advance_beats(plan)
        row.plan_json = plan.to_dict()
        row.generation_id = result.generation_id
        if result.checkpoint_node_id:
            row.checkpoint_node_id = result.checkpoint_node_id
        if plan.all_beats_resolved():
            director_service.set_plan_status(self.db, row, PlanStatus.COMPLETED)
        self.db.commit()
        return PipelineResult(
            scene_id=result.scene_id,
            generation_id=result.generation_id,
            output_text=result.output_text,
            timeline_id=result.timeline_id,
            state=result.state,
            plan_id=row.id,
            director={
                "status": row.status,
                "plan_id": row.id,
                "performed_beat": beat.description if beat else "",
                "resolved_beats": resolved,
                "remaining_beats": [
                    pending.description
                    for pending in plan.beats
                    if pending.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}
                ],
            },
        )

    def interrupt_plan(
        self, *, project_id: str, plan_id: str, reason: str = "user changed direction"
    ) -> dict[str, Any]:
        """Mark beats obsolete after user intervention.

        A beat that can no longer happen is invalidated, not failed. Interrupting
        is a normal outcome of an improvisational session.
        """
        from services.director import director as director_service

        row = repository.get_director_plan(self.db, plan_id)
        if row.project_id != project_id:
            raise ValueError("Director plan does not belong to project")
        plan = director_service.plan_from_row(row)
        invalidated = _invalidate_on_interruption(plan, reason)
        row.plan_json = plan.to_dict()
        row.decision_json = {**(row.decision_json or {}), "interrupted": reason}
        if row.status == PlanStatus.EXECUTING.value and plan.all_beats_resolved():
            # A plan that was mid-performance ends when its last beat is resolved.
            director_service.set_plan_status(self.db, row, PlanStatus.COMPLETED)
        elif row.status != PlanStatus.COMPLETED.value:
            # A plan that never performed was cancelled, not completed. Marking
            # an unstarted plan "completed" would claim work that never happened.
            director_service.set_plan_status(self.db, row, PlanStatus.CANCELLED)
        self.db.add(row)
        self.db.commit()
        payload = self._plan_payload(row)
        payload["invalidated"] = invalidated
        return payload

    def invalidate_plan_beats(
        self, *, project_id: str, plan_id: str, character_id: str, reason: str
    ) -> dict[str, Any]:
        """Invalidate beats that require a character who can no longer act."""
        from services.director import director as director_service

        row = repository.get_director_plan(self.db, plan_id)
        if row.project_id != project_id:
            raise ValueError("Director plan does not belong to project")
        plan = director_service.plan_from_row(row)
        invalidated = [beat.description for beat in plan.invalidate_beats_requiring(character_id, reason)]
        row.plan_json = plan.to_dict()
        if plan.all_beats_resolved():
            director_service.set_plan_status(self.db, row, PlanStatus.COMPLETED)
        self.db.add(row)
        self.db.commit()
        payload = self._plan_payload(row)
        payload["invalidated"] = invalidated
        return payload

    def _infer_location(self, project_id: str, premise: str) -> str:
        locations = self.db.scalars(select(Location).where(Location.project_id == project_id))
        for location in locations:
            if location.name.casefold() in premise.casefold():
                return location.name
        return "Unspecified location"

    def _infer_time(self, premise: str) -> str:
        match = re.search(r"\b(morning|afternoon|evening|night|dawn|dusk|midnight|day)\b", premise, re.IGNORECASE)
        return match.group(0).capitalize() if match else "Unspecified time"

    def _source_context(self, project_id: str, premise: str) -> list[str]:
        sources = self.db.scalars(select(Source).where(Source.project_id == project_id))
        terms = {term.casefold() for term in re.findall(r"[\w'-]+", premise) if len(term) > 3}
        result: list[str] = []
        for source in sources:
            source_terms = {term.casefold() for term in re.findall(r"[\w'-]+", source.content)}
            overlap = terms & source_terms
            if overlap:
                result.append(f"{source.title or 'Source'} references {', '.join(sorted(overlap))}")
        return result[:8]

    def _canon_conflicts(self, project_id: str, premise: str) -> list[str]:
        action_terms = ("betray", "assassin", "kill", "attack", "forgive", "marry")
        constraint_terms = ("loyal", "never", "cannot", "must not", "forbid", "oath")
        normalized_premise = premise.casefold()
        if not any(term in normalized_premise for term in action_terms):
            return []
        conflicts: list[str] = []
        for source in self.db.scalars(select(Source).where(Source.project_id == project_id)):
            normalized_source = source.content.casefold()
            if any(term in normalized_source for term in constraint_terms):
                conflicts.append(f"{source.title or 'Source material'} contains a constraint that may conflict with this premise")
        return conflicts

    def _resolve_control_modes(
        self, participants: list[SceneParticipant], possessed_character_id: str | None
    ) -> dict[str, str]:
        """The one control-mode view for this turn.

        A per-call ``possessed_character_id`` is authoritative *for that call*:
        possessing a character makes them user-controlled, and not passing the
        argument means the persisted ``control_mode`` stands. Possession never
        mutates the character, its knowledge, or anything else — it only decides
        who is allowed to decide.
        """
        modes = {
            participant.character_id: participant.control_mode
            for participant in participants
        }
        if possessed_character_id:
            modes[possessed_character_id] = ControlMode.USER.value
        return modes

    def _choose_actor(
        self,
        project_id: str,
        scene: Any,
        participants: list[SceneParticipant],
        user_input: str,
        possessed_character_id: str | None,
        actor_override: str | None = None,
    ) -> tuple[str | None, str]:
        """Select the acting character and say why.

        The reason is recorded in the generation trace, because "why did the
        system pick this actor" is a question the studio must be able to answer
        after the fact rather than reconstruct from the code.
        """
        participant_ids = {participant.character_id for participant in participants}
        if actor_override and actor_override in participant_ids:
            return actor_override, "explicit actor override"
        if possessed_character_id and possessed_character_id in participant_ids:
            return possessed_character_id, "possessed character"
        explicit = self._character_named_in_input(project_id, user_input, participant_ids)
        if explicit:
            return explicit, "character named in the current instruction"
        for participant in participants:
            if participant.control_mode == ControlMode.USER.value:
                return participant.character_id, "user-controlled participant acts authoritatively"
        if participants:
            return participants[0].character_id, "deterministic participant fallback"
        return None, "no participants"

    def _character_named_in_input(
        self,
        project_id: str,
        text: str,
        participant_ids: set[str] | None = None,
    ) -> str | None:
        if not text:
            return None
        lowered = text.casefold()
        characters = self.db.scalars(select(Character).where(Character.project_id == project_id))
        for character in characters:
            if participant_ids is not None and character.id not in participant_ids:
                continue
            if character.name.casefold() in lowered:
                return character.id
        return None

    def _visible_state(self, state: StateSnapshot, actor: Character | None) -> dict[str, Any]:
        """The actor's slice of the projection.

        Deliberately excludes ``knowledge``: beliefs are rendered by the
        knowledge module, which can state certainty separately. Mixing the two
        into one blob is how a suspicion ends up reading as a fact.
        """
        visible = state.to_dict()
        visible["knowledge"] = {}
        visible["suspicions"] = {}
        if actor is None:
            visible["characters"] = {}
            visible["relationships"] = {}
            visible["items"] = {}
            visible["injuries"] = {}
            visible["control_modes"] = {}
            visible["dead"] = []
            return visible
        actor_id = actor.id
        visible["characters"] = {actor_id: visible["characters"].get(actor_id, {"character_id": actor_id})}
        visible["relationships"] = {
            key: value
            for key, value in visible["relationships"].items()
            if value.get("source_character_id") == actor_id or value.get("target_character_id") == actor_id
        }
        visible["items"] = {actor_id: visible["items"].get(actor_id, [])}
        visible["injuries"] = {actor_id: visible["injuries"].get(actor_id, [])}
        visible["removed_items"] = {actor_id: visible["removed_items"].get(actor_id, [])}
        visible["control_modes"] = {
            actor_id: visible["control_modes"][actor_id]
        } if actor_id in visible["control_modes"] else {}
        visible["dead"] = [actor_id] if actor_id in state.dead else []
        return visible

    def _token_estimator(self) -> TokenEstimator:
        model = self.capabilities.model or str(getattr(self.provider, "model", "") or "")
        return estimator_for(model, allow_exact=self.allow_exact_tokens)

    def _token_budget(self) -> TokenBudget:
        return self.capabilities.token_budget()

    def _known_locations(self, project_id: str) -> set[str]:
        rows = self.db.execute(
            select(Location.id, Location.name).where(Location.project_id == project_id)
        ).all()
        return {str(row.id) for row in rows} | {str(row.name) for row in rows}

    def _detector(
        self,
        state: StateSnapshot,
        scene: Any,
        actor: Character | None,
        knowledge: KnowledgeView,
    ) -> ContradictionDetector:
        return ContradictionDetector(
            state,
            scene_location=str((scene.staging or {}).get("location", "")),
            known_locations=sorted(self._known_locations(scene.project_id)),
            knowledge=knowledge,
        )

    def _memories(
        self,
        project_id: str,
        scene: Any,
        actor: Character | None,
        user_input: str,
        state: StateSnapshot,
    ) -> tuple[list[MemoryCandidate], RetrievalReport]:
        """Retrieve memories for this turn, scoped to the timeline's ancestry.

        The candidate window is a *pool* over which ranking happens, not a
        truncation of what the engine can see. Its size is reported so a report
        can state the bound instead of implying there is none.
        """
        query = " ".join(
            [
                user_input,
                str(scene.title or ""),
                str((scene.staging or {}).get("objective", "")),
                *[fact for fact in state.knowledge.get(actor.id if actor else "", set())][:3],
            ]
        ).strip()
        lineage = repository.timeline_lineage(self.db, scene.timeline_id)
        rows = self.db.scalars(
            select(Memory)
            .where(
                Memory.project_id == project_id,
                (Memory.timeline_id.is_(None)) | (Memory.timeline_id.in_(sorted(lineage))),
            )
            .order_by(desc(Memory.importance), desc(Memory.created_at))
            .limit(self.memory_pool)
        )
        candidates = [
            MemoryCandidate(
                id=memory.id,
                content=memory.content,
                memory_class=MemoryClass(memory.memory_class),
                importance=memory.importance,
                created_at=memory.created_at,
                character_id=memory.character_id,
                scene_id=memory.scene_id,
                metadata={**(memory.metadata_json or {}), "timeline_id": memory.timeline_id},
                scope=MemoryScope(memory.scope) if memory.scope else MemoryScope.SCENE,
                valid_from_sequence=memory.valid_from_sequence,
                is_active=bool(memory.is_active),
                source_event_id=memory.source_event_id,
                inherited_from_id=str((memory.metadata_json or {}).get("inherited_from_memory_id") or "")
                or None,
            )
            for memory in rows
        ]
        return retrieve_with_report(
            candidates,
            query,
            limit=self.memory_limit,
            character_ids={actor.id} if actor else set(),
            scene_id=scene.id,
            timeline_ids=lineage,
            lineage_sequences=repository.timeline_lineage_sequences(self.db, scene.timeline_id),
            pool_limit=self.memory_pool,
        )

    def _recent_events(
        self, timeline_id: str, *, actor_id: str | None = None, limit: int = RECENT_EVENT_LIMIT
    ) -> list[str]:
        """The tail of the timeline, with other characters' knowledge redacted.

        Rendering raw event payloads here leaked knowledge: a
        ``knowledge_acquired`` event recorded for one character was readable
        verbatim in another character's prompt. A fact another character has not
        shared is replaced with a marker, not with the fact.
        """
        rows = list(
            self.db.scalars(
                select(Event)
                .where(Event.timeline_id == timeline_id)
                .order_by(desc(Event.sequence))
                .limit(limit)
            )
        )
        lines: list[str] = []
        for event in reversed(rows):
            payload = dict(event.payload or {})
            summary = ""
            for key in ("text", "fact", "item", "injury", "location_id", "goal"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    summary = value.strip()[:160]
                    break
            if not summary:
                summary = json.dumps(payload, sort_keys=True, default=str)[:160]
            if event.event_type in PRIVATE_KNOWLEDGE_EVENTS and payload.get("character_id") != actor_id:
                summary = WITHHELD_KNOWLEDGE
            lines.append(f"#{event.sequence} {event.event_type}: {summary}")
        return lines

    def _history_digest(self, timeline_id: str) -> str:
        """A counted digest of everything older than the recent tail.

        The previous engine had no representation of older history at all. A
        count plus the distinct event kinds is enough for a model to know that
        material exists without paying for a transcript it cannot use.
        """
        total = self.db.scalar(
            select(func.count(Event.id)).where(Event.timeline_id == timeline_id)
        ) or 0
        if total <= HISTORY_DIGEST_AFTER:
            return ""
        kinds = self.db.execute(
            select(Event.event_type, func.count(Event.id))
            .where(Event.timeline_id == timeline_id)
            .group_by(Event.event_type)
            .order_by(desc(func.count(Event.id)))
        ).all()
        summary = ", ".join(f"{kind} x{count}" for kind, count in kinds)
        return (
            f"{total} events are recorded on this timeline. "
            f"Breakdown by kind: {summary}. "
            "Ask for a detail only if the current instruction depends on it."
        )

    def _assemble(
        self,
        *,
        scene: Any,
        actor: Character | None,
        user_input: str,
        state: StateSnapshot,
        knowledge: KnowledgeView,
        planner_context: dict[str, Any],
        memories: list[MemoryCandidate],
        lore_debug: Any,
        performer_block: str = "",
        performer_request: PerformerRequest | None = None,
    ) -> tuple[ContextAssembly, str]:
        estimator = self._token_estimator()
        staging = dict(scene.staging or {})
        scene_lines = [
            f"Title: {scene.title}",
            f"Status: {scene.status}",
            f"Objective: {staging.get('objective', '')}",
            f"Location: {staging.get('location', '')}",
            f"Time: {staging.get('time', '')}",
            f"Participants: {', '.join(staging.get('characters_present', []) or [])}",
            f"Selected actor: {actor.name if actor else 'the narrative'}",
            f"Initial conditions: {'; '.join(staging.get('initial_conditions', []) or []) or 'none recorded'}",
        ]
        if performer_request is not None:
            # The Performer prompt is spliced into the required scene block, so
            # budget pressure cannot drop it while the instruction survives. It
            # replaces the older Director contract block rather than accompanying
            # it: two overlapping direction blocks would compete for the model's
            # attention and double-count the same guidance.
            scene_lines.append(render_performer_prompt(performer_request))
        elif performer_block:
            # Part of the required scene block, so the Director's guidance can
            # never be dropped by budget pressure while the instruction survives.
            scene_lines.append(performer_block)
        scene_block = "\n".join(scene_lines)
        character_state = self._character_state_block(actor, state, staging)
        memory_lines = [
            (
                f"{memory.content} [{memory.memory_class.value}/{memory.scope.value}"
                f"{f'/{memory.character_id}' if memory.character_id else ''}]",
                memory.importance + memory.valid_from_sequence / 10_000.0,
            )
            for memory in memories
        ]
        request = GenerationContextRequest(
            system_prompt=SYSTEM_RULES,
            scene_block=scene_block,
            instruction=user_input,
            character_state=character_state,
            knowledge=knowledge.render(),
            commitments="",
            recent_events=self._recent_events(scene.timeline_id, actor_id=actor.id if actor else None),
            memories=memory_lines,
            lore=[(entry.name or entry.entry_id, entry.content) for entry in lore_debug.activated],
            history=self._history_digest(scene.timeline_id),
            flavor="",
            estimator=estimator,
            budget=self._token_budget(),
            commitment_metadata=list(planner_context.get("commitments", [])),
        )
        return build_generation_context(request)

    def _character_state_block(
        self, actor: Character | None, state: StateSnapshot, staging: dict[str, Any]
    ) -> str:
        if actor is None:
            return "No actor selected; write the scene rather than a character."
        visible = self._visible_state(state, actor)
        definition = dict(actor.definition or {})
        lines = [
            f"You are playing: {actor.name}",
            f"Definition: {json.dumps(definition, sort_keys=True, default=str)[:1200]}",
            f"Your state: {json.dumps(visible['characters'].get(actor.id, {}), sort_keys=True, default=str)[:600]}",
            f"Present scene location: {staging.get('location', 'unspecified')}",
        ]
        if visible["items"].get(actor.id):
            lines.append(f"You carry: {', '.join(visible['items'][actor.id])}")
        if visible["injuries"].get(actor.id):
            lines.append(f"You are carrying these injuries: {', '.join(visible['injuries'][actor.id])}")
        if actor.id in state.dead:
            lines.append("You are dead in this timeline. Do not act or speak.")
        return "\n".join(lines)

    def _lore_debug(
        self,
        project_id: str,
        scene: Any,
        actor: Character | None,
        user_input: str,
        memories: list[MemoryCandidate],
    ) -> Any:
        entries = self.db.execute(
            select(LorebookEntry, Lorebook)
            .join(Lorebook, LorebookEntry.lorebook_id == Lorebook.id)
            .where(Lorebook.project_id == project_id)
        ).all()
        candidates = [
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
                scope=book.scope if not book.owner_character_id else "character",
            )
            for entry, book in entries
            if book.owner_character_id is None or (actor is not None and book.owner_character_id == actor.id)
        ]
        staging = dict(scene.staging or {})
        # The lore scan surface is the turn's own language, not the memory
        # payload. Feeding memories into the scan previously meant retrieving
        # more context could silently activate more lore.
        text = " ".join(
            [
                user_input,
                str(scene.title or ""),
                str(staging.get("objective", "")),
                str(staging.get("location", "")),
                str(actor.name if actor else ""),
                str(actor.definition.get("description", "")) if actor and isinstance(actor.definition, dict) else "",
            ]
        )
        budgets: list[int] = []
        for _, book in entries:
            if not isinstance(book.extra_data, dict) or book.extra_data.get("token_budget") is None:
                continue
            try:
                budgets.append(int(book.extra_data["token_budget"]))
            except (TypeError, ValueError):
                continue
        estimator = self._token_estimator()
        # Lore is P8: it may never take more than a fifth of the input budget,
        # whatever the book's own budget claims, and never more entries than a
        # scene can actually use. Both bounds are reported in the debug block.
        share = max(256, int(self._token_budget().max_input_tokens * 0.2))
        declared = sum(budgets) if budgets else (self.lore_budget or share)
        token_budget = min(share, declared)
        return evaluate_lore(
            candidates,
            text,
            token_budget=token_budget,
            token_counter=estimator.count,
            max_activations=LORE_ACTIVATION_CAP,
        )

    def _fallback_prose(self, scene: Any, actor: Character | None, user_input: str) -> str:
        if user_input:
            return f"{actor.name if actor else 'The scene'} responds to the user's intervention: {user_input}"
        return f"{actor.name if actor else 'The scene'} holds the tension and chooses the next meaningful action."

    def _overhear(
        self,
        *,
        project_id: str,
        generation_id: str,
        timeline_id: str,
        participant_ids: set[str],
        committed: list[tuple[str, dict[str, Any], Any]],
        state: StateSnapshot,
    ) -> list[str]:
        """Give every listener a suspicion for every fact spoken aloud.

        A spoken fact that stays private to the speaker is a leak through the
        event history: the words are in the prompt of a character who, on paper,
        never learned them. Marking the fact as overheard-suspicion is the
        honest representation — the listener heard it said, which is not the
        same as knowing it is true, exactly as the knowledge model requires.

        Beliefs that are never stated stay private. The redaction in the recent
        history block and the character-scoped memories are what protect those.
        """
        from hashlib import sha1

        from services.context.knowledge import _distinctive, claim_states_fact

        new_ids: list[str] = []
        for index, (event_type, payload, _event) in enumerate(committed):
            if event_type not in OVERHEARD_EVENT_TYPES:
                continue
            speaker = str(payload.get("character_id") or "")
            text = str(payload.get("text") or payload.get("action") or payload.get("prose") or "")
            if not speaker or not text.strip() or speaker not in participant_ids:
                continue
            stated = sorted(state.knowledge.get(speaker, set()) | state.suspicions.get(speaker, set()))
            for fact in stated:
                if len(_distinctive(fact)) < 2 or not claim_states_fact(text, fact):
                    continue
                digest = sha1(fact.encode("utf-8")).hexdigest()[:12]
                for listener in sorted(participant_ids - {speaker}):
                    if fact in state.knowledge.get(listener, set()):
                        continue
                    if fact in state.suspicions.get(listener, set()):
                        continue
                    heard, _node, state = repository.append_event(
                        self.db,
                        project_id=project_id,
                        timeline_id=timeline_id,
                        event_type=KNOWLEDGE_SUSPECTED,
                        payload={
                            "character_id": listener,
                            "fact": fact,
                            "overheard_from": speaker,
                            "generation_id": generation_id,
                        },
                        source=EventSource.SYSTEM,
                        idempotency_key=f"{generation_id}:overhear:{index}:{listener}:{digest}",
                    )
                    new_ids.append(heard.id)
        return new_ids

    def _persist_memories(
        self,
        project_id: str,
        scene: Any,
        *,
        actor_id: str,
        prose: str,
        committed_events: list[tuple[str, dict[str, Any], Any]],
    ) -> dict[str, Any]:
        """Run the memory lifecycle for one turn and persist what survives.

        A turn writes at most one narrative-beat record plus one record per
        durable fact it established. Everything else is scored and reported as
        dropped, which is what stops the transcript becoming the memory store.
        """
        sequence = repository.latest_node(self.db, scene.timeline_id).sequence
        seeds = [memory_lifecycle.beat_seed(prose, scene_id=scene.id, actor_id=actor_id, sequence=sequence)]
        for _event_type, _payload, event in committed_events:
            seed = memory_lifecycle.fact_seed(event)
            if seed is not None:
                seeds.append(seed)
        proposals = memory_lifecycle.proposals_for(seeds)
        for proposal in proposals:
            if not proposal.persist:
                continue
            seed = proposal.seed
            repository.add_memory(
                self.db,
                project_id=project_id,
                content=seed.content,
                memory_class=proposal.memory_class,
                scope=proposal.scope,
                timeline_id=scene.timeline_id,
                scene_id=seed.scene_id or scene.id,
                character_id=seed.character_id,
                importance=proposal.importance,
                source_event_id=seed.source_event_id,
                metadata={
                    "visibility": "actor" if proposal.scope is MemoryScope.CHARACTER else "world",
                    "origin_event_type": seed.event_type,
                    "lifecycle_reason": proposal.reason,
                    "timeline_id": scene.timeline_id,
                },
            )
        return memory_lifecycle.retention_report(proposals)

    def _trace_payload(
        self,
        trace: GenerationTrace,
        timer: StageTimer,
        claims: GenerationClaims | None,
        validation: ClaimValidation | None,
        retention: dict[str, Any] | None,
    ) -> dict[str, Any]:
        trace.timings = timer.to_list()
        trace.generated_events = claim_summary(claims) if claims is not None else trace.generated_events
        if validation is not None:
            trace.validation = validation.to_dict()
            trace.contradictions = [warning.to_dict() for warning in validation.warnings]
        if retention is not None:
            trace.memory_writes = retention.get("proposals", [])
        trace.status = "traced"
        payload = trace.to_dict()
        payload["performance"] = {
            "stages": timer.to_list(),
            "total_ms": timer.total_ms,
            "by_stage": timer.by_name(),
        }
        return payload

    def _scene_title(self, premise: str, fallback: str = "Untitled scene") -> str:
        cleaned = " ".join(premise.split())
        return cleaned[:80] if cleaned else fallback

    def _event_dict(self, event: Any) -> dict[str, Any]:
        return {
            "id": event.id,
            "timeline_id": event.timeline_id,
            "node_id": event.node_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": event.payload,
            "source": event.source,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
