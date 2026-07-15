from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from app.llm.exceptions import SchemaViolation
from app.llm.gateway import chat
from app.schemas import Dossier
from app.tools.stenographer import Metrics


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
MAX_BUSINESS_RETRIES = 2

_LLM_DIMENSIONS: tuple[tuple[str, int], ...] = (
    ("问题设计", 25),
    ("倾听与追问", 30),
    ("现场控制", 20),
    ("语言表达", 15),
)
_INFORMATION_DIMENSION = ("信息收获", 10)
_REWRITE_RE = re.compile(r"^第\s*(\d+)\s*轮我会这么问[：:]")
_NUMBER_RE = re.compile(r"[0-9０-９]")


class Evidence(BaseModel):
    turn: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=15)
    why: str = Field(min_length=1)


class DimScore(BaseModel):
    name: str
    score: int = Field(ge=0)
    max: int = Field(gt=0)
    evidence: list[Evidence]
    comment: str = Field(min_length=1)
    rewrite: str | None = None

    @model_validator(mode="after")
    def score_does_not_exceed_maximum(self) -> Self:
        if self.score > self.max:
            raise ValueError("score cannot exceed max")
        return self


class MissedFact(BaseModel):
    fact_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    closest_turn: int = Field(ge=0)
    clue_quote: str = Field(min_length=1, max_length=15)
    why: str = Field(min_length=1)
    question: str = Field(min_length=1)


class JudgeReport(BaseModel):
    dims: list[DimScore]
    total: int = Field(ge=0, le=100)
    missed_facts: list[MissedFact]
    top3_advice: list[str] = Field(min_length=3, max_length=3)
    highlight_turn: int = Field(ge=0)

    @model_validator(mode="after")
    def total_matches_dimensions(self) -> Self:
        if self.total != sum(dimension.score for dimension in self.dims):
            raise ValueError("total must equal the sum of all dimension scores")
        return self


class _JudgeDraft(BaseModel):
    """LLM-owned fields. The information dimension is deliberately absent."""

    dims: list[DimScore]
    missed_facts: list[MissedFact]
    top3_advice: list[str] = Field(min_length=3, max_length=3)
    highlight_turn: int = Field(ge=0)


async def generate_judge_report(
    dossier: Dossier,
    transcript: Any,
    metrics: Metrics,
    *,
    trace_id: str,
    previous_top3_advice: Sequence[str] = (),
) -> JudgeReport:
    """Judge a complete interview while keeping objective scoring in code.

    The LLM scores only the first four dimensions. ``信息收获`` and ``total``
    are added after the response, so the model cannot alter either value.
    Turn indexes and verbatim quotes are checked against the transcript. A
    business-rule failure is sent back through a fresh judge call at most twice.
    """

    turns = _transcript_turns(transcript)
    normalized_metrics = (
        metrics if isinstance(metrics, Metrics) else Metrics.model_validate(metrics)
    )
    _validate_inputs(dossier, turns, normalized_metrics)

    validation_errors: list[str] = []
    for attempt in range(MAX_BUSINESS_RETRIES + 1):
        prompt = _render_prompt(
            "judge.md",
            {
                "transcript_json": json.dumps(
                    turns,
                    ensure_ascii=False,
                    indent=2,
                ),
                "metrics_json": normalized_metrics.model_dump_json(indent=2),
                "derived_metrics_json": json.dumps(
                    _derived_metrics(turns, normalized_metrics),
                    ensure_ascii=False,
                    indent=2,
                ),
                "dossier_json": dossier.model_dump_json(indent=2),
                "validation_errors_json": json.dumps(
                    validation_errors,
                    ensure_ascii=False,
                    indent=2,
                ),
                "previous_top3_advice_json": json.dumps(
                    list(previous_top3_advice),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
        try:
            result = await chat(
                [{"role": "system", "content": prompt}],
                model_tier="smart",
                schema=_JudgeDraft,
                trace_id=f"{trace_id}-attempt-{attempt + 1}",
            )
        except SchemaViolation:
            break

        if not isinstance(result, _JudgeDraft):
            raise TypeError("Expected _JudgeDraft from validated LLM gateway")

        validation_errors = _draft_errors(
            result,
            turns=turns,
            dossier=dossier,
            metrics=normalized_metrics,
        )
        if not validation_errors:
            return _complete_report(result, dossier, normalized_metrics)

    return _fallback_report(dossier, turns, normalized_metrics)


class Judge:
    async def review(
        self,
        *,
        dossier: Dossier,
        transcript: Any,
        metrics: Metrics,
        trace_id: str = "judge",
        previous_top3_advice: Sequence[str] = (),
    ) -> JudgeReport:
        return await generate_judge_report(
            dossier,
            transcript,
            metrics,
            trace_id=trace_id,
            previous_top3_advice=previous_top3_advice,
        )


def information_score(metrics: Metrics, dossier: Dossier) -> int:
    """Return the deterministic 10-point information-acquisition score.

    Five points come from the share of facts fully found and five from the share
    of available juiciness earned. This function is intentionally LLM-free.
    """

    fact_ratio = _bounded_ratio(metrics.facts_found, metrics.facts_total)
    total_juiciness = sum(fact.juiciness for fact in dossier.facts)
    juiciness_ratio = _bounded_ratio(metrics.juiciness_earned, total_juiciness)
    return round(5 * fact_ratio + 5 * juiciness_ratio)


def _complete_report(
    draft: _JudgeDraft,
    dossier: Dossier,
    metrics: Metrics,
) -> JudgeReport:
    info_score = information_score(metrics, dossier)
    total_juiciness = sum(fact.juiciness for fact in dossier.facts)
    information_dimension = DimScore(
        name=_INFORMATION_DIMENSION[0],
        score=info_score,
        max=_INFORMATION_DIMENSION[1],
        evidence=[],
        comment=(
            f"完整挖出 {metrics.facts_found}/{metrics.facts_total} 条料；"
            f"获得料值 {metrics.juiciness_earned}/{total_juiciness}。"
        ),
        rewrite=None,
    )
    dimensions = [*draft.dims, information_dimension]
    return JudgeReport(
        dims=dimensions,
        total=sum(dimension.score for dimension in dimensions),
        missed_facts=draft.missed_facts,
        top3_advice=draft.top3_advice,
        highlight_turn=draft.highlight_turn,
    )


def _draft_errors(
    draft: _JudgeDraft,
    *,
    turns: list[dict[str, Any]],
    dossier: Dossier,
    metrics: Metrics,
) -> list[str]:
    errors: list[str] = []
    turns_by_index = {turn["idx"]: turn for turn in turns}
    host_indexes = {
        index
        for index, turn in turns_by_index.items()
        if _role(turn) == "host"
    }

    actual_dimensions = [(dimension.name, dimension.max) for dimension in draft.dims]
    if actual_dimensions != list(_LLM_DIMENSIONS):
        errors.append(
            "dims 必须依次且仅包含："
            + "、".join(f"{name}（{maximum}分）" for name, maximum in _LLM_DIMENSIONS)
        )

    for dimension in draft.dims:
        if not _NUMBER_RE.search(dimension.comment):
            errors.append(f"{dimension.name}.comment 必须引用至少一个 Metrics 数字")
        for evidence in dimension.evidence:
            error = _quote_error(
                evidence.turn,
                evidence.quote,
                turns_by_index,
                field=f"{dimension.name}.evidence",
            )
            if error:
                errors.append(error)
        if dimension.rewrite is not None:
            match = _REWRITE_RE.match(dimension.rewrite)
            if match is None:
                errors.append(
                    f"{dimension.name}.rewrite 必须以“第 N 轮我会这么问：”开头"
                )
            elif int(match.group(1)) not in host_indexes:
                errors.append(
                    f"{dimension.name}.rewrite 引用的第 {match.group(1)} 轮不是有效主持人 turn"
                )

    if draft.highlight_turn not in host_indexes:
        errors.append("highlight_turn 必须指向逐字稿中的主持人 turn")

    expected_missed_count = metrics.facts_total - metrics.facts_found
    if len(draft.missed_facts) != expected_missed_count:
        errors.append(
            f"missed_facts 必须有 {expected_missed_count} 条，"
            f"当前有 {len(draft.missed_facts)} 条"
        )

    facts_by_id = {fact.id: fact for fact in dossier.facts}
    missed_ids = [item.fact_id for item in draft.missed_facts]
    if len(missed_ids) != len(set(missed_ids)):
        errors.append("missed_facts.fact_id 不得重复")
    for item in draft.missed_facts:
        fact = facts_by_id.get(item.fact_id)
        if fact is None:
            errors.append(f"missed_facts 包含未知 fact_id：{item.fact_id}")
            continue
        if item.content != fact.content:
            errors.append(f"{item.fact_id}.content 必须逐字复制 dossier 中的 content")
        turn = turns_by_index.get(item.closest_turn)
        if turn is not None and _role(turn) != "guest":
            errors.append(f"{item.fact_id}.closest_turn 必须指向嘉宾 turn")
        error = _quote_error(
            item.closest_turn,
            item.clue_quote,
            turns_by_index,
            field=f"{item.fact_id}.clue_quote",
        )
        if error:
            errors.append(error)

    return errors


def _quote_error(
    turn_index: int,
    quote: str,
    turns_by_index: Mapping[int, Mapping[str, Any]],
    *,
    field: str,
) -> str | None:
    turn = turns_by_index.get(turn_index)
    if turn is None:
        return f"{field} 引用了不存在的 turn={turn_index}"
    if quote not in str(turn.get("content", "")):
        return f"{field} 的 quote 不是 turn={turn_index} 的逐字原文"
    return None


def _validate_inputs(
    dossier: Dossier,
    turns: list[dict[str, Any]],
    metrics: Metrics,
) -> None:
    indexes = [turn["idx"] for turn in turns]
    if not turns:
        raise ValueError("transcript cannot be empty")
    if len(indexes) != len(set(indexes)):
        raise ValueError("transcript turn indexes must be unique")
    if not any(_role(turn) == "host" for turn in turns):
        raise ValueError("transcript must contain at least one host turn")
    if metrics.facts_total != len(dossier.facts):
        raise ValueError("metrics.facts_total must equal the dossier fact count")
    if not 0 <= metrics.facts_found <= metrics.facts_total:
        raise ValueError("metrics.facts_found must be between zero and facts_total")
    total_juiciness = sum(fact.juiciness for fact in dossier.facts)
    if not 0 <= metrics.juiciness_earned <= total_juiciness:
        raise ValueError(
            "metrics.juiciness_earned must be between zero and dossier juiciness total"
        )


def _transcript_turns(transcript: Any) -> list[dict[str, Any]]:
    source = transcript
    if isinstance(transcript, Mapping) and "turns" in transcript:
        source = transcript["turns"]
    elif hasattr(transcript, "turns"):
        source = transcript.turns
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        raise TypeError("transcript must be a turn sequence or expose .turns")

    turns: list[dict[str, Any]] = []
    for position, turn in enumerate(source):
        if isinstance(turn, BaseModel):
            data = turn.model_dump(mode="json")
        elif isinstance(turn, Mapping):
            data = dict(turn)
        else:
            data = {
                "idx": getattr(turn, "idx", position),
                "role": getattr(turn, "role", ""),
                "content": getattr(turn, "content", ""),
            }
        raw_index = data.get("idx", position)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise TypeError("every transcript turn idx must be an integer")
        data["idx"] = raw_index
        role = data.get("role", "")
        if isinstance(role, Enum):
            role = role.value
        data["role"] = str(role).rsplit(".", 1)[-1].casefold()
        data["content"] = str(data.get("content", ""))
        turns.append(data)
    return turns


def _derived_metrics(
    turns: Sequence[Mapping[str, Any]],
    metrics: Metrics,
) -> dict[str, Any]:
    host_question_count = sum(_role(turn) == "host" for turn in turns)
    host_share = (
        metrics.host_talk_ratio / (1 + metrics.host_talk_ratio)
        if metrics.host_talk_ratio >= 0
        else 0.0
    )
    return {
        "host_question_count": host_question_count,
        "open_percentage": _percentage(metrics.open_ratio),
        "closed_percentage": _percentage(
            _bounded_ratio(metrics.closed_count, host_question_count)
        ),
        "probe_percentage": _percentage(metrics.probe_rate),
        "listen_percentage": _percentage(metrics.listen_score),
        "host_character_share_percentage": _percentage(host_share),
        "host_to_guest_character_ratio": metrics.host_talk_ratio,
        "average_question_length_characters": metrics.avg_q_len,
        "long_question_count": metrics.long_q_count,
        "multi_question_count": metrics.multi_q_count,
        "leading_question_count": metrics.leading_q_count,
        "filler_percentages_per_host_sentence": [
            {"word": word, "percentage": _percentage(rate)}
            for word, rate in metrics.filler_top
        ],
        "facts_found": metrics.facts_found,
        "facts_total": metrics.facts_total,
        "juiciness_earned": metrics.juiciness_earned,
    }


def _fallback_report(
    dossier: Dossier,
    turns: list[dict[str, Any]],
    metrics: Metrics,
) -> JudgeReport:
    derived = _derived_metrics(turns, metrics)
    comments = {
        "问题设计": (
            f"工具记录 {derived['host_question_count']} 个问题，"
            f"开放式占 {derived['open_percentage']}%。"
        ),
        "倾听与追问": (
            f"工具记录追问率 {derived['probe_percentage']}%，"
            f"词汇承接度 {derived['listen_percentage']}%。"
        ),
        "现场控制": (
            f"主持人字符占双方对话 {derived['host_character_share_percentage']}%，"
            f"长问题 {metrics.long_q_count} 个。"
        ),
        "语言表达": (
            f"平均问题长度 {metrics.avg_q_len} 字，"
            f"多问合一 {metrics.multi_q_count} 个。"
        ),
    }
    draft_dimensions = [
        DimScore(
            name=name,
            score=0,
            max=maximum,
            evidence=[],
            comment=comments[name],
            rewrite=None,
        )
        for name, maximum in _LLM_DIMENSIONS
    ]
    host_turns = [turn for turn in turns if _role(turn) == "host"]
    guest_turns = [turn for turn in turns if _role(turn) == "guest"]
    closest_turn = guest_turns[-1] if guest_turns else turns[-1]
    clue_quote = str(closest_turn["content"])[:15] or "（无有效原话）"
    missed_count = metrics.facts_total - metrics.facts_found
    missed_facts = [
        MissedFact(
            fact_id=fact.id,
            content=fact.content,
            closest_turn=closest_turn["idx"],
            clue_quote=clue_quote,
            why="评委模型不可用，需人工结合这轮回答复核破绽。",
            question=f"请沿这个方向具体追问：{fact.unlock_hint}",
        )
        for fact in dossier.facts[:missed_count]
    ]
    draft = _JudgeDraft(
        dims=draft_dimensions,
        missed_facts=missed_facts,
        top3_advice=[
            "下一次每个问题只保留一个焦点，并优先使用“为什么/怎么”。",
            "嘉宾出现停顿、改口或跳过时间点时，连续追问同一细节。",
            "每轮提问前删掉背景复述，只保留证据、矛盾和一个问句。",
        ],
        highlight_turn=host_turns[0]["idx"],
    )
    return _complete_report(draft, dossier, metrics)


def _role(turn: Mapping[str, Any]) -> str:
    value = turn.get("role", "")
    if isinstance(value, Enum):
        value = value.value
    return str(value).rsplit(".", 1)[-1].casefold()


def _bounded_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, numerator / denominator))


def _percentage(ratio: float) -> float:
    return round(ratio * 100, 1)


def _render_prompt(filename: str, values: Mapping[str, str]) -> str:
    prompt = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    for placeholder, value in values.items():
        prompt = prompt.replace(f"{{{placeholder}}}", value)
    return prompt


__all__ = [
    "DimScore",
    "Evidence",
    "Judge",
    "JudgeReport",
    "MissedFact",
    "generate_judge_report",
    "information_score",
]
