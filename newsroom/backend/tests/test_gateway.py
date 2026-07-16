from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import BaseModel

from app.llm.gateway import chat


class InterviewAnswer(BaseModel):
    answer: str
    score: int


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "deepseek",
                "LLM_API_KEY": "test-key",
                "LLM_BASE_URL": "https://llm.test/v1",
                "LLM_FAST_MODEL": "mock-fast",
                "LLM_SMART_MODEL": "mock-smart",
                "LLM_THINKING_TYPE": "enabled",
                "LLM_REASONING_EFFORT": "high",
                "LLM_LOG_DIR": self.temp_dir.name,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    async def run_chat(
        self,
        handler,
        *,
        trace_id: str,
        schema: type[BaseModel] | None = InterviewAnswer,
        stream: bool = False,
        thinking_disabled: bool = False,
    ):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://llm.test/v1",
        )
        with patch("app.llm.gateway._client", return_value=client):
            result = await chat(
                [{"role": "system", "content": "你是访谈助手。"}],
                model_tier="smart",
                schema=schema,
                trace_id=trace_id,
                stream=stream,
                thinking_disabled=thinking_disabled,
            )
            if stream:
                return [chunk async for chunk in result]
            return result

    async def test_valid_schema_response_is_parsed(self) -> None:
        result = await self.run_chat(
            lambda request: completion('{"answer":"有效","score":5}'),
            trace_id="valid",
        )

        self.assertEqual(result, InterviewAnswer(answer="有效", score=5))

    async def test_reasoning_options_are_sent_to_provider(self) -> None:
        request_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_body.update(json.loads(request.content))
            return completion('{"answer":"有效","score":5}')

        await self.run_chat(handler, trace_id="reasoning-options")

        self.assertEqual(request_body["thinking"], {"type": "enabled"})
        self.assertEqual(request_body["reasoning_effort"], "high")

    async def test_latency_sensitive_call_disables_thinking(self) -> None:
        request_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_body.update(json.loads(request.content))
            return completion('{"answer":"有效","score":5}')

        await self.run_chat(
            handler,
            trace_id="thinking-disabled",
            thinking_disabled=True,
        )

        self.assertEqual(request_body["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", request_body)

    async def test_markdown_json_fence_is_removed(self) -> None:
        result = await self.run_chat(
            lambda request: completion('```json\n{"answer":"围栏","score":4}\n```'),
            trace_id="fenced",
        )

        self.assertEqual(result, InterviewAnswer(answer="围栏", score=4))

    async def test_schema_error_is_sent_back_and_retried(self) -> None:
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return completion('{"answer":"缺少分数"}')
            return completion('{"answer":"已修正","score":3}')

        result = await self.run_chat(handler, trace_id="retry")

        self.assertEqual(result, InterviewAnswer(answer="已修正", score=3))
        self.assertEqual(len(requests), 2)
        retry_message = requests[1]["messages"][-1]["content"]
        self.assertIn("校验错误", retry_message)
        self.assertIn("score", retry_message)

    async def test_call_log_is_written(self) -> None:
        await self.run_chat(
            lambda request: completion('{"answer":"记录","score":5}'),
            trace_id="logged",
        )

        log_files = list(Path(self.temp_dir.name).rglob("logged-1.json"))
        self.assertEqual(len(log_files), 1)
        record = json.loads(log_files[0].read_text(encoding="utf-8"))
        self.assertEqual(
            set(record),
            {
                "messages",
                "raw_response",
                "parsed",
                "model",
                "latency_ms",
                "usage",
                "retry_count",
            },
        )
        self.assertEqual(record["model"], "mock-smart")
        self.assertEqual(record["parsed"], {"answer": "记录", "score": 5})

    async def test_stream_returns_async_chunks_and_logs(self) -> None:
        body = "\n\n".join(
            [
                'data: {"choices":[{"delta":{"content":"嘉"}}]}',
                'data: {"choices":[{"delta":{"content":"宾"}}]}',
                "data: [DONE]",
            ]
        )

        chunks = await self.run_chat(
            lambda request: httpx.Response(200, text=body),
            trace_id="streamed",
            schema=None,
            stream=True,
        )

        self.assertEqual(chunks, ["嘉", "宾"])
        log_files = list(Path(self.temp_dir.name).rglob("streamed-1.json"))
        self.assertEqual(len(log_files), 1)
        record = json.loads(log_files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["raw_response"], "嘉宾")
