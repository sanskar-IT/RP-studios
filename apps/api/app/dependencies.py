from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from apps.api.app.db import get_db


def db_session() -> Generator[Session, None, None]:
    yield from get_db()


DbSession = Depends(db_session)
