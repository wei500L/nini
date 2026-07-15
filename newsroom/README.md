# Newsroom

本地局域网开发需要同时启动后端和前端。

## 后端

在 `backend` 目录安装 `pyproject.toml` 依赖后运行：

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
