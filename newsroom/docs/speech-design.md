# Whisper 真实语音转录链路

## 链路

```text
浏览器 MediaRecorder / 音频文件
        ↓ multipart/form-data
POST /api/transcription
        ↓ PyAV 解码与 16kHz 单声道重采样
ModelScope openai-mirror/whisper-medium
        ↓ CUDA FP16 推理
转录文本回填输入框
        ↓ 用户确认或修正
POST /api/session/{id}/turn
```

语音只用于生成主持人问题文本，不替代现有的 turn、Guest、Director 和复盘状态机。
上传内容在内存中解码，不落盘；单文件上限 25MB，时长上限由
`WHISPER_MAX_AUDIO_SECONDS` 控制，默认 90 秒。

## 模型与运行时

- 模型：`openai-mirror/whisper-medium`
- 来源：ModelScope
- 架构：`WhisperForConditionalGeneration`
- 权重：Safetensors，约 3.06GB
- 完整性：启动时按 ModelScope 公布的 SHA-256 校验；损坏缓存不会进入推理
- 本机推理：RTX 5060 / CUDA 13.0 / FP16
- 音频解码：PyAV，不依赖系统 FFmpeg

模型按进程懒加载。演播室进入备稿阶段后会调用
`POST /api/transcription/warmup` 在后台预热；缓存存在时优先离线加载，缓存缺失时才从
ModelScope 下载。

## 实测

测试音频内容：`请你说明项目预算为什么翻倍，以及谁批准了这次变更。`

转录结果：`请你说明项目预算为什么翻倍以及谁批准了这次变更。`

- 音频时长：6.242 秒
- 首次下载：约 3.06GB，仅发生一次
- 已缓存后的进程首次加载并转录：约 16 秒
- 同一进程热转录：约 0.76 秒
- 推理设备：`cuda:0`

## 浏览器限制

`getUserMedia` 只在安全上下文开放。`localhost` 可以直接录音；普通局域网 HTTP
地址无法申请麦克风权限，但仍可以上传音频。若要求其他局域网设备直接录音，需要给
前端配置受设备信任的 HTTPS 证书。
