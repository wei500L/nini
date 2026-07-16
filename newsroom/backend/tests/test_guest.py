from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents.guest import GuestAssessment, generate_guest_response
from app.agents.guest_state import update_guard_state
from app.schemas import Dossier, FactState, GuestOutput, Persona


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


def state_for(states: list[FactState], fact_id: str) -> FactState:
    return next(state for state in states if state.fact_id == fact_id)


class GuestStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
        self.states = initial_states(self.dossier)
        self.guards = {fact.id: fact.guard for fact in self.dossier.facts}

    def test_same_fact_probe_decrements_guard_and_increments_streak(self) -> None:
        state = update_guard_state(
            self.states,
            targeted_fact="F5",
            previous_targeted_fact="F5",
            pressure=3,
            initial_guards=self.guards,
            pressure_response="standard",
        )

        self.assertIsNotNone(state)
        self.assertEqual(state.guard_current, 4)
        self.assertEqual(state.consecutive_probes, 1)

    def test_switched_fact_resets_streak_and_rebounds_to_initial_cap(self) -> None:
        state = state_for(self.states, "F5")
        state.guard_current = 4
        state.consecutive_probes = 2

        update_guard_state(
            self.states,
            targeted_fact="F5",
            previous_targeted_fact="F4",
            pressure=3,
            initial_guards=self.guards,
            pressure_response="standard",
        )
        self.assertEqual(state.guard_current, 5)
        self.assertEqual(state.consecutive_probes, 0)

        update_guard_state(
            self.states,
            targeted_fact="F5",
            previous_targeted_fact="F4",
            pressure=3,
            initial_guards=self.guards,
            pressure_response="standard",
        )
        self.assertEqual(state.guard_current, 5)

    def test_inverse_high_pressure_raises_guard(self) -> None:
        state = update_guard_state(
            self.states,
            targeted_fact="F4",
            previous_targeted_fact="F3",
            pressure=5,
            initial_guards=self.guards,
            pressure_response="inverse",
        )

        self.assertIsNotNone(state)
        self.assertEqual(state.guard_current, 5)
        self.assertEqual(state.consecutive_probes, 0)


class GuestAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_answer_emits_provider_chunks_without_hidden_dossier(
        self,
    ) -> None:
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))

        async def token_stream():
            yield "这是"
            yield "公开回答"

        llm = AsyncMock(
            side_effect=[
                GuestAssessment(pressure=1, targeted_fact=None),
                token_stream(),
            ]
        )
        deltas: list[str] = []

        async def collect(delta: str) -> None:
            deltas.append(delta)

        with patch("app.agents.guest.chat", llm):
            result = await generate_guest_response(
                dossier,
                initial_states(dossier),
                [],
                "请介绍公开进展。",
                trace_id="guest-stream",
                on_delta=collect,
            )

        self.assertEqual(deltas, ["这是", "公开回答"])
        self.assertEqual(result.speech, "这是公开回答")
        self.assertEqual(result.action, "deflect")
        stream_prompt = llm.await_args_list[1].args[0][0]["content"]
        hidden_fact = next(
            fact.content
            for fact in dossier.facts
            if fact.content not in dossier.surface_bio
        )
        self.assertNotIn(hidden_fact, stream_prompt)
        self.assertNotIn("guard_current", stream_prompt)

    async def test_six_question_script_obeys_guard_and_persona_rules(self) -> None:
        dossier = Dossier.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
        states = initial_states(dossier)
        history: list[dict] = []
        f5_partial = next(fact.partial for fact in dossier.facts if fact.id == "F5")

        scripted_llm_outputs = [
            # 1-3: repeat a pressure=3 probe against the guard=5 fact.
            GuestAssessment(pressure=3, targeted_fact="F5"),
            GuestOutput(
                pressure=3,
                targeted_fact="F5",
                action="deflect",
                speech="我们始终重视供应链治理，也会持续优化相关流程。",
                stage_direction="语气平稳",
            ),
            GuestAssessment(pressure=3, targeted_fact="F5"),
            GuestOutput(
                pressure=3,
                targeted_fact="F5",
                action="tell",
                speech="这类合作都有正常流程……具体条款我现在记不清。",
                stage_direction="停止敲桌，短暂看向镜头",
            ),
            GuestAssessment(pressure=3, targeted_fact="F5"),
            GuestOutput(
                pressure=3,
                targeted_fact="F5",
                action="partial",
                speech=f5_partial,
                stage_direction="停顿后放慢语速",
            ),
            # 4: inverse persona closes down under pressure=5.
            GuestAssessment(pressure=5, targeted_fact="F4"),
            GuestOutput(
                pressure=5,
                targeted_fact="F4",
                action="deflect",
                speech="你的问题已经预设了结论，我需要先知道这段素材会怎么使用。",
                stage_direction="身体后靠，皱眉看向主持人",
            ),
            # 5: verbosity=1 is hard-capped even if a model over-generates.
            GuestAssessment(pressure=1, targeted_fact=None),
            GuestOutput(
                pressure=1,
                targeted_fact=None,
                action="deflect",
                speech="这个问题需要先界定你所说的具体实验条件，否则任何概括都可能造成误解。",
                stage_direction="抬眼等待主持人说明问题范围",
            ),
            # 6: a fully revealed fact can be repeated at low pressure.
            GuestAssessment(pressure=0, targeted_fact="F2"),
            GuestOutput(
                pressure=0,
                targeted_fact="F2",
                action="reveal",
                speech="林志远连续三年为我们提供品牌咨询，这一点我已经说过。",
                stage_direction="点头",
            ),
        ]
        llm = AsyncMock(side_effect=scripted_llm_outputs)

        with patch("app.agents.guest.chat", llm):
            results: list[GuestOutput] = []
            for index, question in enumerate(
                [
                    "那份协议里的品牌管理费是 6% 吗？",
                    "我再确认一次，品牌管理费是不是 6%？",
                    "请直接回答：协议写的是每月 6% 吗？",
                ],
                start=1,
            ):
                result = await generate_guest_response(
                    dossier,
                    states,
                    history,
                    question,
                    trace_id=f"guest-script-{index}",
                )
                results.append(result)
                history.extend(
                    [
                        {"role": "host", "content": question},
                        {
                            "role": "guest",
                            "content": result.speech,
                            "meta_json": result.model_dump(mode="json"),
                        },
                    ]
                )

            wary = dossier.model_copy(
                deep=True,
                update={
                    "persona": Persona(
                        id="wary_witness",
                        name="有戒心的当事人",
                        verbosity=2,
                        evasiveness=3,
                        hostility=4,
                        pressure_response="inverse",
                        speech_style="谨慎、紧绷，会质疑采访目的。",
                        deflections=["要求先解释素材用途"],
                    )
                },
            )
            wary_states = initial_states(wary)
            wary_result = await generate_guest_response(
                wary,
                wary_states,
                [],
                "所以你把分包给亲属，就是在撒谎，对吗？",
                trace_id="guest-script-4",
            )

            terse = dossier.model_copy(
                deep=True,
                update={
                    "persona": Persona(
                        id="terse_scientist",
                        name="惜字如金的科学家",
                        verbosity=1,
                        evasiveness=1,
                        hostility=1,
                        pressure_response="standard",
                        speech_style="准确、直接、极度简短。",
                        deflections=["要求主持人定义术语"],
                    )
                },
            )
            terse_result = await generate_guest_response(
                terse,
                initial_states(terse),
                [],
                "您怎么看这件事？",
                trace_id="guest-script-5",
            )

            revealed_states = initial_states(dossier)
            state_for(revealed_states, "F2").revealed = "full"
            revealed_result = await generate_guest_response(
                dossier,
                revealed_states,
                [],
                "林志远以前和你们合作过吗？",
                trace_id="guest-script-6",
            )

        self.assertEqual(results[0].action, "deflect")
        self.assertEqual(results[2].action, "partial")
        self.assertEqual(state_for(states, "F5").guard_current, 3)
        self.assertEqual(wary_result.action, "deflect")
        self.assertEqual(state_for(wary_states, "F4").guard_current, 5)
        self.assertLess(len(terse_result.speech), 40)
        self.assertEqual(revealed_result.action, "reveal")
        self.assertEqual(llm.await_count, 12)

    def test_guest_prompt_contains_required_explicit_rules(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "app" / "prompts" / "guest.md"
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertIn("150-250", prompt)
        self.assertIn("不超过 25", prompt)
        self.assertIn("所以你是在撒谎", prompt)
        self.assertIn("PRESSURE > GUARD", prompt)
        self.assertIn("我不能告诉你邮件的事", prompt)
        self.assertIn("revealed=full", prompt)
