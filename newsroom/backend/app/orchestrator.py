from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlalchemy.sql import func
from sqlmodel import Session as DatabaseSession
from sqlmodel import select

from app.agents.director import Director, DirectorHint
from app.agents.guest import generate_guest_response
from app.agents.writer import persisted_grounding_errors
from app.memory.profile import (
    compare_with_previous_session,
    load_or_create_profile,
    previous_top3_advice,
    save_profile,
    update_profile,
)
from app.models import (
    FactState as FactStateRow,
    Report,
    RevealedState,
    Scenario,
    Session as InterviewSession,
    Turn,
    TurnRole,
    utc_now,
)
from app.schemas import Dossier, FactState, GuestOutput
from app.tools.stenographer import Metrics, calculate_metrics


DEFAULT_BRIEFING_SECONDS = 60.0
DEFAULT_DURATION_SECONDS = 8 * 60.0
DEFAULT_WRAPPING_SECONDS = 60.0
EVENT_HISTORY_LIMIT = 200
SSE_HEARTBEAT_SECONDS = 15.0

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "IDLE"
    BRIEFING = "BRIEFING"
    LIVE = "LIVE"
    WRAPPING = "WRAPPING"
    REVIEW = "REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[SessionState, SessionState] = {
    SessionState.IDLE: SessionState.BRIEFING,
    SessionState.BRIEFING: SessionState.LIVE,
    SessionState.LIVE: SessionState.WRAPPING,
    SessionState.WRAPPING: SessionState.REVIEW,
    SessionState.REVIEW: SessionState.DONE,
}


class OrchestratorError(Exception):
    """Base class for errors that can be translated by the HTTP layer."""


class ScenarioNotFound(OrchestratorError):
    pass


class PersonaNotFound(OrchestratorError):
    pass


class ScenarioNotGrounded(OrchestratorError):
    pass


class SessionNotFound(OrchestratorError):
    pass


class ReportNotFound(OrchestratorError):
    pass


class InvalidSessionState(OrchestratorError):
    pass


class GuestAgent(Protocol):
    async def respond(
        self,
        *,
        dossier: Dossier,
        fact_states: list[FactState],
        history: list[dict[str, Any]],
        host_message: str,
        trace_id: str,
    ) -> GuestOutput: ...


class DirectorAgent(Protocol):
    async def observe(
        self,
        *,
        dossier: Dossier,
        fact_states: Sequence[FactState],
        history: list[dict[str, Any]],
        host_message: str,
        guest_output: GuestOutput | Awaitable[GuestOutput],
        trace_id: str,
        chronic_weaknesses: Sequence[str],
    ) -> DirectorHint | str | None: ...


class Guest:
    """Adapter that exposes the existing guest function as ``guest.respond``."""

    async def respond(
        self,
        *,
        dossier: Dossier,
        fact_states: list[FactState],
        history: list[dict[str, Any]],
        host_message: str,
        trace_id: str,
    ) -> GuestOutput:
        return await generate_guest_response(
            dossier,
            fact_states,
            history,
            host_message,
            trace_id=trace_id,
        )


class TranscriptTurn(BaseModel):
    idx: int
    role: TurnRole
    content: str


class Transcript(BaseModel):
    turns: list[TranscriptTurn]


class JudgeResult(BaseModel):
    summary: str
    metrics: dict[str, int | float]


class Stenographer:
    """Deterministic recorder used until a richer recorder agent is introduced."""

    async def transcribe(self, turns: list[dict[str, Any]]) -> Transcript:
        return Transcript(
            turns=[
                TranscriptTurn(
                    idx=turn["idx"],
                    role=turn["role"],
                    content=turn["content"],
                )
                for turn in turns
            ]
        )


class Judge:
    """Minimal deterministic review so every session can complete REVIEW."""

    async def review(
        self,
        *,
        dossier: Dossier,
        transcript: Transcript,
        metrics: Metrics | None = None,
        trace_id: str = "judge",
        previous_top3_advice: Sequence[str] = (),
    ) -> JudgeResult:
        host_turns = [turn for turn in transcript.turns if turn.role == TurnRole.host]
        guest_turns = [turn for turn in transcript.turns if turn.role == TurnRole.guest]
        host_characters = sum(len(turn.content) for turn in host_turns)
        guest_characters = sum(len(turn.content) for turn in guest_turns)
        total_characters = host_characters + guest_characters
        return JudgeResult(
            summary=f"《{dossier.topic}》访谈复盘已生成。",
            metrics={
                "host_turns": len(host_turns),
                "guest_turns": len(guest_turns),
                "host_talk_ratio": (
                    round(host_characters / total_characters, 4)
                    if total_characters
                    else 0.0
                ),
            },
        )


class StenographerAgent(Protocol):
    async def transcribe(self, turns: list[dict[str, Any]]) -> Transcript: ...


class JudgeAgent(Protocol):
    async def review(
        self,
        *,
        dossier: Dossier,
        transcript: Transcript,
        metrics: Metrics,
        trace_id: str,
        previous_top3_advice: Sequence[str],
    ) -> BaseModel: ...


class ServerEvent(BaseModel):
    id: int | None = None
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        event_id = f"id: {self.id}\n" if self.id is not None else ""
        return f"{event_id}event: {self.event}\ndata: {payload}\n\n"


class CreateSessionRequest(BaseModel):
    scenario_id: str = Field(min_length=1)
    persona_id: str = Field(min_length=1)
    student_id: str = Field(default="demo-student", min_length=1)


class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    request_id: str | None = Field(default=None, min_length=8, max_length=100)


class SessionSnapshot(BaseModel):
    id: str
    scenario_id: str
    persona_id: str
    student_id: str
    state: SessionState
    topic: str
    surface_bio: str
    persona_name: str
    facts_total: int
    duration_seconds: int
    briefing_seconds: int
    report_id: str | None = None
    turn_count: int = 0
    error_message: str | None = None


class TurnResponse(BaseModel):
    session_id: str
    state: SessionState
    guest: GuestOutput
    director_hint: str | None


class SessionHistory(BaseModel):
    turns: list[dict[str, Any]]
    facts_found: int
    found_fact_ids: list[str]


@dataclass
class _SessionRuntime:
    id: str
    dossier: Dossier
    persona_id: str
    student_id: str
    chronic_weaknesses: list[str]
    previous_advice: list[str]
    duration_seconds: float
    briefing_seconds: float
    wrapping_seconds: float
    state: SessionState = SessionState.IDLE
    state_history: list[SessionState] = field(
        default_factory=lambda: [SessionState.IDLE]
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: set[asyncio.Queue[ServerEvent]] = field(default_factory=set)
    briefing_deadline: datetime | None = None
    live_deadline: datetime | None = None
    clock_task: asyncio.Task[None] | None = None
    review_task: asyncio.Task[None] | None = None
    wrapping_hint_sent: bool = False
    report_id: str | None = None
    error_message: str | None = None
    event_sequence: int = 0
    event_history: deque[ServerEvent] = field(
        default_factory=lambda: deque(maxlen=EVENT_HISTORY_LIMIT)
    )


class Orchestrator:
    """A hand-written state machine for one or more interview sessions."""

    def __init__(
        self,
        engine: Engine,
        *,
        guest: GuestAgent | None = None,
        director: DirectorAgent | None = None,
        stenographer: StenographerAgent | None = None,
        judge: JudgeAgent | None = None,
        briefing_seconds: float = DEFAULT_BRIEFING_SECONDS,
        duration_seconds: float = DEFAULT_DURATION_SECONDS,
        wrapping_seconds: float = DEFAULT_WRAPPING_SECONDS,
        clock_interval: float = 1.0,
    ) -> None:
        if briefing_seconds < 0:
            raise ValueError("briefing_seconds must be non-negative")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if wrapping_seconds < 0:
            raise ValueError("wrapping_seconds must be non-negative")
        if clock_interval <= 0:
            raise ValueError("clock_interval must be positive")

        self.engine = engine
        self.guest = guest or Guest()
        self.director = director or Director()
        self.stenographer = stenographer or Stenographer()
        self.judge = judge or Judge()
        self.briefing_seconds = briefing_seconds
        self.duration_seconds = duration_seconds
        self.wrapping_seconds = wrapping_seconds
        self.clock_interval = clock_interval
        self._sessions: dict[str, _SessionRuntime] = {}
        self._registry_lock = asyncio.Lock()

    async def create_session(
        self,
        scenario_id: str,
        persona_id: str,
        student_id: str = "demo-student",
    ) -> SessionSnapshot:
        with DatabaseSession(self.engine) as db:
            scenario = db.get(Scenario, scenario_id)
            if scenario is None:
                raise ScenarioNotFound(f"scenario not found: {scenario_id}")
            dossier = Dossier.model_validate(scenario.dossier_json)
            if persisted_grounding_errors(dossier):
                raise ScenarioNotGrounded(
                    "该场景缺少可核验来源，不能开始真实访谈"
                )
            if dossier.persona.id != persona_id:
                raise PersonaNotFound(
                    f"persona {persona_id!r} is not available for scenario {scenario_id!r}"
                )

            profile = load_or_create_profile(db, student_id)
            save_profile(db, profile)
            session_id = uuid4().hex
            db.add(
                InterviewSession(
                    id=session_id,
                    scenario_id=scenario_id,
                    profile_id=student_id,
                    persona_id=persona_id,
                    state=SessionState.IDLE.value,
                    duration_seconds=self.duration_seconds,
                    briefing_seconds=self.briefing_seconds,
                    wrapping_seconds=self.wrapping_seconds,
                )
            )
            for fact in dossier.facts:
                db.add(
                    FactStateRow(
                        session_id=session_id,
                        fact_id=fact.id,
                        guard_current=fact.guard,
                        consecutive_probes=0,
                        revealed=RevealedState.hidden,
                    )
                )
            db.commit()

        runtime = _SessionRuntime(
            id=session_id,
            dossier=dossier,
            persona_id=persona_id,
            student_id=student_id,
            chronic_weaknesses=list(profile.chronic_weaknesses),
            previous_advice=previous_top3_advice(profile),
            duration_seconds=self.duration_seconds,
            briefing_seconds=self.briefing_seconds,
            wrapping_seconds=self.wrapping_seconds,
        )
        runtime.briefing_deadline = utc_now() + timedelta(seconds=runtime.briefing_seconds)
        async with self._registry_lock:
            self._sessions[session_id] = runtime

        self._transition(runtime, SessionState.BRIEFING)
        runtime.clock_task = asyncio.create_task(
            self._run_clock(runtime),
            name=f"interview-clock-{session_id}",
        )
        return self._snapshot(runtime)

    async def submit_turn(
        self,
        session_id: str,
        text: str,
        request_id: str | None = None,
    ) -> TurnResponse:
        runtime = self._runtime(session_id)
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("text cannot be empty")

        async with runtime.lock:
            if runtime.state not in {SessionState.LIVE, SessionState.WRAPPING}:
                raise InvalidSessionState(
                    f"cannot submit a turn while session is {runtime.state.value}"
                )

            history = self._load_turns(session_id)
            fact_states = self._load_fact_states(session_id)
            trace_id = f"session-{session_id}-turn-{len(history)}"

            # Start both adapters together. The real director awaits guest_task so
            # it can inspect GuestOutput, then uses the fast model for its cue.
            guest_task = asyncio.create_task(
                self.guest.respond(
                    dossier=runtime.dossier,
                    fact_states=fact_states,
                    history=history,
                    host_message=normalized_text,
                    trace_id=f"{trace_id}-guest",
                )
            )
            guest_output, director_output = await asyncio.gather(
                guest_task,
                self.director.observe(
                    dossier=runtime.dossier,
                    fact_states=fact_states,
                    history=history,
                    host_message=normalized_text,
                    guest_output=guest_task,
                    trace_id=f"{trace_id}-director",
                    chronic_weaknesses=runtime.chronic_weaknesses,
                ),
            )
            if not isinstance(guest_output, GuestOutput):
                raise TypeError("guest.respond() must return GuestOutput")
            if isinstance(director_output, DirectorHint):
                director_hint = (
                    director_output.hint if director_output.should_speak else None
                )
                director_event = (
                    {
                        "text": director_output.hint,
                        "source": "director",
                        "urgency": director_output.urgency,
                        "type": director_output.type,
                    }
                    if director_output.should_speak
                    else None
                )
            elif director_output is None or isinstance(director_output, str):
                # Keep compatibility with injected Task 4 test doubles.
                director_hint = director_output
                director_event = (
                    {"text": director_output, "source": "director"}
                    if director_output
                    else None
                )
            else:
                raise TypeError(
                    "director.observe() must return DirectorHint, str, or None"
                )

            rows: list[tuple[TurnRole, str, dict[str, Any]]] = [
                (TurnRole.host, normalized_text, {"request_id": request_id}),
                (
                    TurnRole.guest,
                    guest_output.speech,
                    {
                        "guest_output": guest_output.model_dump(mode="json"),
                        "request_id": request_id,
                    },
                ),
            ]
            if director_hint:
                director_metadata = {
                    "request_id": request_id,
                    **(director_event or {}),
                }
                rows.append((TurnRole.director, director_hint, director_metadata))
            indexes = self._persist_turns_and_fact_states(
                session_id,
                rows,
                fact_states,
            )
            guest_turn_idx = indexes[1]

            self._publish(
                runtime,
                "guest_delta",
                {
                    "delta": guest_output.speech,
                    "request_id": request_id,
                    "turn_idx": guest_turn_idx,
                },
            )
            self._publish(
                runtime,
                "guest_done",
                {
                    **guest_output.model_dump(mode="json"),
                    "request_id": request_id,
                    "turn_idx": guest_turn_idx,
                },
            )
            if director_hint:
                self._publish(
                    runtime,
                    "director_hint",
                    director_event or {"text": director_hint, "source": "director"},
                )

            return TurnResponse(
                session_id=session_id,
                state=runtime.state,
                guest=guest_output,
                director_hint=director_hint,
            )

    async def end_session(self, session_id: str) -> SessionSnapshot:
        runtime = self._runtime(session_id)
        async with runtime.lock:
            if runtime.state == SessionState.DONE:
                return self._snapshot(runtime)

            if not any(
                turn["role"] == TurnRole.host for turn in self._load_turns(session_id)
            ):
                raise InvalidSessionState("至少完成一轮有效采访后才能结束")

            if runtime.state == SessionState.FAILED:
                runtime.error_message = None
                self._set_state(runtime, SessionState.REVIEW)
                runtime.review_task = None

            if runtime.review_task is None:
                # An early end still traverses the declared linear state graph.
                if runtime.state == SessionState.BRIEFING:
                    self._transition(runtime, SessionState.LIVE)
                if runtime.state == SessionState.LIVE:
                    self._enter_wrapping(runtime)
                if runtime.state == SessionState.WRAPPING:
                    self._transition(runtime, SessionState.REVIEW)
                if runtime.state != SessionState.REVIEW:
                    raise InvalidSessionState(
                        f"cannot end a session while it is {runtime.state.value}"
                    )
                runtime.review_task = asyncio.create_task(
                    self._run_review(runtime),
                    name=f"interview-review-{session_id}",
                )
                if (
                    runtime.clock_task is not None
                    and runtime.clock_task is not asyncio.current_task()
                ):
                    runtime.clock_task.cancel()
            review_task = runtime.review_task

        await review_task
        return self._snapshot(runtime)

    def get_snapshot(self, session_id: str) -> SessionSnapshot:
        return self._snapshot(self._runtime(session_id))

    def get_history(self, session_id: str) -> SessionHistory:
        self._runtime(session_id)
        turns = self._load_turns(session_id)
        states = self._load_fact_states(session_id)
        found = [state.fact_id for state in states if state.revealed == "full"]
        return SessionHistory(
            turns=[
                {
                    "idx": turn["idx"],
                    "role": turn["role"].value,
                    "content": turn["content"],
                    "meta_json": turn["meta_json"],
                    "ts": turn["ts"],
                }
                for turn in turns
            ],
            facts_found=len(found),
            found_fact_ids=found,
        )

    def get_state_history(self, session_id: str) -> list[SessionState]:
        return list(self._runtime(session_id).state_history)

    def get_review(self, report_id: str) -> dict[str, Any]:
        with DatabaseSession(self.engine) as db:
            report = db.get(Report, report_id)
        if report is None:
            raise ReportNotFound(f"report not found: {report_id}")
        public_review = report.content_json.get("public_review")
        if not isinstance(public_review, dict):
            raise ReportNotFound(f"report has no public review: {report_id}")
        return public_review

    async def events(
        self,
        session_id: str,
        *,
        after_event_id: int | None = None,
    ) -> AsyncIterator[ServerEvent]:
        runtime = self._runtime(session_id)
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        runtime.subscribers.add(queue)
        if after_event_id is not None:
            for event in runtime.event_history:
                if event.id is not None and event.id > after_event_id:
                    queue.put_nowait(event)
        initial_state: dict[str, Any] = {"state": runtime.state.value}
        if runtime.report_id:
            initial_state["report_id"] = runtime.report_id
        if runtime.error_message:
            initial_state["error_message"] = runtime.error_message
        queue.put_nowait(ServerEvent(event="state_change", data=initial_state))
        clock = self._clock_payload(runtime)
        if clock is not None:
            queue.put_nowait(ServerEvent(event="clock", data=clock))

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                except TimeoutError:
                    yield ServerEvent(event="ping", data={})
                    continue
                yield event
                if event.event == "state_change" and event.data.get("state") == "DONE":
                    return
        finally:
            runtime.subscribers.discard(queue)

    async def _run_clock(self, runtime: _SessionRuntime) -> None:
        try:
            while True:
                deadline = runtime.briefing_deadline
                if deadline is None:
                    return
                remaining = _seconds_until(deadline)
                async with runtime.lock:
                    if runtime.state != SessionState.BRIEFING:
                        break
                    self._publish(
                        runtime,
                        "clock",
                        self._clock_data("briefing", remaining),
                    )
                    if remaining <= 0:
                        runtime.live_deadline = (
                            utc_now() + timedelta(seconds=runtime.duration_seconds)
                        )
                        self._transition(runtime, SessionState.LIVE)
                        break
                await asyncio.sleep(min(self.clock_interval, max(remaining, 0.001)))

            while True:
                deadline = runtime.live_deadline
                if deadline is None:
                    return
                remaining = _seconds_until(deadline)
                async with runtime.lock:
                    if runtime.state not in {
                        SessionState.LIVE,
                        SessionState.WRAPPING,
                    }:
                        return
                    self._publish(
                        runtime,
                        "clock",
                        self._clock_data("live", remaining),
                    )
                    if (
                        runtime.state == SessionState.LIVE
                        and remaining <= runtime.wrapping_seconds
                    ):
                        self._enter_wrapping(runtime)
                    expired = remaining <= 0
                if expired:
                    try:
                        await self.end_session(runtime.id)
                    except InvalidSessionState as error:
                        async with runtime.lock:
                            self._fail_runtime(runtime, str(error))
                    return
                await asyncio.sleep(min(self.clock_interval, max(remaining, 0.001)))
        except asyncio.CancelledError:
            return

    async def _run_review(self, runtime: _SessionRuntime) -> None:
        try:
            await self._run_review_inner(runtime)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Review failed session_id=%s", runtime.id)
            async with runtime.lock:
                self._fail_runtime(runtime, "复盘生成失败，可点击结束采访重试")

    async def _run_review_inner(self, runtime: _SessionRuntime) -> None:
        turns = self._load_turns(runtime.id)
        transcript = await self.stenographer.transcribe(turns)
        if not isinstance(transcript, Transcript):
            raise TypeError("stenographer.transcribe() must return Transcript")

        fact_states = self._load_fact_states(runtime.id)
        facts_by_id = {fact.id: fact for fact in runtime.dossier.facts}
        metric_fact_states = [
            {
                **state.model_dump(mode="json"),
                "juiciness": facts_by_id[state.fact_id].juiciness,
            }
            for state in fact_states
        ]
        metrics = calculate_metrics(turns, metric_fact_states)

        review_kwargs: dict[str, Any] = {
            "dossier": runtime.dossier,
            "transcript": transcript,
        }
        review_parameters = inspect.signature(self.judge.review).parameters
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in review_parameters.values()
        )
        if accepts_kwargs or "metrics" in review_parameters:
            review_kwargs["metrics"] = metrics
        if accepts_kwargs or "trace_id" in review_parameters:
            review_kwargs["trace_id"] = f"session-{runtime.id}-judge"
        if accepts_kwargs or "previous_top3_advice" in review_parameters:
            review_kwargs["previous_top3_advice"] = runtime.previous_advice
        result = await self.judge.review(**review_kwargs)
        if not isinstance(result, BaseModel):
            raise TypeError("judge.review() must return a Pydantic model")

        result_data = result.model_dump(mode="json")
        dimensions = result_data.get("dims", [])
        if not isinstance(dimensions, list):
            dimensions = []
        advice = result_data.get("top3_advice", [])
        if not isinstance(advice, list):
            advice = []
        total_score = result_data.get("total")
        if not isinstance(total_score, (int, float)) or isinstance(total_score, bool):
            total_score = None

        report_id = uuid4().hex
        with DatabaseSession(self.engine) as db:
            profile = load_or_create_profile(db, runtime.student_id)
            comparison = compare_with_previous_session(profile, dimensions)
            updated_profile = update_profile(
                profile,
                metrics,
                runtime.persona_id,
                total_score,
                top3_advice=advice,
                dimensions=dimensions,
                session_id=runtime.id,
            )
            save_profile(db, updated_profile)
            public_review = _build_public_review(
                report_id=report_id,
                runtime=runtime,
                turns=turns,
                fact_states=fact_states,
                metrics=metrics,
                review=result_data,
                comparison=comparison,
            )
            db.add(
                Report(
                    id=report_id,
                    session_id=runtime.id,
                    content_json={
                        "transcript": transcript.model_dump(mode="json"),
                        "metrics": metrics.model_dump(mode="json"),
                        "review": result_data,
                        "comparison": comparison,
                        "public_review": public_review,
                    },
                )
            )
            session = db.get(InterviewSession, runtime.id)
            if session is None:
                raise SessionNotFound(f"session not found: {runtime.id}")
            session.ended_at = utc_now()
            session.report_id = report_id
            session.error_message = None
            db.add(session)
            db.commit()

        async with runtime.lock:
            runtime.report_id = report_id
            runtime.error_message = None
            self._publish(runtime, "report_ready", {"report_id": report_id})
            self._transition(runtime, SessionState.DONE)

    def _enter_wrapping(self, runtime: _SessionRuntime) -> None:
        self._transition(runtime, SessionState.WRAPPING)
        if runtime.wrapping_hint_sent:
            return
        runtime.wrapping_hint_sent = True
        hint = "准备收尾"
        self._persist_turns_and_fact_states(
            runtime.id,
            [(TurnRole.director, hint, {"source": "clock"})],
            None,
        )
        self._publish(
            runtime,
            "director_hint",
            {"text": hint, "source": "clock"},
        )

    def _transition(self, runtime: _SessionRuntime, target: SessionState) -> None:
        expected = ALLOWED_TRANSITIONS.get(runtime.state)
        if expected != target:
            raise InvalidSessionState(
                f"invalid transition: {runtime.state.value} -> {target.value}"
            )
        self._set_state(runtime, target)

    def _set_state(self, runtime: _SessionRuntime, target: SessionState) -> None:
        runtime.state = target
        runtime.state_history.append(target)
        self._persist_runtime(runtime)
        payload: dict[str, Any] = {"state": target.value}
        if runtime.report_id:
            payload["report_id"] = runtime.report_id
        if runtime.error_message:
            payload["error_message"] = runtime.error_message
        self._publish(runtime, "state_change", payload)

    def _fail_runtime(self, runtime: _SessionRuntime, message: str) -> None:
        runtime.error_message = message
        self._set_state(runtime, SessionState.FAILED)
        self._publish(runtime, "session_error", {"message": message})

    def _persist_runtime(self, runtime: _SessionRuntime) -> None:
        with DatabaseSession(self.engine) as db:
            row = db.get(InterviewSession, runtime.id)
            if row is None:
                raise SessionNotFound(f"session not found: {runtime.id}")
            row.state = runtime.state.value
            row.persona_id = runtime.persona_id
            row.briefing_deadline = runtime.briefing_deadline
            row.live_deadline = runtime.live_deadline
            row.duration_seconds = runtime.duration_seconds
            row.briefing_seconds = runtime.briefing_seconds
            row.wrapping_seconds = runtime.wrapping_seconds
            row.report_id = runtime.report_id
            row.error_message = runtime.error_message
            db.add(row)
            db.commit()

    def _load_turns(self, session_id: str) -> list[dict[str, Any]]:
        with DatabaseSession(self.engine) as db:
            rows = list(
                db.exec(
                    select(Turn).where(Turn.session_id == session_id).order_by(Turn.idx)
                )
            )
        return [
            {
                "session_id": row.session_id,
                "idx": row.idx,
                "role": row.role,
                "content": row.content,
                "meta_json": row.meta_json,
                "ts": row.ts.isoformat(),
            }
            for row in rows
        ]

    def _load_fact_states(self, session_id: str) -> list[FactState]:
        with DatabaseSession(self.engine) as db:
            rows = list(
                db.exec(
                    select(FactStateRow)
                    .where(FactStateRow.session_id == session_id)
                    .order_by(FactStateRow.fact_id)
                )
            )
        return [
            FactState(
                fact_id=row.fact_id,
                guard_current=row.guard_current,
                consecutive_probes=row.consecutive_probes,
                revealed=row.revealed.value,
            )
            for row in rows
        ]

    def _persist_turns_and_fact_states(
        self,
        session_id: str,
        rows: Sequence[tuple[TurnRole, str, dict[str, Any]]],
        fact_states: Sequence[FactState] | None,
    ) -> list[int]:
        with DatabaseSession(self.engine) as db:
            last_idx = db.exec(
                select(func.max(Turn.idx)).where(Turn.session_id == session_id)
            ).one()
            next_idx = 0 if last_idx is None else int(last_idx) + 1
            for offset, (role, content, metadata) in enumerate(rows):
                db.add(
                    Turn(
                        session_id=session_id,
                        idx=next_idx + offset,
                        role=role,
                        content=content,
                        meta_json=metadata,
                    )
                )

            if fact_states is not None:
                for state in fact_states:
                    row = db.get(FactStateRow, (session_id, state.fact_id))
                    if row is None:
                        raise KeyError(
                            f"missing fact state {state.fact_id} for session {session_id}"
                        )
                    row.guard_current = state.guard_current
                    row.consecutive_probes = state.consecutive_probes
                    row.revealed = RevealedState(state.revealed)
                    db.add(row)
            db.commit()
        return [next_idx + offset for offset in range(len(rows))]

    def _publish(
        self,
        runtime: _SessionRuntime,
        event: str,
        data: dict[str, Any],
    ) -> None:
        runtime.event_sequence += 1
        message = ServerEvent(
            id=runtime.event_sequence,
            event=event,
            data=data,
        )
        runtime.event_history.append(message)
        for queue in tuple(runtime.subscribers):
            queue.put_nowait(message)

    def _clock_payload(self, runtime: _SessionRuntime) -> dict[str, Any] | None:
        if runtime.state == SessionState.BRIEFING and runtime.briefing_deadline:
            return self._clock_data(
                "briefing",
                _seconds_until(runtime.briefing_deadline),
            )
        if runtime.state in {SessionState.LIVE, SessionState.WRAPPING}:
            if runtime.live_deadline is not None:
                return self._clock_data("live", _seconds_until(runtime.live_deadline))
        return None

    @staticmethod
    def _clock_data(phase: str, remaining: float) -> dict[str, Any]:
        return {
            "phase": phase,
            "remaining_seconds": max(0, math.ceil(remaining)),
        }

    def _runtime(self, session_id: str) -> _SessionRuntime:
        try:
            return self._sessions[session_id]
        except KeyError:
            runtime = self._recover_runtime(session_id)
            self._sessions[session_id] = runtime
            return runtime

    def _recover_runtime(self, session_id: str) -> _SessionRuntime:
        with DatabaseSession(self.engine) as db:
            row = db.get(InterviewSession, session_id)
            if row is None:
                raise SessionNotFound(f"session not found: {session_id}")
            scenario = db.get(Scenario, row.scenario_id)
            if scenario is None:
                raise ScenarioNotFound(f"scenario not found: {row.scenario_id}")
            dossier = Dossier.model_validate(scenario.dossier_json)
            if persisted_grounding_errors(dossier):
                raise ScenarioNotGrounded(
                    "该场景缺少可核验来源，不能恢复真实访谈"
                )
            student_id = row.profile_id or "local-student"
            profile = load_or_create_profile(db, student_id)
            try:
                state = SessionState(row.state)
            except ValueError:
                state = SessionState.FAILED
            runtime = _SessionRuntime(
                id=row.id,
                dossier=dossier,
                persona_id=row.persona_id or dossier.persona.id,
                student_id=student_id,
                chronic_weaknesses=list(profile.chronic_weaknesses),
                previous_advice=previous_top3_advice(profile),
                duration_seconds=row.duration_seconds,
                briefing_seconds=row.briefing_seconds,
                wrapping_seconds=row.wrapping_seconds,
                state=state,
                state_history=[state],
                briefing_deadline=row.briefing_deadline,
                live_deadline=row.live_deadline,
                report_id=row.report_id,
                error_message=row.error_message,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return runtime
        if runtime.state in {
            SessionState.BRIEFING,
            SessionState.LIVE,
            SessionState.WRAPPING,
        }:
            runtime.clock_task = loop.create_task(
                self._run_clock(runtime),
                name=f"interview-clock-{session_id}-recovered",
            )
        elif runtime.state == SessionState.REVIEW:
            runtime.review_task = loop.create_task(
                self._run_review(runtime),
                name=f"interview-review-{session_id}-recovered",
            )
        return runtime

    def _snapshot(self, runtime: _SessionRuntime) -> SessionSnapshot:
        turn_count = sum(
            turn["role"] == TurnRole.host for turn in self._load_turns(runtime.id)
        )
        return SessionSnapshot(
            id=runtime.id,
            scenario_id=runtime.dossier.scenario_id,
            persona_id=runtime.persona_id,
            student_id=runtime.student_id,
            state=runtime.state,
            topic=runtime.dossier.topic,
            surface_bio=runtime.dossier.surface_bio,
            persona_name=runtime.dossier.persona.name,
            facts_total=len(runtime.dossier.facts),
            duration_seconds=round(runtime.duration_seconds),
            briefing_seconds=round(runtime.briefing_seconds),
            report_id=runtime.report_id,
            turn_count=turn_count,
            error_message=runtime.error_message,
        )


def _build_public_review(
    *,
    report_id: str,
    runtime: _SessionRuntime,
    turns: Sequence[Mapping[str, Any]],
    fact_states: Sequence[FactState],
    metrics: Metrics,
    review: Mapping[str, Any],
    comparison: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Translate internal report artifacts into the review page contract."""

    raw_dimensions = review.get("dims", [])
    dimensions = [
        {
            "name": str(item.get("name", "")),
            "score": item.get("score", 0),
            "max": item.get("max", 1),
        }
        for item in raw_dimensions
        if isinstance(item, Mapping)
    ] if isinstance(raw_dimensions, Sequence) else []
    total = review.get("total", sum(item["score"] for item in dimensions))
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        total = 0

    revealed_by_id = {state.fact_id: state.revealed for state in fact_states}
    dossier = [
        {
            "id": fact.id,
            "content": fact.content,
            "juiciness": fact.juiciness,
            "status": "found" if revealed_by_id.get(fact.id) == "full" else "missed",
            "unlockHint": fact.unlock_hint,
            "sources": [
                {
                    "url": evidence.source_url,
                    "quote": evidence.quote,
                }
                for evidence in fact.evidence
            ],
        }
        for fact in runtime.dossier.facts
    ]

    rounds: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for turn in turns:
        role = turn.get("role")
        if hasattr(role, "value"):
            role = role.value
        role = str(role).rsplit(".", 1)[-1]
        if role == "host":
            if active is not None:
                rounds.append(active)
            active = {
                "round": len(rounds) + 1,
                "timestamp": _relative_turn_time(turns, turn),
                "host": str(turn.get("content", "")),
                "guest": "",
                "studentAction": "本轮后没有新的提问",
                "followed": None,
                "_targetedFact": None,
                "_directorType": None,
            }
        elif role == "guest" and active is not None:
            active["guest"] = str(turn.get("content", ""))
            metadata = turn.get("meta_json", {})
            if isinstance(metadata, Mapping):
                guest_output = metadata.get("guest_output", {})
                if isinstance(guest_output, Mapping) and guest_output.get("stage_direction"):
                    active["stageDirection"] = str(guest_output["stage_direction"])
                if isinstance(guest_output, Mapping):
                    active["_targetedFact"] = guest_output.get("targeted_fact")
        elif role == "director" and active is not None:
            metadata = turn.get("meta_json", {})
            if isinstance(metadata, Mapping) and metadata.get("source") == "clock":
                continue
            active["director"] = str(turn.get("content", ""))
            if isinstance(metadata, Mapping):
                active["_directorType"] = metadata.get("type")
    if active is not None:
        rounds.append(active)

    for index, round_data in enumerate(rounds):
        if round_data.get("director") and index + 1 < len(rounds):
            next_round = rounds[index + 1]
            round_data["studentAction"] = str(next_round.get("host", ""))
            round_data["followed"] = _director_followed(round_data, next_round)
        round_data.pop("_targetedFact", None)
        round_data.pop("_directorType", None)

    host_share = (
        metrics.host_talk_ratio / (1 + metrics.host_talk_ratio)
        if metrics.host_talk_ratio >= 0
        else 0.0
    )
    objective_metrics = [
        {
            "name": "开放式问题比例",
            "value": f"{round(metrics.open_ratio * 100)}%",
            "ideal": "≥ 60%",
            "inRange": metrics.open_ratio >= 0.6,
        },
        {
            "name": "有效追问率",
            "value": f"{round(metrics.probe_rate * 100)}%",
            "ideal": "≥ 50%",
            "inRange": metrics.probe_rate >= 0.5,
        },
        {
            "name": "主持人话语占比",
            "value": f"{round(host_share * 100)}%",
            "ideal": "20–35%",
            "inRange": 0.2 <= host_share <= 0.35,
        },
        {
            "name": "平均问题长度",
            "value": f"{metrics.avg_q_len:g} 字",
            "ideal": "≤ 28 字",
            "inRange": metrics.avg_q_len <= 28,
        },
        {
            "name": "多问合一",
            "value": f"{metrics.multi_q_count} 次",
            "ideal": "0 次",
            "inRange": metrics.multi_q_count == 0,
        },
    ]
    if metrics.filler_top:
        filler, rate = metrics.filler_top[0]
        objective_metrics.append(
            {
                "name": f"口头禅「{filler}」",
                "value": f"{rate:g}/句",
                "ideal": "≤ 0.2/句",
                "inRange": rate <= 0.2,
            }
        )

    raw_advice = review.get("top3_advice", [])
    advice = (
        [str(item) for item in raw_advice]
        if isinstance(raw_advice, Sequence) and not isinstance(raw_advice, (str, bytes))
        else []
    )
    return {
        "id": report_id,
        "topic": runtime.dossier.topic,
        "personaName": runtime.dossier.persona.name,
        "total": total,
        "duration": _interview_duration(turns),
        "dimensions": dimensions,
        "rounds": rounds,
        "dossier": dossier,
        "metrics": objective_metrics,
        "advice": advice,
        "comparison": [dict(item) for item in comparison],
    }


def _director_followed(
    current_round: Mapping[str, Any],
    next_round: Mapping[str, Any],
) -> bool | None:
    """Only make a followed/not-followed claim when code can verify it."""

    hint_type = current_round.get("_directorType")
    current_target = current_round.get("_targetedFact")
    next_target = next_round.get("_targetedFact")
    next_host = str(next_round.get("host", ""))
    if hint_type == "追问" and current_target is not None:
        return current_target == next_target
    if hint_type == "换角度" and current_target is not None:
        return current_target != next_target and any(
            marker in next_host
            for marker in ("为什么", "怎么", "如何", "什么", "哪些", "请谈", "请说明")
        )
    if hint_type == "别问了" and current_target is not None:
        return current_target != next_target
    if hint_type == "收尾":
        return any(
            marker in next_host
            for marker in ("最后", "总结", "还有什么", "最后一个问题")
        )
    return None


def _relative_turn_time(
    turns: Sequence[Mapping[str, Any]],
    turn: Mapping[str, Any],
) -> str:
    if not turns:
        return "00:00"
    try:
        start = datetime.fromisoformat(str(turns[0].get("ts")))
        current = datetime.fromisoformat(str(turn.get("ts")))
    except ValueError:
        return "00:00"
    seconds = max(0, round((current - start).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _interview_duration(turns: Sequence[Mapping[str, Any]]) -> str:
    if len(turns) < 2:
        return "00:00"
    try:
        start = datetime.fromisoformat(str(turns[0].get("ts")))
        end = datetime.fromisoformat(str(turns[-1].get("ts")))
    except ValueError:
        return "00:00"
    seconds = max(0, round((end - start).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _seconds_until(deadline: datetime) -> float:
    normalized = deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=UTC)
    return (normalized - utc_now()).total_seconds()


def build_router(orchestrator: Orchestrator) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post(
        "/session",
        response_model=SessionSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_session(payload: CreateSessionRequest) -> SessionSnapshot:
        try:
            return await orchestrator.create_session(
                payload.scenario_id,
                payload.persona_id,
                payload.student_id,
            )
        except (ScenarioNotFound, PersonaNotFound) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ScenarioNotGrounded as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/session/{session_id}/stream")
    async def stream_session(session_id: str, request: Request) -> StreamingResponse:
        try:
            last_event_id = request.headers.get("last-event-id")
            after_event_id = int(last_event_id) if last_event_id else None
            events = orchestrator.events(
                session_id,
                after_event_id=after_event_id,
            )
            orchestrator.get_snapshot(session_id)
        except (SessionNotFound, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        async def encoded_events() -> AsyncIterator[str]:
            async for event in events:
                yield event.encode()

        return StreamingResponse(
            encoded_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/session/{session_id}", response_model=SessionSnapshot)
    async def get_session(session_id: str) -> SessionSnapshot:
        try:
            return orchestrator.get_snapshot(session_id)
        except SessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/session/{session_id}/history", response_model=SessionHistory)
    async def get_session_history(session_id: str) -> SessionHistory:
        try:
            return orchestrator.get_history(session_id)
        except SessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/session/{session_id}/turn", response_model=TurnResponse)
    async def submit_turn(session_id: str, payload: TurnRequest) -> TurnResponse:
        try:
            return await orchestrator.submit_turn(
                session_id,
                payload.text,
                payload.request_id,
            )
        except SessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except InvalidSessionState as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/session/{session_id}/end", response_model=SessionSnapshot)
    async def end_session(session_id: str) -> SessionSnapshot:
        try:
            return await orchestrator.end_session(session_id)
        except SessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except InvalidSessionState as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/review/{report_id}")
    async def get_review(report_id: str) -> dict[str, Any]:
        try:
            return orchestrator.get_review(report_id)
        except ReportNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
