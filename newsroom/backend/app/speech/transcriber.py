from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from pydantic import BaseModel

from app.llm.config import BACKEND_ROOT, read_env_file


WhisperDevice = Literal["auto", "cpu", "cuda"]


class AudioDecodeError(ValueError):
    pass


class TranscriptionResult(BaseModel):
    text: str
    language: str
    duration_seconds: float
    processing_ms: int
    device: str
    model_id: str


@dataclass(frozen=True)
class WhisperConfig:
    model_id: str
    cache_dir: Path
    device: WhisperDevice
    default_language: str
    max_audio_seconds: float


def load_whisper_config(env_file: Path | None = None) -> WhisperConfig:
    file_values = read_env_file(env_file or BACKEND_ROOT / ".env")

    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default))

    device_value = env("WHISPER_DEVICE", "auto").strip().lower()
    if device_value not in {"auto", "cpu", "cuda"}:
        raise ValueError("WHISPER_DEVICE must be auto, cpu, or cuda")
    cache_value = env("WHISPER_CACHE_DIR", "./.cache/modelscope").strip()
    cache_dir = Path(cache_value)
    if not cache_dir.is_absolute():
        cache_dir = BACKEND_ROOT / cache_dir

    max_audio_seconds = float(env("WHISPER_MAX_AUDIO_SECONDS", "90"))
    if max_audio_seconds <= 0:
        raise ValueError("WHISPER_MAX_AUDIO_SECONDS must be positive")
    return WhisperConfig(
        model_id=env(
            "WHISPER_MODEL_ID",
            "openai-mirror/whisper-medium",
        ).strip(),
        cache_dir=cache_dir.resolve(),
        device=cast(WhisperDevice, device_value),
        default_language=env("WHISPER_LANGUAGE", "zh").strip() or "zh",
        max_audio_seconds=max_audio_seconds,
    )


class WhisperTranscriber:
    """Lazy, process-local Whisper model backed by a ModelScope snapshot."""

    def __init__(self, config: WhisperConfig | None = None) -> None:
        self.config = config or load_whisper_config()
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._device = "not-loaded"
        self._dtype: Any | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "model_id": self.config.model_id,
            "loaded": self._model is not None,
            "device": self._device,
            "default_language": self.config.default_language,
            "max_audio_seconds": self.config.max_audio_seconds,
            "last_error": self._last_error,
        }

    def warmup(self) -> dict[str, Any]:
        self._ensure_loaded()
        return self.status()

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        audio, duration = decode_audio(audio_bytes)
        if duration > self.config.max_audio_seconds:
            raise AudioDecodeError(
                f"audio exceeds {self.config.max_audio_seconds:g} seconds"
            )

        started = perf_counter()
        with self._inference_lock:
            self._ensure_loaded()
            text = self._transcribe_audio(
                audio,
                language=(language or self.config.default_language),
            )
        return TranscriptionResult(
            text=text,
            language=language or self.config.default_language,
            duration_seconds=round(duration, 3),
            processing_ms=round((perf_counter() - started) * 1000),
            device=self._device,
            model_id=self.config.model_id,
        )

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                import torch
                from modelscope import snapshot_download
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

                use_cuda = self.config.device == "cuda" or (
                    self.config.device == "auto" and torch.cuda.is_available()
                )
                if self.config.device == "cuda" and not torch.cuda.is_available():
                    raise RuntimeError("WHISPER_DEVICE=cuda but CUDA is unavailable")

                self.config.cache_dir.mkdir(parents=True, exist_ok=True)
                download_options = {
                    "cache_dir": str(self.config.cache_dir),
                    "allow_patterns": ["*.json", "*.txt", "model.safetensors"],
                }
                try:
                    model_dir = snapshot_download(
                        self.config.model_id,
                        local_files_only=True,
                        **download_options,
                    )
                except Exception:
                    model_dir = snapshot_download(
                        self.config.model_id,
                        **download_options,
                    )
                dtype = torch.float16 if use_cuda else torch.float32
                model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    model_dir,
                    dtype=dtype,
                    use_safetensors=True,
                )
                processor = AutoProcessor.from_pretrained(model_dir)
                device = "cuda:0" if use_cuda else "cpu"
                model.to(device)
                model.eval()

                self._torch = torch
                self._model = model
                self._processor = processor
                self._device = device
                self._dtype = dtype
                self._last_error = None
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                raise

    def _transcribe_audio(self, audio: Any, *, language: str) -> str:
        torch = self._torch
        model = self._model
        processor = self._processor
        if torch is None or model is None or processor is None:
            raise RuntimeError("Whisper model is not loaded")

        inputs = processor(
            audio,
            sampling_rate=16_000,
            truncation=False,
            padding="longest",
            return_attention_mask=True,
            return_tensors="pt",
        )
        input_features = inputs.input_features.to(
            device=self._device,
            dtype=self._dtype,
        )
        generation_kwargs: dict[str, Any] = {
            "language": language,
            "task": "transcribe",
            "return_timestamps": len(audio) > 30 * 16_000,
        }
        attention_mask = getattr(inputs, "attention_mask", None)
        if attention_mask is not None:
            generation_kwargs["attention_mask"] = attention_mask.to(self._device)

        with torch.inference_mode():
            predicted_ids = model.generate(input_features, **generation_kwargs)
        text = processor.batch_decode(
            predicted_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        if not text:
            raise AudioDecodeError("Whisper returned an empty transcription")
        return text


def decode_audio(audio_bytes: bytes) -> tuple[Any, float]:
    if not audio_bytes:
        raise AudioDecodeError("audio file is empty")
    try:
        import av
        import numpy as np

        chunks: list[Any] = []
        with av.open(io.BytesIO(audio_bytes), mode="r") as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise AudioDecodeError("file contains no audio stream")
            stream = audio_streams[0]
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16_000)
            for frame in container.decode(stream):
                resampled = resampler.resample(frame)
                frames = resampled if isinstance(resampled, list) else [resampled]
                for converted in frames:
                    if converted is not None:
                        chunks.append(converted.to_ndarray().reshape(-1))
            flushed = resampler.resample(None)
            frames = flushed if isinstance(flushed, list) else [flushed]
            for converted in frames:
                if converted is not None:
                    chunks.append(converted.to_ndarray().reshape(-1))
    except AudioDecodeError:
        raise
    except Exception as error:
        raise AudioDecodeError("unable to decode the uploaded audio") from error

    if not chunks:
        raise AudioDecodeError("audio stream contains no samples")
    audio = np.concatenate(chunks).astype(np.float32, copy=False)
    if audio.size < 1_600:
        raise AudioDecodeError("audio must be at least 0.1 seconds")
    return audio, audio.size / 16_000


__all__ = [
    "AudioDecodeError",
    "TranscriptionResult",
    "WhisperConfig",
    "WhisperTranscriber",
    "decode_audio",
    "load_whisper_config",
]
