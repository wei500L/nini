from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from app.llm.exceptions import SchemaViolation
from app.llm.gateway import chat
from app.schemas import DirectorHint, Dossier, FactState, GuestOutput


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
LEAK_SIMILARITY_THRESHOLD = 0.4


async def generate_director_hint(
    dossier: Dossier,
    fact_states: Sequence[FactState],
    history: Sequence[Mapping[str, Any] | BaseModel],
    host_message: str,
    guest_output: GuestOutput,
    *,
    trace_id: str,
    chronic_weaknesses: Sequence[str] = (),
) -> DirectorHint:
    """Generate one earpiece hint and enforce all director hard constraints."""

    normalized_message = host_message.strip()
    if not normalized_message:
        raise ValueError("host_message cannot be empty")
    _validate_fact_states(dossier, fact_states)

    history_data = [_jsonable(turn) for turn in history]
    mandatory_reasons = _mandatory_reasons(
        history_data,
        normalized_message,
        guest_output,
    )
    on_track = _is_correct_follow_up(history_data, guest_output)
    throttled = _is_throttled(history_data)

    # A good, increasingly forceful follow-up should not be disturbed. The explicit
    # urgency=3 conditions take precedence because they are defined as mandatory.
    if on_track and not mandatory_reasons:
        return _silent_hint()

    prompt = _render_prompt(
        "director.md",
        {
            "dossier_json": dossier.model_dump_json(indent=2),
            "fact_states_json": json.dumps(
                [state.model_dump(mode="json") for state in fact_states],
                ensure_ascii=False,
                indent=2,
            ),
            "history_json": json.dumps(
                _recent_four_rounds(history_data),
                ensure_ascii=False,
                indent=2,
            ),
            "host_message_json": json.dumps(
                normalized_message,
                ensure_ascii=False,
            ),
            "guest_output_json": guest_output.model_dump_json(indent=2),
            "code_constraints_json": json.dumps(
                {
                    "mandatory_reasons": mandatory_reasons,
                    "must_speak": bool(mandatory_reasons),
                    "must_be_silent_for_correct_follow_up": on_track,
                    "throttled": throttled,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "chronic_weaknesses_json": json.dumps(
                list(chronic_weaknesses),
                ensure_ascii=False,
                indent=2,
            ),
        },
    )

    try:
        result = await chat(
            [{"role": "system", "content": prompt}],
            model_tier="fast",
            schema=DirectorHint,
            trace_id=trace_id,
        )
    except SchemaViolation:
        result = (
            _mandatory_fallback(mandatory_reasons)
            if mandatory_reasons
            else _silent_hint()
        )

    if not isinstance(result, DirectorHint):
        raise TypeError("Expected DirectorHint from validated LLM gateway")

    candidate = result
    if mandatory_reasons:
        if not candidate.should_speak or not candidate.hint.strip():
            candidate = _mandatory_fallback(mandatory_reasons)
        else:
            candidate = candidate.model_copy(update={"urgency": 3})
    elif throttled and candidate.urgency < 3:
        return _silent_hint(urgency=candidate.urgency)

    if not candidate.should_speak:
        return _silent_hint(urgency=candidate.urgency)

    candidate = candidate.model_copy(update={"hint": candidate.hint.strip()})
    if hint_leaks_unrevealed_fact(candidate.hint, dossier, fact_states):
        # Discard the unsafe candidate. A mandatory event gets one deterministic,
        # content-free replacement so action=tell still produces an urgent cue.
        if mandatory_reasons:
            fallback = _mandatory_fallback(mandatory_reasons)
            if not hint_leaks_unrevealed_fact(fallback.hint, dossier, fact_states):
                return fallback
        return _silent_hint(urgency=candidate.urgency)
    return candidate


def hint_leaks_unrevealed_fact(
    hint: str,
    dossier: Dossier,
    fact_states: Sequence[FactState],
) -> bool:
    """Return whether a hint is too similar to any not-fully-revealed fact."""

    revealed_by_id = {state.fact_id: state.revealed for state in fact_states}
    normalized_hint = _normalize_for_similarity(hint)
    if not normalized_hint:
        return False

    for fact in dossier.facts:
        if revealed_by_id.get(fact.id) == "full":
            continue
        normalized_content = _normalize_for_similarity(fact.content)
        similarity = SequenceMatcher(
            None,
            normalized_hint,
            normalized_content,
        ).ratio()
        if similarity > LEAK_SIMILARITY_THRESHOLD:
            return True
    return False


class Director:
    """Director agent adapter that accepts a ready output or the guest task."""

    async def observe(
        self,
        *,
        dossier: Dossier,
        fact_states: Sequence[FactState],
        history: Sequence[Mapping[str, Any] | BaseModel],
        host_message: str,
        guest_output: GuestOutput | Awaitable[GuestOutput],
        trace_id: str,
        chronic_weaknesses: Sequence[str] = (),
    ) -> DirectorHint:
        if isinstance(guest_output, GuestOutput):
            resolved_guest_output = guest_output
        else:
            resolved_guest_output = await guest_output
        return await generate_director_hint(
            dossier,
            fact_states,
            history,
            host_message,
            resolved_guest_output,
            trace_id=trace_id,
            chronic_weaknesses=chronic_weaknesses,
        )


def _mandatory_reasons(
    history: Sequence[Mapping[str, Any]],
    host_message: str,
    guest_output: GuestOutput,
) -> list[str]:
    reasons: list[str] = []
    if guest_output.action == "tell":
        reasons.append("guest_action_tell")

    previous_host = _last_turn_content(history, "host")
    if previous_host and _is_closed_question(previous_host) and _is_closed_question(
        host_message
    ):
        reasons.append("two_consecutive_closed_questions")

    previous_guest = _last_turn_content(history, "guest")
    if previous_guest and _contains_unmentioned_content_word(
        host_message,
        previous_guest,
    ):
        reasons.append("host_introduced_unheard_content_word")
    return reasons


def _is_correct_follow_up(
    history: Sequence[Mapping[str, Any]],
    guest_output: GuestOutput,
) -> bool:
    previous = _last_guest_output(history)
    return bool(
        previous
        and guest_output.targeted_fact is not None
        and guest_output.targeted_fact == previous.get("targeted_fact")
        and guest_output.pressure > _as_int(previous.get("pressure"), default=5)
    )


def _is_throttled(history: Sequence[Mapping[str, Any]]) -> bool:
    last_director_index: int | None = None
    for index, turn in enumerate(history):
        if _role(turn) == "director" and str(turn.get("content", "")).strip():
            last_director_index = index
    if last_director_index is None:
        return False

    host_turns_since = sum(
        1
        for turn in history[last_director_index + 1 :]
        if _role(turn) == "host"
    )
    # The current host message is not in history. Counts 0 and 1 therefore mean
    # the first and second rounds after the previous cue; the third is allowed.
    return host_turns_since < 2


def _is_closed_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    open_markers = (
        "为什么",
        "怎么",
        "如何",
        "什么",
        "哪些",
        "哪个",
        "谁",
        "哪里",
        "何时",
        "多少",
        "请谈",
        "谈谈",
        "请讲",
        "请说明",
        "请解释",
    )
    if any(marker in normalized for marker in open_markers):
        return False
    closed_markers = (
        "是否",
        "是不是",
        "有没有",
        "能否",
        "可否",
        "会不会",
        "对不对",
        "同不同意",
    )
    return (
        any(marker in normalized for marker in closed_markers)
        or normalized.rstrip("？?").endswith("吗")
    )


_CONTENT_SPLIT_RE = re.compile(
    r"(?:刚才|已经|还是|是否|是不是|有没有|能否|可否|会不会|请问|"
    r"为什么|怎么|如何|什么|哪些|哪个|哪里|何时|多少|时候|"
    r"你们|他们|她们|我们|这个|那个|这些|那些|自己|"
    r"你|您|我|他|她|它|请|觉得|认为|表示|提到|说道|说|"
    r"解释|说明|回答|告诉|谈谈|具体|问题|事情|一下|"
    r"的是|的话|了吗|呢|吗|吧|啊|呀|的|了|过|在|和|与|及|"
    r"就|也|都|又|再|还|那|并|而|但|被|把|给|对|从|到|是|有)"
)
_LATIN_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+|\d+(?:\.\d+)?%?")
_CJK_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _contains_unmentioned_content_word(question: str, guest_speech: str) -> bool:
    terms = _content_terms(question)
    normalized_guest = re.sub(r"\s+", "", guest_speech).casefold()
    return any(term.casefold() not in normalized_guest for term in terms)


def _content_terms(text: str) -> set[str]:
    terms = {term for term in _LATIN_TERM_RE.findall(text) if len(term) >= 2}
    for chunk in _CJK_CHUNK_RE.findall(text):
        for term in _CONTENT_SPLIT_RE.split(chunk):
            term = term.strip()
            if len(term) >= 2:
                terms.add(term)
    return terms


def _recent_four_rounds(
    history: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    for turn in history:
        role = _role(turn)
        if role == "host":
            rounds.append({"host": turn})
        elif role == "guest":
            if not rounds or "guest" in rounds[-1]:
                rounds.append({})
            rounds[-1]["guest"] = turn
    return rounds[-4:]


def _last_guest_output(
    history: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for turn in reversed(history):
        if _role(turn) != "guest":
            continue
        direct = turn.get("guest_output")
        if isinstance(direct, Mapping):
            return direct
        for metadata_key in ("meta_json", "meta"):
            metadata = turn.get(metadata_key)
            if not isinstance(metadata, Mapping):
                continue
            nested = metadata.get("guest_output")
            if isinstance(nested, Mapping):
                return nested
        if "pressure" in turn and "targeted_fact" in turn:
            return turn
    return None


def _last_turn_content(
    history: Sequence[Mapping[str, Any]],
    expected_role: str,
) -> str | None:
    for turn in reversed(history):
        if _role(turn) == expected_role:
            content = str(turn.get("content", "")).strip()
            if content:
                return content
    return None


def _role(turn: Mapping[str, Any]) -> str:
    value = turn.get("role", "")
    if hasattr(value, "value"):
        value = value.value
    return str(value).rsplit(".", 1)[-1].lower()


def _mandatory_fallback(reasons: Sequence[str]) -> DirectorHint:
    if "guest_action_tell" in reasons:
        return DirectorHint(
            should_speak=True,
            urgency=3,
            type="追问",
            hint="追问刚才的反常",
        )
    if "two_consecutive_closed_questions" in reasons:
        return DirectorHint(
            should_speak=True,
            urgency=3,
            type="换角度",
            hint="换个开放式问法",
        )
    return DirectorHint(
        should_speak=True,
        urgency=3,
        type="别问了",
        hint="先接住他刚才的话",
    )


def _silent_hint(*, urgency: int = 1) -> DirectorHint:
    return DirectorHint(
        should_speak=False,
        urgency=urgency,
        type="别问了",
        hint="",
    )


def _validate_fact_states(
    dossier: Dossier,
    fact_states: Sequence[FactState],
) -> None:
    dossier_ids = {fact.id for fact in dossier.facts}
    state_ids = [state.fact_id for state in fact_states]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("fact_states contains duplicate fact_id values")
    if set(state_ids) != dossier_ids:
        missing = sorted(dossier_ids - set(state_ids))
        extra = sorted(set(state_ids) - dossier_ids)
        raise ValueError(
            f"fact_states must cover dossier facts; missing={missing}, extra={extra}"
        )


def _normalize_for_similarity(text: str) -> str:
    return "".join(character.casefold() for character in text if character.isalnum())


def _jsonable(value: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(cast(str, value))
    except (TypeError, ValueError):
        return default


def _render_prompt(filename: str, values: Mapping[str, str]) -> str:
    prompt = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    for placeholder, value in values.items():
        prompt = prompt.replace(f"{{{placeholder}}}", value)
    return prompt
