from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, Session, create_engine

from app.agents.writer import generate_dossier
from app.models import Scenario
from app.schemas import Dossier, WriterCritique


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"


class WriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_fixture_generates_playable_dossier(self) -> None:
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
        critique = WriterCritique(
            approved=True,
            guard_gradient_ok=True,
            unlock_hints_actionable=True,
            surface_bio_consistent=True,
            issues=[],
        )
        llm = AsyncMock(side_effect=[dossier, critique])

        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with (
            patch.dict(
                os.environ,
                {"TAVILY_MODE": "fixture", "TAVILY_API_KEY": ""},
            ),
            patch("app.agents.writer.chat", llm),
            Session(engine) as db,
        ):
            result = await generate_dossier(
                "某高校食堂承包商更换风波",
                db=db,
                trace_id="writer-offline",
            )
            stored = db.get(Scenario, result.scenario_id)

        self.assertGreaterEqual(len(result.facts), 4)
        self.assertGreater(len({fact.guard for fact in result.facts}), 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.dossier_json["scenario_id"], result.scenario_id)
        self.assertEqual(llm.await_count, 2)
