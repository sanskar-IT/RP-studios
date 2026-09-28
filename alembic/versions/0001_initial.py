"""Static baseline schema.

The baseline is written out explicitly instead of calling
``Base.metadata.create_all`` at migration time. Every table, index, foreign
key, and unique constraint that existed before the vertical-slice fields is
declared here, so the chain is reviewable, works on SQLite and PostgreSQL, and
can be downgraded in dependency order.

``memories.embedding`` is a nullable pgvector column. It is intentionally left
without a dimension and without a vector index: no code writes embeddings yet,
and an unconstrained vector column does not require a live pgvector server.
PostgreSQL still needs the ``vector`` extension, which is created below.

``0002_vertical_slice`` adds ``scenes.staging_revision``,
``scenes.approved_staging_revision``, and ``generations.checkpoint_node_id``,
and ``0003_production_constraints`` adds the append-order and current-scene
uniqueness guarantees.
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _create_workspace_tables() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active_timeline_id", sa.String(length=36), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("uri", sa.String(length=1000), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_project_id", "sources", ["project_id"], unique=False)
    op.create_table(
        "worlds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worlds_project_id", "worlds", ["project_id"], unique=False)
    op.create_table(
        "factions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factions_project_id", "factions", ["project_id"], unique=False)
    op.create_index("ix_factions_name", "factions", ["name"], unique=False)
    op.create_table(
        "locations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locations_project_id", "locations", ["project_id"], unique=False)
    op.create_index("ix_locations_name", "locations", ["name"], unique=False)
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("adapter", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_model_providers_name"),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("credential_env", sa.String(length=200), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_configs_provider_id", "model_configs", ["provider_id"], unique=False)


def _create_cast_tables() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_characters_project_id", "characters", ["project_id"], unique=False)
    op.create_index("ix_characters_name", "characters", ["name"], unique=False)
    op.create_table(
        "character_cards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("card_version", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("extensions", sa.JSON(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id", name="uq_character_cards_character_id"),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_character_id", sa.String(length=36), nullable=False),
        sa.Column("target_character_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=100), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_character_id",
            "target_character_id",
            name="uq_relationship",
        ),
    )
    op.create_index("ix_relationships_project_id", "relationships", ["project_id"], unique=False)
    op.create_index(
        "ix_relationships_source_character_id",
        "relationships",
        ["source_character_id"],
        unique=False,
    )
    op.create_index(
        "ix_relationships_target_character_id",
        "relationships",
        ["target_character_id"],
        unique=False,
    )


def _create_timeline_tables() -> None:
    op.create_table(
        "timelines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_timeline_id", sa.String(length=36), nullable=True),
        sa.Column("forked_from_node_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_timeline_id"], ["timelines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_timelines_project_id", "timelines", ["project_id"], unique=False)
    op.create_table(
        "scenes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("staging", sa.JSON(), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenes_project_id", "scenes", ["project_id"], unique=False)
    op.create_index("ix_scenes_timeline_id", "scenes", ["timeline_id"], unique=False)
    op.create_table(
        "timeline_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=False),
        sa.Column("parent_node_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_node_id"], ["timeline_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_timeline_nodes_timeline_id",
        "timeline_nodes",
        ["timeline_id"],
        unique=False,
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor_character_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("causation_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_character_id"], ["characters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["causation_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["node_id"], ["timeline_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
    )
    op.create_index("ix_events_project_id", "events", ["project_id"], unique=False)
    op.create_index("ix_events_timeline_id", "events", ["timeline_id"], unique=False)
    op.create_index("ix_events_node_id", "events", ["node_id"], unique=False)
    op.create_index("ix_events_event_type", "events", ["event_type"], unique=False)
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=True),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("character_id", sa.String(length=36), nullable=True),
        sa.Column("memory_class", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("valid_from_sequence", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memories_project_id", "memories", ["project_id"], unique=False)
    op.create_index("ix_memories_memory_class", "memories", ["memory_class"], unique=False)
    op.create_table(
        "generations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("timeline_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("structured_output", sa.JSON(), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("lore_debug", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generations_project_id", "generations", ["project_id"], unique=False)


def _create_narrative_tables() -> None:
    op.create_table(
        "scene_participants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=False),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("control_mode", sa.String(length=20), nullable=False),
        sa.Column("presence", sa.String(length=30), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", "character_id", name="uq_scene_participant"),
    )
    op.create_index("ix_scene_participants_scene_id", "scene_participants", ["scene_id"], unique=False)
    op.create_index(
        "ix_scene_participants_character_id",
        "scene_participants",
        ["character_id"],
        unique=False,
    )
    op.create_table(
        "lorebooks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_character_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lorebooks_project_id", "lorebooks", ["project_id"], unique=False)
    op.create_index(
        "ix_lorebooks_owner_character_id",
        "lorebooks",
        ["owner_character_id"],
        unique=False,
    )
    op.create_table(
        "lorebook_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lorebook_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("primary_keys", sa.JSON(), nullable=False),
        sa.Column("secondary_keys", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("constant", sa.Boolean(), nullable=False),
        sa.Column("selective", sa.Boolean(), nullable=False),
        sa.Column("recursive", sa.Boolean(), nullable=False),
        sa.Column("insertion_order", sa.Integer(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("scan_depth", sa.Integer(), nullable=False),
        sa.Column("enabled_for_model", sa.JSON(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["lorebook_id"], ["lorebooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lorebook_entries_lorebook_id", "lorebook_entries", ["lorebook_id"], unique=False)
    op.create_table(
        "director_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("intent_type", sa.String(length=50), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("horizon", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_director_intents_project_id", "director_intents", ["project_id"], unique=False)
    op.create_table(
        "story_commitments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["intent_id"], ["director_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_story_commitments_project_id", "story_commitments", ["project_id"], unique=False)
    op.create_index("ix_story_commitments_intent_id", "story_commitments", ["intent_id"], unique=False)


def _create_state_tables() -> None:
    op.create_table(
        "character_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("character_id", "timeline_id", name="uq_character_state_timeline"),
    )
    op.create_index("ix_character_states_project_id", "character_states", ["project_id"], unique=False)
    op.create_index(
        "ix_character_states_character_id",
        "character_states",
        ["character_id"],
        unique=False,
    )
    op.create_index("ix_character_states_timeline_id", "character_states", ["timeline_id"], unique=False)


def _drop_state_tables() -> None:
    op.drop_index("ix_character_states_timeline_id", table_name="character_states")
    op.drop_index("ix_character_states_character_id", table_name="character_states")
    op.drop_index("ix_character_states_project_id", table_name="character_states")
    op.drop_table("character_states")


def _drop_narrative_tables() -> None:
    op.drop_index("ix_story_commitments_intent_id", table_name="story_commitments")
    op.drop_index("ix_story_commitments_project_id", table_name="story_commitments")
    op.drop_table("story_commitments")
    op.drop_index("ix_director_intents_project_id", table_name="director_intents")
    op.drop_table("director_intents")
    op.drop_index("ix_lorebook_entries_lorebook_id", table_name="lorebook_entries")
    op.drop_table("lorebook_entries")
    op.drop_index("ix_lorebooks_owner_character_id", table_name="lorebooks")
    op.drop_index("ix_lorebooks_project_id", table_name="lorebooks")
    op.drop_table("lorebooks")
    op.drop_index("ix_scene_participants_character_id", table_name="scene_participants")
    op.drop_index("ix_scene_participants_scene_id", table_name="scene_participants")
    op.drop_table("scene_participants")


def _drop_timeline_tables() -> None:
    op.drop_index("ix_generations_project_id", table_name="generations")
    op.drop_table("generations")
    op.drop_index("ix_memories_memory_class", table_name="memories")
    op.drop_index("ix_memories_project_id", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_events_node_id", table_name="events")
    op.drop_index("ix_events_timeline_id", table_name="events")
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_timeline_nodes_timeline_id", table_name="timeline_nodes")
    op.drop_table("timeline_nodes")
    op.drop_index("ix_scenes_timeline_id", table_name="scenes")
    op.drop_index("ix_scenes_project_id", table_name="scenes")
    op.drop_table("scenes")
    op.drop_index("ix_timelines_project_id", table_name="timelines")
    op.drop_table("timelines")


def _drop_cast_tables() -> None:
    op.drop_index("ix_relationships_target_character_id", table_name="relationships")
    op.drop_index("ix_relationships_source_character_id", table_name="relationships")
    op.drop_index("ix_relationships_project_id", table_name="relationships")
    op.drop_table("relationships")
    op.drop_table("character_cards")
    op.drop_index("ix_characters_name", table_name="characters")
    op.drop_index("ix_characters_project_id", table_name="characters")
    op.drop_table("characters")


def _drop_workspace_tables() -> None:
    op.drop_index("ix_model_configs_provider_id", table_name="model_configs")
    op.drop_table("model_configs")
    op.drop_table("model_providers")
    op.drop_index("ix_locations_name", table_name="locations")
    op.drop_index("ix_locations_project_id", table_name="locations")
    op.drop_table("locations")
    op.drop_index("ix_factions_name", table_name="factions")
    op.drop_index("ix_factions_project_id", table_name="factions")
    op.drop_table("factions")
    op.drop_index("ix_worlds_project_id", table_name="worlds")
    op.drop_table("worlds")
    op.drop_index("ix_sources_project_id", table_name="sources")
    op.drop_table("sources")
    op.drop_table("projects")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    _create_workspace_tables()
    _create_cast_tables()
    _create_timeline_tables()
    _create_narrative_tables()
    _create_state_tables()


def downgrade() -> None:
    _drop_state_tables()
    _drop_narrative_tables()
    _drop_timeline_tables()
    _drop_cast_tables()
    _drop_workspace_tables()
