# Prompt Changelog

## v0.1.0 - 2026-07-14

- 改了啥：新增 `json-schema-output.md` 和 `schema-retry.md`，分别约束结构化输出和校验失败后的修正输出。
- 为什么改：LLM 网关需要统一追加 JSON Schema，并将 Pydantic 校验错误回灌给模型重试。
- 改完效果：结构化调用会要求只返回 JSON；无效 JSON 最多自动修正 2 次。

## v0.2.0 - 2026-07-14

- 改了啥：新增 `personas/chatty_writer.md`、`terse_scientist.md`、`spin_ceo.md` 和 `wary_witness.md` 四个内置人设。
- 为什么改：为不同访谈训练目标提供稳定、可复用的嘉宾行为数据，并明确 inverse 压迫响应人设。
- 改完效果：四种人设分别突出打断与拉回、开放式提问、识别答非所问、共情与建立信任四类训练重点。

## v0.3.0 - 2026-07-14

- 改了啥：新增 `writer.md` 档案生成提示词和 `writer_critic.md` 独立审稿提示词。
- 为什么改：writer 需要基于搜索摘要生成 Dossier，并通过 Reflection 检查 guard 梯度、unlock_hint 可操作性及公开资料一致性。
- 改完效果：不合格档案会携带具体批评意见重生成，最多 2 轮，最终结果再写入 scenario 表。

## v0.4.0 - 2026-07-15

- 改了啥：新增 `guest_assess.md` 和 `guest.md`，把 pressure 评分、Persona 数值行为、PRESSURE/GUARD 决策表、tell 表演边界与 revealed 复述规则写成显式约束。
- 为什么改：Guest agent 需要先稳定识别本轮压力和目标 fact，再基于代码更新后的 guard 生成可复现、不会直接泄密的角色回答。
- 改完效果：单轮回答按“先 pressure、后 action”的顺序执行；话痨与惜字如金等人设有可测字数和行为边界，tell 只表现破绽而不承认事实。
