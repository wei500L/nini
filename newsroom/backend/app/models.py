from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class TurnRole(str, Enum):
    host = "host"
    guest = "guest"
    director = "director"


class RevealedState(str, Enum):
    hidden = "hidden"
    partial = "partial"
    full = "full"


class Scenario(SQLModel, table=True):
    __tablename__ = "scenario"

    id: str = Field(primary_key=True)
    topic: str
    dossier_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now)


class Profile(SQLModel, table=True):
    __tablename__ = "profile"

    id: str = Field(primary_key=True)
    weaknesses_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    updated_at: datetime = Field(default_factory=utc_now)


class Session(SQLModel, table=True):
    __tablename__ = "session"

    id: str = Field(primary_key=True)
    scenario_id: str = Field(foreign_key="scenario.id", index=True)
    profile_id: str | None = Field(default=None, foreign_key="profile.id", index=True)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None


class Turn(SQLModel, table=True):
    __tablename__ = "turn"

    session_id: str = Field(foreign_key="session.id", primary_key=True)
    idx: int = Field(primary_key=True, ge=0)
    role: TurnRole
    content: str
    meta_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    ts: datetime = Field(default_factory=utc_now)


class FactState(SQLModel, table=True):
    __tablename__ = "fact_state"

    session_id: str = Field(foreign_key="session.id", primary_key=True)
    fact_id: str = Field(primary_key=True)
    guard_current: int = Field(ge=0, le=5)
    consecutive_probes: int = Field(default=0, ge=0)
    revealed: RevealedState = Field(default=RevealedState.hidden)


class Report(SQLModel, table=True):
    __tablename__ = "report"

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now)
