from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents.judge import (
    DimScore,
    Evidence,
    JudgeReport,
    MissedFact,
    _JudgeDraft,
    generate_judge_report,
)
from app.schemas import Dossier
from app.tools.stenographer import Metrics


SEED_PATH = Path(__file__).resolve().parents[2] / "docs" / "seed" / "dossier_demo.json"

TRANSCRIPT = [
    {
        "idx": 0,
        "role": "host",
        "content": "为什么在抽检结果出来前就讨论更换承包商？",
    },
    {
        "idx": 1,
        "role": "guest",
        "content": "我们只是讨论多套预案，最终公告完全合规。",
    },
    {
        "idx": 2,
        "role": "host",
        "content": "你说讨论过多套预案，第二次抽检结果是哪天出来的？",
    },
    {
        "idx": 3,
        "role": "guest",
        "content": "这个时间我记不清了，但流程没有问题。",
    },
    {
        "idx": 4,
        "role": "host",
        "content": "所以你们早就内定了，对吗？",
    },
    {
        "idx": 5,
        "role": "guest",
        "content": "我不接受内定这个说法。",
    },
]

METRICS = Metrics(
    open_ratio=0.6667,
    closed_count=1,
    probe_rate=0.5,
    listen_score=0.35,
    host_talk_ratio=1.5,
    avg_q_len=20.0,
    long_q_count=0,
    multi_q_count=0,
    leading_q_count=1,
    filler_top=[],
    facts_found=4,
    facts_total=5,
    juiciness_earned=12,
)


def valid_draft(dossier: Dossier) -> _JudgeDraft:
    missed = next(fact for fact in dossier.facts if fact.id == "F3")
    return _JudgeDraft(
        dims=[
            DimScore(
                name="问题设计",
                score=20,
                max=25,
                evidence=[
                    Evidence(turn=4, quote="早就内定了", why="预设结论，诱导嘉宾否认。")
                ],
                comment="3 个问题中 1 个封闭式，占 33.3%。",
                rewrite="第 4 轮我会这么问：招标决定是哪天形成的？",
            ),
            DimScore(
                name="倾听与追问",
                score=24,
                max=30,
                evidence=[
                    Evidence(
                        turn=2,
                        quote="第二次抽检结果",
                        why="接住了嘉宾的预案说法并追时间点。",
                    )
                ],
                comment="追问率 50.0%，仍有一半机会没有继续深挖。",
                rewrite=None,
            ),
            DimScore(
                name="现场控制",
                score=15,
                max=20,
                evidence=[
                    Evidence(
                        turn=0,
                        quote="抽检结果出来前",
                        why="开场直接进入关键时间线，节奏明确。",
                    )
                ],
                comment="主持人字符占双方对话 60.0%，话语略多。",
                rewrite=None,
            ),
            DimScore(
                name="语言表达",
                score=12,
                max=15,
                evidence=[
                    Evidence(turn=4, quote="对吗", why="口语简洁，但形成封闭逼问。")
                ],
                comment="平均问题长度 20.0 字，长问题为 0 个。",
                rewrite=None,
            ),
        ],
        missed_facts=[
            MissedFact(
                fact_id=missed.id,
                content=missed.content,
                closest_turn=1,
                clue_quote="最终公告完全合规",
                why="嘉宾跳过第二次抽检，只强调最终公告，暴露时间线缺口。",
                question="第二次抽检结果和退场方案分别是哪天出现的？",
            )
        ],
        top3_advice=[
            "嘉宾跳过时间点时，至少沿同一日期连续追问 2 次。",
            "每次只问一个焦点，删掉问题里的结论性措辞。",
            "主持人字符占比超过 40% 时，把背景压缩成一句证据。",
        ],
        highlight_turn=2,
    )


class JudgeAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dossier = Dossier.model_validate_json(
            SEED_PATH.read_text(encoding="utf-8")
        )

    async def test_fixed_transcript_returns_schema_and_valid_turn_references(self) -> None:
        mocked_chat = AsyncMock(return_value=valid_draft(self.dossier))

        with patch("app.agents.judge.chat", new=mocked_chat):
            report = await generate_judge_report(
                self.dossier,
                TRANSCRIPT,
                METRICS,
                trace_id="judge-valid",
            )

        self.assertIsInstance(report, JudgeReport)
        self.assertEqual([dimension.max for dimension in report.dims], [25, 30, 20, 15, 10])
        self.assertEqual(report.dims[-1].name, "信息收获")
        self.assertEqual(report.dims[-1].score, 8)
        self.assertEqual(report.total, 79)
        self.assertEqual(len(report.top3_advice), 3)

        turns_by_index = {turn["idx"]: turn for turn in TRANSCRIPT}
        for dimension in report.dims:
            for evidence in dimension.evidence:
                self.assertIn(evidence.turn, turns_by_index)
                self.assertIn(evidence.quote, turns_by_index[evidence.turn]["content"])
                self.assertLessEqual(len(evidence.quote), 15)
        for missed in report.missed_facts:
            self.assertIn(missed.closest_turn, turns_by_index)
            self.assertIn(
                missed.clue_quote,
                turns_by_index[missed.closest_turn]["content"],
            )
            self.assertLessEqual(len(missed.clue_quote), 15)

        prompt = mocked_chat.await_args.args[0][0]["content"]
        self.assertIn('"closed_count": 1', prompt)
        self.assertIn('"closed_percentage": 33.3', prompt)
        self.assertIn('"facts_found": 4', prompt)

    async def test_nonexistent_turn_is_retried_with_validation_error(self) -> None:
        invalid = valid_draft(self.dossier).model_copy(deep=True)
        invalid.dims[0].evidence[0].turn = 999
        mocked_chat = AsyncMock(side_effect=[invalid, valid_draft(self.dossier)])

        with patch("app.agents.judge.chat", new=mocked_chat):
            report = await generate_judge_report(
                self.dossier,
                TRANSCRIPT,
                METRICS,
                trace_id="judge-retry",
            )

        self.assertEqual(mocked_chat.await_count, 2)
        self.assertEqual(report.highlight_turn, 2)
        retry_prompt = mocked_chat.await_args_list[1].args[0][0]["content"]
        self.assertIn("不存在的 turn=999", retry_prompt)


if __name__ == "__main__":
    unittest.main()
