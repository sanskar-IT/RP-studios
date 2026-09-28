from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from services.core.enums import (
    AuthorityMode,
    EventSource,
    IntentStatus,
    MemoryClass,
    MemoryScope,
    PlanStatus,
    SceneStatus,
)
from services.core.events import (
    KNOWN_EVENT_TYPES,
    SCENE_CANCELLED,
    SCENE_ENDED,
    SCENE_STARTED,
    TIMELINE_FORKED,
    validate_event_payload,
)
from services.core.models import (
    Character,
    CharacterState,
    DirectorIntent,
    DirectorPlan,
    Event,
    Generation,
    Memory,
    Project,
    Scene,
    SceneParticipant,
    StoryCommitment,
    Timeline,
    TimelineNode,
    World,
    new_id,
    utc_now,
)
from services.core.state import StateSnapshot, apply_event


def create_project(db: Session, name: str, description: str = "", extra_data: dict[str, Any] | None = None) -> Project:
    project = Project(name=name, description=description, extra_data=extra_data or {})
    db.add(project)
    db.flush()
    world = World(project_id=project.id, name=f"{name} world")
    timeline = Timeline(project_id=project.id, name="Main")
    db.add_all([world, timeline])
    db.flush()
    root = TimelineNode(timeline_id=timeline.id, sequence=0, checkpoint=StateSnapshot().to_dict())
    db.add(root)
    db.flush()
    project.active_timeline_id = timeline.id
    db.add(project)
    db.flush()
    return project


def get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found")
    return project


def get_timeline(db: Session, timeline_id: str) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise LookupError("Timeline not found")
    return timeline


def latest_node(db: Session, timeline_id: str) -> TimelineNode:
    node = db.scalar(select(TimelineNode).where(TimelineNode.timeline_id == timeline_id).order_by(desc(TimelineNode.sequence)))
    if node is None:
        raise LookupError("Timeline has no root node")
    return node


def current_state(db: Session, timeline_id: str) -> StateSnapshot:
    return StateSnapshot.from_dict(latest_node(db, timeline_id).checkpoint)


def _event_character_ids(event_type: str, payload: dict[str, Any]) -> set[str]:
    keys = {"character_id", "actor_character_id", "source_character_id", "target_character_id"}
    return {str(payload[key]) for key in keys if payload.get(key)}


def append_event(
    db: Session,
    *,
    project_id: str,
    timeline_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: EventSource | str = EventSource.SYSTEM,
    actor_character_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[Event, TimelineNode, StateSnapshot]:
    if event_type not in KNOWN_EVENT_TYPES:
        raise ValueError(f"Unsupported narrative event type: {event_type}")
    event_payload = dict(payload or {})
    validate_event_payload(event_type, event_payload)
    timeline = db.get(Timeline, timeline_id)
    if timeline is None or timeline.project_id != project_id:
        raise ValueError("Timeline does not belong to project")
    if idempotency_key:
        existing = db.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
        if existing is not None:
            node = db.get(TimelineNode, existing.node_id)
            if node is None:
                raise RuntimeError("Existing event points to a missing node")
            return existing, node, current_state(db, timeline_id)
    parent = latest_node(db, timeline_id)
    previous = StateSnapshot.from_dict(parent.checkpoint)
    node_id = new_id()
    node = TimelineNode(
        id=node_id,
        timeline_id=timeline_id,
        parent_node_id=parent.id,
        sequence=parent.sequence + 1,
        checkpoint=previous.to_dict(),
    )
    db.add(node)
    db.flush()
    event = Event(
        id=new_id(),
        project_id=project_id,
        timeline_id=timeline_id,
        node_id=node_id,
        sequence=node.sequence,
        event_type=event_type,
        payload=event_payload,
        actor_character_id=actor_character_id,
        source=source.value if isinstance(source, EventSource) else str(source),
        causation_id=causation_id,
        idempotency_key=idempotency_key,
    )
    db.add(event)
    next_state = apply_event(previous, event_type, event_payload)
    node.checkpoint = next_state.to_dict()
    for character_id in _event_character_ids(event_type, event_payload):
        state_row = db.scalar(
            select(CharacterState).where(
                CharacterState.character_id == character_id,
                CharacterState.timeline_id == timeline_id,
            )
        )
        if state_row is None:
            state_row = CharacterState(
                project_id=project_id,
                character_id=character_id,
                timeline_id=timeline_id,
                state=next_state.characters.get(character_id, {}),
                revision=next_state.revision,
            )
            db.add(state_row)
        else:
            state_row.state = next_state.characters.get(character_id, {})
            state_row.revision = next_state.revision
    db.flush()
    return event, node, next_state


def create_scene(
    db: Session,
    *,
    project_id: str,
    timeline_id: str,
    title: str,
    staging: dict[str, Any] | None = None,
    participant_ids: list[str] | None = None,
) -> Scene:
    for existing_scene in db.scalars(
        select(Scene).where(
            Scene.project_id == project_id,
            Scene.timeline_id == timeline_id,
            Scene.current.is_(True),
        )
    ):
        existing_scene.current = False
        db.add(existing_scene)
    scene = Scene(
        project_id=project_id,
        timeline_id=timeline_id,
        title=title,
        status=SceneStatus.DRAFT.value,
        staging=staging or {},
        current=True,
    )
    db.add(scene)
    db.flush()
    for character_id in participant_ids or []:
        db.add(SceneParticipant(scene_id=scene.id, character_id=character_id))
    db.flush()
    return scene


def replace_scene_participants(db: Session, scene: Scene, character_ids: list[str]) -> list[SceneParticipant]:
    valid_ids = set(db.scalars(select(Character.id).where(Character.project_id == scene.project_id)))
    invalid_ids = set(character_ids) - valid_ids
    if invalid_ids:
        raise ValueError("Scene participants must belong to the project")
    db.execute(delete(SceneParticipant).where(SceneParticipant.scene_id == scene.id))
    participants = [SceneParticipant(scene_id=scene.id, character_id=character_id) for character_id in character_ids]
    db.add_all(participants)
    db.flush()
    return participants


def set_scene_status(db: Session, scene: Scene, status: SceneStatus | str) -> None:
    scene.status = status.value if isinstance(status, SceneStatus) else str(status)
    db.add(scene)
    db.flush()


def start_scene_event(db: Session, scene: Scene) -> Event:
    event, _, _ = append_event(
        db,
        project_id=scene.project_id,
        timeline_id=scene.timeline_id,
        event_type=SCENE_STARTED,
        payload={"scene_id": scene.id, "title": scene.title},
        source=EventSource.SYSTEM,
    )
    set_scene_status(db, scene, SceneStatus.ACTIVE)
    return event


def end_scene_event(db: Session, scene: Scene) -> Event:
    event, _, _ = append_event(
        db,
        project_id=scene.project_id,
        timeline_id=scene.timeline_id,
        event_type=SCENE_ENDED,
        payload={"scene_id": scene.id},
        source=EventSource.SYSTEM,
    )
    set_scene_status(db, scene, SceneStatus.COMPLETED)
    return event


def cancel_scene_event(db: Session, scene: Scene) -> Event:
    event, _, _ = append_event(
        db,
        project_id=scene.project_id,
        timeline_id=scene.timeline_id,
        event_type=SCENE_CANCELLED,
        payload={"scene_id": scene.id, "title": scene.title},
        source=EventSource.USER,
    )
    set_scene_status(db, scene, SceneStatus.CANCELLED)
    return event


def get_scene(db: Session, scene_id: str) -> Scene:
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise LookupError("Scene not found")
    return scene


def list_characters(db: Session, project_id: str) -> list[Character]:
    return list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id, Character.is_active.is_(True))
            .order_by(Character.name)
        )
    )


def list_scenes(db: Session, project_id: str) -> list[Scene]:
    return list(db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(desc(Scene.updated_at))))


def list_timelines(db: Session, project_id: str) -> list[Timeline]:
    return list(db.scalars(select(Timeline).where(Timeline.project_id == project_id).order_by(Timeline.created_at)))


def list_events(db: Session, timeline_id: str) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.timeline_id == timeline_id).order_by(Event.sequence)))


def list_checkpoints(db: Session, timeline_id: str) -> list[dict[str, Any]]:
    nodes = list(db.scalars(select(TimelineNode).where(TimelineNode.timeline_id == timeline_id).order_by(TimelineNode.sequence)))
    events = {
        event.node_id: event
        for event in db.scalars(select(Event).where(Event.timeline_id == timeline_id))
    }
    generations = {
        generation.checkpoint_node_id: generation.id
        for generation in db.scalars(select(Generation).where(Generation.timeline_id == timeline_id))
        if generation.checkpoint_node_id
    }
    return [
        {
            "id": node.id,
            "timeline_id": node.timeline_id,
            "sequence": node.sequence,
            "parent_node_id": node.parent_node_id,
            "event_id": events[node.id].id if node.id in events else None,
            "event_type": events[node.id].event_type if node.id in events else None,
            "generation_id": generations.get(node.id),
        }
        for node in nodes
    ]


def fork_timeline(db: Session, *, source_timeline_id: str, source_node_id: str, name: str) -> Timeline:
    source_node = db.get(TimelineNode, source_node_id)
    if source_node is None or source_node.timeline_id != source_timeline_id:
        raise LookupError("Fork source node not found")
    source_timeline = db.get(Timeline, source_timeline_id)
    if source_timeline is None:
        raise LookupError("Source timeline not found")
    branch = Timeline(
        project_id=source_timeline.project_id,
        name=name,
        parent_timeline_id=source_timeline.id,
        forked_from_node_id=source_node.id,
    )
    db.add(branch)
    db.flush()
    root = TimelineNode(
        timeline_id=branch.id,
        sequence=0,
        checkpoint=StateSnapshot.from_dict(source_node.checkpoint).to_dict(),
    )
    db.add(root)
    db.flush()
    for state_row in db.scalars(
        select(CharacterState).where(CharacterState.timeline_id == source_timeline.id)
    ):
        db.add(
            CharacterState(
                project_id=branch.project_id,
                character_id=state_row.character_id,
                timeline_id=branch.id,
                state=deepcopy(state_row.state),
                revision=state_row.revision,
            )
        )
    for memory in db.scalars(select(Memory).where(Memory.timeline_id == source_timeline.id)):
        metadata = deepcopy(memory.metadata_json or {})
        if memory.scene_id:
            metadata["source_scene_id"] = memory.scene_id
        # Recorded so retrieval can collapse this copy and the original into one
        # entry instead of injecting the same memory twice on the branch.
        metadata["inherited_from_memory_id"] = memory.id
        metadata["inherited_at_sequence"] = source_node.sequence
        db.add(
            Memory(
                project_id=branch.project_id,
                timeline_id=branch.id,
                scene_id=None,
                character_id=memory.character_id,
                memory_class=memory.memory_class,
                content=memory.content,
                importance=memory.importance,
                scope=memory.scope,
                valid_from_sequence=source_node.sequence,
                is_active=memory.is_active,
                source_event_id=memory.source_event_id,
                archived_at=memory.archived_at,
                embedding=deepcopy(memory.embedding),
                metadata_json=metadata,
            )
        )
    for commitment in db.scalars(
        select(StoryCommitment).where(
            StoryCommitment.project_id == source_timeline.project_id,
            StoryCommitment.timeline_id == source_timeline.id,
        )
    ):
        db.add(
            StoryCommitment(
                project_id=commitment.project_id,
                intent_id=commitment.intent_id,
                timeline_id=branch.id,
                forked_from_commitment_id=commitment.id,
                description=commitment.description,
                status=commitment.status,
                priority=commitment.priority,
                progress=commitment.progress,
                created_sequence=source_node.sequence,
                metadata_json={
                    **deepcopy(commitment.metadata_json or {}),
                    "inherited_at_sequence": source_node.sequence,
                },
            )
        )
    append_event(
        db,
        project_id=branch.project_id,
        timeline_id=branch.id,
        event_type=TIMELINE_FORKED,
        payload={
            "source_timeline_id": source_timeline.id,
            "source_node_id": source_node.id,
            "name": name,
        },
        source=EventSource.SYSTEM,
    )
    db.flush()
    return branch


def update_project_timeline(db: Session, project: Project, timeline_id: str) -> None:
    project.active_timeline_id = timeline_id
    db.add(project)
    db.flush()


def add_character(
    db: Session,
    *,
    project_id: str,
    name: str,
    definition: dict[str, Any] | None = None,
    source_id: str | None = None,
) -> Character:
    character = Character(project_id=project_id, name=name, definition=definition or {}, source_id=source_id)
    db.add(character)
    db.flush()
    return character


def add_memory(
    db: Session,
    *,
    project_id: str,
    content: str,
    memory_class: MemoryClass | str,
    timeline_id: str | None = None,
    scene_id: str | None = None,
    character_id: str | None = None,
    importance: float = 0.5,
    metadata: dict[str, Any] | None = None,
    scope: MemoryScope | str | None = None,
    source_event_id: str | None = None,
    is_active: bool = True,
) -> Memory:
    resolved_class = memory_class.value if isinstance(memory_class, MemoryClass) else str(memory_class)
    resolved_scope = (
        scope.value
        if isinstance(scope, MemoryScope)
        else (str(scope) if scope else default_memory_scope(resolved_class))
    )
    memory = Memory(
        project_id=project_id,
        content=content,
        memory_class=resolved_class,
        timeline_id=timeline_id,
        scene_id=scene_id,
        character_id=character_id,
        importance=importance,
        scope=resolved_scope,
        valid_from_sequence=latest_node(db, timeline_id).sequence if timeline_id else 0,
        is_active=is_active,
        source_event_id=source_event_id,
        metadata_json={**(metadata or {}), "timeline_id": timeline_id} if timeline_id else dict(metadata or {}),
    )
    db.add(memory)
    db.flush()
    return memory


def default_memory_scope(memory_class: str) -> str:
    """The scope a class implies when the caller does not state one.

    Kept in one place so the write policy and the migration backfill cannot
    disagree about what a legacy row meant.
    """
    if memory_class in {MemoryClass.PERMANENT.value, MemoryClass.ARCHIVE.value}:
        return MemoryScope.WORLD.value
    if memory_class == MemoryClass.PERSISTENT.value:
        return MemoryScope.CHARACTER.value
    return MemoryScope.SCENE.value


def timeline_lineage(db: Session, timeline_id: str) -> set[str]:
    """The timeline and every ancestor it was forked from.

    Cycle-safe: a malformed chain stops rather than looping, because a lineage
    walk that never terminates would hang the request.
    """
    lineage: set[str] = set()
    current = db.get(Timeline, timeline_id)
    while current is not None and current.id not in lineage:
        lineage.add(current.id)
        current = db.get(Timeline, current.parent_timeline_id) if current.parent_timeline_id else None
    return lineage


def timeline_lineage_sequences(db: Session, timeline_id: str) -> dict[str, int]:
    """For each ancestor, the sequence at which the current branch left it.

    A memory inherited from an ancestor is only visible at or before the point
    where the branch diverged. This is the gate that stops a sibling branch's
    later memories from appearing on this one.
    """
    sequences: dict[str, int] = {}
    current = db.get(Timeline, timeline_id)
    child = current
    guard = 0
    while child is not None and guard < 64:
        guard += 1
        parent = db.get(Timeline, child.parent_timeline_id) if child.parent_timeline_id else None
        if parent is None:
            break
        fork_node = child.forked_from_node_id
        fork_sequence: int | None = None
        if fork_node:
            fork_row = db.get(TimelineNode, fork_node)
            if fork_row is not None:
                fork_sequence = fork_row.sequence
        sequences[parent.id] = (
            fork_sequence if fork_sequence is not None else latest_node(db, parent.id).sequence
        )
        child = parent
    return sequences


def supersede_memory(db: Session, memory: Memory, replacement_id: str | None = None) -> Memory:
    """Retire a memory without deleting it.

    Memory rows are evidence of what the engine believed and when. Removing one
    would make an investigation of a continuity bug impossible.
    """
    memory.is_active = False
    memory.superseded_by_id = replacement_id
    memory.archived_at = utc_now()
    db.add(memory)
    db.flush()
    return memory


def list_memories(
    db: Session,
    *,
    project_id: str,
    timeline_id: str | None = None,
    include_inactive: bool = True,
    limit: int = 500,
) -> list[Memory]:
    statement = select(Memory).where(Memory.project_id == project_id)
    if timeline_id:
        lineage = timeline_lineage(db, timeline_id)
        statement = statement.where(
            (Memory.timeline_id.is_(None)) | (Memory.timeline_id.in_(sorted(lineage)))
        )
    if not include_inactive:
        statement = statement.where(Memory.is_active.is_(True))
    statement = statement.order_by(desc(Memory.created_at), desc(Memory.importance)).limit(limit)
    return list(db.scalars(statement))


def add_generation(db: Session, **values: Any) -> Generation:
    generation = Generation(**values)
    db.add(generation)
    db.flush()
    return generation


def resolve_authority(db: Session, *, project_id: str, scene_id: str | None = None) -> AuthorityMode:
    """The authority mode in force: scene override, else project default.

    Authority is narrative configuration, not a user property: the same operator
    can run a strict project and an AI-directed one.
    """
    if scene_id:
        scene = db.get(Scene, scene_id)
        if scene is not None and scene.project_id == project_id and scene.authority_mode:
            return AuthorityMode.coerce(scene.authority_mode)
    project = db.get(Project, project_id)
    if project is None:
        return AuthorityMode.DIRECTOR_ASSISTED
    return AuthorityMode.coerce(project.authority_mode)


def record_intent(
    db: Session,
    *,
    project_id: str,
    scene_id: str | None,
    intent_type: str,
    text: str,
    horizon: str = "short",
    intent: Any | None = None,
    lane: str | None = None,
    interpretation_source: str = "",
) -> DirectorIntent:
    """Persist an interpreted intent.

    The interpretation columns are written once, here. A plan built from this
    intent is a separate artifact and may never rewrite these values.
    """
    row = DirectorIntent(
        project_id=project_id,
        scene_id=scene_id,
        intent_type=intent_type,
        text=text,
        goal=text,
        horizon=horizon,
        status=IntentStatus.PENDING.value,
    )
    if intent is not None:
        payload = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
        row.lane = lane or str(payload.get("lane", "")) or None
        row.specification = str(payload.get("specification", "")) or None
        row.consistency = str(payload.get("consistency", "")) or None
        row.confidence = float(payload.get("confidence", 0.0))
        row.objective = str(payload.get("objective", ""))
        row.desired_outcome = str(payload.get("desired_outcome", ""))
        row.constraints_json = list(payload.get("constraints", []))
        row.exclusions_json = list(payload.get("exclusions", []))
        row.tone = str(payload.get("tone", ""))
        row.urgency = str(payload.get("urgency", ""))
        row.canon_preference = str(payload.get("canon_preference", ""))
        row.user_control_level = str(payload.get("user_control_level", ""))
        row.interpretation_json = payload
    row.interpretation_source = interpretation_source
    db.add(row)
    db.flush()
    return row


def add_director_plan(db: Session, **values: Any) -> DirectorPlan:
    plan = DirectorPlan(**values)
    db.add(plan)
    db.flush()
    return plan


def get_director_plan(db: Session, plan_id: str) -> DirectorPlan:
    plan = db.get(DirectorPlan, plan_id)
    if plan is None:
        raise LookupError("Director plan not found")
    return plan


def list_director_plans(
    db: Session,
    *,
    project_id: str,
    scene_id: str | None = None,
    timeline_id: str | None = None,
    limit: int = 50,
) -> list[DirectorPlan]:
    statement = select(DirectorPlan).where(DirectorPlan.project_id == project_id)
    if scene_id:
        statement = statement.where(DirectorPlan.scene_id == scene_id)
    if timeline_id:
        statement = statement.where(DirectorPlan.timeline_id == timeline_id)
    statement = statement.order_by(desc(DirectorPlan.created_at), DirectorPlan.id).limit(limit)
    return list(db.scalars(statement))


def active_director_plan(db: Session, *, scene_id: str) -> DirectorPlan | None:
    """The plan currently driving a scene, if any."""
    return db.scalar(
        select(DirectorPlan)
        .where(
            DirectorPlan.scene_id == scene_id,
            DirectorPlan.status.in_(
                [
                    PlanStatus.APPROVED.value,
                    PlanStatus.EXECUTING.value,
                ]
            ),
        )
        .order_by(desc(DirectorPlan.created_at))
    )


def get_generation(db: Session, generation_id: str) -> Generation:
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise LookupError("Generation not found")
    return generation


def list_generations(
    db: Session,
    *,
    project_id: str,
    timeline_id: str | None = None,
    limit: int = 25,
) -> list[Generation]:
    statement = select(Generation).where(Generation.project_id == project_id)
    if timeline_id:
        statement = statement.where(Generation.timeline_id == timeline_id)
    statement = statement.order_by(desc(Generation.created_at)).limit(limit)
    return list(db.scalars(statement))
