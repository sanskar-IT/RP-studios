from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from alembic.script import ScriptDirectory
from migration_support import (
    alembic_config,
    downgrade,
    format_diff,
    metadata_diff,
    upgrade,
)
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from apps.api.app import repository
from services.core.enums import MemoryClass
from services.core.events import CHARACTER_MOVED
from services.core.models import Base

BASELINE_TABLES = {table.name for table in Base.metadata.sorted_tables}
# The baseline is frozen at the 22 tables that existed before the vertical-slice
# fields. Later revisions add columns, constraints, indexes, and — from
# 0005_narrative_director — the director_plans artifact table.
BASELINE_TABLE_COUNT = 22
POST_BASELINE_TABLES = {"director_plans"}
BASELINE_ONLY_TABLES = BASELINE_TABLES - POST_BASELINE_TABLES
VERTICAL_SLICE_COLUMNS = {
    "scenes": {"staging_revision", "approved_staging_revision"},
    "generations": {"checkpoint_node_id"},
}
DIRECTOR_COLUMNS = {
    "projects": {"authority_mode"},
    "scenes": {"authority_mode"},
}


def sqlite_url(tmp_path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


@pytest.fixture
def foreign_keys_enforced() -> Iterator[None]:
    """Enable `PRAGMA foreign_keys` on every SQLite connection in the process."""
    def enable(dbapi_connection, _connection_record) -> None:
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    event.listen(Engine, "connect", enable)
    try:
        yield
    finally:
        event.remove(Engine, "connect", enable)


@pytest.fixture
def migrated_url(tmp_path) -> Iterator[str]:
    url = sqlite_url(tmp_path, "migrated.db")
    upgrade(url)
    yield url


@pytest.fixture
def migrated_engine(migrated_url) -> Iterator[Engine]:
    engine = create_engine(migrated_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_chain_is_linear_and_has_no_extra_heads():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert script.get_heads() == ["0005_narrative_director"]
    assert script.get_base() == "0001_initial"
    assert [revision.revision for revision in script.walk_revisions()] == [
        "0005_narrative_director",
        "0004_context_reliability",
        "0003_production_constraints",
        "0002_vertical_slice",
        "0001_initial",
    ]


def test_baseline_creates_the_pre_vertical_slice_schema(tmp_path):
    url = sqlite_url(tmp_path, "baseline.db")
    upgrade(url, "0001_initial")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - {"alembic_version"} == BASELINE_ONLY_TABLES
        assert len(BASELINE_ONLY_TABLES) == BASELINE_TABLE_COUNT
        for table_name, column_names in VERTICAL_SLICE_COLUMNS.items():
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert columns.isdisjoint(column_names)
        assert "ix_generations_checkpoint_node_id" not in {
            index["name"] for index in inspector.get_indexes("generations")
        }
        assert "uq_scenes_current_timeline" not in {
            index["name"] for index in inspector.get_indexes("scenes")
        }
    finally:
        engine.dispose()


def test_upgrade_head_matches_the_model_metadata(migrated_engine: Engine):
    diff = metadata_diff(migrated_engine)
    assert diff == [], f"migration chain and models differ:\n{format_diff(diff)}"


def test_head_adds_vertical_slice_columns_and_production_constraints(migrated_engine: Engine):
    inspector = inspect(migrated_engine)
    for table_name, column_names in VERTICAL_SLICE_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert column_names <= columns
    assert "ix_generations_checkpoint_node_id" in {
        index["name"] for index in inspector.get_indexes("generations")
    }
    assert "uq_scenes_current_timeline" in {
        index["name"] for index in inspector.get_indexes("scenes")
    }
    for table_name in ("timeline_nodes", "events"):
        constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert ("timeline_id", "sequence") in constraints


def test_upgrade_head_is_idempotent(tmp_path):
    url = sqlite_url(tmp_path, "idempotent.db")
    upgrade(url)
    upgrade(url)
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == BASELINE_TABLES
        assert metadata_diff(engine) == []
    finally:
        engine.dispose()


def test_downgrade_base_drops_every_object_and_can_be_reapplied(tmp_path):
    url = sqlite_url(tmp_path, "downgrade.db")
    upgrade(url)
    downgrade(url, "base")
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == BASELINE_TABLES
        assert metadata_diff(engine) == []
    finally:
        engine.dispose()


def test_vertical_slice_downgrade_removes_its_own_columns(tmp_path):
    url = sqlite_url(tmp_path, "vertical_downgrade.db")
    upgrade(url)
    downgrade(url, "0001_initial")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        for table_name, column_names in VERTICAL_SLICE_COLUMNS.items():
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert columns.isdisjoint(column_names)
        assert "uq_scenes_current_timeline" not in {
            index["name"] for index in inspector.get_indexes("scenes")
        }
    finally:
        engine.dispose()


def test_downgrade_base_drops_a_populated_database_in_dependency_order(
    tmp_path, foreign_keys_enforced: None
):
    url = sqlite_url(tmp_path, "populated.db")
    upgrade(url)
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            project = repository.create_project(session, "Populated")
            character = repository.add_character(session, project_id=project.id, name="Detective")
            scene = repository.create_scene(
                session,
                project_id=project.id,
                timeline_id=project.active_timeline_id,
                title="The library",
                participant_ids=[character.id],
            )
            repository.append_event(
                session,
                project_id=project.id,
                timeline_id=project.active_timeline_id,
                event_type=CHARACTER_MOVED,
                payload={"character_id": character.id, "location_id": "library"},
            )
            repository.add_generation(
                session,
                project_id=project.id,
                scene_id=scene.id,
                timeline_id=project.active_timeline_id,
                status="completed",
            )
            repository.add_memory(
                session,
                project_id=project.id,
                timeline_id=project.active_timeline_id,
                content="The eastern door is locked",
                memory_class=MemoryClass.PERMANENT,
                character_id=character.id,
            )
            session.commit()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar() == 1
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM scene_participants").scalar() == 1
    finally:
        engine.dispose()

    downgrade(url, "base")

    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
