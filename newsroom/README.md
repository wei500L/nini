# Newsroom

本地局域网开发需要同时启动后端和前端。

## 后端

在 `backend` 目录用 uv 安装依赖。项目已把 PyTorch 指向 CUDA 13.0 官方源：

```powershell
uv sync
```

确认输出中的 PyTorch 版本带有 `+cu130`，然后运行：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 前端

在 `frontend` 目录运行：

```powershell
npm install
npm run dev
```

同一局域网的设备访问 `http://<本机局域网IP>:5173`。Vite 会把 `/api`
请求代理到本机后端。真实外部服务配置保存在被 Git 忽略的 `backend/.env`。

Whisper 模型首次转录时会从 ModelScope 下载约 3GB 权重到
`backend/.cache/modelscope`。通过 `localhost` 访问可以直接使用麦克风；普通局域网
HTTP 地址受浏览器安全策略限制，可以上传音频，或为开发服务配置 HTTPS 后录音。
