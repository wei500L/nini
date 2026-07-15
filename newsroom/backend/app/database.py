from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def database_url() -> str:
    default_path = (BACKEND_ROOT / "newsroom.db").resolve().as_posix()
    return os.getenv("NEWSROOM_DATABASE_URL", f"sqlite:///{default_path}")


def build_engine(url: str | None = None) -> Engine:
    resolved_url = url or database_url()
    connect_args = (
        {"check_same_thread": False, "timeout": 30}
        if resolved_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(resolved_url, connect_args=connect_args)
    if resolved_url.startswith("sqlite"):
        _configure_sqlite(engine)
    SQLModel.metadata.create_all(engine)
    _migrate_session_table(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _migrate_session_table(engine: Engine) -> None:
    """Small local-only additive migration for databases created before v0.2."""

    if engine.dialect.name != "sqlite" or "session" not in inspect(engine).get_table_names():
        return
    existing = {column["name"] for column in inspect(engine).get_columns("session")}
    additions = {
        "persona_id": "VARCHAR",
        "state": "VARCHAR NOT NULL DEFAULT 'BRIEFING'",
        "briefing_deadline": "DATETIME",
        "live_deadline": "DATETIME",
        "duration_seconds": "FLOAT NOT NULL DEFAULT 480.0",
        "briefing_seconds": "FLOAT NOT NULL DEFAULT 60.0",
        "wrapping_seconds": "FLOAT NOT NULL DEFAULT 60.0",
        "report_id": "VARCHAR",
        "error_message": "VARCHAR",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    text(f'ALTER TABLE "session" ADD COLUMN "{name}" {definition}')
                )


__all__ = ["BACKEND_ROOT", "build_engine", "database_url"]
