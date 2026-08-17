"""
Database engine and session factory.

The engine is created lazily (SQLAlchemy default) — no connection
is made at import time. Startup errors (e.g. Postgres not reachable)
are caught in the FastAPI lifespan handler.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    # Send a lightweight SELECT 1 before reusing idle connections.
    pool_pre_ping=True,
    # Keep a modest pool — adjust for production load.
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=True,
)


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context-manager that provides a transactional DB session.

    Commits on success, rolls back on any exception, always closes.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
