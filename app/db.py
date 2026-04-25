from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from .models import Base
from .config import settings


def _create_engine():
    kwargs = {"echo": False, "future": True}
    url = settings.get_database_url()

    if url.startswith("postgresql"):
        kwargs.update({
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        })
    elif url.startswith("sqlite"):
        kwargs.update({
            "connect_args": {"check_same_thread": False, "timeout": 20},
            "pool_timeout": 20,
        })

    return create_engine(url, **kwargs)


engine = _create_engine()
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception:
            pass
