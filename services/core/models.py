from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from services.core.enums import (
    AuthorityMode,
    CommitmentStatus,
    Horizon,
    Lane,
    MemoryScope,
    PlanStatus,
)


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    active_timeline_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    authority_mode: Mapped[str] = mapped_column(
        String(30), default=AuthorityMode.DIRECTOR_ASSISTED.value, server_default=AuthorityMode.DIRECTOR_ASSISTED.value
    )
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(80), default="text")
    title: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CharacterCard(Base):
    __tablename__ = "character_cards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), unique=True)
    card_version: Mapped[str] = mapped_column(String(20), default="2")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CharacterState(Base):
    __tablename__ = "character_states"
    __table_args__ = (UniqueConstraint("character_id", "timeline_id", name="uq_character_state_timeline"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id", ondelete="CASCADE"), index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Lorebook(Base):
    __tablename__ = "lorebooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), default="project")
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LorebookEntry(Base):
    __tablename__ = "lorebook_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lorebook_id: Mapped[str] = mapped_column(ForeignKey("lorebooks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    primary_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    secondary_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    constant: Mapped[bool] = mapped_column(Boolean, default=False)
    selective: Mapped[bool] = mapped_column(Boolean, default=True)
    recursive: Mapped[bool] = mapped_column(Boolean, default=True)
    insertion_order: Mapped[int] = mapped_column(Integer, default=0)
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    scan_depth: Mapped[int] = mapped_column(Integer, default=4)
    enabled_for_model: Mapped[list[str]] = mapped_column(JSON, default=list)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Faction(Base):
    __tablename__ = "factions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("project_id", "source_character_id", "target_character_id", name="uq_relationship"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    target_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(100), default="unknown")
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Timeline(Base):
    __tablename__ = "timelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Main")
    parent_timeline_id: Mapped[str | None] = mapped_column(ForeignKey("timelines.id", ondelete="SET NULL"), nullable=True)
    forked_from_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        Index(
            "uq_scenes_current_timeline",
            "timeline_id",
            unique=True,
            sqlite_where=text("current = 1"),
            postgresql_where=text("current"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="Untitled scene")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    authority_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    staging: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    staging_revision: Mapped[int] = mapped_column(Integer, default=0)
    approved_staging_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SceneParticipant(Base):
    __tablename__ = "scene_participants"
    __table_args__ = (UniqueConstraint("scene_id", "character_id", name="uq_scene_participant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    control_mode: Mapped[str] = mapped_column(String(20), default="ai")
    presence: Mapped[str] = mapped_column(String(30), default="present")
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TimelineNode(Base):
    __tablename__ = "timeline_nodes"
    __table_args__ = (UniqueConstraint("timeline_id", "sequence", name="uq_timeline_node_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id", ondelete="CASCADE"), index=True)
    parent_node_id: Mapped[str | None] = mapped_column(ForeignKey("timeline_nodes.id", ondelete="SET NULL"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("timeline_id", "sequence", name="uq_event_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("timeline_nodes.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_character_id: Mapped[str | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="system")
    causation_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DirectorIntent(Base):
    __tablename__ = "director_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    intent_type: Mapped[str] = mapped_column(String(50), default="story_commitment")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="")
    horizon: Mapped[str] = mapped_column(String(30), default="short")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # Interpretation is recorded, never derived on read: an intent is what the
    # user said, and the plan below is how we intend to accomplish it.
    lane: Mapped[str | None] = mapped_column(String(30), nullable=True)
    specification: Mapped[str | None] = mapped_column(String(30), nullable=True)
    consistency: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective: Mapped[str] = mapped_column(Text, default="", server_default=sql_text("''"))
    desired_outcome: Mapped[str] = mapped_column(Text, default="", server_default=sql_text("''"))
    constraints_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    exclusions_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    tone: Mapped[str] = mapped_column(String(50), default="", server_default=sql_text("''"))
    urgency: Mapped[str] = mapped_column(String(50), default="", server_default=sql_text("''"))
    canon_preference: Mapped[str] = mapped_column(String(50), default="", server_default=sql_text("''"))
    user_control_level: Mapped[str] = mapped_column(String(50), default="", server_default=sql_text("''"))
    interpretation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    interpretation_source: Mapped[str] = mapped_column(String(30), default="", server_default=sql_text("''"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class DirectorPlan(Base):
    """A proposed execution strategy. Narrative artifact, not a transcript.

    A ``proposed`` row is a proposal awaiting a decision: it emits no events and
    affects no projection. Only ``approved`` onward is committed, and
    ``superseded``/``cancelled`` rows are the record of what was *not* chosen.
    The interpreted intent is retained verbatim in ``intent_json`` so editing a
    plan can never silently rewrite what the user asked for.
    """

    __tablename__ = "director_plans"
    __table_args__ = (
        Index("ix_director_plans_scene_status", "scene_id", "status"),
        Index("ix_director_plans_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str | None] = mapped_column(
        ForeignKey("timelines.id", ondelete="SET NULL", name="fk_director_plans_timeline"),
        nullable=True,
        index=True,
    )
    scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL", name="fk_director_plans_scene"),
        nullable=True,
    )
    intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("director_intents.id", ondelete="SET NULL", name="fk_director_plans_intent"),
        nullable=True,
    )
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    checkpoint_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    superseded_by_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), default=PlanStatus.PROPOSED.value, server_default=PlanStatus.PROPOSED.value
    )
    lane: Mapped[str] = mapped_column(String(30), default=Lane.DIRECTION.value, server_default=Lane.DIRECTION.value)
    horizon: Mapped[str] = mapped_column(
        String(30), default=Horizon.NEAR_TERM.value, server_default=Horizon.NEAR_TERM.value
    )
    authority_mode: Mapped[str] = mapped_column(
        String(30),
        default=AuthorityMode.DIRECTOR_ASSISTED.value,
        server_default=AuthorityMode.DIRECTOR_ASSISTED.value,
    )
    objective: Mapped[str] = mapped_column(Text, default="", server_default=sql_text("''"))
    summary: Mapped[str] = mapped_column(Text, default="", server_default=sql_text("''"))
    required_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    current_beat_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    intent_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    canon_conflict_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class StoryCommitment(Base):
    __tablename__ = "story_commitments"
    __table_args__ = (Index("ix_story_commitments_open", "project_id", "timeline_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("director_intents.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str | None] = mapped_column(
        ForeignKey("timelines.id", ondelete="SET NULL", name="fk_story_commitments_timeline"),
        nullable=True,
    )
    forked_from_commitment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=CommitmentStatus.CREATED.value)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    created_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_timeline_active", "timeline_id", "is_active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[str | None] = mapped_column(ForeignKey("timelines.id", ondelete="SET NULL"), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    character_id: Mapped[str | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    memory_class: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    scope: Mapped[str] = mapped_column(
        String(30), default=MemoryScope.SCENE.value, server_default=MemoryScope.SCENE.value, index=True
    )
    valid_from_sequence: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    timeline_id: Mapped[str | None] = mapped_column(ForeignKey("timelines.id", ondelete="SET NULL"), nullable=True)
    checkpoint_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    input_text: Mapped[str] = mapped_column(Text, default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    structured_output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_name: Mapped[str] = mapped_column(String(100), default="")
    model_name: Mapped[str] = mapped_column(String(200), default="")
    lore_debug: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    context_debug: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelProvider(Base):
    __tablename__ = "model_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    adapter: Mapped[str] = mapped_column(String(100), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(1000), default="")
    model_name: Mapped[str] = mapped_column(String(200), default="")
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(ForeignKey("model_providers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), default="")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    credential_env: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
