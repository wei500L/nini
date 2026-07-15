"""Persistent student profile and deterministic cross-session memory rules.

The profile is deliberately updated by code.  LLMs may consume the resulting
memory, but they never decide what is chronic, which filler words are most
frequent, or what a student's personal best is.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session as DatabaseSession

from app.models import Profile
from app.models import utc_now


CHRONIC_WINDOW = 3
DIMENSION_SCORE_THRESHOLD = 0.6
KNOWN_PERSONA_IDS = (
    "chatty_writer",
    "terse_scientist",
    "spin_ceo",
    "wary_witness",
)

# Each predicate is phrased as "this session exhibits the weakness".  Some
# raw metrics are healthy when high and others are healthy when low, so keeping
# the direction here avoids asking an LLM to interpret thresholds.
_METRIC_WEAKNESS_RULES: tuple[tuple[str, str, Any], ...] = (
    ("开放式问题不足", "open_ratio", lambda value: value < 0.6),
    ("不追问", "probe_rate", lambda value: value < 0.5),
    ("没接住嘉宾原话", "listen_score", lambda value: value < 0.3),
    ("主持人说得太多", "host_talk_ratio", lambda value: value > 0.54),
    ("问题太长", "avg_q_len", lambda value: value > 28),
    ("多问合一", "multi_q_count", lambda value: value > 0),
    ("诱导式提问", "leading_q_count", lambda value: value > 0),
)
_WEAKNESS_ORDER = [name for name, _, _ in _METRIC_WEAKNESS_RULES]


class StudentProfile(BaseModel):
    student_id: str = Field(min_length=1)
    sessions_count: int = Field(default=0, ge=0)
    metrics_history: list[dict[str, Any]] = Field(default_factory=list)
    chronic_weaknesses: list[str] = Field(default_factory=list)
    filler_blacklist: list[str] = Field(default_factory=list)
    persona_records: dict[str, Any] = Field(default_factory=dict)


def update_profile(
    profile: StudentProfile,
    metrics: BaseModel | Mapping[str, Any],
    persona_id: str,
    total_score: int | float | None = None,
    *,
    top3_advice: Sequence[str] | None = None,
    dimensions: Sequence[BaseModel | Mapping[str, Any]] | None = None,
    session_id: str | None = None,
    all_persona_ids: Sequence[str] = KNOWN_PERSONA_IDS,
) -> StudentProfile:
    """Return a new profile containing one completed session.

    ``metrics_history`` keeps the complete objective Metrics snapshot plus the
    small amount of review metadata needed by the next session.  Keeping the
    advice beside its originating metrics makes the history auditable.
    """

    if not persona_id.strip():
        raise ValueError("persona_id cannot be empty")

    snapshot = _as_dict(metrics)
    if dimensions is not None:
        snapshot["dimensions"] = [_as_dict(item) for item in dimensions]
    if top3_advice is not None:
        snapshot["top3_advice"] = [str(item) for item in top3_advice]
    if total_score is not None:
        snapshot["total_score"] = total_score
    if session_id is not None:
        snapshot["session_id"] = session_id
    snapshot["persona_id"] = persona_id

    history = [*profile.metrics_history, snapshot]
    records = _updated_persona_records(
        profile.persona_records,
        persona_id=persona_id,
        total_score=total_score,
        all_persona_ids=all_persona_ids,
    )
    return profile.model_copy(
        update={
            "sessions_count": profile.sessions_count + 1,
            "metrics_history": history,
            "chronic_weaknesses": detect_chronic_weaknesses(history),
            "filler_blacklist": cumulative_filler_top5(history),
            "persona_records": records,
        }
    )


def detect_chronic_weaknesses(
    metrics_history: Sequence[BaseModel | Mapping[str, Any]],
    *,
    window: int = CHRONIC_WINDOW,
) -> list[str]:
    """Find weaknesses present in every one of the latest ``window`` sessions."""

    if window <= 0:
        raise ValueError("window must be positive")
    if len(metrics_history) < window:
        return []

    recent = [_session_weaknesses(_as_dict(item)) for item in metrics_history[-window:]]
    chronic = set.intersection(*recent)
    order = {name: index for index, name in enumerate(_WEAKNESS_ORDER)}
    return sorted(chronic, key=lambda name: (order.get(name, len(order)), name))


def cumulative_filler_top5(
    metrics_history: Sequence[BaseModel | Mapping[str, Any]],
) -> list[str]:
    """Rank filler words by their cumulative per-session frequency."""

    totals: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for snapshot in metrics_history:
        for word, frequency in _filler_items(_as_dict(snapshot).get("filler_top", [])):
            if word not in first_seen:
                first_seen[word] = len(first_seen)
            totals[word] += frequency
    ranked = sorted(totals, key=lambda word: (-totals[word], first_seen[word], word))
    return ranked[:5]


def previous_top3_advice(profile: StudentProfile) -> list[str]:
    """Return the most recent advice list, or an empty list for a first session."""

    for snapshot in reversed(profile.metrics_history):
        advice = snapshot.get("top3_advice")
        if isinstance(advice, Sequence) and not isinstance(advice, (str, bytes)):
            return [str(item) for item in advice[:3]]
    return []


def compare_with_previous_session(
    profile: StudentProfile,
    current_dimensions: Sequence[BaseModel | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare current judge dimensions with the immediately preceding session."""

    if not profile.metrics_history:
        return []
    previous = profile.metrics_history[-1].get("dimensions")
    if not isinstance(previous, Sequence) or isinstance(previous, (str, bytes)):
        return []

    previous_by_name = {
        str(item.get("name")): item
        for raw in previous
        if (item := _as_dict(raw)).get("name") is not None
    }
    comparison: list[dict[str, Any]] = []
    for raw in current_dimensions:
        current = _as_dict(raw)
        name = str(current.get("name", ""))
        old = previous_by_name.get(name)
        if not name or old is None:
            continue
        current_score = _number(current.get("score"))
        previous_score = _number(old.get("score"))
        if current_score is None or previous_score is None:
            continue
        delta = round(current_score - previous_score, 2)
        comparison.append(
            {
                "name": name,
                "current": current_score,
                "previous": previous_score,
                "delta": delta,
                "direction": "up" if delta > 0 else "down" if delta < 0 else "same",
            }
        )
    return comparison


def load_or_create_profile(
    db: DatabaseSession,
    student_id: str,
    *,
    all_persona_ids: Sequence[str] = KNOWN_PERSONA_IDS,
) -> StudentProfile:
    """Load a profile row without committing a new row implicitly."""

    row = db.get(Profile, student_id)
    if row is None:
        return StudentProfile(
            student_id=student_id,
            persona_records={persona_id: None for persona_id in all_persona_ids},
        )
    return StudentProfile(
        student_id=row.student_id,
        sessions_count=row.sessions_count,
        metrics_history=list(row.metrics_history),
        chronic_weaknesses=list(row.chronic_weaknesses),
        filler_blacklist=list(row.filler_blacklist),
        persona_records=dict(row.persona_records),
    )


def save_profile(db: DatabaseSession, profile: StudentProfile) -> Profile:
    """Stage a profile upsert in ``db``; the caller owns the transaction."""

    row = db.get(Profile, profile.student_id)
    if row is None:
        row = Profile(student_id=profile.student_id)
    row.sessions_count = profile.sessions_count
    row.metrics_history = list(profile.metrics_history)
    row.chronic_weaknesses = list(profile.chronic_weaknesses)
    row.filler_blacklist = list(profile.filler_blacklist)
    row.persona_records = dict(profile.persona_records)
    row.updated_at = utc_now()
    db.add(row)
    return row


def _session_weaknesses(snapshot: Mapping[str, Any]) -> set[str]:
    explicit = snapshot.get("weak_dimensions")
    weaknesses = (
        {str(item) for item in explicit}
        if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes))
        else set()
    )

    dimensions = snapshot.get("dimensions", snapshot.get("dims"))
    if isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)):
        for raw in dimensions:
            dimension = _as_dict(raw)
            score = _number(dimension.get("score"))
            maximum = _number(dimension.get("max"))
            name = dimension.get("name")
            if name and score is not None and maximum and score / maximum < DIMENSION_SCORE_THRESHOLD:
                weaknesses.add(str(name))

    for weakness, metric_name, predicate in _METRIC_WEAKNESS_RULES:
        value = _number(snapshot.get(metric_name))
        if value is not None and predicate(value):
            weaknesses.add(weakness)
    return weaknesses


def _updated_persona_records(
    existing: Mapping[str, Any],
    *,
    persona_id: str,
    total_score: int | float | None,
    all_persona_ids: Sequence[str],
) -> dict[str, Any]:
    records = {key: value for key, value in existing.items()}
    for known_id in all_persona_ids:
        records.setdefault(known_id, None)
    records.setdefault(persona_id, None)
    if total_score is None:
        return records

    old = records.get(persona_id)
    if isinstance(old, Mapping):
        old = old.get("best_score")
    old_score = _number(old)
    records[persona_id] = total_score if old_score is None else max(old_score, total_score)
    return records


def _filler_items(value: Any) -> list[tuple[str, float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items: list[tuple[str, float]] = []
    for item in value:
        if isinstance(item, Mapping):
            word = item.get("word")
            frequency = item.get("rate", item.get("frequency", item.get("percentage")))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
            word, frequency = item[0], item[1]
        else:
            continue
        number = _number(frequency)
        if word and number is not None:
            items.append((str(word), number))
    return items


def _as_dict(value: BaseModel | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("profile metrics and dimensions must be mappings or Pydantic models")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# A readable alias for call sites that prefer the longer name.
update_student_profile = update_profile


__all__ = [
    "CHRONIC_WINDOW",
    "KNOWN_PERSONA_IDS",
    "StudentProfile",
    "compare_with_previous_session",
    "cumulative_filler_top5",
    "detect_chronic_weaknesses",
    "load_or_create_profile",
    "previous_top3_advice",
    "save_profile",
    "update_profile",
    "update_student_profile",
]
