from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.speech.transcriber import AudioDecodeError, TranscriptionResult


logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/wave",
    "audio/webm",
    "video/mp4",
    "video/webm",
}
ALLOWED_AUDIO_EXTENSIONS = {
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}


class SpeechTranscriber(Protocol):
    def status(self) -> dict[str, Any]: ...

    def warmup(self) -> dict[str, Any]: ...

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult: ...


def build_speech_router(transcriber: SpeechTranscriber) -> APIRouter:
    router = APIRouter(prefix="/api/transcription", tags=["transcription"])

    @router.get("/status")
    async def transcription_status() -> dict[str, Any]:
        return transcriber.status()

    @router.post("/warmup")
    async def warmup_transcriber() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(transcriber.warmup)
        except Exception as error:
            logger.exception("Whisper warmup failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="本地 Whisper 模型预热失败，请查看后端日志。",
            ) from error

    @router.post("", response_model=TranscriptionResult)
    async def transcribe_audio(
        audio: UploadFile = File(...),
        language: str | None = Query(default=None, min_length=2, max_length=12),
    ) -> TranscriptionResult:
        content_type = (audio.content_type or "").split(";", 1)[0].lower()
        extension = Path(audio.filename or "").suffix.lower()
        missing_generic_type = content_type in {"", "application/octet-stream"}
        if content_type not in ALLOWED_AUDIO_TYPES and not (
            missing_generic_type and extension in ALLOWED_AUDIO_EXTENSIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="不支持的音频格式，请使用 WebM、WAV、MP3、M4A、MP4、OGG 或 FLAC。",
            )

        content = await audio.read(MAX_AUDIO_BYTES + 1)
        await audio.close()
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="音频文件不能超过 25MB。",
            )
        try:
            return await asyncio.to_thread(
                transcriber.transcribe,
                content,
                language=language,
            )
        except AudioDecodeError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            logger.exception("Whisper transcription failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="本地 Whisper 转录失败，请查看后端日志。",
            ) from error

    return router


__all__ = [
    "ALLOWED_AUDIO_EXTENSIONS",
    "ALLOWED_AUDIO_TYPES",
    "MAX_AUDIO_BYTES",
    "build_speech_router",
]
