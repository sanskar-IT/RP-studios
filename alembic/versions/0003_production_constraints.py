"""Production constraints for append order and the current scene.

The baseline could not declare these because they arrived with the
vertical-slice behavior, and existing databases may already carry equivalent
objects under database-generated names. Objects are therefore added only when
the inspector does not already report an equivalent constraint, and the
downgrade removes only the objects this revision created.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_production_constraints"
down_revision = "0002_vertical_slice"
branch_labels = None
depends_on = None

TIMELINE_NODE_SEQUENCE_CONSTRAINT = "uq_timeline_node_sequence"
EVENT_SEQUENCE_CONSTRAINT = "uq_event_sequence"
CURRENT_SCENE_INDEX = "uq_scenes_current_timeline"
SEQUENCE_COLUMNS = ["timeline_id", "sequence"]
CURRENT_SCENE_COLUMNS = ["timeline_id"]


def _unique_constraint_name(table_name: str, columns: list[str]) -> str | None:
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_unique_constraints(table_name):
        if list(constraint.get("column_names") or []) == columns:
            return constraint.get("name")
    for index in inspector.get_indexes(table_name):
        if index.get("unique") and list(index.get("column_names") or []) == columns:
            return index.get("name")
    return None


def upgrade() -> None:
    if _unique_constraint_name("timeline_nodes", SEQUENCE_COLUMNS) is None:
        with op.batch_alter_table("timeline_nodes") as batch:
            batch.create_unique_constraint(
                TIMELINE_NODE_SEQUENCE_CONSTRAINT,
                SEQUENCE_COLUMNS,
            )
    if _unique_constraint_name("events", SEQUENCE_COLUMNS) is None:
        with op.batch_alter_table("events") as batch:
            batch.create_unique_constraint(EVENT_SEQUENCE_CONSTRAINT, SEQUENCE_COLUMNS)
    if _unique_constraint_name("scenes", CURRENT_SCENE_COLUMNS) is None:
        op.create_index(
            CURRENT_SCENE_INDEX,
            "scenes",
            CURRENT_SCENE_COLUMNS,
            unique=True,
            postgresql_where=sa.text("current"),
            sqlite_where=sa.text("current = 1"),
        )


def downgrade() -> None:
    for table_name, constraint_name in (
        ("events", EVENT_SEQUENCE_CONSTRAINT),
        ("timeline_nodes", TIMELINE_NODE_SEQUENCE_CONSTRAINT),
    ):
        if _unique_constraint_name(table_name, SEQUENCE_COLUMNS) != constraint_name:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(constraint_name, type_="unique")
    if _unique_constraint_name("scenes", CURRENT_SCENE_COLUMNS) == CURRENT_SCENE_INDEX:
        op.drop_index(CURRENT_SCENE_INDEX, table_name="scenes")
