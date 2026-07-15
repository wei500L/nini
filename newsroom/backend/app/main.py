import os
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel import Session as DatabaseSession

from app.agents.judge import Judge
from app.database import build_engine
from app.llm.config import BACKEND_ROOT, load_config, read_env_file
from app.orchestrator import Orchestrator, build_router
from app.scenarios import build_scenario_router
from app.speech import WhisperTranscriber, build_speech_router


engine = build_engine()


app = FastAPI(title="newsroom")
orchestrator = Orchestrator(engine, judge=Judge())
whisper_transcriber = WhisperTranscriber()
app.include_router(build_scenario_router(engine))
app.include_router(build_speech_router(whisper_transcriber))
app.include_router(build_router(orchestrator))


@app.get("/health")
async def health() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        with DatabaseSession(engine) as db:
            db.exec(text("SELECT 1")).one()
        checks["database"] = {"ready": True}
    except Exception:
        checks["database"] = {"ready": False}

    try:
        config = load_config()
        checks["llm"] = {
            "ready": bool(config.api_key and config.base_url),
            "provider": config.provider,
            "model": config.model_for("smart"),
        }
    except (OSError, ValueError):
        checks["llm"] = {"ready": False}

    env = read_env_file(BACKEND_ROOT / ".env")
    tavily_mode = os.environ.get("TAVILY_MODE", env.get("TAVILY_MODE", "auto"))
    tavily_key = os.environ.get("TAVILY_API_KEY", env.get("TAVILY_API_KEY", ""))
    tavily_url = os.environ.get("TAVILY_MCP_URL", env.get("TAVILY_MCP_URL", ""))
    checks["search"] = {
        "ready": bool(
            tavily_mode == "real"
            and tavily_key
            and tavily_url
        ),
        "mode": tavily_mode,
    }
    checks["whisper"] = whisper_transcriber.status()
    checks["whisper"]["ready"] = checks["whisper"]["last_error"] is None
    ready = all(check.get("ready") for check in checks.values())
    return {"status": "ok" if ready else "degraded", "checks": checks}
