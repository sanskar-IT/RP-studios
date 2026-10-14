"""Attribute events to the scene that produced them.

Adds a nullable ``scene_id`` to ``events`` so the recent-events block can be
scoped to the active scene (ADR 0004). Existing rows keep NULL, which the
renderer treats as legacy: shown everywhere, exactly as today. No backfill is
attempted, because the scene of a historical event cannot be derived reliably
and silently redacting history would be worse than showing it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_event_scene_visibility"
down_revision = "0005_narrative_director"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index.get("name") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _foreign_keys(table_name: str) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        columns = tuple(constraint.get("constrained_columns") or [])
        referred = constraint.get("referred_table")
        if columns and referred:
            keys.add((*columns, referred))
    return keys


def upgrade() -> None:
    if "scene_id" not in _columns("events"):
        with op.batch_alter_table("events") as batch:
            batch.add_column(sa.Column("scene_id", sa.String(length=36), nullable=True))
    if "ix_events_scene_id" not in _indexes("events"):
        op.create_index("ix_events_scene_id", "events", ["scene_id"], unique=False)
    if ("scene_id", "scenes") not in _foreign_keys("events"):
        with op.batch_alter_table("events") as batch:
            batch.create_foreign_key(
                "fk_events_scene",
                "scenes",
                ["scene_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if "ix_events_scene_id" in _indexes("events"):
        op.drop_index("ix_events_scene_id", table_name="events")
    if "scene_id" in _columns("events"):
        with op.batch_alter_table("events") as batch:
            batch.drop_column("scene_id")
