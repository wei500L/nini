# Newsroom Documentation Index

Newsroom Interview Lab documentation for a **local-first multi-agent AI interview training platform** built with DeepSeek, Tavily MCP, Whisper, FastAPI, React, SQLite and Server-Sent Events.

本目录集中记录 Newsroom 的多智能体架构、采访状态机、真实来源门禁、三层记忆、语音转录和 Prompt 行为。安装与运行请先阅读 [项目完整 README](../README.md)；仓库级中英文项目介绍见 [Repository README](../../README.md)。

## Documentation Map

| 文档 | 主要问题 | 适合读者 |
| --- | --- | --- |
| [项目完整 README](../README.md) | 如何安装、配置、启动、测试和排障？ | 所有用户与开发者 |
| [访谈编排时序](sequence.md) | 会话如何从创建进入采访、流式回答和复盘？ | 后端开发、系统设计 |
| [三层记忆设计](memory-design.md) | 对话历史、FactState 和 Profile 为什么必须分开？ | Agent 开发、教学研究 |
| [Whisper 语音链路](speech-design.md) | 音频如何解码、校验、推理和回填？ | 语音、前后端开发 |
| [Prompt 变更记录](prompt-changelog.md) | Prompt 规则为什么修改，行为如何变化？ | Prompt 工程、测试维护 |
| [`app/prompts`](../backend/app/prompts/) | 当前实际生效的 Writer、Guest、Director、Judge Prompt | Agent 与 Prompt 开发 |
| [`tests`](../backend/tests/) | 哪些业务不变量已有自动化验证？ | 维护者、代码审查者 |

## Recommended Reading Paths

### 第一次运行项目

1. [环境要求](../README.md#环境要求)
2. [快速开始](../README.md#快速开始)
3. [环境变量](../README.md#环境变量)
4. [HTTPS 与局域网访问](../README.md#https-与局域网访问)
5. [常见问题](../README.md#常见问题)

### 理解多智能体架构

1. [项目定位与关键词](../README.md#项目定位与关键词)
2. [系统架构](../README.md#系统架构)
3. [访谈编排时序](sequence.md)
4. [三层记忆设计](memory-design.md)
5. [开发原则](../README.md#开发原则)

### 修改实时采访链路

1. `backend/app/agents/guest.py`
2. `backend/app/prompts/guest_assess.md`
3. `backend/app/prompts/guest_stream.md`
4. `backend/app/llm/gateway.py`
5. `backend/app/orchestrator.py`
6. `frontend/src/hooks/useSessionEvents.ts`
7. `backend/tests/test_gateway.py`
8. `backend/tests/test_guest.py`
9. `backend/tests/test_orchestrator.py`

### 修改语音转录

1. [Whisper 语音链路](speech-design.md)
2. `backend/app/speech/transcriber.py`
3. `backend/app/speech/router.py`
4. `frontend/src/hooks/useVoiceTranscription.ts`
5. `backend/tests/test_speech.py`

### 修改复盘与长期学习

1. [三层记忆设计](memory-design.md)
2. `backend/app/tools/stenographer.py`
3. `backend/app/agents/judge.py`
4. `backend/app/memory/profile.py`
5. `frontend/src/components/ReviewPage.tsx`
6. `backend/tests/test_stenographer.py`
7. `backend/tests/test_judge.py`
8. `backend/tests/test_profile.py`

## Architecture Glossary

| 名称 | 含义 |
| --- | --- |
| Dossier | 场景档案，包含公开简报、人物、人设参数、隐藏事实、来源和解锁提示 |
| Writer | 根据真实搜索结果编写 Dossier 的智能体 |
| Critic | 独立检查 Dossier 真实性、一致性和可玩性的反思角色 |
| Guest | 模拟采访对象并根据授权动作生成回答的智能体 |
| Director | 分析现场表现并给主持人耳返的智能体 |
| Stenographer | 将逐字稿转换为确定性客观指标的纯代码模块 |
| Judge | 基于逐字稿、指标和 Dossier 生成证据型复盘的智能体 |
| Orchestrator | 管理状态、计时、并发、事务、SSE 和失败恢复的编排器 |
| FactState | 单条隐藏事实的防线、连续追问次数和释放状态 |
| Profile | 跨场保存指标历史、慢性弱点、口头禅和人设成绩的学生画像 |
| `guest_delta` | DeepSeek 内容块经服务端 SSE 转发后的嘉宾增量事件 |
| truth/playability gate | 场景写入数据库前的真实性与可训练性门禁 |

## Core Design Decisions

### Model handles language; code owns truth

LLM 负责理解问题、生成自然语言和解释训练结果；事实释放、状态迁移、客观指标、数据事务和安全边界由代码决定。该分工使系统在模型输出具有概率性的情况下仍能保持可审计性。

### Real mode never pretends a fixture succeeded

Tavily 真实模式缺少凭据、握手失败或结果不足时直接报错。fixture 只能由测试显式启用，不能作为生产失败的静默后备。

### Streaming means provider-to-browser streaming

系统读取上游 Chat Completions SSE 的 `content` 增量，立即发布 `guest_delta`。它不等待完整回答，也不在浏览器端把完整字符串拆成定时字符。

### Memory must change future behavior

Profile 不是为了“存历史”而存在。慢性弱点和上一场建议会进入下一场 Director 与 Judge 的决策输入；如果记忆不能改变后续行为，就不算完成跨场学习闭环。

## Search Terms

The documentation is relevant to the following topics:

- AI agent architecture and multi-agent orchestration;
- stateful interview simulation and adaptive virtual guests;
- journalism education and interviewer coaching;
- grounded generation with Tavily MCP and source attribution;
- DeepSeek OpenAI-compatible streaming chat completions;
- local Whisper speech-to-text with ModelScope and PyTorch;
- FastAPI Server-Sent Events and React incremental rendering;
- deterministic evaluation, evidence-based LLM judging and student profiles;
- local-first AI applications and LAN HTTPS deployment.

中文主题包括：多智能体协作、新闻采访训练、模拟采访、主持人训练、事实核验、检索增强、导播提示、语音转录、量化复盘、长期记忆与局域网部署。

## Documentation Maintenance

修改文档时遵守以下规则：

1. README 中的默认模型、端口、环境变量和 API 必须与当前代码一致；
2. 不在文档、示例、截图或提交中写入真实 API Key；
3. Prompt 行为变化同步更新 [Prompt Changelog](prompt-changelog.md)；
4. 状态机变化同步更新 [访谈编排时序](sequence.md)；
5. Profile 或 FactState 语义变化同步更新 [三层记忆设计](memory-design.md)；
6. Whisper 模型、缓存、格式或时长限制变化同步更新 [语音链路](speech-design.md)；
7. 任何“已打通”“已验证”结论都应有测试、日志或真实任务记录支撑。
