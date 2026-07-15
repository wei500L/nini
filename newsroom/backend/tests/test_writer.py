from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, Session, create_engine, select

from app.agents.writer import _grounded_excerpt, generate_dossier
from app.models import Scenario
from app.schemas import Dossier, WriterCritique


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"


class WriterTests(unittest.IsolatedAsyncioTestCase):
    def test_grounded_excerpt_removes_irrelevant_page_prefix(self) -> None:
        summary = (
            "注册 登录 购物车。"
            "据公开公告，Apple智能大模型已完成备案，适用于国行手机。"
            "合作方随后公布了上线时间表。"
            "举报邮箱和网站版权信息。"
        ) * 8

        excerpt = _grounded_excerpt(
            summary,
            query="Apple智能 大模型 备案",
            max_chars=120,
        )

        self.assertIn(excerpt, summary)
        self.assertIn("完成备案", excerpt)
        self.assertNotIn("注册 登录", excerpt)
        self.assertLessEqual(len(excerpt), 120)

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

    async def test_quote_drift_is_canonicalized_without_regeneration(self) -> None:
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
        dossier.facts[0].evidence[0].quote = "来源中不存在的句子"
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
            patch.dict(os.environ, {"TAVILY_MODE": "fixture", "TAVILY_API_KEY": ""}),
            patch("app.agents.writer.chat", llm),
            Session(engine) as db,
        ):
            result = await generate_dossier(
                "某高校食堂承包商更换风波",
                db=db,
                trace_id="writer-quote-drift",
            )
            stored = db.get(Scenario, result.scenario_id)

        source_by_url = {source.url: source for source in result.sources}
        repaired_evidence = result.facts[0].evidence[0]
        self.assertEqual(
            repaired_evidence.quote,
            source_by_url[repaired_evidence.source_url].summary,
        )
        self.assertIsNotNone(stored)
        self.assertEqual(llm.await_count, 2)

    async def test_unsupported_dossier_is_never_persisted(self) -> None:
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
        dossier.facts[0].content = "检索结果完全没有提及的虚构内幕"
        dossier.facts[0].evidence[0].source_url = "https://invalid.example/fabricated"
        dossier.facts[0].evidence[0].quote = dossier.facts[0].content
        llm = AsyncMock(side_effect=[dossier, dossier, dossier])
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with (
            patch.dict(os.environ, {"TAVILY_MODE": "fixture", "TAVILY_API_KEY": ""}),
            patch("app.agents.writer.chat", llm),
            Session(engine) as db,
        ):
            with self.assertRaises(ValueError):
                await generate_dossier(
                    "某高校食堂承包商更换风波",
                    db=db,
                    trace_id="writer-unsupported",
                )
            stored = list(db.exec(select(Scenario)))
        self.assertEqual(stored, [])
        self.assertEqual(llm.await_count, 3)
