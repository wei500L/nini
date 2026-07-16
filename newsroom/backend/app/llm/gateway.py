from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.config import BACKEND_ROOT, LLMConfig, ModelTier, load_config
from app.llm.exceptions import SchemaViolation


MAX_SCHEMA_RETRIES = 2
PROMPTS_DIR = BACKEND_ROOT / "app" / "prompts"
_FENCED_JSON = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


async def chat(
    messages: Sequence[Mapping[str, Any]],
    *,
    model_tier: ModelTier,
    schema: type[BaseModel] | None = None,
    trace_id: str,
    stream: bool = False,
    thinking_disabled: bool = False,
) -> BaseModel | str | AsyncIterator[str]:
    config = load_config()
    model = config.model_for(model_tier)
    prepared_messages = _copy_messages(messages)

    if schema is not None:
        if stream:
            raise ValueError("schema and stream=True cannot be used together")
        prepared_messages = _append_schema_instruction(prepared_messages, schema)

    if stream:
        return _stream_chat(
            prepared_messages,
            model=model,
            trace_id=trace_id,
            config=config,
            thinking_disabled=thinking_disabled,
        )

    async with _client(config) as client:
        retry_messages = prepared_messages
        last_raw_response = ""
        last_error = ""

        for retry_count in range(MAX_SCHEMA_RETRIES + 1):
            request_started = perf_counter()
            try:
                response, latency_ms = await _post_chat(
                    client,
                    retry_messages,
                    model=model,
                    stream=False,
                    config=config,
                    thinking_disabled=thinking_disabled,
                )
            except httpx.HTTPError as error:
                await _write_log(
                    config=config,
                    trace_id=trace_id,
                    messages=retry_messages,
                    raw_response=str(error),
                    parsed=None,
                    model=model,
                    latency_ms=round((perf_counter() - request_started) * 1000),
                    usage={},
                    retry_count=retry_count,
                )
                raise

            if response.is_error:
                await _write_log(
                    config=config,
                    trace_id=trace_id,
                    messages=retry_messages,
                    raw_response=response.text,
                    parsed=None,
                    model=model,
                    latency_ms=latency_ms,
                    usage={},
                    retry_count=retry_count,
                )
                response.raise_for_status()

            payload = response.json()
            last_raw_response = _response_content(payload)
            usage = payload.get("usage") or {}

            if schema is None:
                await _write_log(
                    config=config,
                    trace_id=trace_id,
                    messages=retry_messages,
                    raw_response=last_raw_response,
                    parsed=None,
                    model=model,
                    latency_ms=latency_ms,
                    usage=usage,
                    retry_count=retry_count,
                )
                return last_raw_response

            try:
                parsed = schema.model_validate_json(_strip_json_fence(last_raw_response))
            except ValidationError as error:
                last_error = str(error)
                await _write_log(
                    config=config,
                    trace_id=trace_id,
                    messages=retry_messages,
                    raw_response=last_raw_response,
                    parsed=None,
                    model=model,
                    latency_ms=latency_ms,
                    usage=usage,
                    retry_count=retry_count,
                )
                if retry_count == MAX_SCHEMA_RETRIES:
                    break
                retry_messages = _messages_for_retry(
                    retry_messages,
                    raw_response=last_raw_response,
                    validation_error=last_error,
                )
                continue

            await _write_log(
                config=config,
                trace_id=trace_id,
                messages=retry_messages,
                raw_response=last_raw_response,
                parsed=parsed.model_dump(mode="json"),
                model=model,
                latency_ms=latency_ms,
                usage=usage,
                retry_count=retry_count,
            )
            return parsed

    raise SchemaViolation(
        f"LLM response failed schema validation after {MAX_SCHEMA_RETRIES} retries: {last_error}",
        raw_response=last_raw_response,
        retry_count=MAX_SCHEMA_RETRIES,
    )


def _client(config: LLMConfig) -> httpx.AsyncClient:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    if config.provider == "claude":
        headers["x-api-key"] = config.api_key
        headers["anthropic-version"] = "2023-06-01"
    return httpx.AsyncClient(
        base_url=config.base_url,
        headers=headers,
        timeout=config.timeout_seconds,
    )


async def _post_chat(
    client: httpx.AsyncClient,
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    stream: bool,
    config: LLMConfig,
    thinking_disabled: bool,
) -> tuple[httpx.Response, int]:
    started = perf_counter()
    response = await client.post(
        "/chat/completions",
        json=_request_body(
            messages,
            model=model,
            stream=stream,
            config=config,
            thinking_disabled=thinking_disabled,
        ),
    )
    return response, round((perf_counter() - started) * 1000)


def _stream_chat(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    trace_id: str,
    config: LLMConfig,
    thinking_disabled: bool,
) -> AsyncIterator[str]:
    async def generate() -> AsyncIterator[str]:
        raw_parts: list[str] = []
        usage: Mapping[str, Any] = {}
        started = perf_counter()

        try:
            async with _client(config) as client:
                async with client.stream(
                    "POST",
                    "/chat/completions",
                    json=_request_body(
                        messages,
                        model=model,
                        stream=True,
                        config=config,
                        thinking_disabled=thinking_disabled,
                    ),
                ) as response:
                    if response.is_error:
                        error_body = (await response.aread()).decode(errors="replace")
                        raw_parts.append(error_body)
                        response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        event = json.loads(data)
                        if event.get("usage"):
                            usage = event["usage"]
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content")
                        if content:
                            raw_parts.append(content)
                            yield content
        except httpx.HTTPError as error:
            if not raw_parts:
                raw_parts.append(str(error))
            raise
        finally:
            await _write_log(
                config=config,
                trace_id=trace_id,
                messages=messages,
                raw_response="".join(raw_parts),
                parsed=None,
                model=model,
                latency_ms=round((perf_counter() - started) * 1000),
                usage=usage,
                retry_count=0,
            )

    return generate()


def _request_body(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    stream: bool,
    config: LLMConfig,
    thinking_disabled: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": stream,
    }
    if thinking_disabled and config.thinking_type:
        body["thinking"] = {"type": "disabled"}
    elif config.thinking_type:
        body["thinking"] = {"type": config.thinking_type}
    if config.reasoning_effort and not thinking_disabled:
        body["reasoning_effort"] = config.reasoning_effort
    return body


def _copy_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(message) for message in messages]


def _append_schema_instruction(
    messages: list[dict[str, Any]], schema: type[BaseModel]
) -> list[dict[str, Any]]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    template = _load_prompt("json-schema-output.md")
    instruction = template.replace("{json_schema}", schema_json).strip()

    for message in messages:
        if message.get("role") == "system":
            existing = str(message.get("content", "")).rstrip()
            message["content"] = f"{existing}\n\n{instruction}" if existing else instruction
            return messages

    messages.insert(0, {"role": "system", "content": instruction})
    return messages


def _messages_for_retry(
    messages: Sequence[Mapping[str, Any]],
    *,
    raw_response: str,
    validation_error: str,
) -> list[dict[str, Any]]:
    retry_prompt = _load_prompt("schema-retry.md").replace(
        "{validation_error}", validation_error
    )
    return [
        *_copy_messages(messages),
        {"role": "assistant", "content": raw_response},
        {"role": "user", "content": retry_prompt.strip()},
    ]


def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _strip_json_fence(raw_response: str) -> str:
    stripped = raw_response.strip()
    match = _FENCED_JSON.fullmatch(stripped)
    return match.group(1).strip() if match else stripped


def _response_content(payload: Mapping[str, Any]) -> str:
    return str(payload["choices"][0]["message"]["content"])


async def _write_log(
    *,
    config: LLMConfig,
    trace_id: str,
    messages: Sequence[Mapping[str, Any]],
    raw_response: str,
    parsed: Mapping[str, Any] | None,
    model: str,
    latency_ms: int,
    usage: Mapping[str, Any],
    retry_count: int,
) -> Path:
    record = {
        "messages": list(messages),
        "raw_response": raw_response,
        "parsed": parsed,
        "model": model,
        "latency_ms": latency_ms,
        "usage": dict(usage),
        "retry_count": retry_count,
    }
    return await asyncio.to_thread(
        _write_log_sync,
        config.log_dir,
        trace_id,
        record,
    )


def _write_log_sync(log_root: Path, trace_id: str, record: Mapping[str, Any]) -> Path:
    day_dir = log_root / date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_trace_id = re.sub(r"[^A-Za-z0-9_.-]", "_", trace_id)

    sequence = 1
    while True:
        path = day_dir / f"{safe_trace_id}-{sequence}.json"
        try:
            with path.open("x", encoding="utf-8") as log_file:
                json.dump(record, log_file, ensure_ascii=False, indent=2)
            return path
        except FileExistsError:
            sequence += 1
