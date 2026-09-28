"""Narrative Director schema: plans, authority modes, and recorded intent.

Adds the ``director_plans`` artifact table, the project default and scene
override for authority mode, and the columns that record an interpreted intent
alongside the plan built from it. Every column is added only when absent, and
the downgrade removes only what this revision created.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_narrative_director"
down_revision = "0004_context_reliability"
branch_labels = None
depends_on = None

AUTHORITY_DEFAULT = "director_assisted"

# Columns added to tables that already exist.
ADDED_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine, bool, object | None], ...] = (
    ("projects", "authority_mode", sa.String(length=30), False, AUTHORITY_DEFAULT),
    ("scenes", "authority_mode", sa.String(length=30), True, None),
    ("director_intents", "lane", sa.String(length=30), True, None),
    ("director_intents", "specification", sa.String(length=30), True, None),
    ("director_intents", "consistency", sa.String(length=30), True, None),
    ("director_intents", "confidence", sa.Float(), True, None),
    ("director_intents", "objective", sa.Text(), False, sa.text("''")),
    ("director_intents", "desired_outcome", sa.Text(), False, sa.text("''")),
    ("director_intents", "constraints_json", sa.JSON(), True, None),
    ("director_intents", "exclusions_json", sa.JSON(), True, None),
    ("director_intents", "tone", sa.String(length=50), False, sa.text("''")),
    ("director_intents", "urgency", sa.String(length=50), False, sa.text("''")),
    ("director_intents", "canon_preference", sa.String(length=50), False, sa.text("''")),
    ("director_intents", "user_control_level", sa.String(length=50), False, sa.text("''")),
    ("director_intents", "interpretation_json", sa.JSON(), True, None),
    ("director_intents", "interpretation_source", sa.String(length=30), False, sa.text("''")),
)

# The new artifact table, created whole.
PLAN_INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ix_director_plans_scene_status", ("scene_id", "status"), False),
    ("ix_director_plans_project_id", ("project_id",), False),
    ("ix_director_plans_timeline_id", ("timeline_id",), False),
    ("ix_director_plans_generation_id", ("generation_id",), False),
    ("ix_director_plans_status", ("status",), False),
)

PLAN_FOREIGN_KEYS: tuple[tuple[str, str, str, str, str], ...] = (
    ("fk_director_plans_project", "project_id", "projects", "id", "CASCADE"),
    ("fk_director_plans_timeline", "timeline_id", "timelines", "id", "SET NULL"),
    ("fk_director_plans_scene", "scene_id", "scenes", "id", "SET NULL"),
    ("fk_director_plans_intent", "intent_id", "director_intents", "id", "SET NULL"),
)


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index.get("name") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _foreign_keys(table_name: str) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        columns = tuple(constraint.get("constrained_columns") or [])
        referred = constraint.get("referred_table")
        if columns and referred:
            keys.add((*columns, referred))
    return keys


def upgrade() -> None:
    tables = _tables()
    for table_name, column_name, column_type, nullable, server_default in ADDED_COLUMNS:
        if table_name not in tables:
            continue
        if column_name in _columns(table_name):
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
    if "director_plans" in tables:
        return
    op.create_table(
        "director_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("timeline_id", sa.String(length=36), nullable=True),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("intent_id", sa.String(length=36), nullable=True),
        sa.Column("generation_id", sa.String(length=36), nullable=True),
        sa.Column("checkpoint_node_id", sa.String(length=36), nullable=True),
        sa.Column("superseded_by_plan_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="proposed"),
        sa.Column("lane", sa.String(length=30), nullable=False, server_default="direction"),
        sa.Column("horizon", sa.String(length=30), nullable=False, server_default="near_term"),
        sa.Column(
            "authority_mode",
            sa.String(length=30),
            nullable=False,
            server_default=AUTHORITY_DEFAULT,
        ),
        sa.Column("objective", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("required_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_beat_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("intent_json", sa.JSON(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("canon_conflict_json", sa.JSON(), nullable=True),
        sa.Column("decision_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    existing = _indexes("director_plans")
    for index_name, columns, unique in PLAN_INDEXES:
        if index_name in existing:
            continue
        op.create_index(index_name, "director_plans", list(columns), unique=unique)
    # Foreign keys are added after the table exists so the named constraints
    # match the model metadata exactly.
    for name, column_name, referred_table, referred_column, ondelete in PLAN_FOREIGN_KEYS:
        if (column_name, referred_table) in _foreign_keys("director_plans"):
            continue
        with op.batch_alter_table("director_plans") as batch:
            batch.create_foreign_key(
                name,
                referred_table,
                [column_name],
                [referred_column],
                ondelete=ondelete,
            )


def downgrade() -> None:
    tables = _tables()
    if "director_plans" in tables:
        for index_name, _index_columns, _unique in reversed(PLAN_INDEXES):
            if index_name in _indexes("director_plans"):
                op.drop_index(index_name, table_name="director_plans")
        op.drop_table("director_plans")
    for table_name, column_name, _type, _nullable, _default in reversed(ADDED_COLUMNS):
        if table_name not in tables or column_name not in _columns(table_name):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column(column_name)
