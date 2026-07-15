from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.llm.config import BACKEND_ROOT, read_env_file


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "search_results.json"


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
    if not 3 <= max_results <= 5:
        raise ValueError("max_results must be between 3 and 5")

    env_file = read_env_file(BACKEND_ROOT / ".env")
    api_key = os.environ.get("TAVILY_API_KEY", env_file.get("TAVILY_API_KEY", ""))
    if not api_key:
        return _read_fixture(fixture_path or DEFAULT_FIXTURE_PATH, max_results=max_results)

    base_url = os.environ.get(
        "TAVILY_BASE_URL",
        env_file.get("TAVILY_BASE_URL", "https://api.tavily.com"),
    ).rstrip("/")
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

    payload = response.json()
    return [
        SearchResult(
            title=item["title"],
            url=item["url"],
            summary=item.get("content", ""),
        )
        for item in payload.get("results", [])[:max_results]
    ]


def _read_fixture(path: Path, *, max_results: int) -> list[SearchResult]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = [SearchResult.model_validate(item) for item in payload["results"]]
    return results[:max_results]
