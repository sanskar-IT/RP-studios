"""Context reliability columns for memory scope, commitment lifecycle, and tracing.

The narrative reliability milestone needed four things the previous schema could
not express: which eyes a memory is valid for, whether that memory is still
active, which timeline and ancestry a story commitment belongs to, and what
context produced a generation. Each column is added only when the inspector does
not already report it, and the downgrade removes only what this revision created.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_context_reliability"
down_revision = "0003_production_constraints"
branch_labels = None
depends_on = None

MEMORY_SCOPE_DEFAULT = "scene"
MEMORY_SCOPE_INDEX = "ix_memories_scope"
MEMORY_ACTIVE_INDEX = "ix_memories_timeline_active"
COMMITMENT_OPEN_INDEX = "ix_story_commitments_open"

# (table, column, type, nullable, server_default). Nullability and server
# defaults are declared here to match the model metadata exactly; a mismatch
# would show up as a schema drift in the migration parity test.
ADDED_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine, bool, object | None], ...] = (
    ("memories", "scope", sa.String(length=30), False, MEMORY_SCOPE_DEFAULT),
    ("memories", "is_active", sa.Boolean(), False, sa.true()),
    ("memories", "superseded_by_id", sa.String(length=36), True, None),
    ("memories", "source_event_id", sa.String(length=36), True, None),
    ("story_commitments", "timeline_id", sa.String(length=36), True, None),
    ("story_commitments", "forked_from_commitment_id", sa.String(length=36), True, None),
    ("story_commitments", "created_sequence", sa.Integer(), False, sa.text("0")),
    ("generations", "context_debug", sa.JSON(), True, None),
    ("generations", "validation", sa.JSON(), True, None),
    ("generations", "trace", sa.JSON(), True, None),
)

ADDED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (MEMORY_SCOPE_INDEX, "memories", ("scope",)),
    (MEMORY_ACTIVE_INDEX, "memories", ("timeline_id", "is_active")),
    (COMMITMENT_OPEN_INDEX, "story_commitments", ("project_id", "timeline_id", "status")),
)


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _existing_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index.get("name") for index in inspector.get_indexes(table_name)}


ADDED_FOREIGN_KEYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("story_commitments", "fk_story_commitments_timeline", "timeline_id", "timelines", "id"),
)


def _foreign_key_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_foreign_keys(table_name):
        if constraint.get("referred_table") and column_name in (constraint.get("constrained_columns") or []):
            return True
    return False


def upgrade() -> None:
    for table_name, column_name, column_type, nullable, server_default in ADDED_COLUMNS:
        if column_name in _existing_columns(table_name):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(
                sa.Column(
                    column_name,
                    column_type,
                    nullable=nullable,
                    server_default=server_default,
                )
            )
    for table_name, name, column_name, referred_table, referred_column in ADDED_FOREIGN_KEYS:
        if _foreign_key_exists(table_name, column_name):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.create_foreign_key(
                name, referred_table, [column_name], [referred_column], ondelete="SET NULL"
            )
    connection = op.get_bind()
    # Rows written before this revision have no scope, so they are labelled with
    # the scope the previous write policy actually gave them.
    connection.execute(
        sa.text(
            "UPDATE memories SET scope = CASE memory_class "
            "WHEN 'permanent' THEN 'world' "
            "WHEN 'archive' THEN 'world' "
            "WHEN 'persistent' THEN 'character' "
            "ELSE 'scene' END "
            "WHERE scope IS NULL"
        )
    )
    connection.execute(sa.text("UPDATE memories SET is_active = 1 WHERE is_active IS NULL"))
    connection.execute(
        sa.text("UPDATE story_commitments SET created_sequence = 0 WHERE created_sequence IS NULL")
    )
    existing = _existing_indexes("memories") | _existing_indexes("story_commitments")
    for index_name, table_name, columns in ADDED_INDEXES:
        if index_name in existing:
            continue
        op.create_index(index_name, table_name, list(columns))


def downgrade() -> None:
    for table_name, name, column_name, _referred_table, _referred_column in reversed(ADDED_FOREIGN_KEYS):
        if not _foreign_key_exists(table_name, column_name):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(name, type_="foreignkey")
    existing = _existing_indexes("memories") | _existing_indexes("story_commitments")
    for index_name, table_name, _columns in reversed(ADDED_INDEXES):
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)
    for table_name, column_name, _type, _nullable, _default in reversed(ADDED_COLUMNS):
        if column_name not in _existing_columns(table_name):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column(column_name)
