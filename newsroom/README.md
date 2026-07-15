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

同一局域网的设备访问 `https://<本机局域网IP>:5173`。首次访问需在浏览器中
确认并信任本地开发证书；只有被浏览器视为安全上下文的 HTTPS 页面才能在局域网内
申请麦克风权限。Vite 会把
`/api` 和 `/health` 请求代理到本机后端。真实外部服务配置保存在被 Git 忽略的
`backend/.env`。

Whisper 模型首次转录时会从 ModelScope 下载约 3GB 权重到
`backend/.cache/modelscope`。开发服务默认启用 HTTPS；若浏览器或系统没有信任该
开发证书，麦克风仍可能被安全策略禁用，此时可先上传音频完成转录。

只在本机做无麦克风页面调试时，可以临时设置 `VITE_HTTPS=false` 后运行前端；
局域网正式体验不要关闭 HTTPS。
