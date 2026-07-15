from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.tools.search import _mcp_search, _results_from_value, web_search


class TavilyMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_real_credentials_never_falls_back_to_fixture(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("app.tools.search.read_env_file", return_value={}),
        ):
            with self.assertRaisesRegex(ValueError, "required for real search"):
                await web_search("测试选题")

    def test_invalid_or_empty_results_are_filtered(self) -> None:
        results = _results_from_value(
            {
                "results": [
                    {"title": "空摘要", "url": "https://example.test/empty"},
                    {"title": "错误协议", "url": "file:///tmp/a", "content": "内容"},
                    {
                        "title": "有效来源",
                        "url": "https://example.test/valid",
                        "content": "真实摘要",
                    },
                ]
            },
            max_results=5,
        )

        self.assertEqual([result.title for result in results], ["有效来源"])

    async def test_mcp_handshake_lists_and_calls_search_tool(self) -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            method = body["method"]
            methods.append(method)
            if method == "initialize":
                return httpx.Response(
                    200,
                    headers={"Mcp-Session-Id": "session-1"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"protocolVersion": "2025-06-18"},
                    },
                )
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "tools/list":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {
                            "tools": [
                                {
                                    "name": "tavily_search",
                                    "inputSchema": {
                                        "properties": {
                                            "query": {"type": "string"},
                                            "max_results": {"type": "number"},
                                        }
                                    },
                                }
                            ]
                        },
                    },
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    "event: message\n"
                    "data: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(
                                            {
                                                "results": [
                                                    {
                                                        "title": "真实来源",
                                                        "url": "https://example.test/source",
                                                        "content": "来源摘要",
                                                    }
                                                ]
                                            },
                                            ensure_ascii=False,
                                        ),
                                    }
                                ]
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                ),
            )

        results = await _mcp_search(
            "测试选题",
            max_results=5,
            url="https://mcp.test/mcp",
            api_key="test-token",
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(
            methods,
            ["initialize", "notifications/initialized", "tools/list", "tools/call"],
        )
        self.assertEqual(results[0].title, "真实来源")
        self.assertEqual(results[0].summary, "来源摘要")
