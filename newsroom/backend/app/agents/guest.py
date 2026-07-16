from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.agents.guest_state import (
    GuestAction,
    decide_action,
    guard_comparison,
    record_revelation,
    update_guard_state,
)
from app.llm.exceptions import SchemaViolation
from app.llm.gateway import chat
from app.schemas import Dossier, Fact, FactState, GuestOutput


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
DeltaCallback = Callable[[str], Awaitable[None]]


class GuestAssessment(BaseModel):
    pressure: int = Field(ge=0, le=5)
    targeted_fact: str | None


async def generate_guest_response(
    dossier: Dossier,
    fact_states: Sequence[FactState],
    history: Sequence[Mapping[str, Any] | BaseModel],
    host_message: str,
    *,
    trace_id: str,
    on_delta: DeltaCallback | None = None,
) -> GuestOutput:
    """Generate one guest turn and mutate the supplied fact states."""

    normalized_message = host_message.strip()
    if not normalized_message:
        raise ValueError("host_message cannot be empty")
    _validate_fact_states(dossier, fact_states)

    history_data = [_jsonable(item) for item in history]
    shared_values = {
        "dossier_json": dossier.model_dump_json(indent=2),
        "fact_states_json": _fact_states_json(fact_states),
        "history_json": json.dumps(history_data, ensure_ascii=False, indent=2),
        "host_message_json": json.dumps(normalized_message, ensure_ascii=False),
    }

    try:
        assessment = await _assess_question(
            values=shared_values,
            trace_id=f"{trace_id}-assess",
        )
    except (SchemaViolation, httpx.HTTPError):
        return _fallback_output(
            dossier=dossier,
            fact=None,
            pressure=0,
            targeted_fact=None,
            action="deflect",
        )

    fact_by_id = {fact.id: fact for fact in dossier.facts}
    if assessment.targeted_fact not in fact_by_id:
        assessment.targeted_fact = None

    previous_target = _previous_targeted_fact(history_data)
    state = update_guard_state(
        fact_states,
        targeted_fact=assessment.targeted_fact,
        previous_targeted_fact=previous_target,
        pressure=assessment.pressure,
        initial_guards={fact.id: fact.guard for fact in dossier.facts},
        pressure_response=dossier.persona.pressure_response,
    )
    fact = fact_by_id.get(assessment.targeted_fact)
    action = decide_action(
        fact=fact,
        state=state,
        persona=dossier.persona,
        pressure=assessment.pressure,
    )

    decision = {
        "pressure": assessment.pressure,
        "targeted_fact": assessment.targeted_fact,
        "guard_current": state.guard_current if state is not None else None,
        "comparison": guard_comparison(assessment.pressure, state),
        "required_action": action,
    }
    response_values = {
        **shared_values,
        "fact_states_json": _fact_states_json(fact_states),
        "assessment_json": assessment.model_dump_json(indent=2),
        "decision_json": json.dumps(decision, ensure_ascii=False, indent=2),
    }

    if on_delta is not None:
        try:
            output = await _generate_streaming_answer(
                dossier=dossier,
                fact=fact,
                history_data=history_data,
                host_message=normalized_message,
                assessment=assessment,
                action=action,
                trace_id=f"{trace_id}-answer-stream",
                on_delta=on_delta,
            )
        except (SchemaViolation, httpx.HTTPError, TypeError, ValueError):
            output = _fallback_output(
                dossier=dossier,
                fact=fact,
                pressure=assessment.pressure,
                targeted_fact=assessment.targeted_fact,
                action=action,
            )
            await on_delta(output.speech)
    else:
        try:
            output = await _generate_answer(
                values=response_values,
                trace_id=f"{trace_id}-answer",
            )
        except (SchemaViolation, httpx.HTTPError):
            output = _fallback_output(
                dossier=dossier,
                fact=fact,
                pressure=assessment.pressure,
                targeted_fact=assessment.targeted_fact,
                action=action,
            )
        else:
            if not _matches_decision(output, assessment, action):
                output = _fallback_output(
                    dossier=dossier,
                    fact=fact,
                    pressure=assessment.pressure,
                    targeted_fact=assessment.targeted_fact,
                    action=action,
                )
            else:
                output = _enforce_surface_limits(output, dossier.persona.verbosity)

    record_revelation(state, action)
    return output


async def _assess_question(
    *,
    values: Mapping[str, str],
    trace_id: str,
) -> GuestAssessment:
    prompt = _render_prompt("guest_assess.md", values)
    result = await chat(
        [{"role": "system", "content": prompt}],
        model_tier="fast",
        schema=GuestAssessment,
        trace_id=trace_id,
        thinking_disabled=True,
    )
    if not isinstance(result, GuestAssessment):
        raise TypeError("Expected GuestAssessment from validated LLM gateway")
    return result


async def _generate_answer(
    *,
    values: Mapping[str, str],
    trace_id: str,
) -> GuestOutput:
    prompt = _render_prompt("guest.md", values)
    result = await chat(
        [{"role": "system", "content": prompt}],
        model_tier="smart",
        schema=GuestOutput,
        trace_id=trace_id,
        thinking_disabled=True,
    )
    if not isinstance(result, GuestOutput):
        raise TypeError("Expected GuestOutput from validated LLM gateway")
    return result


async def _generate_streaming_answer(
    *,
    dossier: Dossier,
    fact: Fact | None,
    history_data: Sequence[Mapping[str, Any]],
    host_message: str,
    assessment: GuestAssessment,
    action: GuestAction,
    trace_id: str,
    on_delta: DeltaCallback,
) -> GuestOutput:
    authorized_fact = _authorized_fact(fact, action)
    max_chars = _max_speech_chars(dossier.persona.verbosity)
    prompt = _render_prompt(
        "guest_stream.md",
        {
            "persona_json": dossier.persona.model_dump_json(indent=2),
            "surface_bio_json": json.dumps(dossier.surface_bio, ensure_ascii=False),
            "red_lines_json": json.dumps(dossier.red_lines, ensure_ascii=False),
            "history_json": json.dumps(
                list(history_data)[-8:],
                ensure_ascii=False,
                indent=2,
            ),
            "host_message_json": json.dumps(host_message, ensure_ascii=False),
            "decision_json": json.dumps(
                {
                    "pressure": assessment.pressure,
                    "targeted_fact": assessment.targeted_fact,
                    "required_action": action,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "authorized_fact_json": json.dumps(
                authorized_fact,
                ensure_ascii=False,
            ),
            "max_chars": str(max_chars),
        },
    )
    stream = await chat(
        [{"role": "system", "content": prompt}],
        model_tier="smart",
        schema=None,
        trace_id=trace_id,
        stream=True,
        thinking_disabled=True,
    )
    if not hasattr(stream, "__aiter__"):
        raise TypeError("Expected async token stream from LLM gateway")

    parts: list[str] = []
    emitted_chars = 0
    try:
        async for raw_chunk in stream:
            chunk = str(raw_chunk)
            remaining = max_chars - emitted_chars
            if remaining <= 0:
                continue
            visible_chunk = chunk[:remaining]
            if not visible_chunk:
                continue
            parts.append(visible_chunk)
            emitted_chars += len(visible_chunk)
            await on_delta(visible_chunk)
    except (httpx.HTTPError, ValueError):
        if not parts:
            raise

    speech = "".join(parts).strip()
    if not speech:
        raise ValueError("streaming guest returned empty speech")
    return GuestOutput(
        pressure=assessment.pressure,
        targeted_fact=assessment.targeted_fact,
        action=action,
        speech=speech,
        stage_direction=_stream_stage_direction(fact, action),
    )


def _authorized_fact(fact: Fact | None, action: GuestAction) -> str | None:
    if fact is None:
        return None
    if action == "reveal":
        return fact.content
    if action == "partial":
        return fact.partial
    return None


def _max_speech_chars(verbosity: int) -> int:
    return {1: 25, 2: 60, 3: 120, 4: 180, 5: 250}[verbosity]


def _stream_stage_direction(fact: Fact | None, action: GuestAction) -> str:
    if action == "tell" and fact is not None:
        return fact.tell.strip()[:40]
    if action == "deflect":
        return "稍作停顿"
    return "语气平稳"


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
        raise ValueError(f"fact_states must cover dossier facts; missing={missing}, extra={extra}")


def _previous_targeted_fact(history: Sequence[Mapping[str, Any]]) -> str | None:
    for turn in reversed(history):
        role = turn.get("role")
        if role is not None and str(role) not in {"guest", "TurnRole.guest"}:
            continue

        direct = turn.get("targeted_fact")
        if isinstance(direct, str):
            return direct

        for metadata_key in ("meta_json", "meta"):
            metadata = turn.get(metadata_key)
            if not isinstance(metadata, Mapping):
                continue
            nested = metadata.get("guest_output")
            if isinstance(nested, Mapping) and isinstance(
                nested.get("targeted_fact"), str
            ):
                return str(nested["targeted_fact"])
            if isinstance(metadata.get("targeted_fact"), str):
                return str(metadata["targeted_fact"])
    return None


def _matches_decision(
    output: GuestOutput,
    assessment: GuestAssessment,
    action: GuestAction,
) -> bool:
    return (
        output.pressure == assessment.pressure
        and output.targeted_fact == assessment.targeted_fact
        and output.action == action
    )


def _fallback_output(
    *,
    dossier: Dossier,
    fact: Fact | None,
    pressure: int,
    targeted_fact: str | None,
    action: GuestAction,
) -> GuestOutput:
    if action == "reveal" and fact is not None:
        speech = fact.content
    elif action == "partial" and fact is not None and fact.partial is not None:
        speech = fact.partial
    elif action == "tell":
        speech = "……那阵子的事，我一时记不清了。"
    elif dossier.persona.pressure_response == "inverse" and pressure >= 4:
        speech = "我不接受这种提问方式。"
    else:
        speech = "请把问题说具体些。"

    return _enforce_surface_limits(
        GuestOutput(
            pressure=pressure,
            targeted_fact=targeted_fact,
            action=action,
            speech=speech,
            stage_direction="稍作停顿",
        ),
        dossier.persona.verbosity,
    )


def _enforce_surface_limits(output: GuestOutput, verbosity: int) -> GuestOutput:
    speech = output.speech.strip()
    if verbosity == 1 and len(speech) > 25:
        speech = speech[:25].rstrip("，、；：")
    stage_direction = output.stage_direction.strip()[:40]
    return output.model_copy(
        update={"speech": speech, "stage_direction": stage_direction}
    )


def _fact_states_json(fact_states: Sequence[FactState]) -> str:
    return json.dumps(
        [state.model_dump(mode="json") for state in fact_states],
        ensure_ascii=False,
        indent=2,
    )


def _jsonable(value: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _render_prompt(filename: str, values: Mapping[str, str]) -> str:
    prompt = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    for placeholder, value in values.items():
        prompt = prompt.replace(f"{{{placeholder}}}", value)
    return prompt


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one guest interview turn")
    parser.add_argument("--dossier", type=Path, required=True)
    parser.add_argument("--host", required=True, help="The host's current question")
    parser.add_argument("--fact-states", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--trace-id", default="guest-cli")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    dossier = Dossier.model_validate_json(args.dossier.read_text(encoding="utf-8"))
    if args.fact_states:
        states_payload = json.loads(args.fact_states.read_text(encoding="utf-8"))
        fact_states = [FactState.model_validate(item) for item in states_payload]
    else:
        fact_states = [
            FactState(
                fact_id=fact.id,
                guard_current=fact.guard,
                consecutive_probes=0,
                revealed="hidden",
            )
            for fact in dossier.facts
        ]
    history = (
        json.loads(args.history.read_text(encoding="utf-8"))
        if args.history
        else []
    )
    output = await generate_guest_response(
        dossier,
        fact_states,
        history,
        args.host,
        trace_id=args.trace_id,
    )
    print(output.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
