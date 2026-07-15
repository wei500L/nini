from __future__ import annotations

import logging
from datetime import datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlmodel import Session as DatabaseSession
from sqlmodel import select

from app.agents.writer import generate_dossier
from app.llm.exceptions import SchemaViolation
from app.models import Scenario
from app.schemas import Dossier


logger = logging.getLogger(__name__)


class GenerateScenarioRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)


class ScenarioPreview(BaseModel):
    scenario_id: str
    topic: str
    surface_bio: str
    persona_id: str
    persona_name: str
    facts_total: int
    created_at: datetime | None = None


def _preview(dossier: Dossier, *, created_at: datetime | None = None) -> ScenarioPreview:
    return ScenarioPreview(
        scenario_id=dossier.scenario_id,
        topic=dossier.topic,
        surface_bio=dossier.surface_bio,
        persona_id=dossier.persona.id,
        persona_name=dossier.persona.name,
        facts_total=len(dossier.facts),
        created_at=created_at,
    )


def build_scenario_router(engine: Engine) -> APIRouter:
    router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

    @router.get("", response_model=list[ScenarioPreview])
    async def list_scenarios() -> list[ScenarioPreview]:
        with DatabaseSession(engine) as db:
            rows = list(db.exec(select(Scenario).order_by(Scenario.created_at.desc())))
        return [
            _preview(
                Dossier.model_validate(row.dossier_json),
                created_at=row.created_at,
            )
            for row in rows
        ]

    @router.post(
        "/generate",
        response_model=ScenarioPreview,
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_scenario(payload: GenerateScenarioRequest) -> ScenarioPreview:
        trace_id = f"scenario-{uuid4().hex}"
        try:
            with DatabaseSession(engine) as db:
                dossier = await generate_dossier(
                    payload.topic,
                    db=db,
                    trace_id=trace_id,
                )
                row = db.get(Scenario, dossier.scenario_id)
                created_at = row.created_at if row is not None else None
        except (httpx.HTTPError, SchemaViolation, ValueError) as error:
            logger.exception("Scenario generation failed trace_id=%s", trace_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"场景生成失败，trace_id={trace_id}",
            ) from error
        return _preview(dossier, created_at=created_at)

    return router


__all__ = [
    "GenerateScenarioRequest",
    "ScenarioPreview",
    "build_scenario_router",
]
