"""SQLAlchemy 2.0 核心：Base / engine / SessionLocal / get_db（规则 A-06 / D-07）。

- 本模块不依赖应用其他模块（避免循环导入）；
- 请求级 Session 通过 FastAPI 依赖注入管理，请求结束自动关闭；
- SQLite（开发/测试）与 PostgreSQL（生产/Compose）双语支持。
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """全局 ORM 声明基类。"""


def _sqlite_path(url: str) -> str | None:
    # sqlite:///./var/app.db -> ./var/app.db
    if not url.startswith("sqlite"):
        return None
    path = url.split("sqlite:", 1)[1].lstrip("/")
    return path or None


def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        if ":memory:" in database_url or database_url.endswith("sqlite://"):
            from sqlalchemy.pool import StaticPool

            return create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        path = _sqlite_path(database_url)
        if path and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True,
    )


_settings = get_settings()
engine: Engine = build_engine(_settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """请求级 Session：每个请求独立创建，请求结束自动关闭（规则 D-07）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
