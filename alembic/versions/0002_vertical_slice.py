"""Vertical-slice fields: staging revision bookkeeping and checkpoint linkage.

``0001_initial`` is a static baseline, so these columns do not exist when this
revision runs. The guard is still required: databases created before the static
baseline was introduced already contain these columns through
``Base.metadata.create_all``. Every statement is therefore applied only when
the target object is absent, and the downgrade removes only what is present.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_vertical_slice"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

STAGING_REVISION_COLUMN = "staging_revision"
APPROVED_STAGING_REVISION_COLUMN = "approved_staging_revision"
CHECKPOINT_NODE_ID_COLUMN = "checkpoint_node_id"
CHECKPOINT_NODE_ID_INDEX = "ix_generations_checkpoint_node_id"


def _scene_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("scenes")}


def _generation_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("generations")}


def _generation_indexes() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("generations")}


def upgrade() -> None:
    scene_columns = _scene_columns()
    if STAGING_REVISION_COLUMN not in scene_columns:
        op.add_column(
            "scenes",
            sa.Column(STAGING_REVISION_COLUMN, sa.Integer(), nullable=False, server_default="0"),
        )
        with op.batch_alter_table("scenes") as batch:
            batch.alter_column(
                STAGING_REVISION_COLUMN,
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    if APPROVED_STAGING_REVISION_COLUMN not in scene_columns:
        op.add_column(
            "scenes",
            sa.Column(APPROVED_STAGING_REVISION_COLUMN, sa.Integer(), nullable=True),
        )
    generation_columns = _generation_columns()
    if CHECKPOINT_NODE_ID_COLUMN not in generation_columns:
        op.add_column(
            "generations",
            sa.Column(CHECKPOINT_NODE_ID_COLUMN, sa.String(length=36), nullable=True),
        )
    if CHECKPOINT_NODE_ID_INDEX not in _generation_indexes():
        op.create_index(CHECKPOINT_NODE_ID_INDEX, "generations", [CHECKPOINT_NODE_ID_COLUMN])


def downgrade() -> None:
    if CHECKPOINT_NODE_ID_INDEX in _generation_indexes():
        op.drop_index(CHECKPOINT_NODE_ID_INDEX, table_name="generations")
    if CHECKPOINT_NODE_ID_COLUMN in _generation_columns():
        op.drop_column("generations", CHECKPOINT_NODE_ID_COLUMN)
    scene_columns = _scene_columns()
    if APPROVED_STAGING_REVISION_COLUMN in scene_columns:
        op.drop_column("scenes", APPROVED_STAGING_REVISION_COLUMN)
    if STAGING_REVISION_COLUMN in scene_columns:
        op.drop_column("scenes", STAGING_REVISION_COLUMN)
