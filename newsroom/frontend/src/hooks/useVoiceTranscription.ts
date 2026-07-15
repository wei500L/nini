import { useEffect, useRef, useState } from "react";


type VoiceState = "idle" | "recording" | "transcribing";

type TranscriptionResponse = {
  text: string;
  language: string;
  duration_seconds: number;
  processing_ms: number;
  device: string;
  model_id: string;
};

const MAX_RECORDING_SECONDS = 90;

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || "语音转录失败";
  } catch {
    return "语音转录失败";
  }
}

function recorderMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ];
  return candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
}

function inferredAudioType(file: File) {
  if (file.type) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase();
  const types: Record<string, string> = {
    flac: "audio/flac",
    m4a: "audio/m4a",
    mp3: "audio/mpeg",
    mp4: "audio/mp4",
    ogg: "audio/ogg",
    wav: "audio/wav",
    webm: "audio/webm",
  };
  return types[extension || ""] || "application/octet-stream";
}

export function useVoiceTranscription(onText: (text: string) => void) {
  const [state, setState] = useState<VoiceState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<TranscriptionResponse | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const stopTimer = () => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const transcribe = async (file: File) => {
    setState("transcribing");
    setError(null);
    setLastResult(null);
    try {
      const normalizedFile = file.type
        ? file
        : new File([file], file.name, { type: inferredAudioType(file) });
      const form = new FormData();
      form.append("audio", normalizedFile);
      const response = await fetch("/api/transcription?language=zh", {
        method: "POST",
        body: form,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as TranscriptionResponse;
      setLastResult(result);
      onTextRef.current(result.text);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "语音转录失败");
    } finally {
      setState("idle");
    }
  };

  const startRecording = async () => {
    if (state !== "idle") return;
    setError(null);
    setLastResult(null);
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError("当前地址无法使用麦克风。请通过 localhost/HTTPS 访问，或上传音频文件。");
      return;
    }
    if (typeof MediaRecorder === "undefined") {
      setError("当前浏览器不支持 MediaRecorder，请上传音频文件。");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          autoGainControl: true,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      const mimeType = recorderMimeType();
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      setSeconds(0);

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stopTimer();
        releaseStream();
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        chunksRef.current = [];
        recorderRef.current = null;
        const extension = blob.type.includes("mp4") ? "m4a" : "webm";
        void transcribe(
          new File([blob], `question.${extension}`, { type: blob.type }),
        );
      };
      recorder.start(250);
      setState("recording");
      timerRef.current = window.setInterval(() => {
        setSeconds((current) => {
          const next = current + 1;
          if (next >= MAX_RECORDING_SECONDS) {
            recorderRef.current?.stop();
          }
          return next;
        });
      }, 1_000);
    } catch (reason) {
      releaseStream();
      setState("idle");
      setError(
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "麦克风权限被拒绝，请允许访问后重试。"
          : "无法启动麦克风，请检查系统输入设备。",
      );
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  };

  const uploadFile = (file: File | null) => {
    if (file && state === "idle") void transcribe(file);
  };

  useEffect(
    () => () => {
      stopTimer();
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.onstop = null;
        recorderRef.current.stop();
      }
      releaseStream();
    },
    [],
  );

  return {
    state,
    seconds,
    error,
    lastResult,
    startRecording,
    stopRecording,
    uploadFile,
    clearError: () => setError(null),
  };
}
