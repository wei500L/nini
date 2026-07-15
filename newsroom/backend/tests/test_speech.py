from __future__ import annotations

import io
import math
import struct
import unittest
import wave

import httpx
from fastapi import FastAPI

from app.speech.router import build_speech_router
from app.speech.transcriber import TranscriptionResult, decode_audio


def wav_tone(*, seconds: float = 0.25, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = [
            struct.pack(
                "<h",
                round(8_000 * math.sin(2 * math.pi * 440 * index / sample_rate)),
            )
            for index in range(round(seconds * sample_rate))
        ]
        output.writeframes(b"".join(frames))
    return buffer.getvalue()


class FakeTranscriber:
    def status(self) -> dict[str, object]:
        return {"loaded": True, "device": "cuda:0"}

    def warmup(self) -> dict[str, object]:
        return self.status()

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        if not audio_bytes:
            raise AssertionError("audio bytes were not forwarded")
        return TranscriptionResult(
            text="这是一次真实转录",
            language=language or "zh",
            duration_seconds=1.2,
            processing_ms=320,
            device="cuda:0",
            model_id="openai-mirror/whisper-medium",
        )


class AudioDecodeTests(unittest.TestCase):
    def test_wav_is_resampled_to_whisper_input(self) -> None:
        audio, duration = decode_audio(wav_tone())

        self.assertAlmostEqual(duration, 0.25, places=2)
        self.assertEqual(audio.dtype.name, "float32")
        self.assertGreater(len(audio), 3_000)


class SpeechApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_reports_loaded_device(self) -> None:
        app = FastAPI()
        app.include_router(build_speech_router(FakeTranscriber()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/transcription/warmup")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"loaded": True, "device": "cuda:0"})

    async def test_audio_upload_returns_transcription(self) -> None:
        app = FastAPI()
        app.include_router(build_speech_router(FakeTranscriber()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/transcription?language=zh",
                files={"audio": ("question.wav", wav_tone(), "audio/wav")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "这是一次真实转录")
        self.assertEqual(response.json()["device"], "cuda:0")

    async def test_non_audio_upload_is_rejected(self) -> None:
        app = FastAPI()
        app.include_router(build_speech_router(FakeTranscriber()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/transcription",
                files={"audio": ("notes.txt", b"not audio", "text/plain")},
            )

        self.assertEqual(response.status_code, 415)


if __name__ == "__main__":
    unittest.main()
