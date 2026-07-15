from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.models import Scenario, Turn
from app.orchestrator import InvalidSessionState, Orchestrator, SessionState
from app.schemas import DirectorHint, Dossier, GuestOutput


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"


def guest_output() -> GuestOutput:
    return GuestOutput(
        pressure=1,
        targeted_fact=None,
        action="deflect",
        speech="请把问题说具体些。",
        stage_direction="稍作停顿",
    )


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.dossier = Dossier.model_validate_json(
            SEED_PATH.read_text(encoding="utf-8")
        )
        with Session(self.engine) as db:
            db.add(
                Scenario(
                    id=self.dossier.scenario_id,
                    topic=self.dossier.topic,
                    dossier_json=self.dossier.model_dump(mode="json"),
                )
            )
            db.commit()
        self.created: list[tuple[Orchestrator, str]] = []

    async def asyncTearDown(self) -> None:
        for orchestrator, session_id in self.created:
            if orchestrator.get_snapshot(session_id).state not in {
                SessionState.DONE,
                SessionState.FAILED,
            }:
                try:
                    await orchestrator.end_session(session_id)
                except InvalidSessionState:
                    runtime = orchestrator._sessions[session_id]
                    if runtime.clock_task is not None:
                        runtime.clock_task.cancel()

    async def create_interview(
        self,
        *,
        guest: AsyncMock,
        director: AsyncMock,
        briefing_seconds: float = 0.005,
        duration_seconds: float = 5.0,
        wrapping_seconds: float = 0.1,
        clock_interval: float = 0.002,
    ) -> tuple[Orchestrator, str]:
        orchestrator = Orchestrator(
            self.engine,
            guest=SimpleNamespace(respond=guest),
            director=SimpleNamespace(observe=director),
            briefing_seconds=briefing_seconds,
            duration_seconds=duration_seconds,
            wrapping_seconds=wrapping_seconds,
            clock_interval=clock_interval,
        )
        snapshot = await orchestrator.create_session(
            self.dossier.scenario_id,
            self.dossier.persona.id,
        )
        self.created.append((orchestrator, snapshot.id))
        return orchestrator, snapshot.id

    async def wait_for_state(
        self,
        orchestrator: Orchestrator,
        session_id: str,
        state: SessionState,
        *,
        timeout: float = 1.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        while orchestrator.get_snapshot(session_id).state != state:
            if time.monotonic() >= deadline:
                self.fail(f"session did not reach {state.value}")
            await asyncio.sleep(0.001)

    async def test_guest_and_director_are_called_concurrently(self) -> None:
        started: dict[str, float] = {}
        finished: dict[str, float] = {}

        async def guest_call(**_: object) -> GuestOutput:
            started["guest"] = time.monotonic()
            await asyncio.sleep(0.08)
            finished["guest"] = time.monotonic()
            return guest_output()

        async def director_call(**_: object) -> None:
            started["director"] = time.monotonic()
            await asyncio.sleep(0.08)
            finished["director"] = time.monotonic()
            return None

        guest = AsyncMock(side_effect=guest_call)
        director = AsyncMock(side_effect=director_call)
        orchestrator, session_id = await self.create_interview(
            guest=guest,
            director=director,
        )
        await self.wait_for_state(orchestrator, session_id, SessionState.LIVE)

        await orchestrator.submit_turn(session_id, "请说明招标时间线。")

        agent_span = max(finished.values()) - min(started.values())
        self.assertLess(agent_span, 0.13, "two 80ms calls ran sequentially")
        self.assertLess(abs(started["guest"] - started["director"]), 0.03)
        guest.assert_awaited_once()
        director.assert_awaited_once()

    async def test_state_machine_follows_the_declared_order(self) -> None:
        guest = AsyncMock(return_value=guest_output())
        director = AsyncMock(return_value=None)
        orchestrator, session_id = await self.create_interview(
            guest=guest,
            director=director,
            briefing_seconds=0.015,
            duration_seconds=0.06,
            wrapping_seconds=0.025,
            clock_interval=0.002,
        )

        await self.wait_for_state(orchestrator, session_id, SessionState.LIVE)
        await orchestrator.submit_turn(session_id, "请说明招标时间线。")
        await self.wait_for_state(orchestrator, session_id, SessionState.DONE)

        self.assertEqual(
            orchestrator.get_state_history(session_id),
            [
                SessionState.IDLE,
                SessionState.BRIEFING,
                SessionState.LIVE,
                SessionState.WRAPPING,
                SessionState.REVIEW,
                SessionState.DONE,
            ],
        )

    async def test_turn_indexes_are_contiguous_and_unique(self) -> None:
        guest = AsyncMock(return_value=guest_output())
        director = AsyncMock(return_value="他在时间线上有所回避")
        orchestrator, session_id = await self.create_interview(
            guest=guest,
            director=director,
        )
        await self.wait_for_state(orchestrator, session_id, SessionState.LIVE)

        await asyncio.gather(
            *[
                orchestrator.submit_turn(session_id, f"问题 {index}")
                for index in range(6)
            ]
        )
        await orchestrator.end_session(session_id)

        with Session(self.engine) as db:
            turns = list(
                db.exec(
                    select(Turn).where(Turn.session_id == session_id).order_by(Turn.idx)
                )
            )
        indexes = [turn.idx for turn in turns]
        self.assertEqual(indexes, list(range(len(indexes))))
        self.assertEqual(len(indexes), len(set(indexes)))

    async def test_empty_interview_cannot_enter_review(self) -> None:
        orchestrator, session_id = await self.create_interview(
            guest=AsyncMock(return_value=guest_output()),
            director=AsyncMock(return_value=None),
            duration_seconds=5,
        )
        with self.assertRaises(InvalidSessionState):
            await orchestrator.end_session(session_id)
        self.assertNotIn(
            orchestrator.get_snapshot(session_id).state,
            {SessionState.REVIEW, SessionState.DONE},
        )

    async def test_runtime_is_recovered_from_database(self) -> None:
        guest = AsyncMock(return_value=guest_output())
        original, session_id = await self.create_interview(
            guest=guest,
            director=AsyncMock(return_value=None),
            duration_seconds=5,
        )
        await self.wait_for_state(original, session_id, SessionState.LIVE)
        await original.submit_turn(session_id, "请说明招标时间线。")
        runtime = original._sessions[session_id]
        if runtime.clock_task is not None:
            runtime.clock_task.cancel()

        recovered = Orchestrator(self.engine)
        snapshot = recovered.get_snapshot(session_id)
        self.created.append((recovered, session_id))
        self.assertEqual(snapshot.state, SessionState.LIVE)
        self.assertEqual(snapshot.turn_count, 1)
        completed = await recovered.end_session(session_id)
        self.assertEqual(completed.state, SessionState.DONE)
        self.assertIsNotNone(completed.report_id)
        self.created.remove((original, session_id))

    async def test_review_maps_hint_to_the_next_host_question(self) -> None:
        output = guest_output().model_copy(update={"targeted_fact": "F1"})
        director = AsyncMock(
            side_effect=[
                DirectorHint(
                    should_speak=True,
                    urgency=2,
                    type="追问",
                    hint="继续钉住时间线",
                ),
                None,
            ]
        )
        orchestrator, session_id = await self.create_interview(
            guest=AsyncMock(return_value=output),
            director=director,
            duration_seconds=5,
        )
        await self.wait_for_state(orchestrator, session_id, SessionState.LIVE)
        await orchestrator.submit_turn(session_id, "公告是什么时候发布的？")
        await orchestrator.submit_turn(session_id, "测量具体是哪一天？")
        completed = await orchestrator.end_session(session_id)
        review = orchestrator.get_review(completed.report_id or "")
        self.assertEqual(review["rounds"][0]["studentAction"], "测量具体是哪一天？")
        self.assertTrue(review["rounds"][0]["followed"])
