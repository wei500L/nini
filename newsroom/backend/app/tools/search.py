from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel

from app.llm.config import BACKEND_ROOT, read_env_file


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "search_results.json"
MCP_PROTOCOL_VERSION = "2025-06-18"
SearchMode = Literal["auto", "real", "fixture"]


class SearchResult(BaseModel):
    title: str
    url: str
    summary: str


async def web_search(
    topic: str,
    *,
    max_results: int = 5,
    fixture_path: Path | None = None,
) -> list[SearchResult]:
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise ValueError("topic cannot be empty")
    if not 3 <= max_results <= 5:
        raise ValueError("max_results must be between 3 and 5")

    # An explicit fixture is a test-only override and never depends on local secrets.
    if fixture_path is not None:
        return _read_fixture(fixture_path, max_results=max_results)

    env_file = read_env_file(BACKEND_ROOT / ".env")

    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, env_file.get(name, default))

    mode_value = env("TAVILY_MODE", "auto").strip().lower()
    if mode_value not in {"auto", "real", "fixture"}:
        raise ValueError("TAVILY_MODE must be auto, real, or fixture")
    mode = cast(SearchMode, mode_value)
    if mode == "fixture":
        return _read_fixture(DEFAULT_FIXTURE_PATH, max_results=max_results)

    api_key = env("TAVILY_API_KEY").strip()
    if not api_key:
        if mode == "real":
            raise ValueError("TAVILY_API_KEY is required when TAVILY_MODE=real")
        return _read_fixture(DEFAULT_FIXTURE_PATH, max_results=max_results)

    mcp_url = env("TAVILY_MCP_URL").strip()
    if mcp_url:
        return await _mcp_search(
            normalized_topic,
            max_results=max_results,
            url=mcp_url,
            api_key=api_key,
        )

    base_url = env("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/")
    return await _rest_search(
        normalized_topic,
        max_results=max_results,
        base_url=base_url,
        api_key=api_key,
    )


async def _rest_search(
    topic: str,
    *,
    max_results: int,
    base_url: str,
    api_key: str,
) -> list[SearchResult]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{base_url}/search",
            json={
                "api_key": api_key,
                "query": topic,
                "search_depth": "basic",
                "max_results": max_results,
            },
        )
        response.raise_for_status()

    return _results_from_value(response.json(), max_results=max_results)


async def _mcp_search(
    topic: str,
    *,
    max_results: int,
    url: str,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[SearchResult]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        headers=headers,
        timeout=30,
        transport=transport,
    ) as client:
        initialize = await client.post(
            url,
            json=_rpc_request(
                1,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "newsroom", "version": "0.1.0"},
                },
            ),
        )
        initialize_payload = _mcp_payload(initialize)
        _raise_mcp_error(initialize_payload)

        session_id = initialize.headers.get("Mcp-Session-Id")
        if session_id:
            client.headers["Mcp-Session-Id"] = session_id
        client.headers["MCP-Protocol-Version"] = str(
            initialize_payload.get("result", {}).get(
                "protocolVersion",
                MCP_PROTOCOL_VERSION,
            )
        )

        initialized = await client.post(
            url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        initialized.raise_for_status()

        tools_response = await client.post(
            url,
            json=_rpc_request(2, "tools/list", {}),
        )
        tools_payload = _mcp_payload(tools_response)
        _raise_mcp_error(tools_payload)
        tool = _select_search_tool(tools_payload)

        call_response = await client.post(
            url,
            json=_rpc_request(
                3,
                "tools/call",
                {
                    "name": tool["name"],
                    "arguments": _search_arguments(tool, topic, max_results),
                },
            ),
        )
        call_payload = _mcp_payload(call_response)
        _raise_mcp_error(call_payload)

    result = call_payload.get("result", {})
    if isinstance(result, Mapping) and result.get("isError"):
        raise RuntimeError("Tavily MCP search tool returned an error")
    return _results_from_value(result, max_results=max_results)


def _rpc_request(
    request_id: int,
    method: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": dict(params),
    }


def _mcp_payload(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MCP response must be a JSON object")
        return payload

    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if not raw_data:
            continue
        value = json.loads(raw_data)
        if isinstance(value, dict):
            events.append(value)
    if not events:
        raise ValueError("MCP event stream did not contain a JSON-RPC response")
    return events[-1]


def _raise_mcp_error(payload: Mapping[str, Any]) -> None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return
    message = str(error.get("message", "unknown MCP error"))
    raise RuntimeError(f"Tavily MCP error: {message}")


def _select_search_tool(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    tools = result.get("tools") if isinstance(result, Mapping) else None
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
        raise ValueError("Tavily MCP tools/list returned no tools")

    candidates = [dict(tool) for tool in tools if isinstance(tool, Mapping)]
    for preferred in ("tavily_search", "search"):
        for tool in candidates:
            if str(tool.get("name", "")).casefold() == preferred:
                return tool
    for tool in candidates:
        if "search" in str(tool.get("name", "")).casefold():
            return tool
    raise ValueError("Tavily MCP server exposes no search tool")


def _search_arguments(
    tool: Mapping[str, Any],
    topic: str,
    max_results: int,
) -> dict[str, Any]:
    schema = tool.get("inputSchema")
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    supported = set(properties) if isinstance(properties, Mapping) else set()

    arguments: dict[str, Any] = {"query": topic}
    if supported and "query" not in supported:
        if "q" in supported:
            arguments = {"q": topic}
        else:
            raise ValueError("Tavily MCP search tool has no query argument")
    if not supported or "max_results" in supported:
        arguments["max_results"] = max_results
    if not supported or "search_depth" in supported:
        arguments["search_depth"] = "basic"
    return arguments


def _results_from_value(value: Any, *, max_results: int) -> list[SearchResult]:
    items = list(_result_items(value))
    results = [
        SearchResult(
            title=str(item.get("title") or item.get("name") or item["url"]),
            url=str(item["url"]),
            summary=str(
                item.get("summary")
                or item.get("content")
                or item.get("snippet")
                or item.get("raw_content")
                or ""
            ),
        )
        for item in items[:max_results]
    ]
    if not results:
        raise ValueError("Tavily returned no usable search results")
    return results


def _result_items(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, str):
        try:
            return _result_items(json.loads(value))
        except json.JSONDecodeError:
            return []
    if isinstance(value, Mapping):
        if isinstance(value.get("url"), str):
            return [value]
        items: list[Mapping[str, Any]] = []
        for key in ("structuredContent", "content", "results", "data", "result"):
            if key in value:
                items.extend(_result_items(value[key]))
        return items
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items: list[Mapping[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping) and item.get("type") == "text":
                items.extend(_result_items(item.get("text", "")))
            else:
                items.extend(_result_items(item))
        return items
    return []


def _read_fixture(path: Path, *, max_results: int) -> list[SearchResult]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = [SearchResult.model_validate(item) for item in payload["results"]]
    return results[:max_results]
