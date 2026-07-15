from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.models import Scenario
from app.scenarios import build_scenario_router
from app.schemas import Dossier


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"


class ScenarioApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_and_list_return_only_public_preview(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))

        async def fake_generate(topic: str, *, db: Session, trace_id: str) -> Dossier:
            self.assertEqual(topic, "高校食堂招标争议")
            self.assertTrue(trace_id.startswith("scenario-"))
            db.add(
                Scenario(
                    id=dossier.scenario_id,
                    topic=dossier.topic,
                    dossier_json=dossier.model_dump(mode="json"),
                )
            )
            db.commit()
            return dossier

        app = FastAPI()
        app.include_router(build_scenario_router(engine))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            with patch("app.scenarios.generate_dossier", side_effect=fake_generate):
                generated = await client.post(
                    "/api/scenarios/generate",
                    json={"topic": "高校食堂招标争议"},
                )
            listed = await client.get("/api/scenarios")

        self.assertEqual(generated.status_code, 201)
        self.assertEqual(generated.json()["facts_total"], len(dossier.facts))
        self.assertNotIn("facts", generated.json())
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["persona_id"], dossier.persona.id)


if __name__ == "__main__":
    unittest.main()
