from __future__ import annotations

import unittest
from typing import Any

from app.tools.stenographer import Metrics, calculate_metrics


# These three fixtures are hand-written transcripts with hand-labelled expected
# results.  The expected values are intentionally literal (rather than computed
# by test helpers) so this file is usable as the report's objective-accuracy raw
# data.
SAMPLES: list[tuple[str, list[dict[str, str]], Any, Metrics]] = [
    (
        "开放式追问且解锁两条料",
        [
            {"role": "host", "content": "为什么项目延期？"},
            {"role": "guest", "content": "项目延期源于供应商交付延误。"},
            {"role": "director", "content": "抓住供应商"},
            {"role": "host", "content": "供应商交付为什么延误？"},
            {"role": "guest", "content": "工厂设备故障导致生产停摆。"},
            {
                "role": "host",
                "content": "能说说工厂设备故障怎么影响生产吗？",
            },
        ],
        [
            {"revealed": "full", "juiciness": 2},
            {"revealed": "hidden", "juiciness": 4},
            {"revealed": "full", "fact": {"juiciness": 5}},
        ],
        # Eligible overlap sets are 3/3 and 4/5, hence listen_score=7/8.
        Metrics(
            open_ratio=1.0,
            closed_count=0,
            probe_rate=1.0,
            listen_score=0.875,
            host_talk_ratio=1.3333,
            avg_q_len=12.0,
            long_q_count=0,
            multi_q_count=0,
            leading_q_count=0,
            filler_top=[],
            facts_found=2,
            facts_total=3,
            juiciness_earned=7,
        ),
    ),
    (
        "诱导、多问、长问与五种口头禅",
        [
            {"role": "host", "content": "这个是不是内幕交易吗？"},
            {"role": "guest", "content": "董事会批准了关联交易方案。"},
            {
                "role": "host",
                "content": (
                    "嗯，您是不是觉得董事会批准关联交易就没有问题？"
                    "另外，方案是谁提出的？"
                ),
            },
            {"role": "guest", "content": "方案由财务总监提出，法务部门反对。"},
            {
                "role": "host",
                "content": (
                    "那个，难道不是财务总监主导吗？"
                    "顺便问一下，法务为什么反对？"
                ),
            },
            {"role": "guest", "content": "审议程序存在漏洞，董事会仍然推进交易。"},
            {
                "role": "host",
                "content": (
                    "然后，就是说，大家都认为这次审议程序存在严重漏洞，您能不能"
                    "完整解释董事会、法务部门和财务总监分别做了什么，为什么仍然"
                    "推进交易，并说明后来如何整改？"
                ),
            },
        ],
        {
            "fact-a": {"revealed": "partial", "juiciness": 3},
            "fact-b": {"revealed": "hidden", "juiciness": 5},
        },
        # Six host sentences: each observed filler occurs once, so every rate is
        # 1/6.  The three eligible overlaps are all >=2, while their combined
        # lexical overlap is 14 of 26 host content words.
        Metrics(
            open_ratio=0.5,
            closed_count=1,
            probe_rate=1.0,
            listen_score=0.5385,
            host_talk_ratio=3.0,
            avg_q_len=36.75,
            long_q_count=1,
            multi_q_count=2,
            leading_q_count=3,
            filler_top=[
                ("这个", 0.1667),
                ("那个", 0.1667),
                ("然后", 0.1667),
                ("就是说", 0.1667),
                ("嗯", 0.1667),
            ],
            facts_found=0,
            facts_total=2,
            juiciness_earned=0,
        ),
    ),
    (
        "未完成访谈的零分母边界",
        [
            {"role": "host", "content": "有没有证据？"},
            {"role": "host", "content": "呃，对吧？"},
        ],
        [],
        Metrics(
            open_ratio=0.0,
            closed_count=2,
            probe_rate=0.0,
            listen_score=0.0,
            host_talk_ratio=0.0,
            avg_q_len=5.5,
            long_q_count=0,
            multi_q_count=0,
            leading_q_count=0,
            filler_top=[("对吧", 0.5), ("呃", 0.5)],
            facts_found=0,
            facts_total=0,
            juiciness_earned=0,
        ),
    ),
]


class StenographerTests(unittest.TestCase):
    def test_three_hand_labelled_transcripts_match_every_metric(self) -> None:
        for name, turns, fact_state, expected in SAMPLES:
            with self.subTest(name=name):
                actual = calculate_metrics(turns, fact_state)
                self.assertEqual(actual.model_dump(), expected.model_dump())


if __name__ == "__main__":
    unittest.main()
