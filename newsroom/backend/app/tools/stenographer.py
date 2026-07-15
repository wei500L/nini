"""Deterministic transcript metrics; this module never calls an LLM."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

import jieba
import jieba.posseg as pseg
from pydantic import BaseModel

from app.tools.stenographer_config import (
    CLOSED_ENDINGS,
    CLOSED_MAX_LENGTH_EXCLUSIVE,
    CLOSED_PREFIXES,
    CONTENT_STOPWORDS,
    FILLERS,
    FILLER_TOP_LIMIT,
    FOLLOW_UP_MIN_SHARED_WORDS,
    LEADING_QUESTION_PATTERNS,
    LONG_QUESTION_LENGTH_EXCLUSIVE,
    METRIC_DECIMAL_PLACES,
    MULTI_QUESTION_MARK_MINIMUM,
    MULTI_QUESTION_MARKERS,
    OPEN_MARKERS,
)


class Metrics(BaseModel):
    open_ratio: float
    closed_count: int
    probe_rate: float
    listen_score: float
    host_talk_ratio: float
    avg_q_len: float
    long_q_count: int
    multi_q_count: int
    leading_q_count: int
    filler_top: list[tuple[str, float]]
    facts_found: int
    facts_total: int
    juiciness_earned: int


_TOKENIZER = jieba.Tokenizer()
for _filler in FILLERS:
    _TOKENIZER.add_word(_filler, tag="x")
_POS_TOKENIZER = pseg.POSTokenizer(_TOKENIZER)

_LEADING_RE = re.compile("|".join(f"(?:{pattern})" for pattern in LEADING_QUESTION_PATTERNS))
_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？?!；;\n]+")
_TERMINAL_PUNCTUATION = "。！？?!；;，,、…：:）)]】』」”\"' "


def calculate_metrics(
    turns: Sequence[Any],
    fact_state: Sequence[Any] | Mapping[str, Any],
) -> Metrics:
    """Calculate all objective interview metrics from turns and fact state.

    A turn may be a mapping, Pydantic model, dataclass, or ORM object exposing
    ``role`` and ``content``.  Host turns are the unit of question analysis;
    director turns are ignored.  ``fact_state`` items need ``revealed`` and may
    carry ``juiciness`` directly or under a nested ``fact`` object.
    """

    host_questions: list[str] = []
    host_characters = 0
    guest_characters = 0
    host_sentence_count = 0
    filler_counts: Counter[str] = Counter()
    eligible_probe_count = 0
    follow_up_count = 0
    shared_content_words = 0
    question_content_words = 0
    previous_guest_answer: str | None = None

    for turn in turns:
        role = _role(turn)
        content = str(_field(turn, "content", ""))
        if role == "guest":
            guest_characters += len(content)
            previous_guest_answer = content
            continue
        if role != "host":
            continue

        host_characters += len(content)
        if not content.strip():
            continue

        host_questions.append(content)
        sentences = _sentences(content)
        host_sentence_count += len(sentences)
        filler_counts.update(word for word in _TOKENIZER.lcut(content) if word in FILLERS)

        if previous_guest_answer is not None:
            eligible_probe_count += 1
            question_words = _content_words(content)
            answer_words = _content_words(previous_guest_answer)
            overlap_count = len(question_words & answer_words)
            shared_content_words += overlap_count
            question_content_words += len(question_words)
            if overlap_count >= FOLLOW_UP_MIN_SHARED_WORDS:
                follow_up_count += 1

    question_count = len(host_questions)
    open_count = sum(_is_open(question) for question in host_questions)
    closed_count = sum(_is_closed(question) for question in host_questions)
    question_lengths = [len(question) for question in host_questions]
    facts_found, facts_total, juiciness_earned = _fact_metrics(fact_state)

    return Metrics(
        open_ratio=_ratio(open_count, question_count),
        closed_count=closed_count,
        probe_rate=_ratio(follow_up_count, eligible_probe_count),
        listen_score=_ratio(shared_content_words, question_content_words),
        host_talk_ratio=_ratio(host_characters, guest_characters),
        avg_q_len=_average(question_lengths),
        long_q_count=sum(
            length > LONG_QUESTION_LENGTH_EXCLUSIVE for length in question_lengths
        ),
        multi_q_count=sum(_is_multi(question) for question in host_questions),
        leading_q_count=sum(_is_leading(question) for question in host_questions),
        filler_top=_filler_top(filler_counts, host_sentence_count),
        facts_found=facts_found,
        facts_total=facts_total,
        juiciness_earned=juiciness_earned,
    )


def _role(turn: Any) -> str:
    value = _field(turn, "role", "")
    if isinstance(value, Enum):
        value = value.value
    return str(value).rsplit(".", 1)[-1].casefold()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BOUNDARY_RE.split(text) if part.strip()]


def _content_words(text: str) -> set[str]:
    words: set[str] = set()
    for pair in _POS_TOKENIZER.cut(text):
        word = pair.word.strip().casefold()
        if (
            word
            and word not in CONTENT_STOPWORDS
            and (pair.flag.startswith("n") or pair.flag.startswith("v"))
        ):
            words.add(word)
    return words


def _question_stem(question: str) -> str:
    return question.strip().rstrip(_TERMINAL_PUNCTUATION)


def _is_open(question: str) -> bool:
    return any(marker in question for marker in OPEN_MARKERS)


def _is_closed(question: str) -> bool:
    stem = _question_stem(question)
    starts_closed = stem.startswith(CLOSED_PREFIXES)
    ends_short_closed = (
        stem.endswith(CLOSED_ENDINGS)
        and len(question) < CLOSED_MAX_LENGTH_EXCLUSIVE
    )
    return starts_closed or ends_short_closed


def _is_multi(question: str) -> bool:
    question_mark_count = question.count("？") + question.count("?")
    return question_mark_count >= MULTI_QUESTION_MARK_MINIMUM or any(
        marker in question for marker in MULTI_QUESTION_MARKERS
    )


def _is_leading(question: str) -> bool:
    return _LEADING_RE.search(question) is not None


def _filler_top(
    counts: Counter[str],
    host_sentence_count: int,
) -> list[tuple[str, float]]:
    if host_sentence_count == 0:
        return []
    filler_order = {filler: index for index, filler in enumerate(FILLERS)}
    ranked = sorted(
        ((filler, count) for filler, count in counts.items() if count > 0),
        key=lambda item: (-item[1], filler_order[item[0]]),
    )[:FILLER_TOP_LIMIT]
    return [
        (filler, _ratio(count, host_sentence_count)) for filler, count in ranked
    ]


def _fact_metrics(
    fact_state: Sequence[Any] | Mapping[str, Any],
) -> tuple[int, int, int]:
    states = _fact_states(fact_state)
    found = [state for state in states if _revealed_value(state) == "full"]
    return (
        len(found),
        len(states),
        sum(_juiciness(state) for state in found),
    )


def _fact_states(fact_state: Sequence[Any] | Mapping[str, Any]) -> list[Any]:
    if isinstance(fact_state, Mapping):
        nested = fact_state.get("facts")
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            return list(nested)
        if "revealed" in fact_state:
            return [fact_state]
        return list(fact_state.values())
    return list(fact_state)


def _revealed_value(state: Any) -> str:
    revealed = _field(state, "revealed", state if isinstance(state, str) else "")
    if isinstance(revealed, Enum):
        revealed = revealed.value
    if isinstance(revealed, bool):
        return "full" if revealed else "hidden"
    return str(revealed).casefold()


def _juiciness(state: Any) -> int:
    direct = _field(state, "juiciness")
    if direct is None:
        direct = _field(_field(state, "fact"), "juiciness", 0)
    return int(direct)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, METRIC_DECIMAL_PLACES)


def _average(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), METRIC_DECIMAL_PLACES)


__all__ = ["Metrics", "calculate_metrics"]
