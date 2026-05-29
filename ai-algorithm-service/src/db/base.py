"""SQLAlchemy engine / session / Base.

Default la SQLite file local (plan muc 4: local DB cho AI service).
Production co the doi sang MySQL/Postgres qua bien `DATABASE_URL`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import get_settings
from src.core.logger import logger


class Base(DeclarativeBase):
    pass


_settings = get_settings()

_connect_args: dict = {}
if _settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    """Tao bang theo metadata (first-run) + ad-hoc migrations cho MVP."""
    # Import models de dam bao registry duoc load truoc khi create_all.
    from src.db import models  # noqa: F401
    from src.db.migrations import apply_simple_migrations, backfill_area_network_ids

    Base.metadata.create_all(bind=engine)
    apply_simple_migrations(engine)
    backfill_area_network_ids(engine)
    logger.info(f"Local DB initialized at {_settings.database_url}")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context-manager session co auto commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
