import os

from fastapi import FastAPI
from sqlmodel import SQLModel, create_engine

from app.agents.judge import Judge
from app.orchestrator import Orchestrator, build_router
from app.scenarios import build_scenario_router
from app.speech import WhisperTranscriber, build_speech_router


DATABASE_URL = os.getenv("NEWSROOM_DATABASE_URL", "sqlite:///./newsroom.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SQLModel.metadata.create_all(engine)


app = FastAPI(title="newsroom")
orchestrator = Orchestrator(engine, judge=Judge())
whisper_transcriber = WhisperTranscriber()
app.include_router(build_scenario_router(engine))
app.include_router(build_speech_router(whisper_transcriber))
app.include_router(build_router(orchestrator))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
