# Prompt Changelog

## v0.1.0 - 2026-07-14

- 改了啥：新增 `json-schema-output.md` 和 `schema-retry.md`，分别约束结构化输出和校验失败后的修正输出。
- 为什么改：LLM 网关需要统一追加 JSON Schema，并将 Pydantic 校验错误回灌给模型重试。
- 改完效果：结构化调用会要求只返回 JSON；无效 JSON 最多自动修正 2 次。
