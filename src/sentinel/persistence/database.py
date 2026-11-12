"""Database engine and session construction."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config import database_url


def create_database_engine(*, testing: bool = False, url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine using explicit or environment configuration."""
    return create_engine(url or database_url(testing=testing), pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions with explicit transaction boundaries."""
    return sessionmaker(engine, expire_on_commit=False)
