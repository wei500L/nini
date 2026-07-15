from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents.director import DirectorHint, generate_director_hint
from app.schemas import Dossier, FactState, GuestOutput


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"


def initial_states(dossier: Dossier) -> list[FactState]:
    return [
        FactState(
            fact_id=fact.id,
            guard_current=fact.guard,
            consecutive_probes=0,
            revealed="hidden",
        )
        for fact in dossier.facts
    ]


def guest_output(*, action: str = "deflect") -> GuestOutput:
    return GuestOutput(
        pressure=1,
        targeted_fact=None,
        action=action,
        speech="请把问题说具体些。",
        stage_direction="稍作停顿",
    )


class DirectorAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dossier = Dossier.model_validate_json(
            SEED_PATH.read_text(encoding="utf-8")
        )
        self.states = initial_states(self.dossier)

    async def test_leak_guard_discards_fact_like_hint(self) -> None:
        self.dossier.facts[0].content = "中标前已经进校测量"
        leaked = DirectorHint(
            should_speak=True,
            urgency=2,
            type="追问",
            hint="中标前已经进校测量",
        )

        with patch("app.agents.director.chat", new=AsyncMock(return_value=leaked)):
            result = await generate_director_hint(
                self.dossier,
                self.states,
                [],
                "请继续。",
                guest_output(),
                trace_id="director-leak",
            )

        self.assertFalse(result.should_speak)
        self.assertEqual(result.hint, "")

    async def test_throttle_suppresses_nonurgent_hint(self) -> None:
        history = [
            {"role": "director", "content": "换个角度"},
            {"role": "host", "content": "请继续。"},
            {"role": "guest", "content": "继续。"},
        ]
        suggestion = DirectorHint(
            should_speak=True,
            urgency=2,
            type="换角度",
            hint="换个角度",
        )

        with patch(
            "app.agents.director.chat",
            new=AsyncMock(return_value=suggestion),
        ):
            result = await generate_director_hint(
                self.dossier,
                self.states,
                history,
                "请继续。",
                guest_output(),
                trace_id="director-throttle",
            )

        self.assertFalse(result.should_speak)

    async def test_tell_action_must_speak(self) -> None:
        reluctant_model = DirectorHint(
            should_speak=False,
            urgency=1,
            type="别问了",
            hint="",
        )
        mocked_chat = AsyncMock(return_value=reluctant_model)

        with patch("app.agents.director.chat", new=mocked_chat):
            result = await generate_director_hint(
                self.dossier,
                self.states,
                [],
                "请继续。",
                guest_output(action="tell"),
                trace_id="director-tell",
            )

        self.assertTrue(result.should_speak)
        self.assertEqual(result.urgency, 3)
        self.assertLessEqual(len(result.hint), 15)
        self.assertEqual(mocked_chat.await_args.kwargs["model_tier"], "fast")


if __name__ == "__main__":
    unittest.main()
