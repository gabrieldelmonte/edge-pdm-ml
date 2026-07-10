"""Database connection, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings


# pool_pre_ping keeps stale DB connections from breaking long-running containers.
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and always close it on teardown.

    Yields:
        An open SQLAlchemy Session bound to the application database.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
