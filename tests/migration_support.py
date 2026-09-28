"""Helpers for exercising the Alembic chain from tests.

`alembic/env.py` reads the database URL from `Settings`, so a migration cannot
be pointed at a throwaway database by passing a URL to the Alembic
configuration alone. The helpers here swap `NARRATIVE_DATABASE_URL` for the
duration of a command and clear the settings cache, which keeps the migration
chain testable without a running application or a `.env` file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.engine import Engine

from alembic import command
from apps.api.app.config import get_settings
from services.core.models import Base

SchemaDiff = tuple[Any, ...]

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"
DATABASE_URL_ENV = "NARRATIVE_DATABASE_URL"
TEST_DATABASE_URL_ENV = "NARRATIVE_TEST_DATABASE_URL"
COMPARISON_OPTIONS = {
    "compare_type": True,
    "compare_server_default": True,
    "compare_unique_constraint": True,
    "compare_indexes": True,
}
IGNORED_TYPE_DIFFS = {
    # The pgvector column has no portable reflection. SQLite reports the
    # unconstrained vector column as NUMERIC and PostgreSQL reports a
    # user-defined type, so the type of `memories.embedding` is asserted
    # explicitly by the PostgreSQL suite instead of through a type diff.
    ("memories", "embedding"),
}


def _is_type_artifact(op: SchemaDiff) -> bool:
    return len(op) > 3 and op[0] == "modify_type" and (op[2], op[3]) in IGNORED_TYPE_DIFFS


@contextmanager
def database_url(url: str) -> Iterator[None]:
    """Run a block with `Settings` resolving to `url`."""
    previous = os.environ.get(DATABASE_URL_ENV)
    os.environ[DATABASE_URL_ENV] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(DATABASE_URL_ENV, None)
        else:
            os.environ[DATABASE_URL_ENV] = previous
        get_settings.cache_clear()


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade(url: str, revision: str = "head") -> None:
    with database_url(url):
        command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    with database_url(url):
        command.downgrade(alembic_config(url), revision)


def metadata_diff(engine: Engine) -> list[SchemaDiff]:
    """Return schema differences between the database and the model metadata.

    Recent Alembic versions group the operations of a table into a single
    sequence, so each group is flattened before it is inspected.
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts=COMPARISON_OPTIONS)
        return [
            op
            for diff in compare_metadata(context, Base.metadata)
            for op in (diff if isinstance(diff, list) else [diff])
            if not _is_type_artifact(op)
        ]


def format_diff(diff: list[SchemaDiff]) -> str:
    return "\n".join(str(item) for item in diff)
