from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.core.models import Base
from services.providers.base import ScriptedProvider


@pytest.fixture
def engine() -> Engine:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(database)
    return database


@pytest.fixture
def session(engine: Engine) -> Session:
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        yield db


@pytest.fixture
def provider_fixture():
    def factory(name: str, *, substitutions=None, error=None) -> ScriptedProvider:
        path = Path(__file__).parent / "fixtures" / "provider" / name
        response = json.loads(path.read_text(encoding="utf-8"))
        return ScriptedProvider([response], substitutions=substitutions, error=error)

    return factory
