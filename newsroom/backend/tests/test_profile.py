from __future__ import annotations

import unittest

from app.memory.profile import StudentProfile, update_profile
from app.tools.stenographer import Metrics


def fake_metrics(
    *,
    probe_rate: float,
    avg_q_len: float,
    open_ratio: float,
    fillers: list[tuple[str, float]],
) -> Metrics:
    return Metrics(
        open_ratio=open_ratio,
        closed_count=1,
        probe_rate=probe_rate,
        listen_score=0.5,
        host_talk_ratio=0.3,
        avg_q_len=avg_q_len,
        long_q_count=1,
        multi_q_count=0,
        leading_q_count=0,
        filler_top=fillers,
        facts_found=2,
        facts_total=5,
        juiciness_earned=4,
    )


class StudentProfileTests(unittest.TestCase):
    def test_three_sessions_identify_only_continuous_weaknesses(self) -> None:
        profile = StudentProfile(student_id="student-001")
        sessions = [
            fake_metrics(
                probe_rate=0.2,
                avg_q_len=34,
                open_ratio=0.4,
                fillers=[("就是", 0.4), ("然后", 0.2)],
            ),
            fake_metrics(
                probe_rate=0.3,
                avg_q_len=31,
                open_ratio=0.5,
                fillers=[("就是", 0.3), ("嗯", 0.2)],
            ),
            # 开放式比例已达标，所以它不能被误判为连续三场的老毛病。
            fake_metrics(
                probe_rate=0.4,
                avg_q_len=29,
                open_ratio=0.7,
                fillers=[("那个", 0.5), ("就是", 0.1)],
            ),
        ]

        for index, metrics in enumerate(sessions):
            profile = update_profile(
                profile,
                metrics,
                "spin_ceo",
                60 + index,
                top3_advice=["缩短问题", "继续追问", "少说口头禅"],
            )
            if index < 2:
                self.assertEqual(profile.chronic_weaknesses, [])

        self.assertEqual(profile.sessions_count, 3)
        self.assertEqual(profile.chronic_weaknesses, ["不追问", "问题太长"])
        self.assertNotIn("开放式问题不足", profile.chronic_weaknesses)
        self.assertEqual(profile.filler_blacklist[0], "就是")
        self.assertEqual(profile.persona_records["spin_ceo"], 62)
        self.assertIsNone(profile.persona_records["wary_witness"])


if __name__ == "__main__":
    unittest.main()
